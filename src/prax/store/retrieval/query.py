"""The query: the words left out, the FTS5 match expression the store
builds (never the user's string), acronyms the library defines, and the
words the graph adds through an entity's labels and senses."""

from __future__ import annotations

import json
import sqlite3

from prax.graph import ontology

from ..base import (
    _TOKEN,
    _serialized,
)
from .compounds import forms as compound_forms
from .compounds import split as compound_split
from .knobs import knobs

# The words the keyword side leaves out of a query. In a million chunks
# "a", "in" and "and" each match two thirds, so an OR expression holding
# them scores that many rows: a natural-language query took 1.7 s warm
# and 18 s cold on the desktop, 0.06 s without them. FTS5's BM25 gives a
# term in more than half the rows a negative idf besides, so they pulled
# the ranking the wrong way. English and German, the library's
# languages; a query of stopwords alone keeps them. The vector side sees
# the whole query.
STOPWORDS = frozenset(
    """
    a an the and or of to in on at for by with from as is are was were be
    been being it its this that these those there here what which who whom
    how why when where do does did done not no nor so if then than into
    about over under between through during before after above below up
    down out off again further once all any both each few more most other
    some such only own same too very can will just should would could may
    might must shall we you he she they them their our your my i me his her
    der die das den dem des ein eine einer eines einem einen und oder aber
    nicht mit von zu zum zur im am auf für über unter aus bei nach vor
    ist sind war waren wird werden wurde ich du er sie es wir ihr man dass
    als auch noch nur wie was wer wo wann warum
    """.split()  # noqa: SIM905 - a word list reads as one
)


def _keyword(token: str) -> bool:
    """A token the keyword side scores: not a stopword, not a lone
    character — a "2" or an "a" is in two thirds of a million chunks
    (25 s cold for the one OR term), and never what a query is about."""
    return len(token) > 1 and token.lower() not in STOPWORDS


def keyword_terms(terms: list[list[str]]) -> list[list[str]]:
    """The terms for the keyword side: the stopwords and lone characters
    left out, unless the query is nothing but."""
    kept = [t for t in terms if t and _keyword(t[0])]
    return kept or terms


def _fts_query(query: str) -> str | None:
    """Build a safe FTS5 MATCH expression: every token quoted, joined by OR.

    User strings are never passed to MATCH raw; punctuation and FTS operators
    in the input cannot raise. OR keeps recall for natural multi-word queries;
    BM25 ranks chunks that match more (and rarer) terms first. The
    stopwords are left out (``STOPWORDS``) unless the query is nothing but.
    """
    tokens = _TOKEN.findall(query)
    if not tokens:
        return None
    kept = [t for t in tokens if _keyword(t)] or tokens
    return " OR ".join(f'"{t}"' for t in kept)


ACRONYM_MIN_DOCS = 1  # one definition is enough: the phrase only adds an alternative


ACRONYM_EXPANSIONS = 2


RARE_CHUNKS = 50  # a token in fewer chunks than this decides the ranking


RARE_MAX_LEN = 6  # longer tokens are words, not acronyms, unless the table knows them


# Measured on the 62 library queries (docs/eval/retrieval-acronyms-2026-09-12.md):
# keyword-side expansion alone lifts MRR 0.89 -> 0.905; expanding the embedder's
# input and a rank list of chunks holding every term both cost; the rare-terms
# list costs one query and is what makes "adaa iir" find the ADAA papers.
ALL_TERMS_WEIGHT = 0.0  # the rank list of chunks holding every query term; 0 = off


RARE_TERMS_WEIGHT = 3.0  # the rank list of chunks holding the rare terms


VEC_EXPAND = False  # embed the query as typed; expansions only on the keyword side


@_serialized
def replace_acronyms(con: sqlite3.Connection, rows: list[tuple[str, str, int]]) -> int:
    """Replace the acronyms table (the ``acronyms`` pass of ``store.maintain``):
    ``(acronym, expansion, documents)`` rows, lowercased."""
    con.execute("DELETE FROM acronyms")
    con.executemany(
        "INSERT INTO acronyms (acronym, expansion, docs) VALUES (?, ?, ?)",
        [(a.lower(), e.lower(), int(n)) for a, e, n in rows],
    )
    con.commit()
    return len(rows)


def acronym_expansions(
    con: sqlite3.Connection,
    token: str,
    *,
    min_docs: int = ACRONYM_MIN_DOCS,
    limit: int = ACRONYM_EXPANSIONS,
) -> list[str]:
    """The phrases the library defines ``token`` as, best attested first."""
    return [
        r[0]
        for r in con.execute(
            "SELECT expansion FROM acronyms WHERE acronym = ? AND docs >= ?"
            " ORDER BY docs DESC, expansion LIMIT ?",
            (token.lower(), min_docs, limit),
        )
    ]


NAMED_AS = 2  # names an entity answers to, per query term


def known_as(con: sqlite3.Connection, text: str, *, limit: int = NAMED_AS) -> list[str]:
    """The names the graph gives a thing this text is a name of.

    The vocabulary pass left a dictionary behind it: `Olivenöl` is a
    label of the entity called `olive oil`, `Verklemmung` of `deadlock`.
    Nothing had to be built for it, and it is the library's own word for
    the thing rather than a translation service's, with a document behind
    every pair.

    That is what a German query needs on the keyword side, where the
    vectors already cope and BM25 cannot: MRR 0.39 against English's 0.91
    (`docs/eval/retrieval-multilingual-2026-09-24.md`), because nothing
    tells FTS5 that Faltung and convolution are one word.
    """
    return [name for _, name, _ in _named_as(con, text, limit=limit)]


def _named_as(
    con: sqlite3.Connection, text: str, *, limit: int = NAMED_AS
) -> list[tuple[int, str, str]]:
    """``known_as`` with the id and type of the thing each name belongs to."""
    return [
        (int(r[0]), str(r[1]), str(r[2]))
        for r in con.execute(
            "SELECT DISTINCT e.id, e.name, e.type FROM entity_labels l"
            " JOIN entities e ON e.id = l.entity_id"
            " WHERE l.label = ? COLLATE NOCASE AND e.canonical_id IS NULL"
            " AND lower(e.name) != lower(?) LIMIT ?",
            (text, text, max(1, limit)),
        )
    ]


Scope = frozenset[str]


def _lives_in(con: sqlite3.Connection, entity_id: int, etype: str) -> Scope | None:
    """Where a thing's sense lives: its type's domains, and the domains of
    the documents that say something about it. None is everywhere.

    The type alone was too narrow. `synthesis` is a research method, and
    the paper a German question about "Signalsynthese" wanted is in the
    studio domain; a thing is where the library found it as well as where
    its type is declared.
    """

    where = ontology.current().domains_of(etype)
    if where is None:
        return None
    found: set[str] = set(where)
    for (raw,) in con.execute(
        "SELECT DISTINCT json_extract(d.meta, '$.domains') FROM documents d"
        " WHERE d.id IN (SELECT source_doc FROM edges"
        "  WHERE src = ? AND valid_to IS NULL"
        "  UNION SELECT source_doc FROM edges WHERE dst = ? AND valid_to IS NULL)",
        (entity_id, entity_id),
    ):
        if not raw:
            return None  # a document of every module says it
        found.update(json.loads(raw))
    return frozenset(found)


def expand_query(con: sqlite3.Connection, query: str) -> list[list[str]]:
    """``expand_query_senses`` without the senses: every alternative."""
    return expand_query_senses(con, query)[0]


def expand_query_senses(
    con: sqlite3.Connection, query: str
) -> tuple[list[list[str]], dict[str, Scope]]:
    """The query as terms, each a list of alternatives: the token itself,
    the phrases the library defines it as (``[["adaa", "antiderivative
    antialiasing"], ["iir"]]``), and the name the graph knows the thing
    by where the token is one of its other names. Tokens of nine or more
    characters, and digits, are never acronyms.

    A compound the library has no term for is added as its halves
    (``compounds``): `Apfelkuchen` matches nothing where `Apfel` and
    `Kuchen` each match, and German builds nouns that way. The halves are
    alternatives beside the word, not instead of it, so a library that
    holds the compound still ranks it first.

    The whole query is looked up as one name too, because a thing is
    often several words (`dünn besetzte Matrizen`) and no single token of
    it is the name.

    **The senses** are the alternatives only the graph added, each with
    the domains of the thing it names (``_lives_in``). The user
    typed `Apfelkuchen`; the graph added `apple`, reached through the
    ingredient, and a search that matched it everywhere filled its first
    page with Logic manuals, which name the organization
    (docs/eval/apfelkuchen-2026-09-26.md). A sense is searched only where
    its thing lives. An alternative that also arrives another way (the
    token itself, a phrase the library defines, a compound half) or
    through a thing of a core type, which lives everywhere, is no sense.
    """

    plain: set[str] = set()
    scoped: dict[str, set[str]] = {}

    def by_graph(text: str, alts: list[str]) -> None:
        for entity_id, name, etype in _named_as(con, text):
            n = name.lower()
            where = _lives_in(con, entity_id, etype)
            if where is None:
                plain.add(n)
            else:
                scoped.setdefault(n, set()).update(where)
            if n not in alts:
                alts.append(n)

    terms: list[list[str]] = []
    for tok in _TOKEN.findall(query):
        alts = [tok.lower()]
        if 2 <= len(tok) <= 8 and tok.isalpha():
            alts += [e for e in acronym_expansions(con, tok) if e != tok.lower()]
        if knobs.INFLECT:
            # the forms of the word the library also uses: taco and tacos
            alts += [f for f in compound_forms(con, tok) if f not in alts]
        plain.update(alts)
        by_graph(tok, alts)
        for part in compound_split(con, tok):
            plain.add(part)
            if part not in alts:
                alts.append(part)
            # and the half goes through the graph too, because that is the
            # chain the whole thing is for: Apfelkuchen -> apfel -> apple
            by_graph(part, alts)
        terms.append(alts)
    whole = " ".join(_TOKEN.findall(query))
    if len(terms) > 1 and whole:
        named: list[str] = []
        by_graph(whole, named)
        if named:
            # one more term, OR-ed with the rest: a document using the
            # English name matches even though no single token did
            terms.append(named)
    senses = {n: frozenset(w) for n, w in scoped.items() if n not in plain}
    return terms, senses


def _plain_terms(terms: list[list[str]], senses: dict[str, Scope]) -> list[list[str]]:
    """The terms without the senses: what is searched everywhere."""
    out = [[a for a in t if a not in senses] for t in terms]
    return [t for t in out if t]


def _sense_terms(
    terms: list[list[str]], senses: dict[str, Scope], scope: Scope
) -> list[list[str]]:
    """The terms with the senses of one scope: what is searched there."""
    out = [[a for a in t if a not in senses or senses[a] == scope] for t in terms]
    return [t for t in out if t]


def expanded_text(terms: list[list[str]]) -> str:
    """The query with its expansions, for the embedder."""
    return " ".join(alt for term in terms for alt in term)


def _expr(terms: list[list[str]], *, all_terms: bool) -> str | None:
    """MATCH expression: every alternative quoted (a phrase stays a phrase),
    alternatives OR-ed within a term, terms OR-ed (recall) or AND-ed (the
    tier that wants every term present)."""
    if not terms:
        return None
    groups = ["(" + " OR ".join(f'"{a}"' for a in term) + ")" for term in terms]
    return (" AND " if all_terms else " OR ").join(groups)


def _rare_terms(con: sqlite3.Connection, terms: list[list[str]]) -> list[list[str]]:
    """The acronym-shaped terms that match fewer than ``RARE_CHUNKS`` chunks
    (and at least one): a rare exact token like "adaa" should decide the
    ranking, not the common words around it, so those terms get a rank list
    of their own. Only short tokens, tokens with digits and known acronyms
    qualify: a rare inflection of an ordinary word ("reassigning",
    "upmixing") pulled paraphrase queries towards the wrong documents."""
    out = []
    for term in terms:
        tok = term[0]
        acronym_shaped = (
            len(tok) <= RARE_MAX_LEN or any(ch.isdigit() for ch in tok) or len(term) > 1
        )
        if not acronym_shaped:
            continue
        expr = _expr([term], all_terms=False)
        n = con.execute(
            "SELECT count(*) FROM (SELECT rowid FROM chunks_fts WHERE chunks_fts"
            " MATCH ? LIMIT ?)",
            (expr, RARE_CHUNKS),
        ).fetchone()[0]
        if 0 < n < RARE_CHUNKS:
            out.append(term)
    return out
