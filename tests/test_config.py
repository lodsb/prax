"""prax.yaml holds what a host chooses; an environment variable overrides
one setting for one run; the data directory itself stays a variable."""

from __future__ import annotations

from pathlib import Path

import pytest

from prax import config

FILE = """\
embeddings:
  model: bge-small-en-v1.5
  variant: fp32
  providers: [DmlExecutionProvider, CPUExecutionProvider]
  threads: 4
vectors:
  dtype: i8
  ef: 96
parse:
  ocr_max_pages: 12
door:
  cors_origins: moz-extension://abc, chrome-extension://def
  inbox_scan_seconds: 5
"""


@pytest.fixture()
def written(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / config.CONFIG_NAME
    path.write_text(FILE, encoding="utf-8")
    return path


def test_a_setting_comes_from_the_file(written: Path) -> None:
    assert config.setting("embeddings.variant") == "fp32"
    assert config.whole("embeddings.threads") == 4
    assert config.setting("vectors.dtype") == "i8"
    assert config.whole("vectors.ef") == 96
    assert config.whole("parse.ocr_max_pages", "PRAX_OCR_MAX_PAGES", 60) == 12


def test_a_variable_wins_for_one_run(
    written: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAX_EMBED_VARIANT", "int8")
    assert config.setting("embeddings.variant", "PRAX_EMBED_VARIANT") == "int8"
    monkeypatch.setenv("PRAX_EMBED_VARIANT", "")  # empty is not a choice
    assert config.setting("embeddings.variant", "PRAX_EMBED_VARIANT") == "fp32"


def test_the_default_stands_when_neither_says(data_dir: Path) -> None:
    assert config.setting("embeddings.variant", "PRAX_EMBED_VARIANT", "int8") == "int8"
    assert config.whole("vectors.ef", default=64) == 64
    assert config.words("door.cors_origins") == []


def test_a_list_reads_as_a_list_or_a_comma_string(
    written: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert config.words("embeddings.providers") == [
        "DmlExecutionProvider",
        "CPUExecutionProvider",
    ]
    assert config.words("door.cors_origins") == [
        "moz-extension://abc",
        "chrome-extension://def",
    ]
    monkeypatch.setenv("PRAX_CORS_ORIGINS", "one://x , two://y")
    assert config.words("door.cors_origins", "PRAX_CORS_ORIGINS") == [
        "one://x",
        "two://y",
    ]


def test_a_section_nobody_knows_is_refused(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / config.CONFIG_NAME).write_text("embedings:\n  model: x\n", "utf-8")
    with pytest.raises(config.ConfigError, match="unknown section embedings"):
        config.document()


def test_a_number_that_is_not_one_is_refused(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / config.CONFIG_NAME).write_text("vectors:\n  ef: soon\n", "utf-8")
    with pytest.raises(config.ConfigError, match="not a number"):
        config.whole("vectors.ef")


def test_a_switch_is_on_or_off_and_nothing_else(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Findings 44 and 54: a word no switch knows meant off at four of
    them and on at three."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / config.CONFIG_NAME).write_text(
        "parse:\n  comments: off\n  ocr_gpu: ture\n", "utf-8"
    )
    assert config.flag("parse.comments", "PRAX_COMMENTS", True) is False
    assert config.flag("parse.choose_reader", "PRAX_CHOOSE_READER", True) is True
    monkeypatch.setenv("PRAX_COMMENTS", "Yes")
    assert config.flag("parse.comments", "PRAX_COMMENTS", False) is True
    with pytest.raises(config.ConfigError, match="not on or off"):
        config.flag("parse.ocr_gpu", "PRAX_OCR_GPU")


def test_the_embedder_and_the_index_read_their_settings(
    written: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prax.ml import embeddings, vectors

    embeddings._build.cache_clear()
    monkeypatch.setenv("PRAX_EMBED", "hash")  # nothing is downloaded in a test
    assert embeddings.current().name.startswith("hash")
    embeddings._build.cache_clear()
    if not vectors.available():
        pytest.skip("usearch is not installed")
    index = vectors.VectorIndex(written.parent / "x.usearch", dim=8, writable=True)
    assert index._index.dtype.name.lower().startswith("i8")
    assert index._index.expansion_search == 96


def test_editing_the_file_is_seen_by_the_next_call(written: Path) -> None:
    assert config.setting("vectors.dtype") == "i8"
    written.write_text("vectors:\n  dtype: f16\n", encoding="utf-8")
    assert config.setting("vectors.dtype") == "f16"


def test_the_boards_config_loads_and_names_no_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from prax import models

    board = Path(__file__).resolve().parents[1] / "deploy" / "prax.board.yaml"
    monkeypatch.setenv("PRAX_CONFIG", str(board))
    for var in ("PRAX_EXTRACT", "PRAX_ASK", "PRAX_TITLES"):
        monkeypatch.delenv(var, raising=False)
    assert config.setting("vectors.dtype") == "i8"
    for step in ("extract", "ask", "titles"):
        assert models.resolve(step) is None  # the worker's job, not the board's


def test_a_setting_overridden_for_one_call_is_that_threads_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``overriding`` puts a value above the environment for the block, in
    this thread only: a reading's mode no longer leaks into another
    reading running beside it (it was an environment variable)."""
    import threading

    monkeypatch.setenv("PRAX_VISION_PAGES", "scans")
    seen: list[str] = []
    inside = threading.Event()
    done = threading.Event()

    def other() -> None:
        inside.wait(5)
        seen.append(str(config.setting("parse.vision_pages", "PRAX_VISION_PAGES")))
        done.set()

    t = threading.Thread(target=other)
    t.start()
    with config.overriding("PRAX_VISION_PAGES", "all"):
        assert config.setting("parse.vision_pages", "PRAX_VISION_PAGES") == "all"
        inside.set()
        done.wait(5)
    t.join(5)
    assert seen == ["scans"]
    assert config.setting("parse.vision_pages", "PRAX_VISION_PAGES") == "scans"


def test_a_bad_switch_is_found_at_the_start(data_dir: Path) -> None:
    """A mistyped switch raised only when a parse computed its stamp: the
    batch failed, or every document it touched was failed for good, and
    nothing said so at the start (the quality review of 2026-11-11,
    findings 20 and 37). ``config.check`` reads every switch, names each
    bad one, and the door, ``prax up`` and the worker call it first."""
    from fastapi.testclient import TestClient

    from prax.api import app

    config.check()  # nothing written: every default
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / config.CONFIG_NAME).write_text(
        "parse:\n  comments: keep\n  ocr_gpu: ture\n", "utf-8"
    )
    with pytest.raises(config.ConfigError) as got:
        config.check()
    assert "parse.comments: 'keep'" in str(got.value)
    assert "parse.ocr_gpu: 'ture'" in str(got.value)
    with pytest.raises(config.ConfigError), TestClient(app):
        pass


def test_every_switch_the_code_reads_is_checked() -> None:
    """``config.SWITCHES`` is the list ``check`` reads: a ``config.flag``
    call it does not name would be found only when it is read."""
    import ast

    root = Path(__file__).parents[1] / "src" / "prax"
    read: set[str] = set()
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "flag"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "config"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                read.add(str(node.args[0].value))
    assert read and read == {s.dotted for s in config.SWITCHES}


def test_every_setting_the_code_reads_is_documented() -> None:
    """Finding 21 of the 2026-11-11 quality review: about fifteen settings
    the code read were named in neither ``prax.example.yaml`` nor the
    guide. A dotted key read through ``prax.config`` is named in one of
    them: whole, or its last part as a key or in backticks."""
    import ast
    import re

    root = Path(__file__).resolve().parents[1]
    readers = {"setting", "number", "whole", "flag", "words"}
    keys: dict[str, str] = {}
    for p in (root / "src" / "prax").rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            first = node.args[0]
            if (
                name in readers
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and "." in first.value
            ):
                keys.setdefault(first.value, f"{p.name}:{node.lineno}")
    template = (root / "prax.example.yaml").read_text(encoding="utf-8")
    guide = (root / "docs" / "howto.md").read_text(encoding="utf-8")

    def named(key: str) -> bool:
        leaf = key.rsplit(".", 1)[-1]
        return (
            f"`{key}`" in guide
            or f"`{leaf}`" in guide
            or bool(re.search(rf"\b{re.escape(leaf)}\s*:", guide + template))
        )

    missing = sorted(f"{k} ({at})" for k, at in keys.items() if not named(k))
    assert missing == []


def test_a_mistyped_key_is_refused_at_the_start(data_dir: Path) -> None:
    """Finding 21 of the 2026-11-11 quality review, its second half: a key
    the code does not read was read as absent, and its default served
    without a word. ``config.check`` names it; a nested key is walked to
    its leaf, a known key's own value is not, and a section with a grammar
    of its own is left to its reader."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / config.CONFIG_NAME).write_text(
        "door:\n  sqlite_cahce_mb: 64\n  sqlite_cache_mb: 64\n"
        "faces:\n  match:\n    line: 0.5\n    lines: 0.5\n"
        "places:\n  roots:\n    anything: {here: D:/x}\n"
        "maths:\n  timeout_s: 3\n  timeout: 3\n"
        "run:\n  door: {}\n",
        "utf-8",
    )
    assert config.unknown_keys() == [
        "door.sqlite_cahce_mb",
        "faces.match.lines",
        "maths.timeout",
    ]
    with pytest.raises(config.ConfigError) as got:
        config.check()
    assert "door.sqlite_cahce_mb is not a setting" in str(got.value)


def test_every_key_the_code_reads_is_in_the_table() -> None:
    """``config.KEYS`` is what ``check`` holds a section to: a key the code
    reads and the table lacks would stop a door whose prax.yaml sets it,
    and a key the table names that nothing reads is a typo let through."""
    import ast

    root = Path(__file__).resolve().parents[1] / "src" / "prax"
    readers = {"setting", "number", "whole", "flag", "words", "mode"}
    read: set[str] = set()
    for p in root.rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            first = node.args[0]
            if (
                name in readers
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and first.value.split(".", 1)[0] in config.KEYS
                and "." in first.value
            ):
                read.add(first.value)
    # prax.ml.sessions reads these by a section it is given
    for section in ("embeddings", "rerank"):
        read |= {f"{section}.{k}" for k in ("providers", "threads", "arena")}
    table = {f"{s}.{k}" for s, keys in config.KEYS.items() for k in keys}
    assert sorted(read - table) == []
    assert sorted(table - read) == []
