"""A small model that says what a document is and what it is about.

Stage Z of `docs/PLAN.md`. bge-small (the model prax embeds with, 33M
parameters) fine-tuned with one output a label, the genres of
`ontology/genres.yaml` and the subjects of `ontology/subjects.yaml`, on
the labels a person gave and on those a larger model (the teacher: the
local model, `writing.genres.label`) gave to documents nobody had
labelled. Measured on the person's blind labels
(docs/eval/genres-2026-09-29.md): subjects F1 0.66, above every other
reader; genres 0.575, within noise of the teacher's 0.60.

It is trained by `scripts/train_labeller.py` (the ``train`` extra:
PyTorch, transformers, onnx) and runs here without any of them: the
encoder exported to ONNX, the output layer as numpy, the tokenizer as
the embedder's (`tokenizers`). So it runs on the CPU, where the teacher
needs llama-server and seconds a document, in milliseconds.

A trained model is a directory under ``models/labeller/<run>/`` in the
data directory: ``encoder.onnx``, ``tokenizer.json``, ``head.npz`` (the
output layer's ``w`` and ``b``) and ``meta.json`` (the labels in output
order, the vocabularies' versions, the threshold, what it was trained
on). ``models/labeller/CURRENT`` names the run in use; the genres step
uses it when ``steps.genres.method`` is ``small``.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from prax import config
from prax.ml import embeddings, fetch

BATCH = 16
MAX_TOKENS = 512
THRESHOLD = 0.6  # measured on the blind labels, four seeds (the eval doc)


def labeller_dir() -> Path:
    return fetch.models_dir() / "labeller"


def current_run() -> Path | None:
    """The run ``models/labeller/CURRENT`` names, when it exists."""
    pointer = labeller_dir() / "CURRENT"
    if not pointer.exists():
        return None
    run = labeller_dir() / pointer.read_text(encoding="utf-8").strip()
    return run if (run / "meta.json").exists() else None


@dataclass
class Labeller:
    """One trained run, loaded on first use."""

    path: Path
    meta: dict[str, Any] = field(default_factory=dict, init=False)
    _session: Any = field(default=None, init=False, repr=False)
    _run_options: Any = field(default=None, init=False, repr=False)
    _tokenizer: Any = field(default=None, init=False, repr=False)
    _w: Any = field(default=None, init=False, repr=False)
    _b: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.meta = json.loads((self.path / "meta.json").read_text(encoding="utf-8"))

    @property
    def name(self) -> str:
        return f"labeller:{self.path.name}"

    @property
    def labels(self) -> list[str]:
        return list(self.meta["labels"])

    @property
    def threshold(self) -> float:
        return float(self.meta.get("threshold", THRESHOLD))

    def _resolve(self) -> None:
        if self._session is not None:
            return
        ort = importlib.import_module("onnxruntime")
        tokenizers = importlib.import_module("tokenizers")
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        mode = str(
            config.setting("embeddings.arena", "PRAX_EMBED_ARENA", embeddings.ARENA)
        )
        opts.enable_cpu_mem_arena, self._run_options = embeddings.arena_options(
            ort, mode
        )
        self._session = ort.InferenceSession(
            str(self.path / "encoder.onnx"), opts, providers=["CPUExecutionProvider"]
        )
        tok = tokenizers.Tokenizer.from_file(str(self.path / "tokenizer.json"))
        tok.enable_truncation(int(self.meta.get("max_tokens", MAX_TOKENS)))
        tok.enable_padding()
        self._tokenizer = tok
        head = np.load(self.path / "head.npz")
        self._w, self._b = head["w"], head["b"]

    def predict(self, texts: list[str]) -> list[dict[str, float]]:
        """For each text, every label's probability."""
        self._resolve()
        out: list[dict[str, float]] = []
        names = [i.name for i in self._session.get_inputs()]
        for start in range(0, len(texts), BATCH):
            encs = self._tokenizer.encode_batch(texts[start : start + BATCH])
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in names:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self._session.run(None, feed, self._run_options)[0]
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(axis=1) / np.maximum(m.sum(axis=1), 1e-9)
            logits = pooled @ self._w.T + self._b
            p = 1.0 / (1.0 + np.exp(-logits))
            out += [
                {x: round(float(v), 4) for x, v in zip(self.labels, row, strict=True)}
                for row in p
            ]
        return out


def current() -> Labeller | None:
    """The run in use, or None when none has been trained and made current."""
    run = current_run()
    return Labeller(run) if run is not None else None
