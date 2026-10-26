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
  EXTRACTED from the gazetteer, written once: a later picture of the same
  town finds the fact standing.

Every fact has the picture as its source, so the wall hides it with the
picture: a photo no viewer may see says to that viewer nowhere it was
taken, and a town only ever reached through hidden photos is not there
(``_said_openly``). The producer is ``geonames`` and the run names the
reading's version; a picture placed again ends what it said before.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from prax.text import mimes

from ..base import _reading, _serialized, now
from ..documents import _put_meta, _refresh_document_field, get_meta
from .edges import Edge, document_node, end_edges, find_edges, link

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


def _within(
    con: sqlite3.Connection,
    inner: str,
    outer: str,
    *,
    doc_id: int,
    run: str,
    evidence: str,
) -> int:
    """``inner`` located_in ``outer``, unless it stands already or the two
    are one name (Singapore the town in Singapore the country)."""
    if not inner or not outer or inner == outer:
        return 0
    edge = Edge(inner, "place", "located_in", outer, "place")
    if find_edges(con, edge):
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
    geonameid, region, country, cc, km) or None for a position near no
    town. Returns ``placed`` or ``nowhere``."""
    meta = get_meta(con, doc_id)
    run = place_run(version)
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
    link(
        con,
        Edge(name, dtype, "taken_in", town, "place"),
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
    if region and region != country:
        _within(con, town, region, doc_id=doc_id, run=run, evidence=said)
        _within(con, region, country, doc_id=doc_id, run=run, evidence=said)
    else:
        _within(con, town, country, doc_id=doc_id, run=run, evidence=said)
    return "placed"
