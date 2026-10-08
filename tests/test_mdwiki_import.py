"""A repository of papers with a Markdown wiki about them (``prax import
mdwiki``): which files are the wiki, and its links as the library's."""

from __future__ import annotations

from pathlib import Path

from prax.importers import mdwiki


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
    assert len(one) <= mdwiki.SLUG_CHARS and one != two
