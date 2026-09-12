"""Cross-encoder reranking of search hits, optional and off by default.

A cross-encoder reads the query and a candidate passage together and emits
one relevance logit, which is far more precise than comparing two
independent embeddings, at the price of one forward pass per candidate. It
is therefore applied only to the top ``depth`` hits of a search, never to
the index. The same ONNX + ``tokenizers`` route as ``prax.embeddings``.

Environment:

* ``PRAX_RERANK``: ``0`` (default, off), ``stub`` (token-overlap scorer for
  tests), or a model name from ``MODELS``;
* ``PRAX_RERANK_VARIANT``: ``fp32`` or ``int8`` (default int8 on CPU);
* ``PRAX_RERANK_PROVIDERS``: onnxruntime providers.
"""

from __future__ import annotations

import functools
import importlib
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from prax import fetch


@dataclass(frozen=True)
class ModelSpec:
    name: str
    repo: str
    files: dict[str, str]
    max_tokens: int = 512


MODELS: dict[str, ModelSpec] = {
    # 22 M parameters; MS MARCO passage ranking
    "ms-marco-MiniLM-L-6-v2": ModelSpec(
        name="ms-marco-MiniLM-L-6-v2",
        repo="Xenova/ms-marco-MiniLM-L-6-v2",
        files={"fp32": "onnx/model.onnx", "int8": "onnx/model_quantized.onnx"},
    ),
    # 278 M parameters; stronger, about ten times the compute
    "bge-reranker-base": ModelSpec(
        name="bge-reranker-base",
        repo="Xenova/bge-reranker-base",
        files={"fp32": "onnx/model.onnx", "int8": "onnx/model_quantized.onnx"},
    ),
}
DEFAULT_MODEL = "ms-marco-MiniLM-L-6-v2"


def current_spec() -> ModelSpec | None:
    """The model ``PRAX_RERANK`` names, or None when reranking is off or a
    stub."""
    name = os.environ.get("PRAX_RERANK", "0")
    return MODELS.get(name)


class Reranker(Protocol):
    name: str

    def score(self, query: str, texts: list[str]) -> np.ndarray: ...


@dataclass
class OnnxReranker:
    spec: ModelSpec
    variant: str | None = None
    providers: list[str] | None = None
    batch_size: int = 16
    _session: Any = field(default=None, init=False, repr=False)
    _tokenizer: Any = field(default=None, init=False, repr=False)
    _inputs: set[str] = field(default_factory=set, init=False, repr=False)

    @property
    def name(self) -> str:
        return self.spec.name

    def _resolve(self) -> None:
        if self._session is not None:
            return
        ort = importlib.import_module("onnxruntime")
        tokenizers = importlib.import_module("tokenizers")
        available = ort.get_available_providers()
        providers = (
            self.providers
            or _env_list("PRAX_RERANK_PROVIDERS")
            or (
                ["DmlExecutionProvider", "CPUExecutionProvider"]
                if "DmlExecutionProvider" in available
                else ["CPUExecutionProvider"]
            )
        )
        gpu = providers[0] != "CPUExecutionProvider"
        variant = (
            self.variant
            or os.environ.get("PRAX_RERANK_VARIANT")
            or ("fp32" if gpu else "int8")
        )
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self._session = ort.InferenceSession(
            str(fetch.model_file(self.spec.repo, self.spec.files[variant])),
            opts,
            providers=providers,
        )
        self._inputs = {i.name for i in self._session.get_inputs()}
        tok = tokenizers.Tokenizer.from_file(
            str(fetch.model_file(self.spec.repo, "tokenizer.json"))
        )
        tok.enable_truncation(self.spec.max_tokens)
        tok.enable_padding()
        self._tokenizer = tok
        self.variant = variant
        self.providers = providers

    def score(self, query: str, texts: list[str]) -> np.ndarray:
        """One relevance logit per text (higher is better), in order."""
        if not texts:
            return np.zeros(0, dtype=np.float32)
        self._resolve()
        out = np.zeros(len(texts), dtype=np.float32)
        for start in range(0, len(texts), self.batch_size):
            part = texts[start : start + self.batch_size]
            enc = self._tokenizer.encode_batch([(query, t) for t in part])
            feed: dict[str, np.ndarray] = {
                "input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                "attention_mask": np.array(
                    [e.attention_mask for e in enc], dtype=np.int64
                ),
            }
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.array(
                    [e.type_ids for e in enc], dtype=np.int64
                )
            logits = self._session.run(None, feed)[0]
            out[start : start + len(part)] = np.asarray(
                logits, dtype=np.float32
            ).ravel()
        return out


_WORD = re.compile(r"\w+")


@dataclass
class StubReranker:
    """Query-token overlap with a length penalty; deterministic, for tests."""

    name: str = "stub"

    def score(self, query: str, texts: list[str]) -> np.ndarray:
        q = set(_WORD.findall(query.lower()))
        out = []
        for t in texts:
            words = _WORD.findall(t.lower())
            hits = sum(w in q for w in words)
            out.append(hits / (len(words) + 1) if words else 0.0)
        return np.asarray(out, dtype=np.float32)


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [p.strip() for p in raw.split(",") if p.strip()]


@functools.lru_cache(maxsize=2)
def _build(setting: str) -> Reranker | None:
    if setting in ("0", "off", "false", ""):
        return None
    if setting == "stub":
        return StubReranker()
    if setting not in MODELS:
        raise ValueError(f"unknown reranker {setting!r}; known: {sorted(MODELS)}")
    return OnnxReranker(MODELS[setting])


def current() -> Reranker | None:
    """The configured reranker, or None (the default: reranking is off)."""
    return _build(os.environ.get("PRAX_RERANK", "0"))
