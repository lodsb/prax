"""A maintenance pass reads and parses holding no write: what it works
out is applied in short writes behind the store's lock (``maintain._write``).
On 2026-10-07 the markup pass held SQLite's write lock from one commit to
the next, a hundred pages apart, for 71 minutes, and the night's backup
failed on "database is locked"."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from prax import store
from prax.text import dates, language, quotes, schemaorg

PAGE = b"""<html><head><title>Apple cake</title>
<meta name="citation_publication_date" content="2019/07/03">
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Recipe", "name": "Apple cake",
 "author": {"@type": "Person", "name": "Ada Baker"},
 "recipeIngredient": ["3 apples", "200 g flour"]}
</script></head><body><p>Bake the apples into a cake.</p></body></html>"""


def test_the_passes_read_and_parse_with_no_write_open(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # two of each, so a pass's second read comes after its first write
    for n in (1, 2):
        store.register(
            con,
            PAGE.replace(b"3 apples", b"%d apples" % n),
            mime="text/html",
            title=f"Apple cake {n}",
        )
        doc = store.ingest_text(
            con,
            f"The wavelet transform decomposes signal {n}. " * 40,
            title=f"Wavelets {n}",
        )["doc_id"]
        meta = store.get_meta(con, doc)
        meta.pop("lang", None)  # read again by the languages pass
        store.set_meta(con, doc, meta)
        store.link(
            con,
            store.Edge(f"Wavelets {n}", "paper", "uses", "wavelet transform", "method"),
            source_doc=doc,
            evidence=f"The wavelet transform decomposes signal {n}.",
            producer="t",
        )
    con.commit()
    seen: list[tuple[str, bool]] = []
    for module, name in (
        (schemaorg, "of_page"),
        (dates, "from_html"),
        (language, "detect"),
        (quotes, "place"),
    ):
        real = getattr(module, name)

        def spy(*a: Any, _real: Any = real, _name: str = name, **k: Any) -> Any:
            seen.append((_name, con.in_transaction))
            return _real(*a, **k)

        monkeypatch.setattr(module, name, spy)
    out = store.maintain(con, only=["markup", "published", "languages", "places"])
    assert {n for n, _ in seen} == {"of_page", "from_html", "detect", "place"}
    assert not [n for n, open_ in seen if open_], seen
    assert out["places"]["placed"] == 2
    assert store.get_meta(con, doc)["lang"] == "en"
    assert not con.in_transaction  # nothing left open behind it
