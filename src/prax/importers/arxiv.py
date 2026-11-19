"""Under what licence arXiv offers its copy of a paper (AV, 2026-11-19).

arXiv records the licence its submitter chose for every paper since 2004:
the ``<license>`` URL of its OAI-PMH record in the ``arXiv`` metadata
format (arXiv's own non-exclusive licence, or a Creative Commons one). It
speaks of the very copy arXiv serves, which is the one the library holds
for a paper captured from arXiv, so it ranks above the document's other
records (``store.LICENCE_BY``). A paper without the element names none.

The endpoint is ``oaipmh.arxiv.org`` (``export.arxiv.org/oai2`` answers it
with a 301). arXiv asks a harvester for one request every three seconds
(``PAUSE``), so this is a door job of minutes, never a request's work. Each
document read is stamped (``meta.arxiv_record``), found or not, and read
again only with ``refresh``. Nothing goes to arXiv but the paper's id.
"""

from __future__ import annotations

import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from prax import store
from prax.text import licences

OAI = "https://oaipmh.arxiv.org/oai"
PAUSE = 3.0  # seconds between requests, as arXiv asks of a harvester
SOURCE = "arxiv"
_NS = "{http://arxiv.org/OAI/arXiv/}"
_OAI_NS = "{http://www.openarchives.org/OAI/2.0/}"

Fetcher = Callable[[str], bytes | None]


class HttpFetcher:
    """GET the bytes of a URL at arXiv's pace, retrying a 503 (arXiv's
    "retry after") and a 5xx; None for a 404. No contact address goes with
    it: the request names a paper and nothing else."""

    def __init__(self, *, pause: float = PAUSE, timeout: float = 30) -> None:
        self.pause = pause
        self.timeout = timeout
        self.calls = 0
        self._last = 0.0

    def __call__(self, url: str) -> bytes | None:
        for attempt in range(4):
            wait = self._last + self.pause - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.calls += 1
            self._last = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": "prax/0.1"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data: bytes = resp.read()
                return data
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    after = exc.headers.get("Retry-After") if exc.headers else None
                    time.sleep(float(after) if after and after.isdigit() else 10.0)
                    continue
                raise
        return None


def record_url(arxiv_id: str) -> str:
    """The OAI-PMH request for one paper's record in arXiv's own format."""
    ident = urllib.parse.quote(f"oai:arXiv.org:{arxiv_id}", safe=":/")
    return f"{OAI}?verb=GetRecord&identifier={ident}&metadataPrefix=arXiv"


def licence_of(xml: bytes) -> tuple[bool, licences.Licence | None]:
    """What a record says: whether arXiv has the paper, and the licence its
    ``<license>`` names (None when it names none, as before 2004)."""
    root = ET.fromstring(xml)
    if root.find(f"{_OAI_NS}error") is not None:
        return False, None
    meta = root.find(f".//{_NS}arXiv")
    if meta is None:
        return False, None
    url = (meta.findtext(f"{_NS}license") or "").strip()
    return True, licences.from_url(url) if url else None


@dataclass
class Report:
    read: int = 0
    licensed: int = 0
    unknown: int = 0  # not a record arXiv has
    errors: list[str] = field(default_factory=list)


def import_licences(
    con: sqlite3.Connection,
    docs: Iterable[tuple[int, str]],
    *,
    fetch: Fetcher,
    report: Report | None = None,
) -> Report:
    """Each (document, arXiv id): its record asked for, its licence kept
    (``store.set_licence`` by ``arxiv``, under a more trusted one never) and
    the document stamped as read."""
    rep = report or Report()
    for doc_id, arxiv_id in docs:
        try:
            xml = fetch(record_url(arxiv_id))
            found, got = licence_of(xml) if xml else (False, None)
        except (OSError, ET.ParseError, urllib.error.URLError) as exc:
            rep.errors.append(f"doc {doc_id}: {type(exc).__name__}: {exc}")
            continue
        at = store.now()
        rep.read += 1
        if not found:
            rep.unknown += 1
        elif got is not None:
            rep.licensed += 1
            store.set_licence(con, doc_id, got.id, by=SOURCE, url=got.url, run=at[:10])
        stamp = {"at": at, "found": found, "licence": got.id if got else None}
        store.update_meta(con, doc_id, keys={"arxiv_record": stamp})
    return rep
