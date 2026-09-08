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
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

INSTALL_HINT = 'pip install -e ".[local]" (docs/howto.md, section 3f)'
DEFAULT_CTX = 8192


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


@dataclass
class LlamaRuntime:
    """One loaded GGUF model behind the single call the extractors need:
    a system and a user message in, text and token counts out, optionally
    under a GBNF grammar. Loads lazily; grammars compile once per string."""

    model_path: str
    n_ctx: int = DEFAULT_CTX
    n_gpu_layers: int = -1  # everything on the GPU; 0 for a CPU host
    _llm: Any = field(default=None, init=False, repr=False)
    _grammars: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    @property
    def name(self) -> str:
        return "local:" + Path(self.model_path).stem

    def _load(self) -> Any:
        if self._llm is None:
            self._llm = llama_class()(
                model_path=self.model_path,
                n_ctx=self.n_ctx,
                n_gpu_layers=self.n_gpu_layers,
                n_batch=512,
                verbose=False,
            )
        return self._llm

    def _grammar(self, text: str) -> Any:
        g = self._grammars.get(text)
        if g is None:
            from llama_cpp import LlamaGrammar

            g = self._grammars[text] = LlamaGrammar.from_string(text, verbose=False)
        return g

    def chat(
        self,
        system: str,
        user: str,
        *,
        grammar: str | None = None,
        max_tokens: int = 2000,
        temperature: float = 0.0,
        repeat_penalty: float = 1.0,
    ) -> tuple[str, dict[str, int]]:
        llm = self._load()
        llm.reset()
        r = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            grammar=self._grammar(grammar) if grammar else None,
            max_tokens=max_tokens,
            temperature=temperature,
            repeat_penalty=repeat_penalty,
        )
        text = r["choices"][0]["message"]["content"] or ""
        u = r["usage"]
        return text, {
            "input_tokens": int(u["prompt_tokens"]),
            "output_tokens": int(u["completion_tokens"]),
        }
