"""Citation network from a bibliographic source: OpenAlex or Crossref.

Reference lists never reach the LLM extractor (it sees the first part of a
paper) and in-text citations are numbers, so ``cites`` edges come from a
bibliographic source instead. For every document with a DOI in its metadata
(or, with ``resolve_titles``, an exact title match) the source answers with
the work's references and its citation count. Each reference with a title
becomes a ``paper --cites--> paper`` edge through ``store.link`` (invariant
3), ``confidence = EXTRACTED``, ``source_doc`` the citing document, evidence
naming the source and the work ids. A referenced work that is itself in the
library is named by that document's title, so the node coincides with the
paper entity the Zotero importer and the extractor use; other references
become paper entities without a document. The document's metadata gets
``meta.citations`` (source, work id, citation count, reference count, fetch
time): the "how well cited is this" signal, and the idempotence stamp.

Two sources, same interface. OpenAlex returns referenced works as ids and
their titles in batches of 50 (few requests per paper); Crossref returns
the publisher's reference list with titles for about half the entries and
DOIs for the rest, whose titles cost one request each (cached). Neither
needs a key; a ``mailto`` (``PRAX_CITATIONS_MAILTO``) puts requests in
their polite pools. Nothing here writes to the source or to the library
(invariant 10).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from prax import config, store

OPENALEX = "https://api.openalex.org"
CROSSREF = "https://api.crossref.org"
BATCH = 50  # OpenAlex ids per filter request
OPENALEX_FIELDS = "id,title,doi,publication_year,cited_by_count,referenced_works"
_DOI = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)

Fetcher = Callable[[str], dict[str, Any]]


def normalize_doi(value: str | None) -> str | None:
    """``https://doi.org/10.1109/X`` and ``doi:10.1109/X`` -> ``10.1109/x``."""
    if not value:
        return None
    m = _DOI.search(value.strip())
    return m.group(0).rstrip(".,;").lower() if m else None


def normalize_title(title: str) -> str:
    s = unicodedata.normalize("NFKD", title)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


class HttpFetcher:
    """GET JSON with a polite-pool contact, retrying on 429 and 5xx, a
    pause between calls, and a per-request timeout (OpenAlex has been seen
    taking 25 s per request; the caller decides what to do with an error)."""

    def __init__(
        self, mailto: str | None = None, *, pause: float = 0.1, timeout: float = 30
    ) -> None:
        self.mailto = mailto or config.setting(
            "citations.mailto", "PRAX_CITATIONS_MAILTO"
        )
        self.pause = pause
        self.timeout = timeout
        self.calls = 0

    def __call__(self, url: str) -> dict[str, Any]:
        if self.mailto:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}mailto={urllib.parse.quote(self.mailto)}"
        ua = "prax/0.1" + (f" (mailto:{self.mailto})" if self.mailto else "")
        for attempt in range(4):
            self.calls += 1
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                time.sleep(self.pause)
                return data
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return {}
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
        return {}


# ---------------------------------------------------------------- sources


@dataclass
class Ref:
    doi: str | None
    title: str | None
    id: str = ""  # the source's id for the referenced work, when it has one


@dataclass
class Work:
    id: str
    title: str
    doi: str | None
    year: int | None
    cited_by_count: int | None
    references: list[Ref] = field(default_factory=list)


class Source(Protocol):
    name: str

    def by_doi(self, doi: str) -> Work | None: ...
    def by_title(self, title: str) -> Work | None: ...
    def title_for_doi(self, doi: str) -> str | None: ...


class OpenAlexSource:
    name = "openalex"

    def __init__(self, fetch: Fetcher) -> None:
        self.fetch = fetch

    def _work(self, w: dict[str, Any]) -> Work:
        ids = list(w.get("referenced_works") or [])
        details = self._by_ids(ids) if ids else {}
        refs = [
            Ref(normalize_doi(d.get("doi")), (d.get("title") or "").strip() or None, i)
            for i in ids
            if (d := details.get(i)) is not None
        ]
        return Work(
            w["id"],
            (w.get("title") or "").strip(),
            normalize_doi(w.get("doi")),
            w.get("publication_year"),
            w.get("cited_by_count"),
            refs,
        )

    def by_doi(self, doi: str) -> Work | None:
        q = urllib.parse.quote(doi, safe="/")
        w = self.fetch(f"{OPENALEX}/works/doi:{q}?select={OPENALEX_FIELDS}")
        return self._work(w) if w.get("id") else None

    def by_title(self, title: str) -> Work | None:
        q = urllib.parse.quote(title[:300])
        data = self.fetch(
            f"{OPENALEX}/works?search={q}&per-page=3&select={OPENALEX_FIELDS}"
        )
        want = normalize_title(title)
        for w in data.get("results", []):
            if normalize_title(w.get("title") or "") == want:
                return self._work(w)
        return None

    def title_for_doi(self, doi: str) -> str | None:
        w = self.fetch(
            f"{OPENALEX}/works/doi:{urllib.parse.quote(doi, safe='/')}?select=id,title"
        )
        return (w.get("title") or "").strip() or None

    def _by_ids(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        short = [i.rsplit("/", 1)[-1] for i in ids]
        for start in range(0, len(short), BATCH):
            part = short[start : start + BATCH]
            data = self.fetch(
                f"{OPENALEX}/works?filter=openalex_id:{'|'.join(part)}"
                f"&per-page={BATCH}&select=id,title,doi"
            )
            for w in data.get("results", []):
                out[w["id"]] = w
        return out


class CrossrefSource:
    name = "crossref"

    def __init__(self, fetch: Fetcher) -> None:
        self.fetch = fetch
        self._titles: dict[str, str | None] = {}

    def _work(self, m: dict[str, Any]) -> Work:
        refs = []
        for r in m.get("reference") or []:
            title = (r.get("article-title") or r.get("volume-title") or "").strip()
            doi = normalize_doi(r.get("DOI"))
            if title or doi:
                refs.append(Ref(doi, title or None, r.get("key", "")))
        titles = m.get("title") or [""]
        year = None
        for key in ("published-print", "published-online", "issued", "created"):
            parts = (m.get(key) or {}).get("date-parts") or [[None]]
            if parts and parts[0] and parts[0][0]:
                year = int(parts[0][0])
                break
        return Work(
            f"doi:{normalize_doi(m.get('DOI')) or ''}",
            titles[0].strip(),
            normalize_doi(m.get("DOI")),
            year,
            m.get("is-referenced-by-count"),
            refs,
        )

    def by_doi(self, doi: str) -> Work | None:
        data = self.fetch(f"{CROSSREF}/works/{urllib.parse.quote(doi, safe='/')}")
        m = data.get("message") or {}
        return self._work(m) if m.get("DOI") else None

    def by_title(self, title: str) -> Work | None:
        q = urllib.parse.quote(title[:300])
        data = self.fetch(f"{CROSSREF}/works?query.title={q}&rows=3")
        want = normalize_title(title)
        for m in (data.get("message") or {}).get("items", []):
            if any(normalize_title(t) == want for t in m.get("title") or []):
                return self._work(m)
        return None

    def title_for_doi(self, doi: str) -> str | None:
        if doi not in self._titles:
            data = self.fetch(f"{CROSSREF}/works/{urllib.parse.quote(doi, safe='/')}")
            titles = (data.get("message") or {}).get("title") or []
            self._titles[doi] = titles[0].strip() if titles and titles[0] else None
        return self._titles[doi]


def source_named(name: str, fetch: Fetcher) -> Source:
    if name == "openalex":
        return OpenAlexSource(fetch)
    if name == "crossref":
        return CrossrefSource(fetch)
    raise ValueError(f"unknown citation source {name!r}; openalex or crossref")


# --------------------------------------------------------------- importer


@dataclass
class Report:
    documents: int = 0  # documents with a work found
    unresolved: int = 0  # no DOI / no title match
    linked: int = 0
    existing: int = 0
    untitled: int = 0  # references with neither a title nor a resolvable DOI
    library_refs: int = 0  # references that are documents in the store
    skipped: int = 0  # already fetched
    errors: list[str] = field(default_factory=list)


def _library_dois(con: sqlite3.Connection) -> dict[str, str]:
    """DOI -> document title for every document that has one."""
    rows = con.execute(
        "SELECT title, json_extract(meta, '$.doi') AS doi FROM documents"
        " WHERE json_extract(meta, '$.doi') IS NOT NULL AND title IS NOT NULL"
    ).fetchall()
    out: dict[str, str] = {}
    for r in rows:
        d = normalize_doi(r["doi"])
        if d:
            out[d] = r["title"]
    return out


def candidates(
    con: sqlite3.Connection,
    *,
    limit: int | None = None,
    refresh: bool = False,
    doi_only: bool = False,
) -> list[int]:
    """Documents to look up: not yet fetched, those with a DOI first."""
    sql = "SELECT id FROM documents WHERE title IS NOT NULL"
    if doi_only:
        sql += " AND json_extract(meta, '$.doi') IS NOT NULL"
    if not refresh:
        sql += " AND json_extract(meta, '$.citations.fetched_at') IS NULL"
    sql += " ORDER BY (json_extract(meta, '$.doi') IS NULL), id"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [r["id"] for r in con.execute(sql)]


def import_citations(
    con: sqlite3.Connection,
    doc_ids: Iterable[int],
    *,
    source: Source,
    resolve_titles: bool = False,
    report: Report | None = None,
) -> Report:
    rep = report or Report()
    library = _library_dois(con)
    for doc_id in doc_ids:
        doc = store.get_document(con, doc_id, max_chars=0)
        if doc is None:
            continue
        meta = doc["meta"] or {}
        if meta.get("citations", {}).get("fetched_at"):
            rep.skipped += 1
            continue
        try:
            _one(
                con,
                doc_id,
                doc["title"] or "",
                meta,
                library,
                source,
                resolve_titles,
                rep,
            )
        except Exception as exc:  # noqa: BLE001 - one document must not stop the run
            rep.errors.append(f"doc {doc_id}: {type(exc).__name__}: {exc}")
    return rep


def _one(
    con: sqlite3.Connection,
    doc_id: int,
    title: str,
    meta: dict[str, Any],
    library: dict[str, str],
    source: Source,
    resolve_titles: bool,
    rep: Report,
) -> None:
    doi = normalize_doi(meta.get("doi"))
    work = source.by_doi(doi) if doi else None
    if work is None and resolve_titles and title:
        work = source.by_title(title)
    stamp = {
        "fetched_at": store.now(),
        "source": source.name,
    }
    if work is None:
        rep.unresolved += 1
        meta["citations"] = {**stamp, "resolved": False}
        store.set_meta(con, doc_id, meta)
        return
    linked = 0
    for ref in work.references:
        name = library.get(ref.doi) if ref.doi else None
        if name:
            rep.library_refs += 1
        else:
            name = ref.title
            if not name and ref.doi:
                name = source.title_for_doi(ref.doi)
        if not name or not title or name == title:
            rep.untitled += 1 if not name else 0
            continue
        edge = store.Edge(title, "paper", "cites", name, "paper")
        if store.find_edges(con, edge):
            rep.existing += 1
            continue
        store.link(
            con,
            edge,
            confidence="EXTRACTED",
            source_doc=doc_id,
            evidence=f"{source.name} {work.id} references {ref.doi or ref.id or name}",
            producer=source.name,
            run=stamp["fetched_at"][:10],
        )
        linked += 1
    rep.linked += linked
    rep.documents += 1
    meta["citations"] = {
        **stamp,
        "resolved": True,
        "id": work.id,
        "doi": work.doi,
        "year": work.year,
        "cited_by_count": work.cited_by_count,
        "references": len(work.references),
        "linked": linked,
    }
    store.set_meta(con, doc_id, meta)
