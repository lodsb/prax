"""Finding things: the query, the two indexes, and their fusion.

Acronym expansion and the FTS5 match expression, BM25 over chunks and over
the document field, KNN over the chunk and document vectors (the usearch
files and their deltas), reciprocal rank fusion at document level, the
optional cross-encoder rerank, and the bookkeeping of which chunk has a
vector from which model.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from prax import chunking, config, embeddings, ontology
from prax import rerank as rerank_mod

from .base import (
    _ASIDE,
    _INDEX_LOCK,
    _NOW,
    _TOKEN,
    _delta,
    _delta_path,
    _doc_index_path,
    _drop_index_views,
    _get_vector,
    _has_vectors,
    _index,
    _index_path,
    _indexes,
    _knn,
    _open_index,
    _reading,
    _serialized,
    vectors_available,
)
from .documents import DOCTYPES, _chunk_shape, find_chunk

RERANK_DEPTH = 30  # hits rescored when reranking is on (rerank.depth)


RRF_K = 60  # reciprocal rank fusion constant


RRF_DEPTH = 100  # candidates per side before fusion (docs/eval: 30 vs 100 vs 300)


# The document-field BM25 list is short and precise (a field names what a
# document is); at equal weight a lone rank-1 field hit loses to any document
# two chunk lists agree on. A short query names a thing, so the field gets
# FIELD_WEIGHT there; a long paraphrase is about content, and the field's
# incidental word matches would mislead, so the weight fades to 1 by
# FIELD_WEIGHT_WORDS words (docs/eval/retrieval-field-2026-09-10.md).
FIELD_WEIGHT = 2.0


FIELD_WEIGHT_WORDS = 7


VEC_SEARCH_CAP = 4000  # widest KNN candidate set when post-filtering by kind


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


# A word the graph added to a query is searched only in the domains of the
# thing it names (``expand_query_senses``). Off, every expansion is searched
# everywhere, as before 2026-09-26: the switch the measurement flips.
SENSES = True

Scope = frozenset[str]


def _lives_in(con: sqlite3.Connection, entity_id: int, etype: str) -> Scope | None:
    """Where a thing's sense lives: its type's domains, and the domains of
    the documents that say something about it. None is everywhere.

    The type alone was too narrow. `synthesis` is a research method, and
    the paper a German question about "Signalsynthese" wanted is in the
    studio domain; a thing is where the library found it as well as where
    its type is declared.
    """
    from prax import ontology

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
    (`prax.compounds`): `Apfelkuchen` matches nothing where `Apfel` and
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
    from prax import compounds

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
        plain.update(alts)
        by_graph(tok, alts)
        for part in compounds.split(con, tok):
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


SEARCH_MODES = ("hybrid", "fts", "vec")


WIDE_SCOPE = 0.5  # a scope holding this share of the documents is filtered after
WIDE_DRAW = 3  # and the ranked list is drawn this many times deeper for it

_SHARES: dict[tuple[Scope, int], float] = {}


def _share(con: sqlite3.Connection, scope: Scope) -> float:
    """The share of the library's documents a scope holds, remembered
    while the library holds as many documents."""
    total = int(con.execute("SELECT count(*) FROM documents").fetchone()[0] or 0)
    key = (scope, total)
    if key not in _SHARES:
        where, args = _in_domains(scope)
        n = con.execute(
            f"SELECT count(*) FROM documents d WHERE 1 = 1{where}", args
        ).fetchone()[0]
        _SHARES[key] = (n or 0) / max(total, 1)
    return _SHARES[key]


def _in_domains(scope: Scope | None) -> tuple[str, tuple[str, ...]]:
    """A WHERE clause keeping the documents of ``scope`` (and those with
    no domain set, which are in every module), and its arguments."""
    if not scope:
        return "", ()
    marks = ",".join("?" * len(scope))
    clause = (
        " AND (json_extract(d.meta, '$.domains') IS NULL OR EXISTS"
        " (SELECT 1 FROM json_each(d.meta, '$.domains') j"
        f" WHERE j.value IN ({marks})))"
    )
    return clause, tuple(sorted(scope))


def _fts_search(
    con: sqlite3.Connection,
    query: str,
    limit: int,
    kind: str | None,
    *,
    snippets: bool = True,
    expr: str | None = None,
    scope: Scope | None = None,
) -> list[dict[str, Any]]:
    """BM25 over chunks. ``snippets=False`` skips the snippet() call, which
    reads every matched chunk's text and dominates the cost of deep lists;
    ``_fts_snippets`` fills them in for the few hits that survive fusion.
    ``scope`` keeps the chunks of documents in those domains, inside the
    ranking: a sense like `apple` matches thousands of chunks elsewhere,
    and a filter after the ranking would leave none."""
    expr = expr or _fts_query(query)
    if expr is None:
        return []
    snippet_col = (
        "snippet(chunks_fts, 0, '[', ']', '…', 12)"
        if snippets
        else "substr(c.text, 1, 160)"
    )
    where, where_args = _in_domains(scope)
    # a scope that is most of the library is dense among the best matches:
    # rank in the index, draw deeper, filter after. Inside the ranking it
    # joined every matched chunk first, and "signal synthesis" scoped to
    # research took 940 ms against 83 (2026-09-26). A small scope (the
    # kitchen, where `apple` has thousands of chunks elsewhere) keeps the
    # filter inside
    wide = bool(scope) and _share(con, scope) >= WIDE_SCOPE
    if kind is None and not snippets and (not scope or wide):
        # rank inside the keyword index alone, then join the survivors: the
        # joins to chunks and documents ran for every matched row before
        # the sort, and doubled a common query's cost (51 ms against 25 on
        # "feedback delay network" over 3.4 M chunks, 2026-09-22). The
        # aside kinds are dropped after; the inner list is three times as
        # deep so they seldom cost a slot (they are few: reference entries
        # and ask blocks)
        sql = f"""
            SELECT c.id AS chunk_id, c.doc_id, d.title,
                   {snippet_col} AS snippet,
                   f.score,
                   c.kind, c.locator, c.heading, c.data
            FROM (SELECT rowid, bm25(chunks_fts) AS score FROM chunks_fts
                  WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?) f
            JOIN chunks c ON c.id = f.rowid
            JOIN documents d ON d.id = c.doc_id
            WHERE 1 = 1 {_ASIDE}{where}
            ORDER BY f.score LIMIT ?
            """
        deeper = limit * (3 * WIDE_DRAW if wide else 3)
        args: tuple[Any, ...] = (expr, deeper, *where_args, limit)
    else:
        # a kind asked for is a small share of the chunks (tables, figures),
        # so its filter has to sit inside the ranking; snippet() needs the
        # keyword index in the outer query (the raw keyword mode): the join
        # stays there for both
        kind_clause = "AND c.kind = ?" if kind is not None else _ASIDE
        sql = f"""
            SELECT c.id AS chunk_id, c.doc_id, d.title,
                   {snippet_col} AS snippet,
                   bm25(chunks_fts) AS score,
                   c.kind, c.locator, c.heading, c.data
            FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
                            JOIN documents d ON d.id = c.doc_id
            WHERE chunks_fts MATCH ? {kind_clause}{where}
            ORDER BY score LIMIT ?
            """
        args = (
            (expr, kind, *where_args, limit)
            if kind is not None
            else (expr, *where_args, limit)
        )
    rows = con.execute(sql, args).fetchall()
    out = []
    for r in rows:
        hit = {k: r[k] for k in ("chunk_id", "doc_id", "title", "snippet", "score")}
        hit.update(_chunk_shape(r))
        out.append(hit)
    return out


def _fts_snippets(
    con: sqlite3.Connection, query: str, chunk_ids: list[int], expr: str | None = None
) -> dict[int, str]:
    """Match-marked snippets for a handful of chunks (the fused hits)."""
    expr = expr or _fts_query(query)
    if expr is None or not chunk_ids:
        return {}
    marks = ",".join("?" * len(chunk_ids))
    rows = con.execute(
        f"""
        SELECT rowid, snippet(chunks_fts, 0, '[', ']', '…', 12) AS snippet
        FROM chunks_fts WHERE chunks_fts MATCH ? AND rowid IN ({marks})
        """,
        (expr, *chunk_ids),
    ).fetchall()
    return {r["rowid"]: r["snippet"] for r in rows}


def _vec_search(
    con: sqlite3.Connection, vector: Any, limit: int, kind: str | None
) -> list[dict[str, Any]]:
    """KNN over the usearch index (cosine distance), joined to live chunks.

    The index has no filter of its own, so ``kind`` is applied after the
    search over a wider candidate set (chunks of one kind are a few percent
    of the index). Keys whose chunk no longer exists are dropped here.
    """
    model = embeddings.current().name  # type: ignore[union-attr]
    if not _has_vectors(_index_path(model)):
        return []
    want = limit * (25 if kind is not None else 3)
    found = _knn(_index_path(model), vector, min(max(want, limit), VEC_SEARCH_CAP))
    if not found:
        return []
    distance = dict(found)
    ids = list(distance)
    out: list[dict[str, Any]] = []
    for i in range(0, len(ids), 500):
        part = ids[i : i + 500]
        marks = ",".join("?" * len(part))
        kind_clause = "AND c.kind = ?" if kind is not None else _ASIDE
        args: tuple[Any, ...] = (*part, kind) if kind is not None else tuple(part)
        rows = con.execute(
            f"""
            SELECT c.id AS chunk_id, c.doc_id, d.title, c.kind, c.locator,
                   c.heading, c.data, substr(c.text, 1, 160) AS head
            FROM chunks c JOIN documents d ON d.id = c.doc_id
            WHERE c.id IN ({marks}) {kind_clause}
            """,
            args,
        ).fetchall()
        for r in rows:
            hit = {
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "title": r["title"],
                "snippet": r["head"],
                "score": distance[r["chunk_id"]],
            }
            hit.update(_chunk_shape(r))
            out.append(hit)
    out.sort(key=lambda h: h["score"])
    return out[:limit]


def _field_fts_search(
    con: sqlite3.Connection,
    query: str,
    limit: int,
    expr: str | None = None,
    scope: Scope | None = None,
) -> list[dict[str, Any]]:
    """BM25 over the document field; hits carry no chunk yet."""
    expr = expr or _fts_query(query)
    if expr is None:
        return []
    where, where_args = _in_domains(scope)
    rows = con.execute(
        f"""
        SELECT f.rowid AS doc_id, d.title,
               snippet(documents_fts, 0, '[', ']', '…', 14) AS snippet,
               bm25(documents_fts) AS score
        FROM documents_fts f JOIN documents d ON d.id = f.rowid
        WHERE documents_fts MATCH ?{where} ORDER BY score LIMIT ?
        """,
        (expr, *where_args, limit),
    ).fetchall()
    return [_field_hit(r["doc_id"], r["title"], r["snippet"], r["score"]) for r in rows]


def _field_vec_search(
    con: sqlite3.Connection, model: str, vector: Any, limit: int
) -> list[dict[str, Any]]:
    """KNN over the document-field index."""
    if not _has_vectors(_doc_index_path(model)):
        return []
    found = _knn(_doc_index_path(model), vector, limit)
    if not found:
        return []
    distance = dict(found)
    ids = list(distance)
    marks = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT d.id, d.title, f.field FROM documents d"
        f" JOIN documents_fts f ON f.rowid = d.id WHERE d.id IN ({marks})",
        ids,
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    return [
        _field_hit(i, by_id[i]["title"], by_id[i]["field"][:160], distance[i])
        for i in ids
        if i in by_id
    ]


def _field_hit(doc_id: int, title: str, snippet: str, score: float) -> dict[str, Any]:
    return {
        "chunk_id": None,
        "doc_id": doc_id,
        "title": title,
        "snippet": snippet,
        "score": score,
        "kind": None,
        "heading": [],
        "page": None,
        "figure": None,
    }


def _fill_chunks(
    con: sqlite3.Connection,
    hits: list[dict[str, Any]],
    query: str,
) -> None:
    """A hit that came from the document field alone gets the document's
    chunk that speaks to the query (``documents.find_chunk``), so every
    hit opens somewhere; the field snippet stays, it says why the
    document matched."""
    for h in hits:
        if h.get("chunk_id") is not None:
            continue
        found = find_chunk(con, h["doc_id"], query)
        if found is None:
            continue
        h["chunk_id"] = found["chunk_id"]
        for k in ("kind", "heading", "page", "figure"):
            h[k] = found[k]


def _filter_doctype(
    con: sqlite3.Connection, hits: list[dict[str, Any]], doctype: str
) -> list[dict[str, Any]]:
    if not hits:
        return hits
    ids = list({h["doc_id"] for h in hits})
    marks = ",".join("?" * len(ids))
    keep = {
        r[0]
        for r in con.execute(
            f"SELECT d.id FROM documents d WHERE d.id IN ({marks})"
            f" AND {DOCTYPES[doctype]}",
            ids,
        )
    }
    return [h for h in hits if h["doc_id"] in keep]


def _rrf(
    ranked: list[list[dict[str, Any]]],
    names: list[str],
    limit: int,
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Reciprocal rank fusion at document level.

    Each side contributes a document's best chunk rank: score(doc) = sum over
    sides of 1/(RRF_K + rank of its first chunk in that list). Fusing chunk
    ids instead lets a long document's many chunks crowd each list and never
    adds one document's evidence across sides; measured on the library set
    that was worse than FTS alone (docs/eval/). One hit per document is
    returned, carrying the chunk that ranked best on the side that found it
    first, plus ``fts_rank`` / ``vec_rank`` (document ranks, None if absent).
    """
    fused: dict[int, dict[str, Any]] = {}
    for hits, name in zip(ranked, names, strict=True):
        weight = (weights or {}).get(name, 1.0)
        seen: set[int] = set()
        rank = 0
        for hit in hits:
            doc = hit["doc_id"]
            if doc in seen:
                continue
            seen.add(doc)
            rank += 1
            entry = fused.get(doc)
            if entry is None:
                entry = {**hit, "score": 0.0, **{f"{n}_rank": None for n in names}}
                fused[doc] = entry
            elif entry.get("chunk_id") is None and hit.get("chunk_id") is not None:
                # a field-only entry adopts the first chunk another side found
                for k in ("chunk_id", "kind", "heading", "page", "figure"):
                    entry[k] = hit.get(k)
            entry["score"] += weight / (RRF_K + rank)
            entry[f"{name}_rank"] = rank
    out = sorted(fused.values(), key=lambda h: -h["score"])
    return out[:limit]


@_reading
def vec_status(con: sqlite3.Connection) -> dict[str, Any]:
    """Whether vectors are usable and how many exist, per model, plus the
    state of the current model's index file."""
    status: dict[str, Any] = {"available": vectors_available(), "rows": 0, "models": {}}
    rows = con.execute(
        "SELECT model, count(*) AS n FROM chunk_embeddings GROUP BY model"
    ).fetchall()
    status["models"] = {r["model"]: r["n"] for r in rows}
    status["rows"] = sum(status["models"].values())
    status["documents"] = {
        r[0]: r[1]
        for r in con.execute(
            "SELECT model, count(*) FROM document_embeddings GROUP BY model"
        )
    }
    emb = embeddings.current()
    status["embedder"] = emb.name if emb else None
    status["index"] = None
    if emb is not None and vectors_available():
        idx = _index(emb.name, writable=False)
        if idx is not None:
            status["index"] = idx.stats()
        try:
            status["delta"] = delta_counts(emb.name)
        except Exception:  # noqa: BLE001 - a status must not fail on a stale file
            status["delta"] = None
        if status["index"] is not None and status["delta"]:
            # what a query can find: the main file plus the delta
            status["index"]["count"] += status["delta"]["chunks"]
    return status


@_reading
def search(
    con: sqlite3.Connection,
    query: str,
    limit: int = 10,
    *,
    kind: str | None = None,
    mode: str = "hybrid",
    rerank: bool | None = None,
    doctype: str | None = None,
    domain: str | None = None,
    timing: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Search returning compact snippets + ids (agent-shaped). ``timing``,
    when given, is filled with the seconds each side took (``fts``,
    ``embed``, ``vec``, ``field``, ``dvec``, ``finish``): what the door's
    slow-request log says of a search that took long.

    ``hybrid`` fuses four rank lists at document level: chunk BM25, chunk
    KNN, and BM25 and KNN over the document field (title, kind, summary;
    ``documents_fts``), so a query that names what a document *is* finds it
    even when other documents mention the term more. ``doctype`` keeps
    documents of one type: ``pdf``, ``web``, ``image``, ``text``, ``note``.

    ``hybrid`` (default) runs FTS5/BM25 and vector KNN in parallel and fuses
    them with reciprocal rank fusion; each hit carries ``score`` (the fused
    score), ``fts_rank`` and ``vec_rank`` (None when absent from that list).
    It degrades to FTS-only when embeddings are disabled, no index exists
    or no vectors exist yet. ``fts`` and ``vec`` force one side.
    Every hit carries the chunk's ``kind``, section ``heading`` path and
    ``page``, and a figure chunk's ``figure`` reference (the image is
    ``GET /doc/{doc_id}/figure/{figure}``); ``kind`` filters to one kind.

    ``rerank`` rescores the top ``RERANK_DEPTH`` hits with the configured
    cross-encoder (``prax.rerank``; None follows ``PRAX_RERANK``, which is
    off by default) and adds ``rerank_score``.
    """
    if kind is not None and kind not in chunking.KINDS:
        raise ValueError(f"kind must be one of {chunking.KINDS}")
    if mode not in SEARCH_MODES:
        raise ValueError(f"mode must be one of {SEARCH_MODES}")
    if doctype is not None and doctype not in DOCTYPES:
        raise ValueError(f"doctype must be one of {tuple(DOCTYPES)}")
    reranker = rerank_mod.current() if rerank is None or rerank else None
    if rerank and reranker is None:
        raise ValueError("rerank requested but PRAX_RERANK names no model")
    if domain is not None and domain not in ontology.current().modules:
        raise ValueError(f"unknown domain {domain!r}")
    depth = config.number("rerank.depth", "PRAX_RERANK_DEPTH", RERANK_DEPTH)
    fetch = max(limit, int(depth)) if reranker else limit
    if domain:
        fetch *= 3
    hits = _search_hits(con, query, fetch, kind, mode, doctype, timing)
    if domain:
        hits = _filter_domain(con, hits, domain)
    if reranker is not None and hits:
        with _Took(timing)("rerank"):
            hits = _apply_rerank(con, reranker, query, hits)
    return hits[:limit]


def _filter_domain(
    con: sqlite3.Connection, hits: list[dict[str, Any]], domain: str
) -> list[dict[str, Any]]:
    """Keep hits whose document is in ``domain``; a document without a
    domain set is in every module and stays."""
    ids = {h["doc_id"] for h in hits}
    if not ids:
        return hits
    marks = ",".join("?" * len(ids))
    rows = con.execute(
        "SELECT id, json_extract(meta, '$.domains') FROM documents"
        f" WHERE id IN ({marks})",
        tuple(ids),
    ).fetchall()
    allowed = {r[0] for r in rows if not r[1] or domain in json.loads(r[1])}
    return [h for h in hits if h["doc_id"] in allowed]


def _apply_rerank(
    con: sqlite3.Connection,
    reranker: rerank_mod.Reranker,
    query: str,
    hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ids = [h["chunk_id"] for h in hits]
    marks = ",".join("?" * len(ids))
    texts = {
        r["id"]: r["text"]
        for r in con.execute(f"SELECT id, text FROM chunks WHERE id IN ({marks})", ids)
    }
    scores = reranker.score(query, [texts.get(i, "") for i in ids])
    for h, s in zip(hits, scores, strict=True):
        h["rerank_score"] = float(s)
    return sorted(hits, key=lambda h: -h["rerank_score"])


def _search_hits(
    con: sqlite3.Connection,
    query: str,
    limit: int,
    kind: str | None,
    mode: str,
    doctype: str | None = None,
    timing: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    emb = embeddings.serving() if mode != "fts" else None
    vectors_ready = (
        emb is not None
        and vectors_available()
        and _has_vectors(_index_path(emb.name))
        and _vec_count(con) > 0
    )
    if mode == "vec" and not vectors_ready:
        raise ValueError("vector search unavailable: no embedder, index or vectors")
    fetch = limit * 4 if doctype else limit
    # the pages that are the model's own answers (a standing question, a
    # briefing) are never evidence: a question would find its own page
    # first and the model would cite itself
    aside = _aside_documents(con)
    # the query as terms with the library's own expansions of its acronyms;
    # the keyword side without the stopwords (the embedder sees them all):
    # one OR expression for recall, one AND expression for the tier that
    # wants every term present (only when there is more than one term)
    terms, senses = expand_query_senses(con, query)
    if not SENSES:
        senses = {}
    keywords = keyword_terms(terms)
    # the senses are searched where they live, each scope a list of its
    # own; what is left is searched everywhere, as it always was
    plain = _plain_terms(keywords, senses) or keywords
    scopes = sorted(set(senses.values()), key=sorted)
    sensed = [
        (scope, _expr(_sense_terms(keywords, senses, scope), all_terms=False))
        for scope in scopes
    ]
    full_expr = _expr(keywords, all_terms=False)  # what a snippet marks
    or_expr = _expr(plain, all_terms=False)
    and_expr = _expr(plain, all_terms=True) if len(plain) > 1 else None
    if mode == "fts":  # the raw chunk list, expanded but not fused
        hits = _fts_sensed(
            con, query, fetch + len(aside), kind, or_expr, sensed, snippets=True
        )
        hits = _without(hits, aside)
        return _finish(con, hits, query, limit, doctype, expr=full_expr)
    depth = max(limit * 3, RRF_DEPTH)
    # keyword lists: any term (recall), optionally every term, and the rare
    # terms alone: "adaa iir" must not be decided by the thousands of chunks
    # that say "iir"
    weights: dict[str, float] = {
        "fts_all": ALL_TERMS_WEIGHT,
        "fts_rare": RARE_TERMS_WEIGHT,
    }
    took = _Took(timing)
    with took("fts"):
        lists = [_fts_sensed(con, query, depth, kind, or_expr, sensed)]
    names = ["fts"]
    if and_expr and ALL_TERMS_WEIGHT > 0:
        with took("fts"):
            lists.append(
                _fts_search(con, query, depth, kind, snippets=False, expr=and_expr)
            )
        names.append("fts_all")
    rare = _rare_terms(con, plain) if RARE_TERMS_WEIGHT > 0 else []
    if rare and len(rare) == len(terms):
        # the whole query is rare tokens ("adaa"): the keyword list carries
        # the weight itself, vector neighbours of letters must not outvote it
        weights["fts"] = RARE_TERMS_WEIGHT
    if rare and len(rare) < len(terms):
        rare_expr = _expr(rare, all_terms=True)
        lists.append(
            _fts_search(con, query, depth, kind, snippets=False, expr=rare_expr)
        )
        names.append("fts_rare")
    if not vectors_ready:
        if kind is None:  # hybrid without vectors: the field too
            _field_lists(con, query, depth, or_expr, sensed, lists, names, weights)
        fused = _rrf([_without(x, aside) for x in lists], names, fetch, weights)
        return _finish(con, fused, query, limit, doctype, expr=full_expr)
    assert emb is not None
    with took("embed"):
        vector = emb.embed_query(expanded_text(terms) if VEC_EXPAND else query)
    if mode == "vec":
        return _finish(
            con,
            _without(_vec_search(con, vector, fetch + len(aside), kind), aside),
            query,
            limit,
            doctype,
            expr=full_expr,
        )
    with took("vec"):
        lists.append(_vec_search(con, vector, depth, kind))
    names.append("vec")
    if kind is None:  # the field has no chunk kind to filter by
        with took("field"):
            _field_lists(con, query, depth, or_expr, sensed, lists, names, weights)
        with took("dvec"):
            lists.append(_field_vec_search(con, emb.name, vector, depth))
        names.append("dvec")
    fused = _rrf([_without(x, aside) for x in lists], names, fetch, weights)
    with took("finish"):
        return _finish(con, fused, query, limit, doctype, expr=full_expr)


def _field_lists(
    con: sqlite3.Connection,
    query: str,
    depth: int,
    or_expr: str | None,
    sensed: list[tuple[Scope, str | None]],
    lists: list[list[dict[str, Any]]],
    names: list[str],
    weights: dict[str, float],
) -> None:
    """The document field's keyword list, its senses counted where they
    live (``_by_score``)."""
    hits = _field_fts_search(con, query, depth, expr=or_expr)
    for scope, expr in sensed:
        hits += _field_fts_search(con, query, depth, expr=expr, scope=scope)
    lists.append(_by_score(hits, "doc_id", depth) if sensed else hits)
    names.append("field")
    weights["field"] = _field_weight(query)


def _fts_sensed(
    con: sqlite3.Connection,
    query: str,
    depth: int,
    kind: str | None,
    or_expr: str | None,
    sensed: list[tuple[Scope, str | None]],
    *,
    snippets: bool = False,
) -> list[dict[str, Any]]:
    """The chunk keyword list, its senses counted where they live."""
    hits = _fts_search(con, query, depth, kind, snippets=snippets, expr=or_expr)
    for scope, expr in sensed:
        hits += _fts_search(
            con, query, depth, kind, snippets=snippets, expr=expr, scope=scope
        )
    return _by_score(hits, "chunk_id", depth) if sensed else hits


def _by_score(hits: list[dict[str, Any]], key: str, depth: int) -> list[dict[str, Any]]:
    """One keyword list out of the plain one and the scoped ones, by BM25,
    each hit once at its best.

    A term's BM25 weight does not depend on the rest of the expression, so
    a chunk's score under the plain words and under the plain words with a
    sense differ by what the sense adds. Merged, a chunk in the sense's
    domains scores as it did before senses were scoped, and one outside
    scores as if the graph had not added the word. As extra rank lists the
    senses were extra votes instead: every document of the scope that
    matched any word was counted twice, and a German question about signal
    synthesis lost its paper to the other research documents
    (2026-09-26)."""
    seen: set[int] = set()
    out = []
    for h in sorted(hits, key=lambda h: h["score"]):
        if h[key] not in seen:
            seen.add(h[key])
            out.append(h)
    return out[:depth]


ASIDE_PAGE_KINDS = ("question", "briefing")


def _aside_documents(con: sqlite3.Connection) -> frozenset[int]:
    """The documents a search never returns: the pages that are the
    model's own answers (``ASIDE_PAGE_KINDS``)."""
    marks = ",".join("?" * len(ASIDE_PAGE_KINDS))
    return frozenset(
        r[0]
        for r in con.execute(
            f"SELECT doc_id FROM pages WHERE kind IN ({marks})", ASIDE_PAGE_KINDS
        )
    )


def _without(hits: list[dict[str, Any]], aside: frozenset[int]) -> list[dict[str, Any]]:
    if not aside:
        return hits
    return [h for h in hits if h["doc_id"] not in aside]


class _Took:
    """``with took("vec"):`` adds the block's seconds to ``timing["vec"]``
    (nothing when no dict was given)."""

    def __init__(self, timing: dict[str, float] | None) -> None:
        self.timing = timing
        self.name = ""
        self.t0 = 0.0

    def __call__(self, name: str) -> _Took:
        self.name = name
        return self

    def __enter__(self) -> None:
        self.t0 = time.perf_counter()

    def __exit__(self, *exc: object) -> None:
        if self.timing is not None:
            self.timing[self.name] = (
                self.timing.get(self.name, 0.0) + time.perf_counter() - self.t0
            )


def _field_weight(query: str) -> float:
    words = len(query.split())
    if words <= 3:
        return FIELD_WEIGHT
    span = max(1, FIELD_WEIGHT_WORDS - 3)
    return max(1.0, FIELD_WEIGHT - (FIELD_WEIGHT - 1.0) * (words - 3) / span)


def _finish(
    con: sqlite3.Connection,
    hits: list[dict[str, Any]],
    query: str,
    limit: int,
    doctype: str | None,
    expr: str | None = None,
) -> list[dict[str, Any]]:
    if doctype:
        hits = _filter_doctype(con, hits, doctype)
    hits = hits[:limit]
    field_only = {h["doc_id"] for h in hits if h.get("chunk_id") is None}
    _fill_chunks(con, hits, query)
    chunk_ids = [
        h["chunk_id"]
        for h in hits
        if h.get("chunk_id") is not None
        and h["doc_id"] not in field_only
        and h.get("fts_rank") is not None
    ]
    marked = _fts_snippets(con, query, chunk_ids, expr=expr) if chunk_ids else {}
    for h in hits:
        if h.get("chunk_id") in marked:
            h["snippet"] = marked[h["chunk_id"]]
    return hits


def _vec_count(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0]


def _all_chunks_embedded(con: sqlite3.Connection, model: str) -> bool:
    """Two counts before the scan: when every chunk has its vector from
    ``model`` there is nothing to look for. The scan below walks a
    million chunks probing the embeddings table for each — seventeen
    seconds with nothing pending, under the store's lock, every worker
    cycle: the door's searches stood in that queue (2026-09-18)."""
    chunks = con.execute(f"SELECT count(*) FROM chunks c WHERE 1=1{_ASIDE}").fetchone()[
        0
    ]
    done = con.execute(
        "SELECT count(*) FROM chunk_embeddings WHERE model = ?", (model,)
    ).fetchone()[0]
    return done >= chunks


@_reading
def pending_embeddings(
    con: sqlite3.Connection, model: str, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Chunks without a vector from ``model``: ``{chunk_id, kind, text}``,
    the newest first — new chunks are where the work is, and the scan
    stops at ``limit`` as soon as it has them."""
    if _all_chunks_embedded(con, model):
        return []
    sql = (
        "SELECT c.id AS chunk_id, c.kind, c.text FROM chunks c"
        " LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id"
        f" WHERE (e.chunk_id IS NULL OR e.model != ?){_ASIDE} ORDER BY c.id DESC"
    )
    args: tuple[Any, ...] = (model,)
    if limit is not None:
        sql += " LIMIT ?"
        args = (model, limit)
    return [dict(r) for r in con.execute(sql, args)]


@_reading
def count_pending_embeddings(con: sqlite3.Connection, model: str) -> int:
    """How many chunks ``pending_embeddings`` would return, without the text."""
    if _all_chunks_embedded(con, model):
        return 0
    return con.execute(
        "SELECT count(*) FROM chunks c"
        " LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id"
        " WHERE e.chunk_id IS NULL OR e.model != ?",
        (model,),
    ).fetchone()[0]


@_serialized
def store_embeddings(
    con: sqlite3.Connection,
    items: list[tuple[int, str | None, Any]],
    model: str,
) -> int:
    """Add ``(chunk_id, kind, vector)`` rows for ``model`` to its index
    (in memory until ``save_vectors``) and record them in ``chunk_embeddings``.
    Requires the usearch library; the batch job calls this."""
    if not vectors_available():
        raise RuntimeError("usearch is not installed; vectors cannot be stored")
    if not items:
        return 0
    idx = _delta(_index_path(model))
    ids = [cid for cid, _, _ in items]
    import numpy as np

    with _INDEX_LOCK:
        idx.add(ids, np.vstack([np.asarray(v, dtype=np.float32) for _, _, v in items]))
    con.executemany(
        "INSERT INTO chunk_embeddings (chunk_id, model) VALUES (?, ?)"
        " ON CONFLICT(chunk_id) DO UPDATE SET model = excluded.model,"
        f" embedded_at = {_NOW}",
        [(cid, model) for cid in ids],
    )
    con.commit()
    return len(items)


@_reading
def pending_document_embeddings(
    con: sqlite3.Connection, model: str, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Document fields without a vector from ``model``: ``{doc_id, text}``,
    the newest first."""
    sql = (
        "SELECT f.rowid AS doc_id, f.field AS text FROM documents_fts f"
        " LEFT JOIN document_embeddings e ON e.doc_id = f.rowid"
        " WHERE e.doc_id IS NULL OR e.model != ? ORDER BY f.rowid DESC"
    )
    args: tuple[Any, ...] = (model,)
    if limit is not None:
        sql += " LIMIT ?"
        args = (model, limit)
    return [dict(r) for r in con.execute(sql, args)]


@_reading
def count_pending_document_embeddings(con: sqlite3.Connection, model: str) -> int:
    return con.execute(
        "SELECT count(*) FROM documents_fts f"
        " LEFT JOIN document_embeddings e ON e.doc_id = f.rowid"
        " WHERE e.doc_id IS NULL OR e.model != ?",
        (model,),
    ).fetchone()[0]


@_serialized
def store_document_embeddings(
    con: sqlite3.Connection, items: list[tuple[int, Any]], model: str
) -> int:
    """Add ``(doc_id, vector)`` rows to the document index of ``model`` (in
    memory until ``save_document_vectors``) and record them."""
    if not vectors_available():
        raise RuntimeError("usearch is not installed; vectors cannot be stored")
    if not items:
        return 0
    idx = _delta(_doc_index_path(model))
    import numpy as np

    ids = [d for d, _ in items]
    with _INDEX_LOCK:
        idx.add(ids, np.vstack([np.asarray(v, dtype=np.float32) for _, v in items]))
    con.executemany(
        "INSERT INTO document_embeddings (doc_id, model) VALUES (?, ?)"
        " ON CONFLICT(doc_id) DO UPDATE SET model = excluded.model,"
        f" embedded_at = {_NOW}",
        [(d, model) for d in ids],
    )
    con.commit()
    return len(items)


DELTA_MERGE_AT = 50_000  # vectors in the delta before a merge is due


def _save_delta(path: Path) -> dict[str, Any]:
    """Write the delta beside ``path`` (small, nobody maps it) and merge it
    into the main file when it has grown past ``DELTA_MERGE_AT``."""
    with _INDEX_LOCK:
        delta = _delta(path)
        delta.save()
        due = len(delta) >= DELTA_MERGE_AT or not path.exists()
        if not due:
            main = _open_index(path, writable=False)
            return {
                "count": (len(main) if main is not None else 0) + len(delta),
                "delta": len(delta),
                "bytes": path.stat().st_size if path.exists() else 0,
            }
    return _merge(path)  # a large delta, or no main file yet: outside the lock


def _merge(path: Path) -> dict[str, Any]:
    """Fold the delta into the main file. The building — the main index
    loaded into memory, the delta's vectors added, the result written to a
    file beside it — happens outside the index lock: a 1.2 GB file with
    fifty thousand new vectors takes half a minute, and every search of
    the evening waited on it. The lock is held twice, briefly: to take the
    delta's vectors, and to swap the new file in — this process's views
    dropped first (Windows will not replace a mapped file) — with what
    arrived during the build kept in a fresh delta."""
    import numpy as np

    from prax import vectors as vectors_mod

    from .base import VEC_DIM

    dpath = _delta_path(path)
    with _INDEX_LOCK:
        delta = _delta(path)
        keys = [int(k) for k in delta.all_keys()]
        vecs = np.vstack([delta.get(k) for k in keys]) if keys else None
    taken = set(keys)
    # the build: a private in-memory copy of the main file, nobody's view
    main = vectors_mod.VectorIndex(path, VEC_DIM, writable=True)
    if keys:
        main.add(keys, vecs)
    tmp = path.with_suffix(path.suffix + ".merging")
    main.save_to(tmp)
    main.close()
    # a door that serves the index from memory loads the new file now,
    # outside the lock (seconds for a gigabyte), and installs the loaded
    # copy at the swap; a mapping door reopens at the swap (milliseconds)
    preloaded = (
        vectors_mod.VectorIndex(tmp, VEC_DIM, writable=False)
        if vectors_mod.serve_in_memory()
        else None
    )
    with _INDEX_LOCK:
        delta = _delta(path)
        later = [int(k) for k in delta.all_keys() if int(k) not in taken]
        later_vecs = np.vstack([delta.get(k) for k in later]) if later else None
        for key in [(str(path), False), (str(path), True), (str(dpath), True)]:
            idx = _indexes.pop(key, None)
            if idx is not None:
                idx.close()
        os.replace(tmp, path)
        if dpath.exists():
            dpath.unlink()
        if later:
            fresh = _delta(path)  # a new, empty one
            fresh.add(later, later_vecs)
            fresh.save()
        if preloaded is not None:
            preloaded.path = path
            _indexes[(str(path), False)] = preloaded
        reopened = _open_index(path, writable=False)
        return {
            "count": (len(reopened) if reopened is not None else 0) + len(later),
            "merged": len(keys),
            "delta": len(later),
            "bytes": path.stat().st_size,
        }


@_reading
def warm_fts(con: sqlite3.Connection) -> dict[str, int]:
    """Read the keyword index through once (``chunks_fts_data``, 0.7 GB
    at 1.4 M chunks, a second from the disk), so the first searches after
    a start do not pay for it a posting list at a time: a query of common
    words took 25 s cold on the desktop, where llama-server's model file
    holds the operating system's cache, and 0.1 s warm."""
    rows, size = con.execute(
        "SELECT count(*), coalesce(sum(length(block)), 0) FROM chunks_fts_data"
    ).fetchone()
    con.execute("SELECT coalesce(sum(length(field)), 0) FROM documents_fts").fetchone()
    return {"blocks": int(rows), "bytes": int(size)}


def fts_merge(con: sqlite3.Connection, *, seconds: float = 60.0) -> dict[str, int]:
    """Merge the keyword index's segments a little at a time (FTS5's
    ``merge``, 500 pages a step) for up to ``seconds``: every batch of
    chunks leaves a segment behind, and a term spread over two dozen of
    them is read from two dozen places. Returns the steps taken and the
    segments before and after."""
    before = con.execute("SELECT count(DISTINCT segid) FROM chunks_fts_idx").fetchone()[
        0
    ]
    steps = 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        changes = con.total_changes
        con.execute("INSERT INTO chunks_fts(chunks_fts, rank) VALUES('merge', 500)")
        con.commit()
        steps += 1
        if con.total_changes - changes <= 1:  # nothing left to merge
            break
    after = con.execute("SELECT count(DISTINCT segid) FROM chunks_fts_idx").fetchone()[
        0
    ]
    return {
        "steps": steps,
        "segments_before": int(before),
        "segments_after": int(after),
    }


def warm_indexes(model: str) -> dict[str, int]:
    """Open the read views of ``model``'s two index files now, so no
    request pays for it: with ``vectors.serve: memory`` the load takes
    seconds (a hundred while llama-server reads its model from the same
    disk), and it happened under the index lock, on the first search."""
    out: dict[str, int] = {}
    for name, path in (
        ("chunks", _index_path(model)),
        ("documents", _doc_index_path(model)),
    ):
        idx = _open_index(path, writable=False) if path.exists() else None
        out[name] = len(idx) if idx is not None else 0
    return out


def save_document_vectors(model: str) -> dict[str, Any]:
    return _save_delta(_doc_index_path(model))  # index files only, as save_vectors


def save_vectors(model: str) -> dict[str, Any]:
    """Write ``model``'s new vectors (the delta) to disk; the main file is
    rewritten only when the delta is large (``merge_vectors``). The
    bookkeeping rows are committed as they are written, so a crash between
    two saves leaves rows that the next ``pending_embeddings`` run will not
    repeat; ``compact_vectors`` reconciles the two. Not behind the store's
    write lock: it touches the index files only, which have a lock of
    their own — a merge inside it once held every write for half a minute."""
    return _save_delta(_index_path(model))


@_serialized
def merge_vectors(model: str) -> dict[str, Any]:
    """Fold both deltas of ``model`` into their main files now."""
    return {
        "chunks": _merge(_index_path(model)),
        "documents": _merge(_doc_index_path(model)),
    }


@_reading
def delta_counts(model: str) -> dict[str, int]:
    out = {}
    paths = (("chunks", _index_path(model)), ("documents", _doc_index_path(model)))
    with _INDEX_LOCK:
        for name, path in paths:
            dpath = _delta_path(path)
            idx = _indexes.get((str(dpath), True))
            if idx is None and dpath.exists():
                idx = _delta(path)
            out[name] = len(idx) if idx is not None else 0
    return out


def adopt_vectors(
    con: sqlite3.Connection, model: str, *, job: Any | None = None
) -> dict[str, int]:
    """Record that this model's index already holds vectors for these
    chunks — the way back from a model switch, without recomputing.

    ``chunk_embeddings.chunk_id`` is the primary key and the embed step
    writes ``ON CONFLICT DO UPDATE SET model``, so the table remembers
    **one model per chunk**: re-embedding into a new model overwrites the
    record that the old one's vectors exist. The vectors themselves are
    untouched — they are in ``vectors-<old>.usearch``, which nothing
    deletes — so going back is bookkeeping rather than compute, and this
    is that bookkeeping.

    It is the mirror of ``compact_vectors``, which forgets rows whose
    vector is missing. This claims vectors whose row is missing, for the
    chunks and documents that still exist; a key whose chunk is gone is
    left alone, because the row would not survive the foreign key.

    Used after ``embeddings.model`` in prax.yaml is put back, and then
    the door restarted so it opens that model's index.

    **A job, not a request.** The first version of this was `@_serialized`
    and held the store's write lock *and* the index lock for the whole
    run, which wedged the door: `/health` answered in 0.6 s while
    `/search` timed out (2026-09-26). The keys are read once, outside
    both; the rows go in batches, each its own short write; and the
    caller is a thread with a job row, so progress is visible and nothing
    else waits on it.
    """
    out = {"chunks": 0, "documents": 0, "missing": 0}
    for what, path, table, column in (
        ("chunks", _index_path(model), "chunk_embeddings", "chunk_id"),
        ("documents", _doc_index_path(model), "document_embeddings", "doc_id"),
    ):
        table_of = "chunks" if what == "chunks" else "documents"
        live = [r[0] for r in con.execute(f"SELECT id FROM {table_of}")]
        take = _index_has(path, live)
        if not take:
            continue
        for i in range(0, len(take), 20_000):
            _adopt_batch(con, table, column, model, take[i : i + 20_000])
            if job is not None:
                job.note(f"{what}: {min(i + 20_000, len(take)):,} of {len(take):,}")
        out[what] = len(take)
    return out


@_serialized
def _adopt_batch(
    con: sqlite3.Connection, table: str, column: str, model: str, keys: list[int]
) -> int:
    """One batch of bookkeeping, behind the write lock for its own length
    and no longer."""
    con.executemany(
        f"INSERT INTO {table} ({column}, model) VALUES (?, ?)"
        f" ON CONFLICT({column}) DO UPDATE SET model = excluded.model,"
        f" embedded_at = {_NOW}",
        [(k, model) for k in keys],
    )
    con.commit()
    return len(keys)


def _index_has(path: Path, ids: list[int]) -> list[int]:
    """Which of ``ids`` this index and its delta hold.

    Asked of the index rather than read out of it. Reading every key and
    intersecting in Python took over twenty-five minutes of a core on a
    1.58 M-vector file and wedged the door doing it (2026-09-26);
    ``contains`` over the ids actually wanted is vectorised and answers
    in 0.13 s for 1.12 M. The question was always the intersection, so
    asking for the whole key set was work nobody needed.
    """
    if not ids:
        return []
    import numpy as np

    want = np.asarray(ids, dtype=np.uint64)
    found = np.zeros(len(want), dtype=bool)
    with _INDEX_LOCK:
        for p_ in (path, _delta_path(path)):
            if not p_.exists():
                continue
            with contextlib.suppress(Exception):
                # an empty or half-written file says nothing, which is an
                # answer: it holds none of them
                from usearch.index import Index

                idx = Index.restore(str(p_), view=True)
                if idx is not None and len(idx):
                    found |= np.asarray(idx.contains(want))
    return [int(x) for x in want[found]]


@_serialized
def compact_vectors(con: sqlite3.Connection, model: str) -> dict[str, int]:
    """Reconcile the index with the bookkeeping: drop keys whose chunk is
    gone, and forget bookkeeping rows whose vector is missing from the
    index (so they get embedded again). Saves the index."""
    _merge(_index_path(model))
    idx = _index(model, writable=True)
    assert idx is not None
    live = {r[0] for r in con.execute("SELECT id FROM chunks")}
    booked = {
        r[0]
        for r in con.execute(
            "SELECT chunk_id FROM chunk_embeddings WHERE model = ?", (model,)
        )
    }
    keys = {int(k) for k in idx.all_keys()}
    stale = keys - live
    removed = idx.remove(stale)
    missing = booked - keys
    if missing:
        ids = list(missing)
        for i in range(0, len(ids), 500):
            part = ids[i : i + 500]
            marks = ",".join("?" * len(part))
            con.execute(
                f"DELETE FROM chunk_embeddings WHERE chunk_id IN ({marks})", part
            )
        con.commit()
    _drop_index_views(model)
    idx.save()
    count = len(idx)
    _indexes.pop((str(_index_path(model)), True), None)
    idx.close()  # the writable copy is not kept around: the delta takes new vectors
    return {"removed_stale": removed, "forgot_missing": len(missing), "count": count}


CONTEXT_LIMIT = 8


CENTROID_CHUNKS = 64  # chunk vectors averaged for a document's similarity query


CENTROID_MIN_CHARS = 120  # shorter chunks are headers and template lines


@_reading
def similar_documents(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    limit: int = CONTEXT_LIMIT,
    domain: str | None = None,
) -> list[dict[str, Any]]:
    """Documents nearest to this one in vector space: the centroid of up to
    ``CENTROID_CHUNKS`` of its text chunks' vectors (spread over the
    document), one KNN, grouped by document, scored by the best hit.
    ``domain`` keeps the neighbours of one ontology module."""
    return _similar_documents(con, doc_id, limit=limit, domain=domain)


def _similar_documents(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    limit: int = CONTEXT_LIMIT,
    domain: str | None = None,
) -> list[dict[str, Any]]:
    """Two views fused by reciprocal rank: the document-field vector's
    neighbours (what the document is; one KNN over the document index) and
    the chunk-centroid neighbours (what it says; one KNN over the chunk
    index, grouped by document). The centroid skips chunks under
    ``CENTROID_MIN_CHARS`` (boilerplate headers) that pull it toward every
    document sharing the same template."""
    emb = embeddings.serving()
    if emb is None:
        return []
    import numpy as np  # the embed extra; only reachable when an index exists

    depth = max(40, 5 * limit)
    lists: list[list[int]] = []
    dpath = _doc_index_path(emb.name)
    if _has_vectors(dpath) and (fv := _get_vector(dpath, doc_id)) is not None:
        found = _knn(dpath, fv, depth + 1)
        lists.append([int(k) for k, _ in found if int(k) != doc_id][:depth])
    cpath = _index_path(emb.name)
    if _has_vectors(cpath):
        rows = con.execute(
            "SELECT c.id FROM chunks c JOIN chunk_embeddings e ON e.chunk_id = c.id"
            " WHERE c.doc_id = ? AND e.model = ? AND c.kind = 'text'"
            " AND length(c.text) >= ? ORDER BY c.seq",
            (doc_id, emb.name, CENTROID_MIN_CHARS),
        ).fetchall()
        ids = [r["id"] for r in rows]
        step = max(1, len(ids) // CENTROID_CHUNKS)
        vecs = [
            v
            for k in ids[::step][:CENTROID_CHUNKS]
            if (v := _get_vector(cpath, k)) is not None
        ]
        if vecs:
            centroid = np.mean(np.stack(vecs), axis=0)
            norm = float(np.linalg.norm(centroid)) or 1.0
            found = _knn(
                cpath,
                (centroid / norm).astype(np.float32),
                min(VEC_SEARCH_CAP, 40 * limit),
            )
            keys = [int(k) for k, _ in found]
            order: list[int] = []
            for i in range(0, len(keys), 500):
                part = keys[i : i + 500]
                marks = ",".join("?" * len(part))
                by_chunk = {
                    r["id"]: r["doc_id"]
                    for r in con.execute(
                        f"SELECT id, doc_id FROM chunks WHERE id IN ({marks})", part
                    )
                }
                for k in part:
                    d = by_chunk.get(k)
                    if d is not None and d != doc_id and d not in order:
                        order.append(d)
            lists.append(order[:depth])
    if not lists:
        return []
    scores: dict[int, float] = {}
    for ranked in lists:
        for rank, d in enumerate(ranked, 1):
            scores[d] = scores.get(d, 0.0) + 1.0 / (RRF_K + rank)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if domain:  # a wider slice, then the module's documents, then the cut
        ranked = [
            (did, score)
            for did, score in ranked[: max(limit * 5, 40)]
            if _filter_domain(con, [{"doc_id": did}], domain)
        ]
    out = []
    for did, score in ranked[:limit]:
        d = con.execute(
            "SELECT title, mime FROM documents WHERE id = ?", (did,)
        ).fetchone()
        if d is not None:
            out.append(
                {
                    "doc_id": did,
                    "title": d["title"],
                    "mime": d["mime"],
                    "score": round(score, 4),
                }
            )
    return out
