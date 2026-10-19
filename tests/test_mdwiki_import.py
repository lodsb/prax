"""A repository of papers with a Markdown wiki about them (``prax import
mdwiki``): which files are the wiki, and its links as the library's."""

from __future__ import annotations

from pathlib import Path

from prax import store
from prax.importers import mdwiki
from prax.text import slugs


def _repo(root: Path) -> Path:
    files = {
        "README.md": "# Music 423\n\nTopics: [diffusion](diffusion/).\n",
        "CLAUDE.md": "# tooling\n",
        "diffusion/DDPM-2006.11239.pdf": "%PDF-1.4",
        "diffusion/README.md": "# Diffusion\n\nPapers: [DDPM](DDPM-2006.11239.pdf).\n",
        "diffusion/txt/DDPM.txt": "a copy of the text",
        "diffusion/wiki/index.md": "---\ntags: [index]\n---\n\n# Diffusion wiki\n",
        "diffusion/wiki/log.md": "# log\n",
        "diffusion/wiki/CLAUDE.md": "# tooling\n",
        "diffusion/wiki/sources/ddpm.md": (
            "---\ntags: [source]\n---\n\n# DDPM\n\n"
            "PDF: [the paper](../../DDPM-2006.11239.pdf). See [noise](../noise.md),"
            " [attention](../../../attention/), [missing](../../gone.pdf),"
            " [web](https://arxiv.org/abs/2006.11239) and ![fig](fig.png).\n"
        ),
        "diffusion/wiki/noise.md": "# Noise\n\nFrom [DDPM](sources/ddpm.md).\n",
        "diffusion/.obsidian/notes.md": "# not the wiki\n",
        "attention/README.md": "# Attention\n",
        "admin/Schedule.md": "# Schedule\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def test_the_wiki_is_its_pages_and_not_its_tooling(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert mdwiki.pdfs(root) == ["diffusion/DDPM-2006.11239.pdf"]
    found = {n.path: n for n in mdwiki.notes(root, "m423")}
    assert set(found) == {
        "README.md",
        "attention/README.md",
        "diffusion/README.md",
        "diffusion/wiki/index.md",
        "diffusion/wiki/noise.md",
        "diffusion/wiki/sources/ddpm.md",
    }
    ddpm = found["diffusion/wiki/sources/ddpm.md"]
    assert ddpm.title == "DDPM" and ddpm.topic == "diffusion"
    assert not ddpm.text.startswith("---")  # the front matter is not the page
    assert ddpm.slug == "m423-diffusion-wiki-sources-ddpm"
    entries = mdwiki.topic_entries(list(found.values()))
    assert entries == {
        "diffusion": "diffusion/wiki/index.md",
        "attention": "attention/README.md",
    }


def test_a_notes_links_become_the_librarys(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    ddpm = next(
        n for n in mdwiki.notes(root, "m423") if n.path.endswith("sources/ddpm.md")
    )
    text = mdwiki.rewrite(
        ddpm.text,
        ddpm.path,
        documents={"diffusion/DDPM-2006.11239.pdf": 10},
        pages={"diffusion/wiki/noise.md": 20},
        topics={"attention": 30},
    )
    assert "[the paper](#doc/10)" in text
    assert "[noise](#doc/20)" in text
    assert "[attention](#doc/30)" in text  # a topic's folder is its entry page
    assert "missing" in text and "gone.pdf" not in text  # words kept, link gone
    assert "[web](https://arxiv.org/abs/2006.11239)" in text
    assert "![fig](fig.png)" in text


def test_a_long_path_keeps_a_slug_of_its_own() -> None:
    long = "topic/wiki/sources/" + "a-very-long-paper-name-" * 6 + "{}.md"
    one, two = (
        mdwiki.slug_for("m423", long.format(1)),
        mdwiki.slug_for("m423", long.format(2)),
    )
    assert len(one) <= slugs.LIMIT and one != two
    assert store.slugify(one) == one  # the door keeps it whole


def test_a_note_sits_under_its_wiki_index_and_the_index_under_its_topic() -> None:
    paths = {
        "README.md",
        "diffusion/README.md",
        "diffusion/wiki/index.md",
        "diffusion/wiki/noise.md",
        "diffusion/wiki/sources/ddpm.md",
        "attention/README.md",
        "attention/wiki/notes/x.md",
    }
    p = mdwiki.parent_of
    assert p("README.md", paths) is None
    assert p("diffusion/README.md", paths) == "README.md"
    assert p("diffusion/wiki/index.md", paths) == "diffusion/README.md"
    assert p("diffusion/wiki/noise.md", paths) == "diffusion/wiki/index.md"
    assert p("diffusion/wiki/sources/ddpm.md", paths) == "diffusion/wiki/index.md"
    # a topic without a wiki index: its pages under the topic's README
    assert p("attention/wiki/notes/x.md", paths) == "attention/README.md"


def test_another_layout_is_an_option_and_what_is_left_out_is_counted(
    tmp_path: Path,
) -> None:
    # the quality review of 2026-10-18 (17): a wiki of another shape was
    # imported as its READMEs, and said nothing
    root = _repo(tmp_path)
    # CLAUDE.md twice, the wiki's log, .obsidian's note and the admin page
    assert mdwiki.left_out(root) == {"skipped": 5, "outside": 0}
    (root / "notes" / "topic").mkdir(parents=True)
    (root / "notes" / "topic" / "a.md").write_text("---  \nx: 1\n---\n# A\n\nbody\n")
    assert mdwiki.left_out(root)["outside"] == 1
    other = mdwiki.Layout(wiki="notes", skip=frozenset({".git", "admin"}))
    got = {n.path: n for n in mdwiki.notes(root, "m", other)}
    assert "notes/topic/a.md" in got and "diffusion/wiki/noise.md" not in got
    # front matter with a space after its marker is front matter still (15)
    assert got["notes/topic/a.md"].text == "# A\n\nbody\n"
    assert mdwiki.pdfs(root, other) == ["diffusion/DDPM-2006.11239.pdf"]
