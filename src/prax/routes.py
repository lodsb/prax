"""The routes a document can take from here: what has been done to it
and what can be asked for, as one answer for the document page.

A scanned PDF can be OCRed, its pages read by the vision model, its
figures read (or read again under a better prompt), its equations
recovered by marker and then read, its graph extracted by the local
model or by the expensive one. Every one of those exists as a reading
request (``store.request_reading``), an extraction request
(``store.request_extraction``) or the promote flag (``store.promote``).
This module puts them in one list with the document's state beside
each: the text's stamp and whether it has a layer, how many figures
and formulas there are and how many have a reading, which model each
step resolves to on this host and whether it costs money, what is
requested already. The document page shows it as the "process…"
dialog; ``GET /doc/{id}/routes`` returns it.

Nothing here writes. The buttons in the dialog call the endpoints the
routes name.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from prax import models, store
from prax.config import ConfigError
from prax.parsers.figures import UNCAPTIONED

# the steps whose model the dialog names
_STEPS = ("vision", "formulas", "polish", "extract", "promote")
# which step of the worker takes a route's kind of request, for the
# "nobody is asking for this" line under a route already requested
_ASKED_BY = {"reading": "parse", "extract": "extract", "promote": "promote"}


def _model(step: str) -> dict[str, Any] | None:
    """The model a step resolves to on this host: name and whether it is
    paid; None when the step is off or misconfigured."""
    try:
        spec = models.resolve(step)
    except ConfigError:  # a config error is the host's problem, not the page's
        return None
    if spec is None:
        return None
    return {"name": spec.name, "paid": spec.paid}


def _counts(con: sqlite3.Connection, doc_id: int) -> dict[str, int]:
    """Figures and formulas in the document's chunks, how many of each
    carry a reading, and the figures no caption claims (``Figure on
    page N``) apart, since the captioned pass leaves those out."""
    out = {
        "figures": 0,
        "figures_read": 0,
        "figures_uncaptioned": 0,
        "figures_uncaptioned_read": 0,
        "formulas": 0,
        "formulas_read": 0,
    }
    for row in con.execute(
        "SELECT kind, data FROM chunks"
        " WHERE doc_id = ? AND kind IN ('figure', 'formula')",
        (doc_id,),
    ):
        data = json.loads(row["data"]) if row["data"] else {}
        key = "figures" if row["kind"] == "figure" else "formulas"
        out[key] += 1
        if data.get("readings"):
            out[key + "_read"] += 1
        if key == "figures" and str(data.get("caption", "")).startswith(UNCAPTIONED):
            out["figures_uncaptioned"] += 1
            if data.get("readings"):
                out["figures_uncaptioned_read"] += 1
    return out


def _promote_done(meta: dict[str, Any]) -> bool:
    """Has the promote step's model read this flagged document under the
    current ontology? The same test ``store.promoted_documents`` makes,
    for this one document's meta."""
    from prax import extraction, ontology

    try:
        producer = extraction.current("promote").name
    except RuntimeError:
        return False
    onto = ontology.current()
    return store.extracted_by(
        meta, producer, ontology_version=store.expected_version(meta, onto)
    )


def routes_for(con: sqlite3.Connection, doc_id: int) -> dict[str, Any]:
    """The document's state and the routes from it. Each route is
    ``{id, group, label, detail, action, model, paid, available, note,
    pending}``; ``action`` is what a button sends: ``{"kind": "reading",
    "extractor", "mode"}``, ``{"kind": "extract"}`` or
    ``{"kind": "promote"}``. Raises ``KeyError`` for no such document."""
    row = con.execute(
        "SELECT mime, text_hash, meta FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    meta = json.loads(row["meta"] or "{}")
    mime = row["mime"] or ""
    is_pdf = mime == "application/pdf"
    is_html = mime in ("text/html", "application/xhtml+xml")
    is_image = mime.startswith("image/")
    is_page = bool(meta.get("page"))
    text_len = int(
        con.execute(
            "SELECT coalesce(sum(length(text)), 0) FROM chunks WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()[0]
    )
    counts = _counts(con, doc_id)
    reading = meta.get("reading") or None
    waiting = reading if reading and reading.get("state") == "requested" else {}
    extraction = meta.get("extraction") or None
    stale = meta.get("extraction_stale") or None
    promote = meta.get("promote") or None
    history = meta.get("parse_history") or []
    last_parse = history[-1] if history else {}
    pages = meta.get("pages")
    no_text = row["text_hash"] is None and bool(history)
    thin = bool(
        is_pdf
        and pages
        and int(pages) >= store.THIN_MIN_PAGES
        and text_len < store.THIN_BYTES_PER_PAGE * int(pages)
    )

    models_of = {step: _model(step) for step in _STEPS}
    why_of: dict[str, str] = {}  # one answer per kind, asked at most once

    def why_pending(kind: str) -> str:
        if kind not in why_of:
            from prax import work

            why_of[kind] = work.who_runs(con, _ASKED_BY[kind])["why"]
        return why_of[kind]

    state: dict[str, Any] = {
        "mime": mime,
        "text_source": meta.get("text_source"),
        "text_len": text_len,
        "pages": pages,
        "no_text": no_text,
        "thin": thin,
        "last_parse": (
            {k: last_parse.get(k) for k in ("extractor", "outcome", "error", "at")}
            if last_parse
            else None
        ),
        "figures": counts["figures"],
        "figures_read": counts["figures_read"],
        "figures_uncaptioned": counts["figures_uncaptioned"],
        "figures_uncaptioned_read": counts["figures_uncaptioned_read"],
        "formulas": counts["formulas"],
        "formulas_read": counts["formulas_read"],
        "video": bool(meta.get("video")),
        "polished": bool(meta.get("polished")),
        "extraction": extraction,
        "extraction_stale": stale,
        "promote": promote,
        "reading": reading,
        "models": models_of,
    }

    routes: list[dict[str, Any]] = []

    def add(
        rid: str,
        group: str,
        label: str,
        detail: str,
        action: dict[str, Any],
        *,
        step: str | None = None,
        available: bool = True,
        note: str = "",
    ) -> None:
        model = models_of[step] if step else None
        if step and model is None:
            available = False
            note = note or f"no model for the {step} step on this host (prax.yaml)"
        pending = False
        if action["kind"] == "reading":
            # the same extractor, and the same mode where the route names one
            pending = waiting.get("extractor") == action["extractor"] and (
                action["mode"] is None or waiting.get("mode") == action["mode"]
            )
        elif action["kind"] == "extract":
            pending = bool(stale and stale.get("requested"))
        elif action["kind"] == "promote":
            pending = bool(promote) and not _promote_done(meta)
        routes.append(
            {
                "id": rid,
                "group": group,
                "label": label,
                "detail": detail,
                "action": action,
                "model": model["name"] if model else None,
                "paid": bool(model and model["paid"]),
                "available": available,
                "note": note,
                "pending": pending,
                "why": why_pending(action["kind"]) if pending else "",
            }
        )

    if is_page:
        return {"doc_id": doc_id, "state": state, "routes": routes}
    if text_len or row["text_hash"]:
        add(
            "rechunk",
            "text",
            "Chunk the text again",
            "rebuild this document's chunks with the chunker as it stands"
            f" ({text_len:,} characters of text now); nothing else changes,"
            " and a chunk whose text did not change keeps its vector",
            {"kind": "rechunk"},
        )

    # -- the text
    if is_pdf:
        looks = ""
        if no_text:
            looks = " (this looks like a scan: no text at all)"
        elif thin:
            looks = " (this looks like a scan read as its cover: <100 bytes a page)"
        add(
            "ocr",
            "text",
            "OCR the scanned pages",
            "RapidOCR over the pages without a text layer; the text replaces"
            " what is there" + looks,
            {"kind": "reading", "extractor": "pymupdf4llm-ocr", "mode": None},
            note="another language than the host's: mode=<language>"
            " (ch, en, latin, arabic, cyrillic…)",
        )
        add(
            "vision-pages",
            "text",
            "Read the scanned pages with the vision model",
            "handwriting, scores, photographed notebooks: a transcription of every"
            " page without a text layer, 4–10 s a page locally",
            {"kind": "reading", "extractor": "vision-pages", "mode": "scans"},
            step="vision",
        )
        add(
            "vision-pages-all",
            "text",
            "Read every page with the vision model",
            "printed pages with notes in the margin too",
            {"kind": "reading", "extractor": "vision-pages", "mode": "all"},
            step="vision",
        )
        add(
            "marker",
            "text",
            "Recover the mathematics with marker",
            "display equations as LaTeX (formula chunks), tables as tables;"
            " 2 s a page through its server. Needs the marker role up:"
            " prax up --start marker",
            {"kind": "reading", "extractor": "marker", "mode": "fast"},
        )
        add(
            "reread",
            "text",
            "Read the PDF again",
            "the default extractor at its current revision: finds the figures,"
            " cleans the text",
            {"kind": "reading", "extractor": "pymupdf4llm", "mode": None},
        )
        add(
            "docling",
            "text",
            "Read with Docling",
            "its layout model labels code and tables; slow (minutes)",
            {"kind": "reading", "extractor": "docling", "mode": None},
        )
    elif is_html:
        add(
            "reread",
            "text",
            "Read the page again",
            "trafilatura at its current revision: finds the figures, fences the"
            " code, the comments under their heading",
            {"kind": "reading", "extractor": "trafilatura", "mode": None},
        )
        if state["video"]:
            add(
                "polish",
                "text",
                "Punctuate the transcript",
                "the polish model: sentences and capitals, the fillers dropped,"
                " nothing else changed",
                {"kind": "reading", "extractor": "polish", "mode": None},
                step="polish",
            )
    elif is_image:
        add(
            "vision",
            "text",
            "Describe the image with the vision model",
            "a description and a transcription of its text; a second model's"
            " reading joins the first",
            {"kind": "reading", "extractor": "vision", "mode": None},
            step="vision",
        )

    # -- the figures
    if is_pdf or is_html:
        loose = counts["figures_uncaptioned"]
        n = counts["figures"] - loose  # the captioned ones
        unread = n - (counts["figures_read"] - counts["figures_uncaptioned_read"])
        if n:
            detail = (
                f"{unread} of {n} captioned figures without a reading; the vision"
                " model is shown the caption and the text around each, about"
                " 4 s a figure locally"
            )
        elif loose:
            detail = "no captioned figures: every image here is one no caption claims"
        else:
            detail = (
                "no figure references in the text yet: read the document again"
                " first (finds them)"
            )
        add(
            "figures",
            "figures",
            "Read the figures nobody has read",
            detail,
            {"kind": "reading", "extractor": "figures", "mode": "captioned"},
            step="vision",
            available=unread > 0,
        )
        add(
            "figures-again",
            "figures",
            "Read every figure again",
            f"the {n} captioned figures under the current prompt (the one that"
            " shows the model the text around the figure); this model's earlier"
            " readings replaced, another model's kept",
            {"kind": "reading", "extractor": "figures", "mode": "again"},
            step="vision",
            available=n > 0,
        )
        if is_pdf:
            loose_unread = loose - counts["figures_uncaptioned_read"]
            add(
                "figures-all",
                "figures",
                "Read the images no caption claims too",
                f"{loose_unread} of {loose} unread; a manual's screenshots and"
                " panels, but often decoration",
                {"kind": "reading", "extractor": "figures", "mode": "all"},
                step="vision",
                available=loose_unread > 0,
            )

    # -- the formulas
    if not is_image:
        n = counts["formulas"]
        unread = n - counts["formulas_read"]
        add(
            "formulas",
            "formulas",
            "Read the equations",
            (
                f"{unread} of {n} display equations without a reading: one to"
                " three sentences under each, in the document's terms"
            )
            if n
            else "no display equations in the text"
            + (": marker recovers them from a PDF" if is_pdf else ""),
            {"kind": "reading", "extractor": "formulas", "mode": "new"},
            step="formulas",
            available=n > 0,
        )
        if n:
            add(
                "formulas-again",
                "formulas",
                "Read every equation again",
                "this model's earlier readings replaced",
                {"kind": "reading", "extractor": "formulas", "mode": "again"},
                step="formulas",
            )

    # -- the graph
    if row["text_hash"] is not None:
        if extraction:
            done = (
                f"read by {extraction.get('extractor')} under"
                f" {extraction.get('ontology_version')}"
                f" on {str(extraction.get('at') or '')[:10]}"
            )
        else:
            done = "not extracted yet"
        add(
            "extract",
            "graph",
            "Extract the graph again" if extraction else "Extract the graph",
            f"{done}. The local model reads the document against its ontology"
            " modules on the worker's next pass; the earlier reading's edges"
            " are retired, history kept",
            {"kind": "extract"},
            step="extract",
        )
        add(
            "promote",
            "graph",
            "Promote to the expensive model",
            "the richer pass (claims, relations between methods; an image"
            " described again first). Flags the document; the worker runs the"
            " pass only with --spend",
            {"kind": "promote"},
            step="promote",
        )
    return {"doc_id": doc_id, "state": state, "routes": routes}
