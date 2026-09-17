"""The review queue's second life: replaying items against a newer ontology.

Extraction parks a triple whose types or relation the ontology of the day
rejected (invariant 9), with its evidence and source document. When the
ontology grows, ``replay`` re-checks every open item that has both types
and links the ones that now fit through ``store.link``, keeping evidence
and source; the item is closed as ``linked``. Nothing is re-extracted and
no model is called. Items still rejected stay open, and ``unmapped`` items
(no types) are never replayed: those need a person or a typing pass.

``apply_typing_rules`` is that typing pass for the systematic misfits a
model produces (the document typed as what it is about, ``authored_by``
reversed, ``cites`` for a tool it uses): rules retype, flip, rename or
drop, never invent, and write what they link as INFERRED edges with the
producer ``typing-rules``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from prax import ontology, store


@dataclass
class ReplayReport:
    checked: int = 0
    linked: int = 0
    existing: int = 0  # the edge was already in the graph; item closed
    still_open: int = 0
    ontology_version: str = ""


def replay(
    con: sqlite3.Connection, *, onto: ontology.Ontology | None = None
) -> ReplayReport:
    onto = onto or ontology.current()
    rep = ReplayReport(ontology_version=onto.version)
    offset = 0
    while True:
        items = store.list_review(con, limit=500, offset=offset, unmapped=False)
        if not items:
            break
        offset += len(items)
        for it in items:
            if not (it["src_type"] and it["dst_type"] and it["rel"]):
                continue
            rep.checked += 1
            try:
                onto.check_edge(it["src_type"], it["rel"], it["dst_type"])
            except ValueError:
                rep.still_open += 1
                continue
            edge = store.Edge(
                it["src"], it["src_type"], it["rel"], it["dst"], it["dst_type"]
            )
            if store.find_edges(con, edge):
                rep.existing += 1
            else:
                store.link(
                    con,
                    edge,
                    confidence="EXTRACTED",
                    source_doc=it["source_doc"],
                    evidence=it["evidence"],
                    producer="replay",
                    run=f"ontology-v{onto.version}",
                )
                rep.linked += 1
            store.resolve_review(con, it["id"], "linked")
            offset -= 1  # the item left the open set the listing pages over
    return rep


# ------------------------------------------------------------ typing rules
# A local model's misfits are systematic (docs/eval/extractors-local-2026-09-11.md
# and the queue after the backlog): it writes the document under the type
# of what the document is about ("this manual" as a tool), reverses
# authored_by, says "cites" for a tool or method it references, and "about"
# for a claim it makes or a paper it discusses. Each of those is a rule:
# what the model meant is recoverable from the item itself and the source
# document's title. Rules never invent content: they retype, flip or
# rename a relation, or drop what no relation can hold; everything else
# stays in the queue for a person or a bigger ontology.

RULES_PRODUCER = "typing-rules"
_ORG = re.compile(
    r"\b(universit|institut|laborator|\blabs?\b|department|dept\.|faculty|school|"
    r"college|centre|center|foundation|fund\b|council|academy|gmbh|inc\.?\b|ltd|"
    r"corporation|company|group|studios?\b|research|cnrs|ircam|ccrma|mit\b|"
    r"hochschule|fraunhofer|association|society|consortium|programme|program\b|"
    r"agency|ministry|technolog|audio|software|systems|instruments|electronics|"
    r"devices|networks|solutions|media|records|project)",
    re.IGNORECASE,
)
_PARTICLES = "van|von|de|der|den|du|la|le|di|da"
_PERSON = re.compile(
    r"^[A-ZÀ-Ý][\w'\-\.]*(?: (?:[A-ZÀ-Ý][\w'\-\.]*|" + _PARTICLES + r")){1,4}$"
)
_TYPE_WORD = re.compile(
    r"\b(tool|software|library|plugin|service|system|method|algorithm|technique|dataset|corpus|database|benchmark|concept)\b",
    re.IGNORECASE,
)
_WORD_TYPE = {
    "tool": "tool",
    "software": "tool",
    "library": "tool",
    "plugin": "tool",
    "service": "tool",
    "system": "tool",
    "method": "method",
    "algorithm": "method",
    "technique": "method",
    "dataset": "dataset",
    "corpus": "dataset",
    "database": "dataset",
    "benchmark": "dataset",
    "concept": "concept",
}


_URL = re.compile(r"^(https?://|www\.)", re.IGNORECASE)
_ACRONYM = re.compile(r"^[A-Z]{2,7}$")
_NOISE_NAMES = frozenset(
    {
        "unknown",
        "none",
        "n/a",
        "n.d.",
        "s.n.",
        "[s.n.]",
        "s.n.]",
        "?",
        "-",
        "title",
        "no claims",
        "no authors",
        "not stated",
        "[venue not stated]",
        "various",
        "author",
        "authors",
        "paper",
        "source",
        "target",
        "entity",
        "document",
    }
)
_NOT_A_NAME = re.compile(r"not stated|unknown|/|\bn\.?d\.?\b|^\W*$", re.IGNORECASE)


_VAGUE = re.compile(
    r"^(this|these|those|our|the proposed|a proposed|specific|various|different|"
    r"several|some|other)\b"
)
_NOT_A_THING = re.compile(
    r"^(unknown\b|\(unknown|comment: \d+ pages?|supporting document\b"
    r"|fig(ure)?\.? ?\d)",
    re.IGNORECASE,
)
_REFNUM = re.compile(r"\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]")


def _placeholder(name: str) -> bool:
    """A name the model left as a placeholder or a non-name: "source name",
    "target name", "unknown", "(unknown paper)", "this inference scheme",
    "supporting document [34]", a bare URL, an empty pattern."""
    low = " ".join(name.lower().split())
    if _REFNUM.search(name) and len(_REFNUM.sub("", name).split()) <= 3:
        return True
    return (
        low in store.PLACEHOLDER_NAMES
        or low in _NOISE_NAMES
        or bool(_URL.match(low))
        or bool(_MALFORMED.search(name))
        or bool(_VAGUE.match(name))  # lower case on purpose: "The Beatles" stays
        or bool(_NOT_A_THING.match(name))
    )


def _looks_title(name: str) -> bool:
    """A cited work rather than a person, a tool or a fragment: several
    words and some length."""
    return (
        len(name) >= 30
        and len(name.split()) >= 5
        and not _looks_person(name)
        and not _placeholder(name)
    )


def _looks_venue(name: str) -> bool:
    """A journal, conference, publisher or series: some letters, no URL
    path, not a placeholder for one."""
    return (
        len(name) >= 4
        and not _placeholder(name)
        and not _NOT_A_NAME.search(name)
        and bool(re.search(r"[A-Za-zÀ-ÿ]{3}", name))
    )


_TOP_ORG = re.compile(
    r"universit|hochschule|college|institute of technology|gmbh|\binc\b|\bltd|"
    r"corporation|foundation|ministry|agency",
    re.IGNORECASE,
)


def _inside(src: str, dst: str) -> bool:
    """Two organizations where the first can be part of the second: a
    lab in a university, a group in a company — never a university in a
    course, and never two universities (aliases of one, more likely)."""
    return (
        _looks_org(src)
        and _looks_org(dst)
        and not (_TOP_ORG.search(src) and not _TOP_ORG.search(dst))
        and not (_TOP_ORG.search(src) and _TOP_ORG.search(dst))
    )


def _looks_org(name: str) -> bool:
    # an organization word wins over the shape of a name: "Stanford
    # University" and "Waves Audio" are two capitalized words, and organizations
    return bool(_ORG.search(name))


def _looks_person(name: str) -> bool:
    # two to five capitalized words, none an organization word and none
    # longer than a surname gets ("Betriebssysteme" is a course, not a person)
    return (
        bool(_PERSON.match(name))
        and not _ORG.search(name)
        and all(len(w) <= 14 for w in name.split())
    )


def decide_unmapped(
    item: dict[str, Any], doc: tuple[str, ...] | None
) -> tuple[str, list[store.Edge], str | None]:
    """Rules for items without types: the relation the model named and the
    shape of the two names decide. Affiliation between a person and an
    organization, supervision between two people, authorship between a
    title and a person, funding and development towards an organization,
    and a mention whose reason names what kind of thing it is."""
    if (edges := _self_as_device(item, doc)) is not None:
        return "link", edges, "self-as-device"
    src, dst, rel = item["src"], item["dst"], (item["rel"] or "").lower()
    title, own = (doc or ("", "paper"))[:2]
    if title and src.lower() in ("paper", "this paper", "the paper", "document"):
        src = title
    if _placeholder(src) or _placeholder(dst):
        return "drop", [], "placeholder-name"
    if rel in ("unknown", ""):  # related_to stays: the queue is its evidence
        return "drop", [], "no-relation"
    is_self = bool(title) and src == title
    if rel in ("affiliation", "affiliated_with", "affiliated with"):
        # the document itself and an institution: written there
        if is_self and not _looks_person(dst) and _looks_venue(dst):
            return (
                "link",
                [store.Edge(src, own, "written_at", dst, "organization")],
                "written_at",
            )
        # two institutions "affiliated": the smaller is part of the larger
        if not is_self and _inside(src, dst):
            return (
                "link",
                [store.Edge(src, "organization", "part_of", dst, "organization")],
                "affiliation->part_of",
            )
        # exactly one side is a person; the other is the institution
        ps, pd = _looks_person(src), _looks_person(dst)
        if ps and not pd and not _looks_person(dst):
            return (
                "link",
                [store.Edge(src, "author", "affiliated_with", dst, "organization")],
                "affiliation",
            )
        if pd and not ps:
            return (
                "link",
                [store.Edge(dst, "author", "affiliated_with", src, "organization")],
                "affiliation",
            )
        return "open", [], None
    if rel in ("located_in", "located_at", "based_in", "location"):
        # an organization and a place: nothing else is located anywhere
        if not is_self and _looks_org(src) and not _looks_person(dst) and len(dst) >= 3:
            return (
                "link",
                [store.Edge(src, "organization", "located_in", dst, "place")],
                "located_in",
            )
        return "open", [], None
    if rel in ("published_by", "publisher", "issued_by"):
        if is_self and not _looks_person(dst) and _looks_venue(dst):
            return (
                "link",
                [store.Edge(src, own, "published_by", dst, "organization")],
                "published_by",
            )
        return "open", [], None
    if rel in ("part_of", "is_part_of", "member_of", "belongs_to", "division_of"):
        # a lab in a university, a subsidiary in a group: both organizations
        if not is_self and _inside(src, dst):
            return (
                "link",
                [store.Edge(src, "organization", "part_of", dst, "organization")],
                "part_of-organizations",
            )
        return "open", [], None
    if rel in ("published_in", "published in", "appeared_in", "venue"):
        # the document itself, in something that is not a person
        if is_self and not _looks_person(dst) and _looks_venue(dst):
            return (
                "link",
                [store.Edge(src, own, "published_in", dst, "venue")],
                "published_in",
            )
        return "open", [], None
    if rel in ("cites", "references", "cited"):
        # the document itself citing something shaped like a work's title
        if is_self and _looks_title(dst):
            return (
                "link",
                [store.Edge(src, own, "cites", dst, "paper")],
                "cites-title",
            )
        return "open", [], None
    if rel in ("advised_by", "supervised_by", "advisor", "supervisor"):
        if _looks_person(src) and _looks_person(dst):
            return (
                "link",
                [store.Edge(src, "author", "advised_by", dst, "author")],
                "advised_by",
            )
        return "open", [], None
    if rel in ("author", "author_of", "authored_by", "authors", "written_by"):
        a, b = (src, dst) if _looks_person(dst) else (dst, src)
        if _looks_person(b) and not _looks_person(a) and len(a) > 12:
            return (
                "link",
                [store.Edge(a, "paper", "authored_by", b, "author")],
                "authored_by",
            )
        return "open", [], None
    if (
        rel in ("funded_by", "funding", "supported_by")
        and (_looks_org(dst) or _ACRONYM.match(dst))
        and not _looks_person(src)
    ):
        st = own if title and src == title else "paper"
        return (
            "link",
            [store.Edge(src, st, "funded_by", dst, "organization")],
            "funded_by",
        )
    if rel in ("developed_by", "created_by", "made_by", "built_by"):
        if _looks_org(dst):  # "Waves Audio" reads like two names; the org words win
            return (
                "link",
                [store.Edge(src, "tool", "developed_by", dst, "organization")],
                "developed_by",
            )
        if _looks_person(dst):
            return (
                "link",
                [store.Edge(src, "tool", "developed_by", dst, "author")],
                "developed_by",
            )
        return "open", [], None
    if rel in ("mentions", "references", "discusses", "names"):
        m = _TYPE_WORD.search(item.get("reason") or "")
        if m and (title and src == title or src == item["src"]):
            st = own if title and src == title else "paper"
            return (
                "link",
                [store.Edge(src, st, "mentions", dst, _WORD_TYPE[m.group(1).lower()])],
                "mentions",
            )
        return "open", [], None
    return "open", [], None


_MALFORMED = re.compile(
    r"(src_type=|dst_type=|confidence=|evidence=|\trel=|target name=|source name=|"
    r"^(?:paper|author|source|target|src|dst|title|name|entity)=)"
)
# (rel, src_type, dst_type) -> new relation, after the self-name retyping
REMAP: dict[tuple[str, str, str], str] = {
    ("affiliated_with", "paper", "organization"): "written_at",
    ("affiliated_with", "document", "organization"): "written_at",
    ("affiliated_with", "page", "organization"): "written_at",
    ("affiliated_with", "project", "organization"): "written_at",
    # the affiliation on the first page, read as a location: the document
    # came out of the institution (74 open items, 2026-09-17)
    ("located_in", "paper", "organization"): "written_at",
    ("located_in", "document", "organization"): "written_at",
    ("cites", "paper", "tool"): "uses",
    ("cites", "paper", "method"): "uses",
    ("cites", "paper", "dataset"): "uses",
    ("cites", "paper", "concept"): "about",
    ("cites", "paper", "document"): "mentions",  # when not title-shaped (RETYPE)
    ("cites", "paper", "work"): "mentions",
    ("about", "paper", "document"): "mentions",
    ("affiliated_with", "paper", "author"): "authored_by",
    ("cites", "paper", "person"): "mentions",
    ("cites", "paper", "author"): "mentions",
    ("cites", "paper", "organization"): "mentions",
    ("cites", "paper", "event"): "mentions",
    ("cites", "paper", "place"): "mentions",
    ("part_of", "paper", "venue"): "published_in",
    ("part_of", "paper", "event"): "mentions",
    ("part_of", "paper", "organization"): "mentions",
    ("about", "paper", "claim"): "proposes",
    ("about", "paper", "paper"): "cites",
    ("about", "tool", "concept"): "implements",
    ("about", "tool", "method"): "implements",
    ("about", "method", "concept"): "implements",
    ("about", "method", "method"): "implements",
    ("implements", "paper", "method"): "uses",
    ("implements", "paper", "concept"): "about",
}
# (rel, src_type, dst_type) -> (src_type, dst_type): a near miss retyped —
# a cited "document" or "work" in a research library is a paper, a listed
# "person" on authored_by is its author
RETYPE: dict[tuple[str, str, str], tuple[str, str]] = {
    ("affiliated_with", "paper", "person"): ("paper", "author"),
    ("cites", "paper", "document"): ("paper", "paper"),
    ("cites", "paper", "work"): ("paper", "paper"),
    ("cites", "document", "paper"): ("paper", "paper"),
    ("cites", "document", "document"): ("paper", "paper"),
    ("authored_by", "paper", "person"): ("paper", "author"),
    ("authored_by", "document", "author"): ("paper", "author"),
    ("authored_by", "document", "person"): ("paper", "author"),
    ("published_in", "paper", "organization"): ("paper", "venue"),
    ("published_in", "document", "venue"): ("paper", "venue"),
}
# (rel, src_type, dst_type) with "*" as a wildcard -> dropped
DROP: set[tuple[str, str, str]] = {
    ("cites", "paper", "venue"),
    ("cites", "paper", "claim"),
    ("cites", "paper", "project"),
    ("cites", "paper", "page"),
    ("about", "paper", "author"),
    ("about", "author", "*"),
    ("about", "concept", "*"),
    ("about", "venue", "*"),
    ("uses", "author", "*"),
    ("authored_by", "paper", "paper"),
    # a document is not in a city: the institution's place, read off the
    # first page, is the institution's business (located_in organization
    # is remapped above)
    ("located_in", "paper", "place"),
    ("located_in", "document", "place"),
    ("located_in", "page", "*"),
}


@dataclass
class TypingReport:
    checked: int = 0
    linked: int = 0
    dropped: int = 0
    existing: int = 0
    still_open: int = 0
    by_rule: dict[str, int] = field(default_factory=dict)
    run: str = ""

    def hit(self, rule: str) -> None:
        self.by_rule[rule] = self.by_rule.get(rule, 0) + 1


def _doc_titles(
    con: sqlite3.Connection, ids: set[int]
) -> dict[int, tuple[str, str, str | None]]:
    """``{doc_id: (title, own type, device)}``: a page's own type is page or
    project; otherwise the type the graph gave the document itself (a
    paper, a manual), paper by default. ``device`` is what a studio
    document ``describes`` when that is one thing."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    out: dict[int, tuple[str, str, str | None]] = {}
    for r in con.execute(
        f"SELECT d.id, d.title, p.kind FROM documents d LEFT JOIN pages p"
        f" ON p.doc_id = d.id WHERE d.id IN ({marks})",
        tuple(ids),
    ):
        title = r["title"] or ""
        if r["kind"]:
            kind = "project" if r["kind"] == "project" else "page"
        else:
            own = con.execute(
                "SELECT s.type FROM edges e JOIN entities s ON s.id = e.src"
                " WHERE e.source_doc = ? AND s.name = ? AND e.valid_to IS NULL"
                " ORDER BY e.id DESC LIMIT 1",
                (r["id"], title),
            ).fetchone()
            kind = own[0] if own else "paper"
        devices = [
            x[0]
            for x in con.execute(
                "SELECT DISTINCT t.name FROM edges e"
                " JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst"
                " WHERE e.source_doc = ? AND e.rel = 'describes' AND s.name = ?"
                " AND t.type = 'device' AND e.valid_to IS NULL",
                (r["id"], title),
            )
        ]
        out[r["id"]] = (title, kind, devices[0] if len(devices) == 1 else None)
    return out


# a studio document put where its device belongs: "the manual has this
# feature" means the device it describes has it
DEVICE_RELS = frozenset(
    {"has_part", "has_feature", "has_spec", "conforms_to", "compatible_with"}
)
DEVICE_DST = {
    "has_part": "component",
    "has_feature": "feature",
    "has_spec": "spec",
    "conforms_to": "standard",
    "compatible_with": "device",
}


def _self_as_device(
    item: dict[str, Any], doc: tuple[str, ...] | None
) -> list[store.Edge] | None:
    if not doc or len(doc) < 3 or not doc[2]:
        return None
    title, device = doc[0], doc[2]
    src, rel, dt = item["src"], item["rel"], item["dst_type"]
    if not title or src != title:
        return None
    if rel == "covers" and dt == "component":
        rel = "has_part"
    if rel not in DEVICE_RELS:
        return None
    return [store.Edge(device, "device", rel, item["dst"], dt or DEVICE_DST[rel])]


def decide(
    item: dict[str, Any], doc: tuple[str, ...] | None
) -> tuple[str, list[store.Edge], str | None]:
    """What the rules say about one typed item: ``("link", edges, rule)``,
    ``("drop", [], rule)`` or ``("open", [], None)``. Pure; the ontology
    check happens in ``apply_typing_rules``."""
    src, dst = item["src"], item["dst"]
    rel, st, dt = item["rel"], item["src_type"], item["dst_type"]
    if _MALFORMED.search(src) or _MALFORMED.search(dst):
        return "drop", [], "malformed-name"
    if _placeholder(src) or _placeholder(dst):
        return "drop", [], "placeholder-name"
    if rel == "unknown":
        return "drop", [], "no-relation"
    title, own = (doc or ("", "paper"))[:2]
    rule = None
    if (edges := _self_as_device(item, doc)) is not None:
        return "link", edges, "self-as-device"
    # written backwards: the author "authored_by" the paper, the
    # organization "affiliated_with" the person
    if rel == "authored_by" and st == "author" and dt in ("paper", "document"):
        return (
            "link",
            [store.Edge(dst, "paper", "authored_by", src, "author")],
            "flip-authored_by",
        )
    if rel == "affiliated_with" and st == "organization" and dt in ("person", "author"):
        return (
            "link",
            [store.Edge(dst, "author", "affiliated_with", src, "organization")],
            "flip-affiliated_with",
        )
    # the document under a wrong type: it is itself
    if title and src == title and st != own:
        st, rule = own, "self-name"
    # authored_by written backwards, or with authors on both ends
    if rel == "authored_by" and st == "author":
        if title and dst == title and (dt == own or _looks_person(src)):
            return (
                "link",
                [store.Edge(title, own, "authored_by", src, "author")],
                "flip-authored_by",
            )
        if dt == "author" and title:
            names = [src] if src == dst else [src, dst]
            return (
                "link",
                [store.Edge(title, own, "authored_by", n, "author") for n in names],
                "authors-both-ends",
            )
    if " ".join(src.lower().split()) == " ".join(dst.lower().split()):
        return (
            "drop",
            [],
            "self-edge",
        )  # after the authored_by rules: both ends an author
    if (rel, st, dt) in RETYPE and not (
        rel == "cites" and len(dst) < 12 and len(dst.split()) < 2
    ):  # a one-word "document" is not a paper we can name
        st2, dt2 = RETYPE[(rel, st, dt)]
        rel2 = REMAP.get((rel, st2, dt2), rel)
        return (
            "link",
            [store.Edge(src, st2, rel2, dst, dt2)],
            f"retype-{rel}-{st}-{dt}" + (f"->{rel2}" if rel2 != rel else ""),
        )
    if (rel, st, dt) in REMAP:
        return (
            "link",
            [store.Edge(src, st, REMAP[(rel, st, dt)], dst, dt)],
            f"{rel}->{REMAP[(rel, st, dt)]}",
        )
    if (rel, st, dt) in DROP or (rel, st, "*") in DROP:
        return "drop", [], f"drop-{rel}-{st}-{dt}"
    if rule == "self-name":
        return "link", [store.Edge(src, st, rel, dst, dt)], rule
    return "open", [], None


def apply_typing_rules(
    con: sqlite3.Connection,
    *,
    commit: bool = True,
    onto: ontology.Ontology | None = None,
    source_doc: int | None = None,
    run: str | None = None,
) -> TypingReport:
    """Run the rules over every open typed item (or one document's, with
    ``source_doc``: what the door does right after an extraction). Links
    carry the item's evidence and source document, confidence INFERRED (a
    rule read what the model meant), producer ``typing-rules`` and one run
    id per pass; an edge already in the graph closes the item as
    ``linked`` too. With ``commit=False`` nothing is written and the
    report says what would be."""
    onto = onto or ontology.current()
    rep = TypingReport(
        run=run or "typing-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    )
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = store.list_review(con, limit=1000, offset=offset)
        if not page:
            break
        offset += len(page)
        items.extend(
            it for it in page if source_doc is None or it["source_doc"] == source_doc
        )
    docs = _doc_titles(con, {it["source_doc"] for it in items if it["source_doc"]})
    for it in items:
        rep.checked += 1
        if it["src_type"] and it["dst_type"]:
            action, edges, rule = decide(it, docs.get(it["source_doc"]))
        else:
            action, edges, rule = decide_unmapped(it, docs.get(it["source_doc"]))
        if action == "open":
            rep.still_open += 1
            continue
        if action == "drop":
            rep.dropped += 1
            rep.hit(rule or "drop")
            if commit:
                store.resolve_review(con, it["id"], "dropped")
            continue
        try:
            for e in edges:
                onto.check_edge(e.src_type, e.rel, e.dst_type)
        except ValueError:
            rep.still_open += 1
            continue
        rep.hit(rule or "link")
        wrote = False
        for e in edges:
            if store.find_edges(con, e):
                continue
            wrote = True
            if commit:
                store.link(
                    con,
                    e,
                    confidence="INFERRED",
                    source_doc=it["source_doc"],
                    evidence=it["evidence"],
                    producer=RULES_PRODUCER,
                    run=rep.run,
                )
        if wrote:
            rep.linked += 1
        else:
            rep.existing += 1
        if commit:
            store.resolve_review(con, it["id"], "linked")
    return rep
