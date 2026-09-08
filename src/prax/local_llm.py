"""Local llama.cpp runtime (the ``local`` extra).

``llama-cpp-python`` is an optional dependency: batch hosts with a GPU can
run extraction or generation with a GGUF model instead of the Claude API.
This module owns the one platform quirk of loading it, so that callers
(``scripts/bench_local_llm.py``, a local extractor) only ask for the class.

On Windows the wheel's loader opens ``llama.dll`` with ``winmode=0``, which
searches ``PATH`` and ignores ``os.add_dll_directory``; the CUDA build also
needs ``cudart64_12.dll`` and ``cublas64_12.dll``, which the
``nvidia-cuda-runtime-cu12`` and ``nvidia-cublas-cu12`` wheels put under
``site-packages/nvidia/*/bin``. ``llama_class`` prepends those folders to
``PATH`` before the import.
"""

from __future__ import annotations

import glob
import os
import sys
from functools import cache
from typing import Any

INSTALL_HINT = 'pip install -e ".[local]" (docs/howto.md, section 3f)'


def windows_dll_dirs() -> list[str]:
    """Folders that must be on PATH for ``llama_cpp`` to load on Windows."""
    root = os.path.join(sys.prefix, "Lib", "site-packages")
    dirs = glob.glob(os.path.join(root, "nvidia", "*", "bin"))
    dirs.append(os.path.join(root, "llama_cpp", "lib"))
    return [d for d in dirs if os.path.isdir(d)]


def _prepend_path(dirs: list[str]) -> None:
    path = os.environ.get("PATH", "")
    present = set(path.split(os.pathsep))
    missing = [d for d in dirs if d not in present]
    if missing:
        os.environ["PATH"] = os.pathsep.join([*missing, path])


@cache
def llama_class() -> type[Any]:
    """``llama_cpp.Llama``, with the runtime folders on PATH first.

    Raises ``RuntimeError`` with the install hint when the extra is missing.
    """
    if sys.platform == "win32":
        _prepend_path(windows_dll_dirs())
    try:
        from llama_cpp import Llama
    except ImportError as e:
        raise RuntimeError(f"llama-cpp-python is not installed: {INSTALL_HINT}") from e
    return Llama
