#!/usr/bin/env python3
"""Benchmark a local GGUF model for graph extraction (docs/eval/local-llm-*.md).

    python scripts/bench_local_llm.py <model.gguf> [--docs 3] [--json-only]
    python scripts/bench_local_llm.py <model.gguf> --no-think --temp 0.6 --repeat 1.1

Reports prompt-processing and generation tok/s, VRAM, then runs the real
extraction prompt (ontology system prompt, JSON schema, ``build_input`` on
documents the selection returns) and prints validity, triple counts and time
per document. Needs the ``local`` extra and a CUDA 12 runtime on the PATH
(``docs/howto.md`` 3f). Raw model output goes to ``--out-dir`` when given.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import extraction, local_llm, ontology, store


def vram_mib() -> int:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return int(out.strip().splitlines()[0])
    except (OSError, subprocess.CalledProcessError, ValueError):
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("model")
    ap.add_argument("--docs", type=int, default=3)
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--max-tokens", type=int, default=3000)
    ap.add_argument("--no-think", action="store_true", help="append /no_think (Qwen3)")
    ap.add_argument("--flash", action="store_true", help="flash attention")
    ap.add_argument(
        "--no-grammar", action="store_true", help="JSON by instruction only"
    )
    ap.add_argument(
        "--json-only",
        action="store_true",
        help="generic JSON grammar, schema in the prompt",
    )
    ap.add_argument("--temp", type=float, default=0.0)
    ap.add_argument("--repeat", type=float, default=1.0, help="repeat penalty")
    ap.add_argument("--out-dir", help="write raw model output per document here")
    a = ap.parse_args()

    Llama = local_llm.llama_class()
    name = Path(a.model).stem
    base = vram_mib()
    t0 = time.perf_counter()
    llm = Llama(
        model_path=a.model,
        n_gpu_layers=-1,
        n_ctx=a.ctx,
        n_batch=512,
        flash_attn=a.flash,
        verbose=False,
    )
    load_s = time.perf_counter() - t0
    print(f"{name}: loaded in {load_s:.1f}s, VRAM +{vram_mib() - base} MiB")

    onto = ontology.current()
    sys_prompt = extraction.system_prompt(onto)
    schema = extraction.output_schema(onto)
    con = store.connect()
    ids = store.select_for_extraction(
        con, ontology_version=onto.version, limit=a.docs, mime_prefix="application/pdf"
    )
    docs = [extraction.build_input(con, i) for i in ids]
    if not docs:
        print("nothing selected for extraction", file=sys.stderr)
        return 1

    llm.create_completion("Hello", max_tokens=4)  # warm-up

    prompt = docs[0].as_message()
    n_prompt = len(llm.tokenize(prompt.encode("utf-8")))
    llm.reset()
    t0 = time.perf_counter()
    llm.create_completion(prompt, max_tokens=1)
    pp = time.perf_counter() - t0
    print(f"prompt processing: {n_prompt} tokens, {pp:.2f}s, {n_prompt / pp:.0f} tok/s")

    llm.reset()
    t0 = time.perf_counter()
    r = llm.create_completion(
        "Write a detailed essay about digital audio signal processing.",
        max_tokens=256,
        temperature=0.7,
    )
    tg = time.perf_counter() - t0
    n_gen = r["usage"]["completion_tokens"]
    print(f"generation: {n_gen} tokens in {tg:.2f}s = {n_gen / tg:.1f} tok/s")
    print(f"system prompt: {len(llm.tokenize(sys_prompt.encode('utf-8')))} tokens")

    response_format: dict[str, object] | None
    if a.no_grammar:
        response_format = None
    elif a.json_only:
        response_format = {"type": "json_object"}
    else:
        response_format = {"type": "json_object", "schema": schema}
    for d in docs:
        msg = d.as_message() + (" /no_think" if a.no_think else "")
        if a.no_grammar or a.json_only:
            msg += (
                "\n\nAnswer with a single JSON object matching this schema,"
                " nothing else:\n" + json.dumps(schema)
            )
        llm.reset()
        t0 = time.perf_counter()
        r = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": msg},
            ],
            response_format=response_format,
            max_tokens=a.max_tokens,
            temperature=a.temp,
            repeat_penalty=a.repeat,
        )
        dt = time.perf_counter() - t0
        text = r["choices"][0]["message"]["content"] or ""
        u = r["usage"]
        if a.out_dir:
            Path(a.out_dir).mkdir(parents=True, exist_ok=True)
            out = Path(a.out_dir, f"{name}_{d.doc_id}.json")
            out.write_text(text, encoding="utf-8")
        try:
            ex = extraction.parse_output(json.loads(text))
            status = f"{len(ex.triples)} triples, {len(ex.unmapped)} unmapped"
            rels: dict[str, int] = {}
            for t in ex.triples:
                rels[t.rel] = rels.get(t.rel, 0) + 1
            detail = ", ".join(f"{k} {v}" for k, v in sorted(rels.items()))
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            status = f"INVALID JSON ({e}); finish={r['choices'][0]['finish_reason']}"
            detail = text[:200].replace("\n", " ")
        print(
            f"doc {d.doc_id} ({d.title[:40]!r}): in {u['prompt_tokens']},"
            f" out {u['completion_tokens']} tok, {dt:.1f}s -> {status}"
        )
        print(f"   {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
