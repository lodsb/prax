"""The door-side importers: GitHub stars, chat exports and lists of links
become documents through the door, once each, and again only when the
source changed."""

from __future__ import annotations

import base64
import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import inbox
from prax.client import Door
from prax.importers import chats, feed, github, links

PAGE = (
    "<html><head><title>A page about reverb</title></head><body><p>{}</p></body></html>"
)


@pytest.fixture()
def door(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Door]:
    from prax.api import app

    pages: dict[str, str] = {}

    def fake_fetch(url: str, *, timeout: float = 0) -> tuple[bytes, str, str]:
        body = pages.get(url, "Feedback delay networks make a reverb. " * 30)
        return PAGE.format(body).encode(), "text/html", url

    monkeypatch.setattr(inbox, "fetch_url", fake_fetch)
    with TestClient(app) as client:
        d = Door("http://testserver", client=client, name="test")
        d.pages = pages  # type: ignore[attr-defined]
        yield d


# ------------------------------------------------------------------ github


def _repo(name: str, **more: Any) -> dict[str, Any]:
    return {
        "id": hash(name) % 100000,
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "description": more.pop("description", "A wave digital filter library"),
        "language": "C++",
        "stargazers_count": 1234,
        "topics": ["dsp", "audio"],
        "license": {"spdx_id": "MIT"},
        "pushed_at": more.pop("pushed_at", "2025-01-02T03:04:05Z"),
        "created_at": "2020-01-01T00:00:00Z",
        **more,
    }


class FakeGitHub:
    """The two calls the importer makes, answered from a dict."""

    def __init__(self, stars: list[dict[str, Any]], readmes: dict[str, str]) -> None:
        self.stars = stars
        self.readmes = readmes
        self.calls = 0

    def __call__(
        self, url: str, headers: dict[str, str]
    ) -> tuple[int, dict[str, str], bytes]:
        self.calls += 1
        assert headers["User-Agent"].startswith("prax")
        if "/starred" in url:
            page = 2 if "page=2" in url else 1
            rows = self.stars[(page - 1) * 2 : page * 2]
            link = (
                {"Link": f'<{url.split("&page")[0]}&page=2>; rel="next"'}
                if page == 1 and len(self.stars) > 2
                else {}
            )
            return (
                200,
                {**link, "X-RateLimit-Remaining": "4999"},
                json.dumps(rows).encode(),
            )
        if url.endswith("/readme"):
            name = url.split("/repos/")[1].removesuffix("/readme")
            if name not in self.readmes:
                return 404, {}, b"{}"
            content = base64.b64encode(self.readmes[name].encode()).decode()
            return (
                200,
                {},
                json.dumps({"encoding": "base64", "content": content}).encode(),
            )
        return 500, {}, b""


def test_github_stars_become_documents_once_and_again_when_pushed(door: Door) -> None:
    stars = [
        {"starred_at": "2024-03-05T10:00:00Z", "repo": _repo("alice/wdf")},
        {
            "starred_at": "2024-02-01T10:00:00Z",
            "repo": _repo("bob/fft", description=""),
        },
        {"starred_at": "2024-01-01T10:00:00Z", "repo": _repo("carol/noreadme")},
    ]
    fake = FakeGitHub(
        stars, {"alice/wdf": "# WDF\n\nWave digital filters in C++.", "bob/fft": "fast"}
    )
    api = github.GitHub(fetch=fake)
    report = feed.run(
        door, github.SOURCE, github.items(api, "octocat"), domains=["research"]
    )
    assert (report.added, report.seen, report.failed) == (3, 0, [])
    listing = door.get_json("/documents", {"source": "github"})
    assert listing["total"] == 3
    by_key = {d["meta"]["github"]["key"]: d for d in listing["items"]}
    wdf = by_key["alice/wdf"]
    assert wdf["title"].startswith("alice/wdf: A wave digital filter")
    assert wdf["source_url"] == "https://github.com/alice/wdf"
    assert wdf["meta"]["github"]["topics"] == ["dsp", "audio"]
    assert wdf["meta"]["tags"] == ["github:dsp", "github:audio"]
    assert wdf["meta"]["domains"] == ["research"]
    text = door.get_json(f"/get/{wdf['id']}")["text"]
    assert text.startswith("# alice/wdf\n\nA wave digital filter library\n")
    assert "- Language: C++ · Stars: 1,234 · Licence: MIT" in text
    assert "Starred: 2024-03-05 · Last push: 2025-01-02" in text
    assert text.rstrip().endswith("Wave digital filters in C++.")
    assert by_key["bob/fft"]["title"] == "bob/fft"
    assert (
        "(no README)" in door.get_json(f"/get/{by_key['carol/noreadme']['id']}")["text"]
    )
    # the same stars again: nothing new, and no README was fetched for them
    before = fake.calls
    again = feed.run(door, github.SOURCE, github.items(api, "octocat"))
    assert (again.added, again.seen) == (0, 3)
    assert (
        fake.calls - before == 2
    )  # the two listing pages; READMEs are fetched per item
    # a push: --refresh replaces that one document and retires the old one
    stars[0]["repo"]["pushed_at"] = "2025-06-01T00:00:00Z"
    fake.readmes["alice/wdf"] = "# WDF\n\nNow with adaptors."
    third = feed.run(door, github.SOURCE, github.items(api, "octocat"), refresh=True)
    assert (third.refreshed, third.seen) == (1, 2)
    now = door.get_json("/documents", {"source": "github"})
    assert now["total"] == 3
    fresh = next(d for d in now["items"] if d["meta"]["github"]["key"] == "alice/wdf")
    assert fresh["id"] != wdf["id"]
    old = door.get_json(f"/get/{wdf['id']}")
    assert old["meta"]["retired"]["of"] == fresh["id"]


def test_github_rate_limit_and_missing_user_are_said_plainly() -> None:
    def spent(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        return 403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "0"}, b""

    with pytest.raises(RuntimeError, match="rate limit.*token"):
        list(github.GitHub(fetch=spent).starred("x"))

    def nobody(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        return 404, {}, b""

    with pytest.raises(RuntimeError, match="knows no user"):
        list(github.GitHub(fetch=nobody).starred("nobody"))
    api = github.GitHub(token="t", fetch=lambda u, h: (200, {}, b"[]"))
    assert api._headers("a")["Authorization"] == "Bearer t"
    assert list(api.starred(None)) == []


# -------------------------------------------------------------------- chats


TELEGRAM = {
    "name": "Alice",
    "type": "personal_chat",
    "id": 1,
    "messages": [
        {
            "id": 1,
            "type": "message",
            "date": "2024-03-05T10:04:00",
            "from": "Alice",
            "text": "hello",
        },
        {
            "id": 2,
            "type": "message",
            "date": "2024-03-05T10:05:30",
            "from": "Me",
            "text": [
                "look ",
                {"type": "link", "text": "https://example.org/wdf"},
                " nice",
            ],
        },
        {
            "id": 3,
            "type": "service",
            "date": "2024-03-06T00:00:00",
            "actor": "Alice",
            "action": "phone_call",
        },
        {
            "id": 4,
            "type": "message",
            "date": "2024-03-07T12:00:00",
            "from": "Alice",
            "text": "",
            "photo": "photos/photo_1.jpg",
        },
        {
            "id": 5,
            "type": "message",
            "date": "2024-04-01T09:00:00",
            "from": "Alice",
            "text": "new month",
        },
    ],
}

SIGTOP = [
    {
        "type": "incoming",
        "sent_at": 1709632800000,
        "body": "hi from signal",
        "source": "+491",
    },
    {
        "type": "outgoing",
        "sent_at": 1709632900000,
        "body": "see https://example.org/paper.pdf",
        "attachments": [],
    },
    {
        "type": "outgoing",
        "sent_at": 1709633000000,
        "body": "",
        "attachments": [{"fileName": "sketch.png", "contentType": "image/png"}],
    },
    {
        "type": "incoming",
        "sent_at": 1709633100000,
        "body": "reply",
        "quote": {"text": "see https://example.org/paper.pdf"},
    },
]

WHATSAPP = """\
05/03/2024, 10:04 - Alice: hello there
05/03/2024, 10:05 - Bob: a second
line of the same message
05/03/2024, 10:06 - Alice: <Media omitted>
[13/03/2024, 11:00:00] Bob: bracketed https://example.org/x
"""


def test_telegram_export_becomes_a_document_per_month(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps(TELEGRAM), encoding="utf-8")
    (conv,) = chats.read(path)
    assert conv.app == "telegram" and conv.name == "Alice"
    assert [m.who for m in conv.messages] == [
        "Alice",
        "Me",
        "Alice",
        "Alice",
    ]  # the call is not a message
    items = list(chats.items([conv]))
    assert [i.key for i in items] == [
        "telegram/Alice/2024-03",
        "telegram/Alice/2024-04",
    ]
    march = items[0]
    assert march.title == "Alice · March 2024 (Telegram)"
    assert march.meta["messages"] == 3 and march.meta["links"] == 1
    assert march.text.startswith(
        "# Alice — Telegram, March 2024\n\n## 2024-03-05\n\n**Alice** 10:04 — hello\n"
    )
    assert "**Me** 10:05 — look https://example.org/wdf nice" in march.text
    assert "**Alice** 12:00 — [attachment: photo_1.jpg]" in march.text
    assert march.text.rstrip().endswith("## Links\n\n- https://example.org/wdf")
    # the whole-export shape
    whole = {"chats": {"list": [TELEGRAM, {**TELEGRAM, "name": "Bob", "id": 2}]}}
    path.write_text(json.dumps(whole), encoding="utf-8")
    assert [c.name for c in chats.read(path)] == ["Alice", "Bob"]


def test_sigtop_and_whatsapp_exports_are_read(tmp_path: Path) -> None:
    sig = tmp_path / "Alice (+491).json"
    sig.write_text(json.dumps(SIGTOP), encoding="utf-8")
    (conv,) = chats.read(sig)
    assert conv.app == "signal" and conv.name == "Alice (+491)"
    assert [m.who for m in conv.messages] == ["+491", "me", "me", "Alice (+491)"]
    assert conv.messages[0].at.strftime("%Y-%m-%d %H:%M") == "2024-03-05 10:00"
    assert conv.messages[2].attachments == ["sketch.png"]
    assert conv.messages[3].quote.startswith("see https")
    (item,) = chats.items([conv])
    assert item.key == "signal/Alice (+491)/2024-03"
    assert (
        "> see https://example.org/paper.pdf\n**Alice (+491)** 10:05 — reply"
        in item.text
    )
    wa = tmp_path / "WhatsApp Chat with Bob.txt"
    wa.write_text(WHATSAPP, encoding="utf-8")
    (conv,) = chats.read(wa)
    assert conv.app == "whatsapp"
    assert [m.at.strftime("%d.%m %H:%M") for m in conv.messages] == [
        "05.03 10:04",
        "05.03 10:05",
        "05.03 10:06",
        "13.03 11:00",
    ]
    assert conv.messages[1].text == "a second\nline of the same message"
    assert conv.messages[2].attachments == ["Media omitted"]
    (item,) = chats.items([conv])
    assert "**Bob** 10:05 — a second\n  line of the same message" in item.text
    with pytest.raises(ValueError, match="expected"):
        chats.read(tmp_path / "x.csv")


def test_chat_months_land_once_and_grow_with_refresh(
    door: Door, tmp_path: Path
) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps(TELEGRAM), encoding="utf-8")
    report = feed.run(
        door,
        chats.SOURCE,
        chats.items(chats.read(path), with_links=True),
        tags=["friends"],
    )
    assert (report.added, report.failed) == (3, [])  # two months and one link
    docs = door.get_json("/documents", {"source": "chat"})
    assert docs["total"] == 2
    march = next(d for d in docs["items"] if d["meta"]["chat"]["month"] == "2024-03")
    assert march["meta"]["tags"] == ["friends", "chat:Alice"]
    captures = door.get_json("/captures", {"url": "https://example.org/wdf"})
    assert len(captures) == 1
    cap = door.get_json(f"/get/{captures[0]['doc_id']}")
    assert cap["meta"]["capture"]["by"] == "import:chat"
    assert cap["meta"]["capture"]["note"].startswith(
        "Me, 2024-03-05 10:05 in Alice: look https"
    )
    assert cap["meta"]["tags"] == ["friends", "chat:Alice"]
    # again: nothing new, the link is not fetched again
    again = feed.run(door, chats.SOURCE, chats.items(chats.read(path), with_links=True))
    assert (again.added, again.seen) == (0, 3)
    # April grows: --refresh replaces April, leaves March
    TELEGRAM["messages"].append(
        {
            "id": 6,
            "type": "message",
            "date": "2024-04-02T09:00:00",
            "from": "Me",
            "text": "more",
        }
    )
    path.write_text(json.dumps(TELEGRAM), encoding="utf-8")
    third = feed.run(door, chats.SOURCE, chats.items(chats.read(path)), refresh=True)
    assert (third.refreshed, third.seen) == (1, 1)
    TELEGRAM["messages"].pop()
    now = {
        d["meta"]["chat"]["month"]: d
        for d in door.get_json("/documents", {"source": "chat"})["items"]
    }
    assert now["2024-03"]["id"] == march["id"]
    assert "more" in door.get_json(f"/get/{now['2024-04']['id']}")["text"]


# -------------------------------------------------------------------- links


BOOKMARKS = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3 ADD_DATE="1709632800">Reading</H3>
    <DL><p>
        <DT><H3>DSP</H3>
        <DL><p>
            <DT><A HREF="https://example.org/wdf" ADD_DATE="1709632800"
                TAGS="filters,wdf">Wave digital filters</A>
        </DL><p>
        <DT><A HREF="https://example.org/fdn">Feedback delay networks</A>
    </DL><p>
    <DT><A HREF="https://example.org/wdf">a duplicate</A>
    <DT><A HREF="javascript:void(0)">not a link</A>
</DL><p>
"""

POCKET = """title,url,time_added,tags,status
Wave digital filters,https://example.org/wdf,1709632800,filters|wdf,unread
,https://example.org/fdn,1709632800,,archive
"""

RAINDROP = (
    "title,note,excerpt,url,folder,tags,created\n"
    "WDF,my note,an excerpt,https://example.org/wdf,Reading/DSP,"
    '"filters, wdf",2024-03-05T10:00:00Z\n'
)


def test_bookmark_files_csvs_and_text_are_read() -> None:
    items = list(links.read_html(BOOKMARKS))
    assert [i.url for i in items] == [
        "https://example.org/wdf",
        "https://example.org/fdn",
    ]
    wdf, fdn = items
    assert wdf.title == "Wave digital filters"
    assert wdf.tags == ["filters", "wdf", "folder:Reading/DSP"]
    assert wdf.meta == {"added": "2024-03-05"}
    assert fdn.tags == ["folder:Reading"]
    pocket = list(links.read_csv(POCKET))
    assert [(i.url, i.title, i.tags, i.meta) for i in pocket] == [
        (
            "https://example.org/wdf",
            "Wave digital filters",
            ["filters", "wdf"],
            {"added": "2024-03-05"},
        ),
        ("https://example.org/fdn", None, [], {"added": "2024-03-05"}),
    ]
    (rain,) = links.read_csv(RAINDROP)
    assert rain.tags == ["filters", "wdf", "folder:Reading/DSP"]
    assert rain.note == "my note · an excerpt" and rain.meta == {"added": "2024-03-05"}
    with pytest.raises(ValueError, match="url column"):
        list(links.read_csv("a,b\n1,2\n"))
    text = list(
        links.read_text(
            "- https://example.org/wdf the classic paper.\nnothing here\nhttps://example.org/fdn\n"
        )
    )
    assert [(i.url, i.note) for i in text] == [
        ("https://example.org/wdf", "the classic paper."),
        ("https://example.org/fdn", None),
    ]


def test_mediums_export_zip_yields_bookmarks_lists_and_highlights(
    tmp_path: Path,
) -> None:
    def page(items: list[str]) -> str:
        return "<html><body><ul>" + "".join(items) + "</ul></body></html>"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "medium-export/bookmarks/bookmarks-0001.html",
            page(
                [
                    '<li><a href="https://medium.com/p/abc">A story</a></li>',
                    '<li><a href="https://medium.com/me/lists">me</a></li>',
                ]
            ),
        )
        zf.writestr(
            "medium-export/lists/dsp-reading-1234.html",
            page(
                [
                    (
                        '<li><a href="https://custom.example/story">'
                        "On a custom domain</a></li>"
                    )
                ]
            ),
        )
        zf.writestr(
            "medium-export/highlights/highlights-0001.html",
            page(
                [
                    (
                        '<li><a href="https://medium.com/p/abc">A story</a>'
                        " — the sentence I marked</li>"
                    )
                ]
            ),
        )
        zf.writestr(
            "medium-export/posts/draft.html",
            page(['<li><a href="https://medium.com/p/mine">mine</a></li>']),
        )
        zf.writestr("medium-export/profile/about.html", "<html></html>")
    path = tmp_path / "medium-export.zip"
    path.write_bytes(buf.getvalue())
    items = list(links.read(path))
    assert [(i.url, i.tags, i.note) for i in items] == [
        ("https://medium.com/p/abc", ["medium:bookmarks"], None),
        ("https://custom.example/story", ["medium:list:dsp-reading"], None),
    ]


def test_links_are_fetched_by_the_door_once(door: Door, tmp_path: Path) -> None:
    path = tmp_path / "bookmarks.html"
    path.write_text(BOOKMARKS, encoding="utf-8")
    door.pages["https://example.org/wdf"] = (
        "Wave digital filters model analogue circuits. " * 30
    )  # type: ignore[attr-defined]
    report = feed.run(
        door, links.SOURCE, links.read(path), domains=["research"], dry_run=True
    )
    assert [i.url for i in report.planned] == [
        "https://example.org/wdf",
        "https://example.org/fdn",
    ]
    assert door.get_json("/documents", {"source": "capture"})["total"] == 0
    report = feed.run(door, links.SOURCE, links.read(path), domains=["research"])
    assert (report.added, report.failed) == (2, [])
    (cap,) = door.get_json("/captures", {"url": "https://example.org/wdf"})
    doc = door.get_json(f"/get/{cap['doc_id']}")
    assert doc["meta"]["capture"]["by"] == "import:links"
    assert doc["meta"]["tags"] == ["filters", "wdf", "folder:Reading/DSP"]
    assert doc["meta"]["domains"] == ["research"]
    assert (
        doc["title"] == "Wave digital filters"
    )  # the bookmark's title wins over the page's
    again = feed.run(door, links.SOURCE, links.read(path))
    assert (again.added, again.seen) == (0, 2)
    # a page that changed is a new version linked to the old one, not a retirement
    door.pages["https://example.org/wdf"] = (
        "The page was rewritten around adaptors. " * 30
    )  # type: ignore[attr-defined]
    third = feed.run(door, links.SOURCE, links.read(path), refresh=True)
    assert (third.refreshed, third.same) == (1, 1)
    both = door.get_json("/captures", {"url": "https://example.org/wdf"})
    assert len(both) == 2
    newer = door.get_json(f"/get/{both[-1]['doc_id']}")
    assert newer["meta"]["previous_capture"] == cap["doc_id"]


def test_a_fetch_that_fails_is_reported_and_the_run_goes_on(
    door: Door, monkeypatch: pytest.MonkeyPatch
) -> None:
    def flaky(url: str, *, timeout: float = 0) -> tuple[bytes, str, str]:
        if "broken" in url:
            raise OSError("HTTP Error 404: Not Found")
        return PAGE.format("fine " * 50).encode(), "text/html", url

    monkeypatch.setattr(inbox, "fetch_url", flaky)
    items = [
        feed.Item(key=u, kind="link", url=u)
        for u in ("https://example.org/broken", "https://example.org/ok")
    ]
    report = feed.run(door, links.SOURCE, items)
    assert report.added == 1
    assert report.failed[0][0] == "https://example.org/broken"
    assert "404" in report.failed[0][1]
    assert str(report) == "links: 1 added, 1 failed"
    assert feed.held_capture(door, "not a url") is None
