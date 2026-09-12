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


def test_the_embedder_and_the_index_read_their_settings(
    written: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prax import embeddings, vectors

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
