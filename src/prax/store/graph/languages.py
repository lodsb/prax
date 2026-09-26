"""One name per thing across languages: the store's half of the vocabulary
step, both ways, and the corpus's recorded rulings."""

from __future__ import annotations

import sqlite3
from typing import Any

from prax import ontology

from ..base import _reading, _serialized
from .decisions import queue_review
from .labels import add_label, merge_entities


def _named_by_language(con: sqlite3.Connection, entity_id: int) -> str | None:
    """The language of a document that named this entity, where they all
    agree. Two languages naming one thing say nothing about the name."""
    rows = con.execute(
        "SELECT DISTINCT json_extract(d.meta, '$.lang') AS lang FROM edges x"
        " JOIN documents d ON d.id = x.source_doc"
        " WHERE x.valid_to IS NULL AND (x.src = ? OR x.dst = ?)"
        " AND json_extract(d.meta, '$.lang') IS NOT NULL LIMIT 3",
        (entity_id, entity_id),
    ).fetchall()
    return str(rows[0]["lang"]) if len(rows) == 1 else None


@_serialized
def name_in_english(
    con: sqlite3.Connection,
    entity_id: int,
    english: str,
    *,
    lang: str | None = None,
    producer: str = "vocabulary",
    run: str | None = None,
    confidence: str | None = None,
) -> dict[str, Any]:
    """Give an entity the name English uses, keeping the one the document
    used as a label in its own language.

    Three outcomes, and the caller is told which. An entity of the same
    type already called that: the two are merged, and the German name
    becomes a label of the survivor. An entity of *another* type called
    that: nothing is merged — ``Olivenöl`` is an ingredient where ``olive
    oil`` is a concept, and which of the two is right is the review
    queue's question, not this pass's. Nobody called that yet: the entity
    is renamed and the old name kept as a label, so a German search still
    reaches it.

    Everything this writes carries ``producer`` and ``run``, so a round
    is undoable whole (``unmerge_run``).
    """
    from prax import language

    english = " ".join(english.split())
    if not english:
        raise ValueError("a name cannot be empty")
    row = con.execute(
        "SELECT id, name, type, canonical_id FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such entity: {entity_id}")
    if row["canonical_id"]:
        # already folded into another entity, which `foreign_names` skips
        return {
            "entity": entity_id,
            "action": "merged away",
            "into": row["canonical_id"],
        }
    was = str(row["name"])
    # a name is one to three words, which is under the detector's floor, so
    # it says nothing about nearly all of them — and a label whose language
    # is unknown cannot answer "show me this in German". The document that
    # named the entity knows: `meta.lang` of a document with an edge to it
    lang = lang or language.detect(was) or _named_by_language(con, entity_id)
    # the same name can be several entities, of several types: the one to
    # fold into is one of this entity's own type that is still standing.
    # Without the ordering `page table` the concept (itself already merged
    # away) answered for `page table` the method, and a fold that should
    # have happened was reported as a clash instead (2026-09-24)
    twin = con.execute(
        "SELECT id, type, canonical_id FROM entities WHERE name = ? COLLATE NOCASE"
        " AND id != ? ORDER BY (type = ?) DESC, (canonical_id IS NULL) DESC, id"
        " LIMIT 1",
        (english, entity_id, row["type"]),
    ).fetchone()
    out: dict[str, Any] = {"entity": entity_id, "was": was, "name": english}
    # what a fold would land on is the twin's survivor, not the twin: a
    # twin of this entity's own type may itself have been folded into one
    # of another type, and comparing the twin let that through to
    # `merge_entities`, which refused it — silently, because the worker's
    # summary line does not print errors (2026-09-24)
    into = None
    if twin is not None:
        into = con.execute(
            "SELECT id, type FROM entities WHERE id = ?",
            (int(twin["canonical_id"] or twin["id"]),),
        ).fetchone()
    if into is not None and into["type"] != row["type"]:
        # the same words, a different kind of thing: a question, not a fold
        queue_review(
            con,
            src=was,
            src_type=row["type"],
            rel="same_as",
            dst=english,
            dst_type=str(into["type"]),
            reason=(
                f"{producer}: {was!r} is {english!r} in English, but the library"
                f" has that as a {into['type']} and this as a {row['type']}"
            ),
            source_doc=None,
        )
        add_label(
            con,
            entity_id,
            english,
            lang=language.canonical(),
            kind="alt",
            producer=producer,
            run=run,
            confidence=confidence,
        )
        out["action"] = "type clash"
        out["twin"] = int(into["id"])
        return out
    if into is not None:
        survivor = int(into["id"])
        if survivor == entity_id:
            # the English name is already one of this entity's own: an
            # earlier pass folded it in. Nothing to do — but say so with a
            # label, because an outcome that writes nothing leaves the
            # entity a candidate and the pass asks about it for ever
            # (62 of them, on the night of 2026-09-24)
            add_label(
                con,
                entity_id,
                english,
                lang=language.canonical(),
                kind="pref",
                producer=producer,
                run=run,
                confidence=confidence,
            )
            return {**out, "action": "already"}
        merge_entities(
            con,
            entity_id,
            survivor,
            producer=producer,
            run=run,
            confidence=confidence,
        )
        out["action"] = "merged"
        out["into"] = survivor
        return out
    # a rename is two label writes and nothing else: the name the
    # document used stops being preferred in its language, the English one
    # starts being preferred in English, and `entities.name` follows the
    # labels (docs/identity.md). `was` stays only so `unmerge_run` knows
    # which label to prefer again.
    add_label(
        con,
        entity_id,
        was,
        lang=lang,
        kind="alt",
        producer=producer,
        run=run,
        confidence=confidence,
        was=True,
    )
    add_label(
        con,
        entity_id,
        english,
        lang=language.canonical(),
        kind="pref",
        producer=producer,
        run=run,
        confidence=confidence,
    )
    con.commit()
    out["action"] = "renamed"
    return out


@_reading
def foreign_names(
    con: sqlite3.Connection, *, limit: int = 200, skip: tuple[int, ...] = ()
) -> list[dict[str, Any]]:
    """Entities whose name is not the word English uses for the thing.

    Three conditions, cheapest first. The type names a kind of thing, so
    the name may be translated at all (``naming: common``, invariant 9).
    Every document behind it is in one language and that language is not
    English — a term that an English document also uses is that
    document's word, not a translation. And the name occurs nowhere in
    the English half of the library as often as in the rest
    (``vocabulary.in_english_text``), which is the dictionary this uses
    instead of a rule per language.

    An entity a pass has already decided is passed over — a label under
    ``vocabulary`` — so a run picks up where the last one stopped. The
    most connected first: the whole point is the edges the two halves of
    a name divide between them.

    **What the corpus rules out is recorded.** The third condition costs
    an FTS lookup a name, and on an exhausted queue it was paid for every
    candidate on every ask: 87 seconds to answer "nothing", which is why
    this step could not be one a worker asks for by itself. The ruling is
    kept although it is a judgement on the library as it stood: a rate
    moves as documents arrive, but a word English documents use as often
    as the others do does not become a German one by a few more recipes.
    An entity the corpus rules out is marked
    ``vocabulary:corpus``, which is a truthful thing to say about it: the
    library's own text was asked, and this name is already the word the
    library uses.
    """
    from prax import vocabulary

    common = sorted(ontology.current().common_types)
    if not common:
        return []
    marks = ",".join("?" * len(common))
    rows = con.execute(
        f"""
        SELECT e.id, e.name, e.type, count(DISTINCT x.doc) AS docs,
               count(*) AS edges,
               group_concat(DISTINCT x.lang) AS langs
          FROM entities e
          JOIN (SELECT src AS ent, source_doc AS doc,
                       json_extract(d.meta, '$.lang') AS lang
                  FROM edges JOIN documents d ON d.id = source_doc
                 WHERE valid_to IS NULL
                 UNION ALL
                SELECT dst, source_doc, json_extract(d.meta, '$.lang')
                  FROM edges JOIN documents d ON d.id = source_doc
                 WHERE valid_to IS NULL) x ON x.ent = e.id
         WHERE e.canonical_id IS NULL AND e.type IN ({marks})
           AND NOT EXISTS (SELECT 1 FROM entity_labels l
                            WHERE l.entity_id = e.id
                              AND l.producer LIKE 'vocabulary%')
         GROUP BY e.id
        HAVING langs IS NOT NULL AND langs NOT LIKE '%en%' AND langs NOT LIKE '%,%'
         ORDER BY edges DESC, docs DESC, e.id
        """,
        tuple(common),
    ).fetchall()
    out: list[dict[str, Any]] = []
    ruled_out: list[tuple[int, str]] = []
    sizes = vocabulary.library_sizes(con)
    for r in rows:
        if r["id"] in skip:
            continue
        if vocabulary.in_english_text(con, r["name"], sizes=sizes):
            ruled_out.append((int(r["id"]), str(r["name"])))
            continue
        out.append(
            {
                "id": int(r["id"]),
                "name": str(r["name"]),
                "type": str(r["type"]),
                "lang": str(r["langs"]),
                "docs": int(r["docs"]),
                "edges": int(r["edges"]),
            }
        )
        if len(out) >= limit:
            break
    if ruled_out:
        _mark_corpus_ruling(con, ruled_out)
    return out


def unlabelled_names(
    con: sqlite3.Connection,
    langs: tuple[str, ...],
    *,
    limit: int = 200,
    skip: tuple[int, ...] = (),
) -> list[dict[str, Any]]:
    """Entities the library names in its own language and a reader of
    ``langs`` would not find: no label in that language yet.

    The other half of ``foreign_names``. That one folds a German word
    into the English name when a German document printed it; this one
    writes the German word for an English name no German document has
    printed. "Apfelkuchen" split into `apfel` and found nothing, because
    nothing in this library had ever called an apple `Apfel`
    (docs/eval/apfelkuchen-2026-09-26.md).

    Only a common type (``naming: common``), only a name the library's
    own documents use, so it is known to be the library's word, and only
    a name of up to ``vocabulary.LABEL_WORDS`` words: a longer one is a
    dish's title, which came back as a literal translation nobody would
    type. A label in the language, whoever wrote it, takes the entity
    out, so the pass converges on its own answers.
    """
    from prax import language, vocabulary

    common = sorted(ontology.current().common_types)
    if not common or not langs:
        return []
    marks = ",".join("?" * len(common))
    canonical = language.canonical()
    out: list[dict[str, Any]] = []
    for lang in langs:
        rows = con.execute(
            f"""
            SELECT e.id, e.name, e.type, count(*) AS edges
              FROM entities e
              JOIN (SELECT src AS ent FROM edges x
                      JOIN documents d ON d.id = x.source_doc
                     WHERE x.valid_to IS NULL
                       AND json_extract(d.meta, '$.lang') = ?
                     UNION ALL
                    SELECT dst FROM edges x
                      JOIN documents d ON d.id = x.source_doc
                     WHERE x.valid_to IS NULL
                       AND json_extract(d.meta, '$.lang') = ?) x ON x.ent = e.id
             WHERE e.canonical_id IS NULL AND e.type IN ({marks})
               AND length(e.name) - length(replace(e.name, ' ', '')) < ?
               AND NOT EXISTS (SELECT 1 FROM entity_labels l
                                WHERE l.entity_id = e.id AND l.lang = ?)
             GROUP BY e.id
             ORDER BY edges DESC, e.id
             LIMIT ?
            """,
            (
                canonical,
                canonical,
                *common,
                vocabulary.LABEL_WORDS,
                lang,
                limit + len(skip),
            ),
        ).fetchall()
        for r in rows:
            if r["id"] in skip:
                continue
            out.append(
                {
                    "id": int(r["id"]),
                    "name": str(r["name"]),
                    "type": str(r["type"]),
                    "edges": int(r["edges"]),
                    "into": lang,
                }
            )
            if len(out) >= limit:
                return out
    return out


@_serialized
def label_in_language(
    con: sqlite3.Connection,
    entity_id: int,
    label: str,
    *,
    lang: str,
    producer: str = "vocabulary",
    run: str | None = None,
    confidence: str | None = "INFERRED",
) -> str:
    """Write what a reader of ``lang`` calls this entity, as an
    alternative label: the shown name does not move.

    ``same`` when the word is the entity's own name (German says
    `Mozzarella` too). It is written all the same, in that language,
    which is what keeps the entity from being asked again. The entity's
    own name is placed in the library's language first when it has none:
    ``add_label`` otherwise moves a language-less label of the same text
    into ``lang``, and an English name would become a German one.
    """
    from prax import language

    row = con.execute("SELECT name FROM entities WHERE id = ?", (entity_id,)).fetchone()
    if row is None:
        raise ValueError(f"no entity {entity_id}")
    name = str(row["name"])
    con.execute(
        "UPDATE entity_labels SET lang = ? WHERE entity_id = ? AND label = ?"
        " AND lang IS NULL",
        (language.canonical(), entity_id, name),
    )
    same = label.strip().casefold() == name.casefold()
    add_label(
        con,
        entity_id,
        name if same else label.strip(),
        lang=lang,
        kind="alt",
        producer=producer,
        run=run,
        confidence=confidence,
    )
    con.commit()
    return "same" if same else "labelled"


@_serialized
def _mark_corpus_ruling(
    con: sqlite3.Connection, ruled_out: list[tuple[int, str]]
) -> int:
    """Record that the library's own text answered for these names.

    The entity already carries the label (every one does since migration
    23); this says who decided it and on what evidence, which is what
    keeps the pass from asking the corpus about it again.
    """
    con.executemany(
        "UPDATE entity_labels SET producer = 'vocabulary:corpus'"
        " WHERE entity_id = ? AND label = ? COLLATE NOCASE"
        " AND (producer IS NULL OR producer = 'baseline')",
        ruled_out,
    )
    con.commit()
    return len(ruled_out)


def corpus_rulings(con: sqlite3.Connection) -> list[tuple[int, str]]:
    """The entities the corpus ruled already the library's word
    (``vocabulary:corpus``), with the name it ruled on."""
    return [
        (int(r[0]), str(r[1]))
        for r in con.execute(
            "SELECT DISTINCT l.entity_id, l.label FROM entity_labels l"
            " JOIN entities e ON e.id = l.entity_id"
            " WHERE l.producer = 'vocabulary:corpus' AND e.canonical_id IS NULL"
        )
    ]


@_serialized
def unmark_corpus_ruling(
    con: sqlite3.Connection, overturned: list[tuple[int, str]]
) -> int:
    """Take back the corpus's ruling on these names, so the vocabulary
    pass asks about them again: the label goes back to ``baseline``, which
    is what it was before the ruling was recorded on it."""
    con.executemany(
        "UPDATE entity_labels SET producer = 'baseline'"
        " WHERE entity_id = ? AND label = ? AND producer = 'vocabulary:corpus'",
        overturned,
    )
    con.commit()
    return len(overturned)
