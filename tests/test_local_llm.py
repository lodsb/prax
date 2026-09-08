"""The local runtime loader: PATH handling and a clear error without the extra."""

from __future__ import annotations

import os
import sys

import pytest

from prax import local_llm


@pytest.fixture(autouse=True)
def _fresh_cache():
    local_llm.llama_class.cache_clear()
    yield
    local_llm.llama_class.cache_clear()


def test_dll_dirs_exist_and_are_prepended_once(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = tmp_path / "Lib" / "site-packages"
    (lib / "nvidia" / "cublas" / "bin").mkdir(parents=True)
    (lib / "llama_cpp" / "lib").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    dirs = local_llm.windows_dll_dirs()
    parents = [os.path.basename(os.path.dirname(d)) for d in dirs]
    assert parents == ["cublas", "llama_cpp"]
    monkeypatch.setenv("PATH", "C:\\elsewhere")
    local_llm._prepend_path(dirs)
    local_llm._prepend_path(dirs)  # idempotent
    parts = os.environ["PATH"].split(os.pathsep)
    assert parts[:2] == dirs and parts.count(dirs[0]) == 1
    assert parts[-1] == "C:\\elsewhere"


def test_missing_extra_gives_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "llama_cpp", None)  # makes the import fail
    with pytest.raises(RuntimeError, match=r"\[local\]"):
        local_llm.llama_class()
