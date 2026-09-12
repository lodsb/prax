"""Text embeddings: a small registry of ONNX models behind one interface.

The default is bge-small-en-v1.5 (384 dimensions, rationale R6/R8), run
through onnxruntime with the ``tokenizers`` library; model files are
fetched once into ``<data dir>/models`` (``prax.fetch``). Nothing here is imported
by the serving path until the first hybrid search, and ``PRAX_EMBED=0``
keeps it out entirely (search stays FTS-only).

Settings (``embeddings:`` in prax.yaml; the environment variable in
brackets overrides it for one run):
* ``model`` [``PRAX_EMBED``]: ``0`` disables embeddings; ``hash`` selects
  the deterministic test embedder; otherwise a model name from ``MODELS``
  (default ``bge-small-en-v1.5``).
* ``variant`` [``PRAX_EMBED_VARIANT``]: ``int8`` (default: the faster one
  on CPU and on DirectML alike, and what every vector in the store was
  made with) or ``fp32``.
* ``providers`` [``PRAX_EMBED_PROVIDERS``]: onnxruntime providers
  (default: DirectML if the runtime offers it, else CPU).
* ``threads`` [``PRAX_EMBED_THREADS``]: intra-op threads for the CPU
  provider.

Every vector is L2-normalized, so cosine similarity is a dot product. The
model name is what the store records per vector; changing the model means
re-embedding (the dimension is baked into ``chunks_vec``).
"""

from __future__ import annotations

import functools
import hashlib
import importlib
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from prax import config, fetch


@dataclass(frozen=True)
class ModelSpec:
    name: str
    repo: str
    dim: int
    files: dict[str, str]  # variant -> path in the repo
    tokenizer: str = "tokenizer.json"
    max_tokens: int = 512
    query_prefix: str = ""  # instruction prepended to queries (bge style)
    pooling: str = "cls"


MODELS: dict[str, ModelSpec] = {
    "bge-small-en-v1.5": ModelSpec(
        name="bge-small-en-v1.5",
        repo="Xenova/bge-small-en-v1.5",
        dim=384,
        files={"fp32": "onnx/model.onnx", "int8": "onnx/model_quantized.onnx"},
        query_prefix="Represent this sentence for searching relevant passages: ",
    ),
    # same dimension, multilingual; 3-4x the compute of bge-small
    "multilingual-e5-small": ModelSpec(
        name="multilingual-e5-small",
        repo="Xenova/multilingual-e5-small",
        dim=384,
        files={"fp32": "onnx/model.onnx", "int8": "onnx/model_quantized.onnx"},
        query_prefix="query: ",
        pooling="mean",
    ),
}
DEFAULT_MODEL = "bge-small-en-v1.5"


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


def _normalize(x: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    return (x / np.maximum(norms, 1e-12)).astype(np.float32)


# ------------------------------------------------------------------- onnx


@dataclass
class OnnxEmbedder:
    spec: ModelSpec
    variant: str | None = None
    providers: list[str] | None = None
    threads: int | None = None
    batch_size: int = 32
    _session: Any = field(default=None, init=False, repr=False)
    _tokenizer: Any = field(default=None, init=False, repr=False)
    _inputs: set[str] = field(default_factory=set, init=False, repr=False)

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def dim(self) -> int:
        return self.spec.dim

    def _resolve(self) -> None:
        if self._session is not None:
            return
        ort = importlib.import_module("onnxruntime")
        tokenizers = importlib.import_module("tokenizers")
        available = ort.get_available_providers()
        providers = (
            self.providers
            or config.words("embeddings.providers", "PRAX_EMBED_PROVIDERS")
            or (
                ["DmlExecutionProvider", "CPUExecutionProvider"]
                if "DmlExecutionProvider" in available
                else ["CPUExecutionProvider"]
            )
        )
        variant = (
            self.variant or config.setting("embeddings.variant", "PRAX_EMBED_VARIANT")
        ) or "int8"
        path = str(fetch.model_file(self.spec.repo, self.spec.files[variant]))
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        threads = self.threads or config.whole(
            "embeddings.threads", "PRAX_EMBED_THREADS", 0
        )
        if threads:
            opts.intra_op_num_threads = threads
        self._session = ort.InferenceSession(path, opts, providers=providers)
        self._inputs = {i.name for i in self._session.get_inputs()}
        tok = tokenizers.Tokenizer.from_file(
            str(fetch.model_file(self.spec.repo, self.spec.tokenizer))
        )
        tok.enable_truncation(self.spec.max_tokens)
        tok.enable_padding()
        self._tokenizer = tok
        self.variant = variant
        self.providers = providers

    def _run(self, encodings: list[Any]) -> np.ndarray:
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feed: dict[str, np.ndarray] = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self._session.run(None, feed)[0]  # (batch, tokens, dim)
        if self.spec.pooling == "mean":
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(axis=1) / np.maximum(m.sum(axis=1), 1e-9)
        else:
            pooled = hidden[:, 0]
        return _normalize(pooled)

    def embed(self, texts: list[str]) -> np.ndarray:
        """Vectors for ``texts`` in order; batches are length-sorted so
        padding is minimal."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        self._resolve()
        encs = self._tokenizer.encode_batch(texts)
        order = sorted(range(len(texts)), key=lambda i: len(encs[i].ids))
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for start in range(0, len(order), self.batch_size):
            idx = order[start : start + self.batch_size]
            # re-pad within the batch to its longest member
            batch = self._tokenizer.encode_batch([texts[i] for i in idx])
            out[idx] = self._run(batch)
        return out

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([self.spec.query_prefix + text])[0]


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [p.strip() for p in raw.split(",") if p.strip()]


# ------------------------------------------------------------------- test


_WORD = re.compile(r"\w+")


@dataclass
class HashEmbedder:
    """Deterministic bag-of-hashed-words vectors; shares nothing with a real
    model except the interface. Lets the vector layer be tested offline."""

    name: str = "hash-test"
    dim: int = 384

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for w in _WORD.findall(text.lower()):
                h = int.from_bytes(
                    hashlib.blake2b(w.encode(), digest_size=4).digest(), "big"
                )
                out[row, h % self.dim] += 1.0 if (h >> 31) else -1.0
        return _normalize(out)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


# ---------------------------------------------------------------- current


@functools.lru_cache(maxsize=2)
def _build(setting: str) -> Embedder | None:
    if setting in ("0", "off", "false", ""):
        return None
    if setting == "hash":
        return HashEmbedder()
    if setting not in MODELS:
        raise ValueError(
            f"unknown embedding model {setting!r}; known: {sorted(MODELS)}"
        )
    return OnnxEmbedder(MODELS[setting])


def current() -> Embedder | None:
    """The configured embedder, or None when embeddings are disabled.

    Built once per setting; the ONNX session itself loads on first use.
    """
    return _build(str(config.setting("embeddings.model", "PRAX_EMBED", DEFAULT_MODEL)))
