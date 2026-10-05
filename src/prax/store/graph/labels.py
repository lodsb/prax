"""What a thing is called: its labels, the name it shows in the host's
language, merging two entities into one and taking a run of merges back."""

from __future__ import annotations

import sqlite3
from typing import Any

from prax.text import language

from ..base import _NOW, _reading, _serialized, now
from .edges import _entity_id

PERSON = "human"  # who a decision on the review page is signed by
SPLIT = "split-names"  # the type a pair of two unrelated types is filed under


def _record_pair(
    con: sqlite3.Connection, x: int, y: int, *, same: bool, by: str, etype: str
) -> None:
    """A decided pair in ``entity_candidates``, whether or not a worker
    proposed it: the likely tier does not ask again, and a person's
    decisions are the gold labels a model's confidence is measured
    against (docs/PLAN.md, Q)."""
    a, b = (x, y) if x < y else (y, x)
    con.execute(
        "INSERT INTO entity_candidates (a, b, type, score, producer, at, decided,"
        f" decided_by) VALUES (?, ?, ?, 0, ?, {_NOW}, ?, ?)"
        " ON CONFLICT (a, b) DO UPDATE SET decided = excluded.decided,"
        " decided_by = excluded.decided_by",
        (a, b, etype, by, "same" if same else "different", by),
    )


@_serialized
def merge_entities(
    con: sqlite3.Connection,
    duplicate_id: int,
    into_id: int,
    *,
    across_types: bool = False,
    producer: str | None = None,
    run: str | None = None,
    confidence: str | None = None,
) -> None:
    """Record that ``duplicate_id`` is the same thing as ``into_id``.

    Nothing is deleted or rewritten: the duplicate keeps its name and its
    edges (they are evidence), and gets ``canonical_id`` pointing at the
    survivor; ``traverse`` and lookups follow the pointer. Chains are
    flattened so every alias points straight at the final survivor.

    The duplicate's name becomes a label of the survivor
    (``entity_labels``) with its language and, when the caller says so,
    the producer and run that decided it. That is what makes a round of
    merging retirable (``unmerge_run``): a merge is a claim like an edge,
    and a claim nobody signed cannot be taken back.
    """
    if duplicate_id == into_id:
        raise ValueError("an entity cannot be merged into itself")
    rows = {
        r["id"]: r
        for r in con.execute(
            "SELECT id, type, canonical_id FROM entities WHERE id IN (?, ?)",
            (duplicate_id, into_id),
        )
    }
    if len(rows) != 2:
        raise KeyError("no such entity")
    if rows[duplicate_id]["type"] != rows[into_id]["type"] and not across_types:
        raise ValueError("entities of different types cannot be merged")
    survivor = rows[into_id]["canonical_id"] or into_id
    if survivor == duplicate_id:
        raise ValueError("that merge would form a cycle")
    con.execute(
        "UPDATE entities SET canonical_id = ? WHERE id = ? OR canonical_id = ?",
        (survivor, duplicate_id, duplicate_id),
    )
    # the claim carries who made it, on the row it changes (migration 25):
    # the label below is not always written, and was the only record
    con.execute(
        "UPDATE entities SET merged_by = ?, merged_run = ? WHERE id = ?",
        (producer, run, duplicate_id),
    )
    _label_from_merge(con, survivor, duplicate_id, producer, run, confidence)
    con.commit()


def _label_from_merge(
    con: sqlite3.Connection,
    survivor: int,
    duplicate_id: int,
    producer: str | None,
    run: str | None,
    confidence: str | None,
) -> None:
    """The duplicate's name, kept as a label of the survivor."""

    row = con.execute(
        "SELECT name FROM entities WHERE id = ?", (duplicate_id,)
    ).fetchone()
    if row is None:
        return
    name = str(row["name"] if hasattr(row, "keys") else row[0])
    con.execute(
        "INSERT OR IGNORE INTO entity_labels (entity_id, label, lang, kind,"
        " from_entity, producer, run, confidence) VALUES (?, ?, ?, 'alt', ?, ?, ?, ?)",
        (
            survivor,
            name,
            language.detect(name),
            duplicate_id,
            producer,
            run,
            confidence,
        ),
    )


def display_language() -> str:
    """Which of a thing's names this host shows: the language the library
    is written in (`prax.text.language.canonical`, `graph.language` in
    prax.yaml). The graph in German for a German reader, one node either
    way — the point of the label table."""

    return language.canonical()


def _display_name(con: sqlite3.Connection, entity_id: int) -> str | None:
    """The name to show for an entity: its preferred label in the host's
    language, then in English, then any preferred label it has.

    None when it has none, which after migration 23 means the entity was
    made by something that bypassed ``_entity_id`` — the caller then
    leaves the name it has.
    """

    want = display_language()
    rows = con.execute(
        "SELECT label, lang FROM entity_labels WHERE entity_id = ? AND kind = 'pref'",
        (entity_id,),
    ).fetchall()
    if not rows:
        return None
    by_lang = {r["lang"]: r["label"] for r in rows}
    for lang in (want, language.CANONICAL, None):
        if lang in by_lang:
            return str(by_lang[lang])
    return str(rows[0]["label"])


def _refresh_name(con: sqlite3.Connection, entity_id: int) -> str | None:
    """Rewrite the cached name from the labels. Returns it when it moved.

    ``entities.name`` is a cache of the preferred label in the host's
    language (docs/identity.md): the labels say what a thing is called,
    the column is what a join reads and what the unique index guards. A
    name another entity of that type already shows is left alone — two
    things may not display the same, which is the type clash the review
    queue is for, not something to resolve by overwriting.
    """
    show = _display_name(con, entity_id)
    if show is None:
        return None
    row = con.execute(
        "SELECT name, type FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None or row["name"] == show:
        return None
    taken = con.execute(
        "SELECT 1 FROM entities WHERE name = ? AND type = ? AND id != ?",
        (show, row["type"], entity_id),
    ).fetchone()
    if taken is not None:
        return None
    # the name being replaced is a name this entity answers to, and is
    # recorded before it goes. `Chomsky-Normalform` was lost because
    # migration 23 had skipped entities that already had a preferred
    # label, so their own name was in no row and a rebuild renamed them
    # with nothing left to put back. A guard would have caught that one
    # path; this makes the loss impossible on all of them.
    # a name appears once per entity whatever its language, so this asks
    # for the text rather than leaning on the unique index, which counts
    # a different `lang` as a different label
    con.execute(
        "INSERT INTO entity_labels (entity_id, label, kind, producer)"
        " SELECT ?, ?, 'alt', 'baseline' WHERE NOT EXISTS ("
        "   SELECT 1 FROM entity_labels WHERE entity_id = ?"
        "    AND label = ? COLLATE NOCASE)",
        (entity_id, row["name"], entity_id, row["name"]),
    )
    con.execute("UPDATE entities SET name = ? WHERE id = ?", (show, entity_id))
    return show


@_serialized
def rename_display_language(con: sqlite3.Connection) -> dict[str, int]:
    """Every entity's shown name rebuilt from its labels: what a host
    runs after changing `graph.language`. The maintain pass calls it."""
    moved = 0
    rows = con.execute(
        "SELECT DISTINCT entity_id FROM entity_labels WHERE kind = 'pref'"
    ).fetchall()
    for n, r in enumerate(rows, 1):
        entity_id = int(r["entity_id"])
        if _refresh_name(con, entity_id):
            moved += 1
        if n % 2000 == 0:
            con.commit()
    con.commit()
    out: dict[str, Any] = {
        "entities": len(rows),
        "renamed": moved,
        "language": display_language(),
    }
    return out


PRINTED_MAX = 120  # a printed name longer than this is a sentence


@_serialized
def keep_printed(
    con: sqlite3.Connection,
    name: str,
    etype: str,
    printed: str,
    *,
    lang: str | None = None,
    source_doc: int | None = None,
    producer: str | None = None,
    run: str | None = None,
) -> int:
    """Keep the word a document printed for a thing the graph names
    otherwise, as an alternative label in the document's language.

    The fallback, not the main route. From 2026-09-24 to -26 the
    extraction prompt wrote a common noun in the library's language, and
    a new entity got one label, its own name — so a German recipe's
    "Äpfel" became `apple` with no German label anywhere, and that label
    is what a German query crosses to the English documents on
    (`Apfelkuchen` -> `apfel` -> `apple`, ``store.expand_query``). The
    prompt now writes names as printed and the watched vocabulary pass
    translates them, keeping the word by construction
    (``docs/eval/apfelkuchen-2026-09-26.md``). This keeps it when a model
    translates anyway and says so in the optional field.

    Nothing is written when the printed word is the name, is empty, or is
    too long to be a name. The entity is the one ``link`` landed on
    (``_entity_id`` answers the same way it did there), and a word printed
    again by another document is the same label, not a second one.
    Returns the rows written.
    """
    said = " ".join((printed or "").split())
    if (
        not said
        or len(said) > PRINTED_MAX
        or said.casefold() == " ".join(name.split()).casefold()
    ):
        return 0
    eid = _entity_id(con, name, etype)
    row = con.execute(
        "SELECT COALESCE(canonical_id, id) FROM entities WHERE id = ?", (eid,)
    ).fetchone()
    return add_label(
        con,
        int(row[0]),
        said,
        lang=lang,
        kind="alt",
        producer=producer,
        run=run,
        source_doc=source_doc,
    )


@_serialized
def add_label(
    con: sqlite3.Connection,
    entity_id: int,
    label: str,
    *,
    lang: str | None = None,
    kind: str = "alt",
    producer: str | None = None,
    run: str | None = None,
    source_doc: int | None = None,
    confidence: str | None = None,
    was: bool = False,
) -> int:
    """A name this entity is also known by, in a language.

    ``kind`` is ``pref`` for the name to show in that language and ``alt``
    otherwise, the two SKOS gives a concept. There is one preferred name
    per language (migration 22 holds it), so a new one demotes the one
    already there rather than colliding with it — a language whose
    preferred name is decided twice should end with the later answer, not
    with an error.

    ``was`` marks the name the entity carried before a pass renamed it,
    which is what ``unmerge_run`` puts back. It is a fact about the label,
    not a kind of label.

    Returns how many rows were written (0 when it was already there).
    """
    if not label.strip():
        raise ValueError("a label needs a name")
    # a name appears once per entity: its language is a property of the
    # label, not part of which label it is. Migration 23 gives every
    # entity a label with no language, and the passes then learn one — so
    # without this a rename left two rows differing only in `lang`, and
    # the `languages` backfill would collide on the unique index trying
    # to place the first (2026-09-24)
    if lang is not None:
        con.execute(
            "UPDATE entity_labels SET lang = ? WHERE entity_id = ? AND label = ?"
            " AND lang IS NULL",
            (lang, entity_id, label),
        )
    if kind == "pref" and lang is not None:
        con.execute(
            "UPDATE entity_labels SET kind = 'alt'"
            " WHERE entity_id = ? AND lang = ? AND kind = 'pref' AND label != ?",
            (entity_id, lang, label),
        )
    cur = con.execute(
        "INSERT OR IGNORE INTO entity_labels (entity_id, label, lang, kind,"
        " source_doc, producer, run, confidence, was)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entity_id,
            label,
            lang,
            kind,
            source_doc,
            producer,
            run,
            confidence,
            1 if was else 0,
        ),
    )
    if not cur.rowcount:
        # the name was already a label of this entity: the caller is
        # saying something about it — which kind it is, whether it is the
        # name the entity used to carry, and who says so. The provenance
        # has to move with it, or a pass cannot mark what it decided and
        # `unmerge_run` cannot find its own work: every entity carries a
        # `baseline` label from migration 23, so this is the common path
        # now rather than the rare one (2026-09-24)
        con.execute(
            # a caller naming a label it did not give a kind to is saying
            # the name exists, not that it stopped being the preferred
            # one: the kind goes up, never down
            "UPDATE entity_labels SET"
            " kind = CASE WHEN ? = 'pref' THEN 'pref' ELSE kind END,"
            " was = max(was, ?),"
            " producer = COALESCE(?, producer), run = COALESCE(?, run),"
            " confidence = COALESCE(?, confidence)"
            " WHERE entity_id = ? AND label = ?"
            " AND coalesce(lang, '') = coalesce(?, '')",
            (
                kind,
                1 if was else 0,
                producer,
                run,
                confidence,
                entity_id,
                label,
                lang,
            ),
        )
    if kind == "pref":
        _refresh_name(con, entity_id)  # the column follows the labels
    con.commit()
    return int(cur.rowcount or 0)


@_reading
def entity_labels(
    con: sqlite3.Connection, entity_id: int, *, lang: str | None = None
) -> list[dict[str, Any]]:
    """Every name an entity is known by, newest first; ``lang`` narrows to
    one language and the labels nobody could place."""
    where = " AND (lang = ? OR lang IS NULL)" if lang else ""
    args: tuple[Any, ...] = (entity_id, lang) if lang else (entity_id,)
    return [
        dict(r)
        for r in con.execute(
            "SELECT id, label, lang, kind, from_entity, source_doc, producer,"
            f" run, confidence, at FROM entity_labels WHERE entity_id = ?{where}"
            " ORDER BY kind = 'pref' DESC, id DESC",
            args,
        )
    ]


@_reading
def entities_by_label(con: sqlite3.Connection, label: str) -> list[int]:
    """The entities known by this name, whatever their own name is: how a
    German word reaches an entity the library calls something else.

    A merged alias answers for its survivor, not for itself: every entity
    carries its own name as a label since migration 23, so without that a
    lookup would return the fold and the thing it folded into.
    """
    return [
        int(r[0])
        for r in con.execute(
            "SELECT DISTINCT COALESCE(e.canonical_id, e.id) FROM entity_labels l"
            " JOIN entities e ON e.id = l.entity_id"
            " WHERE l.label = ? COLLATE NOCASE",
            (label,),
        )
    ]


@_serialized
def decide_pair(
    con: sqlite3.Connection,
    keep: int,
    other: int,
    *,
    same: bool,
    by: str = PERSON,
    across_types: bool = False,
) -> dict[str, Any]:
    """Answer "are these one thing?" for two entities. Same: ``other`` is
    merged into ``keep``, under a run of its own that ``unmerge_run`` takes
    back. Different: nothing moves. Either way the pair is recorded as
    decided and signed (``_record_pair``). Two entities of unrelated types
    (a split name) are filed under ``SPLIT``, and merge only with
    ``across_types``."""
    if keep == other:
        raise ValueError("a pair needs two entities")
    rows = {
        int(r["id"]): r
        for r in con.execute(
            "SELECT id, type, canonical_id FROM entities WHERE id IN (?, ?)",
            (keep, other),
        )
    }
    if len(rows) != 2:
        raise KeyError("no such entity")
    kinds = {rows[keep]["type"], rows[other]["type"]}
    etype = rows[keep]["type"] if len(kinds) == 1 else SPLIT
    run = None
    group = {int(rows[i]["canonical_id"] or i) for i in (keep, other)}
    if same and len(group) == 1:
        pass  # one thing already (a merge confirmed): the decision is recorded
    elif same:
        run = f"decide-{now().replace(':', '').replace('-', '')}-{other}"
        merge_entities(
            con, other, keep, across_types=across_types, producer=by, run=run
        )
    _record_pair(con, keep, other, same=same, by=by, etype=etype)
    con.commit()
    return {"keep": keep, "other": other, "same": same, "run": run}


@_serialized
def undecide_pair(con: sqlite3.Connection, x: int, y: int) -> bool:
    """A decision taken back (a click undone): a pair a worker proposed is
    open again, one only a decision made is gone. Whatever the decision
    merged is ``unmerge_run``'s to take back; this is the record, so a
    mistaken click does not stay in the gold sample."""
    a, b = (x, y) if x < y else (y, x)
    row = con.execute(
        "SELECT producer, decided_by FROM entity_candidates WHERE a = ? AND b = ?",
        (a, b),
    ).fetchone()
    if row is None or row["decided_by"] is None:
        return False
    if row["producer"] == row["decided_by"]:
        con.execute("DELETE FROM entity_candidates WHERE a = ? AND b = ?", (a, b))
    else:
        con.execute(
            "UPDATE entity_candidates SET decided = NULL, decided_by = NULL"
            " WHERE a = ? AND b = ?",
            (a, b),
        )
    con.commit()
    return True


@_serialized
def unmerge_entity(
    con: sqlite3.Connection, entity_id: int, *, by: str = PERSON
) -> dict[str, Any]:
    """Take one merge back: the entity stands on its own again, the label
    its merge gave the survivor goes, and the pair is recorded as
    different so nothing folds it again. For a merge no run can undo
    alone: the adjudicated round of 2026-09-17 predates the stamp, and
    ``unmerge_run`` would take the whole round."""
    row = con.execute(
        "SELECT id, type, canonical_id FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such entity: {entity_id}")
    survivor = row["canonical_id"]
    if survivor is None:
        raise ValueError("that entity is not merged into another")
    kind = con.execute(
        "SELECT type FROM entities WHERE id = ?", (survivor,)
    ).fetchone()["type"]
    con.execute(
        "UPDATE entities SET canonical_id = NULL, merged_by = NULL,"
        " merged_run = NULL WHERE id = ?",
        (entity_id,),
    )
    con.execute(
        "DELETE FROM entity_labels WHERE entity_id = ? AND from_entity = ?",
        (survivor, entity_id),
    )
    _refresh_name(con, int(survivor))
    _record_pair(
        con,
        int(survivor),
        entity_id,
        same=False,
        by=by,
        etype=row["type"] if row["type"] == kind else SPLIT,
    )
    con.commit()
    return {"entity": entity_id, "from": int(survivor)}


@_serialized
def unmerge_run(con: sqlite3.Connection, run: str) -> int:
    """Undo a round: every entity the run folded away stands on its own
    again, every entity it renamed is called what it was called, and the
    labels it wrote are gone, and the edges it wrote are ended. Returns how
    many entities came back.

    The counterpart of ``retire_run`` for edges. A merge is a claim, and
    a pass that claimed wrongly has to be undoable, or nobody can try a
    new rule on the live graph. A rename is a claim too — the vocabulary
    pass makes both — which is why the name an entity had is written as a
    ``was`` label under the same run and put back here. Without that a
    pass could be taken back halfway: the folds undone, the wrong names
    left behind.
    """
    # the merges stamped with the run (migration 25), and those only a
    # label of the run records, from before the stamp
    ids = sorted(
        {
            int(r[0])
            for r in con.execute("SELECT id FROM entities WHERE merged_run = ?", (run,))
        }
        | {
            int(r[0])
            for r in con.execute(
                "SELECT from_entity FROM entity_labels WHERE run = ? AND from_entity"
                " IS NOT NULL",
                (run,),
            )
        }
    )
    for entity_id in ids:
        con.execute(
            "UPDATE entities SET canonical_id = NULL, merged_by = NULL,"
            " merged_run = NULL WHERE id = ? OR canonical_id = ?",
            (entity_id, entity_id),
        )
    renamed = con.execute(
        "SELECT entity_id, label FROM entity_labels WHERE run = ? AND was = 1",
        (run,),
    ).fetchall()
    # what the run *wrote* goes; what it only *claimed* stays. A `was`
    # label is the entity's own older name, which the run demoted rather
    # than made — deleting it with the rest took away the very name the
    # undo exists to put back (2026-09-24)
    con.execute("DELETE FROM entity_labels WHERE run = ? AND was = 0", (run,))
    for r in renamed:
        con.execute(
            "UPDATE entity_labels SET kind = 'pref', was = 0, run = NULL"
            " WHERE entity_id = ? AND label = ?",
            (r["entity_id"], r["label"]),
        )
        _refresh_name(con, int(r["entity_id"]))
    # and the edges the round wrote: the venue tier links each edition
    # part_of its series under the round's run (found by the review of
    # 2026-10-04); ended, as retire_run ends them
    con.execute(
        f"UPDATE edges SET valid_to = {_NOW} WHERE run = ? AND valid_to IS NULL", (run,)
    )
    con.commit()
    return len(ids) + len(renamed)


@_serialized
def rename_entity(con: sqlite3.Connection, entity_id: int, name: str) -> str:
    """Give an entity a better name — what the heal pass does with a title
    a citation importer left markup in. When another entity of the same
    type already carries that name, this one is merged into it instead
    (two rows with one name is what the unique index forbids and what the
    graph means anyway). Returns ``renamed``, ``merged`` or ``unchanged``."""
    row = con.execute(
        "SELECT name, type FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such entity: {entity_id}")
    name = name.strip()
    if not name or name == row["name"]:
        return "unchanged"
    other = con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = ?", (name, row["type"])
    ).fetchone()
    if other is not None and other["id"] != entity_id:
        merge_entities(con, entity_id, other["id"])
        return "merged"
    con.execute("UPDATE entities SET name = ? WHERE id = ?", (name, entity_id))
    con.commit()
    return "renamed"


def entity_names(con: sqlite3.Connection, etype: str) -> list[tuple[int, str]]:
    """The unmerged entities of one type, id and name, by id: what a
    worker embeds for the likely tier of resolution (``prax.work``)."""
    rows = con.execute(
        "SELECT id, name FROM entities WHERE type = ? AND canonical_id IS NULL"
        " ORDER BY id",
        (etype,),
    ).fetchall()
    return [(int(r["id"]), str(r["name"])) for r in rows]


WIRE_KIND = "wire"  # a label that was an extractor's wire syntax, kept as the record


@_serialized
def set_aside_label(
    con: sqlite3.Connection, label_id: int, cleaned: str | None, *, run: str
) -> bool:
    """A label that holds an extractor's wire syntax ("ARP 2600
    dst_type=tool confidence=…") set aside as ``kind = 'wire'``, never
    deleted, and the words before the syntax written beside it as a label
    of the same kind and language when the entity has no such label yet
    (producer and run ``run``, so ``unmerge_run`` takes them back). False
    when there was no such label."""
    row = con.execute(
        "SELECT entity_id, lang, kind FROM entity_labels WHERE id = ?", (label_id,)
    ).fetchone()
    if row is None or row["kind"] == WIRE_KIND:
        return False
    con.execute("UPDATE entity_labels SET kind = ? WHERE id = ?", (WIRE_KIND, label_id))
    if cleaned:
        con.execute(
            "INSERT OR IGNORE INTO entity_labels"
            " (entity_id, label, lang, kind, producer, run)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (row["entity_id"], cleaned, row["lang"], row["kind"], run, run),
        )
    con.commit()
    return True
