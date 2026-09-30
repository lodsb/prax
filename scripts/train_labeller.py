#!/usr/bin/env python
"""Train the small model that labels genres and subjects (`prax.ml.labeller`).

    python scripts/train_labeller.py [--extra teacher.jsonl] [--activate]

Needs the ``train`` extra (PyTorch, transformers, onnx; for a GPU,
PyTorch's CUDA build: `docs/howto.md`, "The small labeller"). A client of
the door (``PRAX_DOOR``, ``PRAX_TOKEN``): the labelled documents and their
views come from ``GET /genres/training``.

What it trains on: every labelled document except the person's blind
labels, which are only measured on. A person's other labels count
``--human-copies`` times, so the person's conventions outweigh a model's.
``--extra`` adds labels a teacher gave to documents nobody labelled, one
JSON object a line with ``view``, ``g`` and ``s``: lists of labels, or
dicts of the labels the teacher kept to their probability.

What it writes: ``models/labeller/<run>/`` in the data directory
(``encoder.onnx``, ``tokenizer.json``, ``head.npz``, ``meta.json``), after
checking that the exported model gives the probabilities PyTorch gave.
``--activate`` makes the run the one the genres step uses
(``models/labeller/CURRENT``). It prints the F1 on the blind labels at the
threshold, the same scoring as `scripts/eval_genres.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from prax.client import Door
from prax.graph import ontology
from prax.ml import labeller

BASE = "BAAI/bge-small-en-v1.5"


def prf(pairs: list[tuple[set[str], set[str]]]) -> tuple[float, float, float]:
    tp = fp = fn = 0
    for said, truth in pairs:
        tp += len(said & truth)
        fp += len(said - truth)
        fn += len(truth - said)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def blind_scores(
    blind: list[dict[str, Any]], probs: list[dict[str, float]], threshold: float
) -> dict[str, float]:
    """F1 on the blind labels: genres, levels, subjects, groups."""
    G, S = ontology.genres(), ontology.subjects()
    out = {}
    for name, key, facet, part in (
        ("genres", "g", G, 1),
        ("levels", "g", G, 0),
        ("subjects", "s", S, 1),
        ("groups", "s", S, 0),
    ):
        pairs = []
        for item, p in zip(blind, probs, strict=True):
            if key == "s" and not item["s"]:
                continue
            said = set(
                facet.implied(
                    x for x, v in p.items() if v >= threshold and x in facet.labels()
                )
            )

            def part_of(
                xs: set[str], f: ontology.Facet = facet, k: int = part
            ) -> set[str]:
                return {x for x in xs if (f.level_of(x) == x) == (k == 0)}

            pairs.append((part_of(said), part_of(set(item[key]))))
        out[name] = round(prf(pairs)[2], 3)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--extra", type=Path, action="append", default=[])
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--human-copies", type=int, default=3)
    ap.add_argument("--threshold", type=float, default=labeller.THRESHOLD)
    ap.add_argument("--activate", action="store_true")
    ap.add_argument("--device", default="auto", help="auto, cpu or cuda")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    import torch
    from transformers import AutoModel, AutoTokenizer

    door = Door(
        os.environ.get("PRAX_DOOR", "http://127.0.0.1:8000"),
        token=os.environ.get("PRAX_TOKEN"),
        timeout=600.0,
    )
    got = door.get_json("/genres/training")
    G, S = ontology.genres(), ontology.subjects()
    labels = list(got["genres"]) + list(got["subjects"])
    index = {x: k for k, x in enumerate(labels)}
    blind = [it for it in got["items"] if it["blind"]]
    train: list[tuple[str, list[str]]] = []
    counts = {"person": 0, "model": 0, "extra": 0}
    for it in got["items"]:
        if it["blind"]:
            continue
        copies = a.human_copies if it["by"] == "human" else 1
        counts["person" if it["by"] == "human" else "model"] += 1
        train += [(it["view"], it["g"] + it["s"])] * copies
    for path in a.extra:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            t = json.loads(line)

            def kept(v: Any) -> list[str]:
                return list(v)  # a dict's keys are the labels the teacher kept

            g, s = kept(t.get("g") or {}), kept(t.get("s") or {})
            if not g:
                continue
            train.append((t["view"], G.implied(g) + S.implied(s)))
            counts["extra"] += 1
    print(f"train {len(train)} rows ({counts}), blind {len(blind)}", flush=True)

    torch.manual_seed(a.seed)
    random.seed(a.seed)
    dev = (
        a.device
        if a.device != "auto"
        else ("cuda" if torch.cuda.is_available() else "cpu")
    )
    tok = AutoTokenizer.from_pretrained(BASE)
    enc = AutoModel.from_pretrained(BASE).to(dev)
    head = torch.nn.Linear(enc.config.hidden_size, len(labels)).to(dev)

    def pooled(texts: list[str]) -> Any:
        b = tok(
            texts,
            truncation=True,
            max_length=labeller.MAX_TOKENS,
            padding=True,
            return_tensors="pt",
        ).to(dev)
        h = enc(**b).last_hidden_state
        m = b["attention_mask"].unsqueeze(-1).float()
        return (h * m).sum(1) / m.sum(1).clamp(min=1e-9)

    y = torch.zeros(len(train), len(labels))
    for i, (_, xs) in enumerate(train):
        for x in xs:
            if x in index:
                y[i, index[x]] = 1.0
    pos = y.mean(0).clamp(min=1e-3)
    loss_fn = torch.nn.BCEWithLogitsLoss(
        pos_weight=((1 - pos) / pos).clamp(max=20.0).to(dev)
    )
    opt = torch.optim.AdamW(
        [
            {"params": enc.parameters(), "lr": 3e-5},
            {"params": head.parameters(), "lr": 1e-3},
        ],
        weight_decay=0.01,
    )
    t0 = time.monotonic()
    for ep in range(a.epochs):
        order = list(range(len(train)))
        random.shuffle(order)
        enc.train()
        total = 0.0
        for s in range(0, len(order), 16):
            idx = order[s : s + 16]
            loss = loss_fn(head(pooled([train[i][0] for i in idx])), y[idx].to(dev))
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += loss.item() * len(idx)
        print(
            f"epoch {ep + 1}: loss {total / len(train):.4f}"
            f" ({time.monotonic() - t0:.0f} s)",
            flush=True,
        )
    enc.eval()
    with torch.no_grad():
        torch_probs = [
            {
                x: float(v)
                for x, v in zip(
                    labels,
                    torch.sigmoid(head(pooled([it["view"]])))[0].tolist(),
                    strict=True,
                )
            }
            for it in blind
        ]
    print("blind, PyTorch:", blind_scores(blind, torch_probs, a.threshold), flush=True)

    run = time.strftime("labeller-%Y%m%dT%H%M%S")
    out = labeller.labeller_dir() / run
    out.mkdir(parents=True, exist_ok=True)

    class Encoder(torch.nn.Module):
        def __init__(self, inner: Any) -> None:
            super().__init__()
            self.inner = inner

        def forward(
            self, input_ids: Any, attention_mask: Any, token_type_ids: Any
        ) -> Any:
            return self.inner(
                input_ids=input_ids,
                attention_mask=attention_mask,
                token_type_ids=token_type_ids,
            ).last_hidden_state

    cpu = enc.to("cpu")
    sample = tok(["a sample"], return_tensors="pt")
    torch.onnx.export(
        Encoder(cpu),
        (sample["input_ids"], sample["attention_mask"], sample["token_type_ids"]),
        str(out / "encoder.onnx"),
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["last_hidden_state"],
        dynamic_axes={
            "input_ids": {0: "batch", 1: "tokens"},
            "attention_mask": {0: "batch", 1: "tokens"},
            "token_type_ids": {0: "batch", 1: "tokens"},
            "last_hidden_state": {0: "batch", 1: "tokens"},
        },
        opset_version=17,
        dynamo=False,
    )
    tok.backend_tokenizer.save(str(out / "tokenizer.json"))
    w = head.weight.detach().to("cpu").numpy().astype(np.float32)
    b = head.bias.detach().to("cpu").numpy().astype(np.float32)
    np.savez(out / "head.npz", w=w, b=b)
    (out / "meta.json").write_text(
        json.dumps(
            {
                "run": run,
                "base": BASE,
                "labels": labels,
                "versions": got["versions"],
                "threshold": a.threshold,
                "max_tokens": labeller.MAX_TOKENS,
                "trained_on": counts,
                "rows": len(train),
                "epochs": a.epochs,
                "seed": a.seed,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    onnx_probs = labeller.Labeller(out).predict([it["view"] for it in blind])
    worst = max(
        abs(p[x] - q[x])
        for p, q in zip(torch_probs, onnx_probs, strict=True)
        for x in labels
    )
    scores = blind_scores(blind, onnx_probs, a.threshold)
    print(f"blind, exported: {scores} (largest difference from PyTorch {worst:.4f})")
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    meta["blind"] = scores
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    if worst > 0.01:
        raise SystemExit(
            f"the export differs from PyTorch by {worst:.4f}: not activated"
        )
    if a.activate:
        (labeller.labeller_dir() / "CURRENT").write_text(run, encoding="utf-8")
        print(f"{run} is the labeller now")
    print(out)


if __name__ == "__main__":
    main()
