"""Where a picture was taken, as facts of the graph (the ``geocode`` step,
``prax.steps.photos``; ``docs/biography.md`` §7, core 5).

The photos step keeps a picture's GPS position in ``meta.photo.gps``. The
geocode step's worker finds the town it lies in (``prax.graph.gazetteer``,
GeoNames) and the door writes what that says:

- the picture ``taken_in`` the town, INFERRED (the nearest town is a
  reading of the position, not the position), dated in the world by when
  the picture was taken (``meta.taken``), so a walk at a date finds where
  the camera was;
- the town ``located_in`` its region and the region in its country,
  EXTRACTED from the gazetteer, stated by each picture: a picture placed
  again or retired ends its own statement, never another picture's, and
  the wall shows the fact wherever one picture that states it is seen.

A place is a node by its name until a second place of that name comes:
GeoNames' Paris in Texas is not the Paris a picture placed earlier, and
a transitive ``located_in`` through one node would put each in the
other's country. The second becomes ``Paris, Texas`` (``_place_node``),
and a region named as a country is (Georgia, United States) likewise.

A place's names in the library's languages (``graph.language`` and the
label languages, ``Gazetteer.names``) are labels of its node, producer
``geonames``: preferred in a language the node has no preferred name in
yet, an alternative otherwise, so a German search for München finds the
pictures of Munich and the ``names`` pass shows it in the library's
language. A node is found by what answers to its name, so a renamed one
is still the place it was.

Every fact has the picture as its source, so the wall hides it with the
picture: a photo no viewer may see says to that viewer nowhere it was
taken, and a town only ever reached through hidden photos is not there
(``_said_openly``). The producer is ``geonames`` and the run names the
reading's version; a picture placed again ends what it said before,
unless the new reading names the same town, region and country: then its
facts stand, and only its stamp and the places' names are new.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from prax.text import mimes

from ..base import _reading, _serialized, now
from ..documents import _put_meta, _refresh_document_field, get_meta
from .edges import Edge, document_edges_of, document_node, end_edges, link
from .labels import add_label
from .lookups import entity_answering

PLACE_PRODUCER = "geonames"


def place_run(version: int) -> str:
    """The run a version of the reading writes under."""
    return f"geocode-v{version}"


@_reading
def places_due(
    con: sqlite3.Connection,
    version: int,
    *,
    limit: int,
    skip: frozenset[int] = frozenset(),
) -> list[dict[str, Any]]:
    """Pictures with a GPS position and no place of ``version`` (or a later
    one), oldest first: each one's ``doc_id``, ``lat`` and ``lon``. No
    original is read, so a share being offline does not matter."""
    out: list[dict[str, Any]] = []
    for r in con.execute(
        "SELECT id, mime, json_extract(meta, '$.photo.gps.lat') AS lat,"
        " json_extract(meta, '$.photo.gps.lon') AS lon FROM documents"
        " WHERE json_extract(meta, '$.photo.gps.lat') IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
        " AND coalesce(json_extract(meta, '$.place.v'), 0) < ?"
        " ORDER BY id",
        (version,),
    ):
        if len(out) >= limit:
            break
        if r["id"] in skip or not mimes.is_picture(r["mime"]):
            continue
        out.append({"doc_id": int(r["id"]), "lat": r["lat"], "lon": r["lon"]})
    return out


@_reading
def place_counts(con: sqlite3.Connection, version: int) -> dict[str, int]:
    """How many pictures with a position are placed at ``version``, lie
    nowhere the reading knows (at sea, far from a town), and wait."""
    row = con.execute(
        "SELECT"
        " sum(coalesce(json_extract(meta, '$.place.v'), 0) >= ?"
        "     AND json_extract(meta, '$.place.name') IS NOT NULL),"
        " sum(coalesce(json_extract(meta, '$.place.v'), 0) >= ?"
        "     AND json_extract(meta, '$.place.name') IS NULL),"
        " sum(coalesce(json_extract(meta, '$.place.v'), 0) < ?)"
        " FROM documents WHERE json_extract(meta, '$.photo.gps.lat') IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL",
        (version, version, version),
    ).fetchone()
    return {"placed": row[0] or 0, "nowhere": row[1] or 0, "waiting": row[2] or 0}


def _gazetteer_ends(con: sqlite3.Connection, name: str) -> tuple[set[int], bool]:
    """The entities the gazetteer's facts say a place node ``name`` is
    inside, and whether any place is said to be inside it. Ids, not names:
    a region renamed into another language is still the region (on
    2026-10-28, 43 of 77 towns were split in two that way)."""
    # by the entity that answers to the name, so a node the names pass
    # renamed into the library's language is still found by its own name
    node = entity_answering(con, name, "place")
    if node is None:
        return set(), False
    outer = {
        int(r[0])
        for r in con.execute(
            "SELECT e.dst FROM edges e"
            " WHERE e.src = ? AND e.rel = 'located_in' AND e.producer = ?"
            " AND e.valid_to IS NULL",
            (node, PLACE_PRODUCER),
        )
    }
    inner = con.execute(
        "SELECT 1 FROM edges WHERE dst = ? AND rel = 'located_in'"
        " AND producer = ? AND valid_to IS NULL LIMIT 1",
        (node, PLACE_PRODUCER),
    ).fetchone()
    return outer, inner is not None


def _name_place(
    con: sqlite3.Connection,
    nodes: dict[str, str],
    names: dict[str, dict[str, str]],
    *,
    run: str,
) -> int:
    """The gazetteer's names of a picture's town, region and country in
    other languages, as labels of their nodes; how many were new."""
    added = 0
    for lang, parts in names.items():
        for part, label in parts.items():
            node = nodes.get(part)
            eid = entity_answering(con, node, "place") if node else None
            if eid is None or not str(label).strip():
                continue
            preferred = con.execute(
                "SELECT 1 FROM entity_labels WHERE entity_id = ? AND lang = ?"
                " AND kind = 'pref'",
                (eid, lang),
            ).fetchone()
            added += add_label(
                con,
                eid,
                str(label),
                lang=lang,
                kind="alt" if preferred else "pref",
                producer=PLACE_PRODUCER,
                run=run,
            )
    return added


def _place_node(con: sqlite3.Connection, name: str, parent: str) -> str:
    """The node a gazetteer place of ``name`` inside ``parent`` is: its
    own name, unless the gazetteer already put a node of that name
    elsewhere (inside another place, or as a country, which is inside
    nothing and holds places), when it is ``name, parent``. A country
    (no ``parent``) a node of its name is inside something is ``name
    (country)``."""
    if not name:
        return name
    outer, holds = _gazetteer_ends(con, name)
    if not parent:
        return f"{name} (country)" if outer else name
    if outer - {entity_answering(con, parent, "place")} or (holds and not outer):
        return f"{name}, {parent}"
    return name


def _within(
    con: sqlite3.Connection,
    inner: str,
    outer: str,
    *,
    doc_id: int,
    run: str,
    evidence: str,
) -> int:
    """``inner`` located_in ``outer`` as the picture ``doc_id`` states it,
    unless it states it already or the two are one name (Singapore the
    town in Singapore the country)."""
    if not inner or not outer or inner == outer:
        return 0
    edge = Edge(inner, "place", "located_in", outer, "place")
    if document_edges_of(con, edge, doc_id):
        return 0
    link(
        con,
        edge,
        confidence="EXTRACTED",
        source_doc=doc_id,
        evidence=evidence,
        producer=PLACE_PRODUCER,
        run=run,
    )
    return 1


@_serialized
def apply_place(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    place: dict[str, Any] | None,
    version: int,
    by: str,
) -> str:
    """A picture's place: ``meta.place``, its facts in the graph and the
    town in its search field. ``place`` is the gazetteer's answer (name,
    geonameid, region, country, cc, km, and ``names``, its parts' names
    by language) or None for a position near no
    town. Returns ``placed`` or ``nowhere``."""
    meta = get_meta(con, doc_id)
    run = place_run(version)
    if _same_answer(con, doc_id, meta.get("place") or {}, place):
        assert place is not None
        meta["place"] = {**meta["place"], "v": version, "by": by, "at": now()}
        _put_meta(con, doc_id, meta)
        con.commit()
        _name_any(con, place, run=run)
        return "placed"
    before = [
        int(r[0])
        for r in con.execute(
            "SELECT id FROM edges WHERE source_doc = ? AND producer = ?"
            " AND valid_to IS NULL",
            (doc_id, PLACE_PRODUCER),
        )
    ]
    end_edges(con, before, run=run)
    stamp: dict[str, Any] = {"v": version, "by": by, "at": now()}
    if not place or not place.get("name"):
        meta["place"] = {**stamp, "none": True}
        _put_meta(con, doc_id, meta)
        _refresh_document_field(con, doc_id)
        con.commit()
        return "nowhere"
    town = str(place["name"])
    region, country = str(place.get("region") or ""), str(place.get("country") or "")
    gid = int(place.get("geonameid") or 0)
    meta["place"] = {
        **stamp,
        "name": town,
        "region": region,
        "country": country,
        "cc": str(place.get("cc") or ""),
        "km": float(place.get("km") or 0),
        "id": gid,
    }
    _put_meta(con, doc_id, meta)
    _refresh_document_field(con, doc_id)
    con.commit()
    gps = (meta.get("photo") or {}).get("gps") or {}
    taken = str((meta.get("taken") or {}).get("date") or "")[:10]
    name, dtype = document_node(con, doc_id)
    nodes = _place_nodes(con, town, region, country)
    town_node, region_node = nodes["town"], nodes["region"]
    country_node = nodes["country"]
    link(
        con,
        Edge(name, dtype, "taken_in", town_node, "place"),
        confidence="INFERRED",
        source_doc=doc_id,
        evidence=(
            f"GPS {float(gps.get('lat', 0)):.4f}, {float(gps.get('lon', 0)):.4f}:"
            f" {meta['place']['km']} km from {town} (GeoNames {gid})"
        ),
        producer=PLACE_PRODUCER,
        run=run,
        world_from=taken if len(taken) == 10 else None,
    )
    said = f"GeoNames {gid}: {', '.join(x for x in (town, region, country) if x)}"
    if region_node:
        _within(con, town_node, region_node, doc_id=doc_id, run=run, evidence=said)
        _within(con, region_node, country_node, doc_id=doc_id, run=run, evidence=said)
    else:
        _within(con, town_node, country_node, doc_id=doc_id, run=run, evidence=said)
    _name_any(con, place, run=run, nodes=nodes)  # once its nodes are there
    return "placed"


def _place_nodes(
    con: sqlite3.Connection, town: str, region: str, country: str
) -> dict[str, str]:
    """The nodes of a reading's town, region and country, outermost
    first, so each is told the node it is inside; ``region`` is empty
    when the reading's region is its country."""
    held_in = region if region and region != country else ""
    country_node = _place_node(con, country, "") if country else ""
    region_node = _place_node(con, held_in, country_node) if held_in else ""
    if held_in and town == held_in:
        town_node = region_node  # a town that is its region (a city state)
    elif town == country:
        town_node = country_node
    else:
        town_node = _place_node(con, town, region_node or country_node)
    return {"town": town_node, "region": region_node, "country": country_node}


def _same_answer(
    con: sqlite3.Connection,
    doc_id: int,
    old: dict[str, Any],
    place: dict[str, Any] | None,
) -> bool:
    """Whether a new reading names what the picture's live facts already
    say: the same town by its geonameid, region and country, and the
    picture still ``taken_in`` the node that town is."""
    if not place or not place.get("name") or not old.get("name"):
        return False
    if (
        str(place["name"]) != old["name"]
        or str(place.get("region") or "") != old.get("region")
        or str(place.get("country") or "") != old.get("country")
        or int(place.get("geonameid") or 0) != old.get("id")
    ):
        return False
    nodes = _place_nodes(
        con, old["name"], str(old.get("region")), str(old.get("country"))
    )
    town = entity_answering(con, nodes["town"], "place")
    held = con.execute(
        "SELECT 1 FROM edges WHERE source_doc = ? AND rel = 'taken_in'"
        " AND producer = ? AND valid_to IS NULL AND dst = ?",
        (doc_id, PLACE_PRODUCER, town),
    ).fetchone()
    return town is not None and held is not None


def _name_any(
    con: sqlite3.Connection,
    place: dict[str, Any],
    *,
    run: str,
    nodes: dict[str, str] | None = None,
) -> int:
    """The reading's names of its places, when it carries any."""
    names = place.get("names") or {}
    if not isinstance(names, dict) or not names:
        return 0
    if nodes is None:
        nodes = _place_nodes(
            con,
            str(place["name"]),
            str(place.get("region") or ""),
            str(place.get("country") or ""),
        )
    return _name_place(con, nodes, names, run=run)
