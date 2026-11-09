"""How prax builds an ONNX Runtime session, said once.

Every ONNX model prax runs (the embedder, the reranker, the genre
labeller, the face finder and recogniser) gets its session here, so a
setting that keeps a model small on the board reaches all of them. Until
2026-11-09 there were five copies, and the reranker, the one that runs
inside a search request, had no arena setting (structure review finding
51).

A model with a section in prax.yaml (``embeddings``, ``rerank``) reads
three settings from it; the variable in brackets overrides one for a run:
* ``providers`` [``PRAX_EMBED_PROVIDERS``, ``PRAX_RERANK_PROVIDERS``]: the
  onnxruntime providers (default: DirectML if the runtime offers it, else
  the CPU);
* ``threads`` [``PRAX_EMBED_THREADS``, ``PRAX_RERANK_THREADS``]: intra-op
  threads for the CPU provider (0: onnxruntime decides);
* ``arena`` [``PRAX_EMBED_ARENA``, ``PRAX_RERANK_ARENA``]: what onnxruntime
  does with the CPU memory a run needed, one of ``ARENAS``.
"""

from __future__ import annotations

import importlib
from typing import Any

from prax import config

# What ONNX Runtime does with the CPU memory a batch needed (``<section>.arena``):
# ``keep`` it for the next batch (its default, and prax's until 2026-09-30),
# ``shrink`` it back after every batch, or ``off``, no arena at all. Measured
# for the embedder on 2,000 chunks of the library, three runs each
# (docs/log.md, 2026-09-30): keep 24.0 chunks/s and 2.3 GB held by the process
# for good; shrink 22.0 chunks/s and 30 MB held; off 17.6 chunks/s and 50 MB.
# A query takes 3 ms in all three. The door and the worker each kept their
# 2.3 GB, idle, for days.
ARENAS = ("shrink", "keep", "off")
ARENA = "shrink"

# the variables of a section's settings
_ENV = {"embeddings": "PRAX_EMBED", "rerank": "PRAX_RERANK"}
CPU = ["CPUExecutionProvider"]


def arena_options(ort: Any, mode: str, section: str) -> tuple[bool, Any]:
    """(whether the session has a CPU arena, the run options a batch runs
    with) for an arena mode."""
    if mode not in ARENAS:
        raise ValueError(f"{section}.arena is one of {ARENAS}, not {mode!r}")
    run = ort.RunOptions()
    if mode == "shrink":
        run.add_run_config_entry("memory.enable_memory_arena_shrinkage", "cpu:0")
    return mode != "off", run


def providers_for(section: str, given: list[str] | None = None) -> list[str]:
    """The providers a model of ``section`` runs on: ``given``, else the
    section's setting, else DirectML where the runtime offers it and the
    CPU behind it."""
    if given:
        return list(given)
    named = config.words(f"{section}.providers", f"{_ENV[section]}_PROVIDERS")
    if named:
        return list(named)
    ort = importlib.import_module("onnxruntime")
    if "DmlExecutionProvider" in ort.get_available_providers():
        return ["DmlExecutionProvider", *CPU]
    return list(CPU)


def session(
    model: str | bytes,
    section: str | None = None,
    *,
    providers: list[str] | None = None,
    threads: int | None = None,
) -> tuple[Any, Any]:
    """(an inference session over ``model``, a path or the model's bytes;
    the run options every ``session.run`` passes). ``section`` names the
    prax.yaml section whose providers, threads and arena apply; without one
    the session runs on the CPU with onnxruntime's own arena (the worker's
    face models, a few small runs a picture). ``providers`` and
    ``threads`` override the section's."""
    ort = importlib.import_module("onnxruntime")
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    if section is None:
        return ort.InferenceSession(model, opts, providers=providers or CPU), None
    env = _ENV[section]
    count = threads or config.whole(f"{section}.threads", f"{env}_THREADS", 0)
    if count:
        opts.intra_op_num_threads = count
    mode = str(config.setting(f"{section}.arena", f"{env}_ARENA", ARENA))
    opts.enable_cpu_mem_arena, run = arena_options(ort, mode, section)
    chosen = providers_for(section, providers)
    return ort.InferenceSession(model, opts, providers=chosen), run
