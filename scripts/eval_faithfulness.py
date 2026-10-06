#!/usr/bin/env python3
"""Faithfulness checkers on prax's own answers (docs/research-faithfulness.md).

    python scripts/eval_faithfulness.py collect OUT   # the answers and their passages
    python scripts/eval_faithfulness.py pairs OUT     # sentences, passages, variants
    python scripts/eval_faithfulness.py score OUT --checker hhem|lettuce|judge
    python scripts/eval_faithfulness.py report OUT

``collect`` asks the door (``PRAX_DOOR``, ``PRAX_TOKEN``) every question of
the eval sets with ``steps: 0`` and keeps each answer with its bundle.
``pairs`` splits an answer into sentences; a sentence that cites passages
is a hypothesis, the passages it cites its premise. Each gets variants:

- **planted errors**, written by rule and no model: a number changed, a
  negation added, a name swapped for another the premise names. A checker
  must score them low;
- **a paraphrase**, written by the host's local model, told to keep every
  fact: a checker must score it as the original. A model's paraphrase may
  itself change a fact, so a disagreement here is read, not counted blind.

``score`` runs one checker over every pair, each a probability that the
premise supports the hypothesis: HHEM-2.1-Open (its class vendored below,
weights pinned by revision, no remote code), LettuceDetect's base English
model (its detector module read from the downloaded wheel, ``--lettuce``),
or the local model asked yes or no (P(yes), as the judge of pairs is).
``report`` says, per checker and per passage language, how many planted
errors scored under 0.5, how many paraphrases did, and the originals'
mean. A person's labels (``labels.jsonl``: ``{"id", "label": 1|0}``) join
as a gold set when there are some.

Read only: nothing is written to the store.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parents[1]
SETS = (
    ROOT / "tests" / "eval" / "questions-sections.yaml",
    ROOT / "tests" / "eval" / "questions-equations.yaml",
)
FOLLOWUPS = ROOT / "tests" / "eval" / "followups-library.yaml"
HHEM = (
    "vectara/hallucination_evaluation_model",
    "8e4a2e6e96c708cc76c2344f7e4757df2515292c",
)
FLAN = ("google/flan-t5-base", "7bcac572ce56db69c1ea7c8af255c5d7c9672fc2")
LETTUCE = (
    "KRLabsOrg/lettucedect-base-modernbert-en-v1",
    "bbd77832f52f9bd87546a3924c032467921f5c34",
)
# HHEM-2.1-Open's prompt (configuration_hhem_v2.py, Apache-2.0, Vectara)
HHEM_PROMPT = (
    "<pad> Determine if the hypothesis is true given the premise?\n\n"
    "Premise: {text1}\n\nHypothesis: {text2}"
)
_CITE = re.compile(r"\s*\[(\d+)\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9*(\"'])")
_NUMBER = re.compile(r"(?<![\w.\[])(\d+(?:\.\d+)?)(?![\w\]])")
_NEGATABLE = re.compile(r"\b(is|are|was|were|can|does|do|has|have|will)\b")
_NAME = re.compile(r"\b[A-Z][a-zA-Z0-9-]{2,}\b")


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


# ----------------------------------------------------------------- collect


def collect(out: Path) -> None:
    import yaml

    from prax.client import DEFAULT_DOOR, Door

    door = Door(
        os.environ.get("PRAX_DOOR") or DEFAULT_DOOR,
        token=os.environ.get("PRAX_TOKEN") or None,
        name="eval-faithfulness",
    )
    questions = []
    for path in SETS:
        questions += [
            q["q"]
            for q in yaml.safe_load(path.read_text(encoding="utf-8"))["questions"]
        ]
    questions += [
        c["first"]
        for c in yaml.safe_load(FOLLOWUPS.read_text(encoding="utf-8"))["cases"]
    ]
    have = {r["question"] for r in _read(out / "answers.jsonl")}
    with (out / "answers.jsonl").open("a", encoding="utf-8") as f:
        for n, q in enumerate(questions, 1):
            if q in have:
                continue
            try:
                got = door.post_json("/ask", {"question": q, "steps": 0})
            except Exception as exc:  # noqa: BLE001 - one question's failure is kept
                print(f"{n}: {exc}", file=sys.stderr)
                continue
            row = {
                "question": q,
                "answer": got.get("answer") or "",
                "passages": [
                    {"n": p["n"], "doc_id": p["doc_id"], "text": p.get("text") or ""}
                    for p in got.get("passages") or []
                ],
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{n}/{len(questions)} asked", flush=True)


# ------------------------------------------------------------------- pairs


def _sentences(answer: str) -> list[str]:
    text = re.sub(r"[*_`#>]+", "", answer)
    out = []
    for block in re.split(r"\n\s*\n|\n[-•]\s*|\n\d+\.\s+", text):
        out += [
            s.strip() for s in _SENTENCE.split(" ".join(block.split())) if s.strip()
        ]
    return out


def _planted(sentence: str, premise: str, rng: random.Random) -> list[tuple[str, str]]:
    """The sentence with one fact changed, each way that applies."""
    out = []
    numbers = _NUMBER.findall(sentence)
    if numbers:
        old = rng.choice(numbers)
        new = (
            str(int(float(old)) * 2 + 3)
            if "." not in old
            else f"{float(old) * 2 + 0.5:g}"
        )
        out.append(
            (
                "number",
                re.sub(rf"(?<![\w.]){re.escape(old)}(?![\w])", new, sentence, count=1),
            )
        )
    m = _NEGATABLE.search(sentence)
    if m and not sentence[m.end() :].lstrip().lower().startswith(("not", "n't", "no ")):
        out.append(("negation", sentence[: m.end()] + " not" + sentence[m.end() :]))
    mine = _names(sentence)
    others = sorted(set(_names(premise)) - set(_NAME.findall(sentence)))
    if mine and others:
        old, new = rng.choice(mine), rng.choice(others)
        out.append(("name", re.sub(rf"\b{re.escape(old)}\b", new, sentence, count=1)))
    return out


# capitalised words that are no names: a sentence's opening, a caption's
_NOT_NAMES = frozenset(
    {
        "The",
        "This",
        "These",
        "That",
        "Those",
        "There",
        "Their",
        "They",
        "It",
        "Its",
        "In",
        "On",
        "At",
        "For",
        "From",
        "With",
        "And",
        "But",
        "Or",
        "If",
        "When",
        "While",
        "Where",
        "Which",
        "What",
        "How",
        "Fig",
        "Figure",
        "Table",
        "Section",
        "Equation",
        "Eq",
        "Passage",
        "Page",
        "Chapter",
        "Note",
        "See",
        "Also",
        "First",
        "Second",
        "Finally",
    }
)


def _names(text: str) -> list[str]:
    """Capitalised words inside a sentence, never its first, and no
    common word that a capital starts: what a swap may take a name from."""
    out = []
    for sentence in _SENTENCE.split(text):
        words = _NAME.findall(sentence)
        out += [w for w in words[1:] if w not in _NOT_NAMES]
    return out


def _paraphrase(sentence: str) -> str | None:
    from prax import models

    spec = models.resolve("ask")
    if spec is None:
        return None
    rt = models.runtime(spec)
    text, _usage = rt.chat(
        "Rewrite the sentence in other words. Keep every fact, number and name"
        " exactly; change only the wording and the order. Answer with the"
        " sentence alone.",
        sentence,
        max_tokens=200,
        temperature=0.3,
    )
    text = " ".join(str(text).split()).strip().strip('"')
    return text or None


def pairs(out: Path, paraphrase: bool) -> None:
    from prax.text import language

    rng = random.Random(7)
    # the paraphrases a run before wrote are kept: they cost the model
    before = _read(out / "pairs.jsonl")
    text_of = {r["id"]: r["hypothesis"] for r in before}
    said_before = {
        r.get("of_text") or text_of[r["of"]]: r["hypothesis"]
        for r in before
        if r["variant"] == "paraphrase"
    }
    rows: list[dict[str, Any]] = []
    for a in _read(out / "answers.jsonl"):
        by_n = {p["n"]: p for p in a["passages"]}
        for s in _sentences(a["answer"]):
            cited = sorted({int(m.group(1)) for m in _CITE.finditer(s)} & set(by_n))
            hyp = _CITE.sub("", s).strip()
            if not cited or len(hyp.split()) < 6:
                continue
            premise = "\n\n".join(by_n[n]["text"] for n in cited)
            lang = language.detect(premise) or "?"
            base = {"question": a["question"], "premise": premise, "lang": lang}
            rid = len(rows)
            rows.append(
                {**base, "id": rid, "of": rid, "variant": "original", "hypothesis": hyp}
            )
            for kind, text in _planted(hyp, premise, rng):
                if text != hyp:
                    rows.append(
                        {
                            **base,
                            "id": len(rows),
                            "of": rid,
                            "variant": kind,
                            "hypothesis": text,
                        }
                    )
            if paraphrase:
                said = said_before.get(hyp) or _paraphrase(hyp)
                if said and said != hyp:
                    rows.append(
                        {
                            **base,
                            "id": len(rows),
                            "of": rid,
                            "variant": "paraphrase",
                            "hypothesis": said,
                            "of_text": hyp,
                        }
                    )
    _write(out / "pairs.jsonl", rows)
    kinds: dict[str, int] = {}
    for r in rows:
        kinds[r["variant"]] = kinds.get(r["variant"], 0) + 1
    print(len(rows), "pairs:", kinds)


# ------------------------------------------------------------------ checkers


def _hhem() -> Any:
    """HHEM-2.1-Open without its remote code: flan-t5-base's token
    classifier (two labels: hallucinated, consistent), its weights from the
    pinned safetensors, the score the softmax of the first token's logits,
    as ``modeling_hhem_v2.py`` (Apache-2.0, Vectara) computes it."""
    import torch
    from huggingface_hub import hf_hub_download
    from safetensors.torch import load_file
    from transformers import AutoConfig, AutoTokenizer, T5ForTokenClassification

    cfg = AutoConfig.from_pretrained(FLAN[0], revision=FLAN[1], num_labels=2)
    tok = AutoTokenizer.from_pretrained(FLAN[0], revision=FLAN[1])
    model = T5ForTokenClassification(cfg)
    state = load_file(hf_hub_download(HHEM[0], "model.safetensors", revision=HHEM[1]))
    state = {k.removeprefix("t5."): v for k, v in state.items()}
    missing, unexpected = model.load_state_dict(state, strict=False)
    # the encoder's embedding is tied to the shared one, which is in the file
    tied = ("lm_head.weight", "embed_tokens.weight")
    if [k for k in missing if not k.endswith(tied)] or unexpected:
        raise SystemExit(
            f"weights do not fit: missing {missing[:5]}, unexpected {unexpected[:5]}"
        )
    model.tie_weights()
    model.eval()  # type: ignore[no-untyped-call]

    def score(premise: str, hypothesis: str) -> float:
        inputs = tok(
            HHEM_PROMPT.format(text1=premise, text2=hypothesis), return_tensors="pt"
        )
        with torch.no_grad():
            logits = model(**inputs).logits[:, 0, :]
        return float(torch.softmax(logits, dim=-1)[0, 1])

    return score


def _lettuce(source: Path) -> Any:
    """LettuceDetect's detector module, imported from the unpacked wheel
    without its package ``__init__`` (which pulls in an LLM client):
    the base English model, pinned. The hypothesis's support is one less
    the largest hallucination probability of its tokens."""
    import importlib.util
    import types

    pkg = types.ModuleType("lettucedetect")
    pkg.__path__ = [str(source / "lettucedetect")]
    sys.modules["lettucedetect"] = pkg
    spec = importlib.util.find_spec("lettucedetect.detectors.transformer")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    from huggingface_hub import snapshot_download

    path = snapshot_download(LETTUCE[0], revision=LETTUCE[1])
    det = mod.TransformerDetector(model_path=path, max_length=4096, device="cpu")

    def score(premise: str, hypothesis: str, question: str | None = None) -> float:
        toks = det.predict(
            context=[premise],
            answer=hypothesis,
            question=question,
            output_format="tokens",
        )
        return 1.0 - max((t["prob"] for t in toks), default=0.0)

    return score


def _judge() -> Any:
    """The host's local model asked yes or no, one token, P(yes)."""
    import urllib.request

    from prax import models
    from prax.graph import calibration

    spec = models.resolve("ask")
    if spec is None or not spec.base_url:
        raise SystemExit("steps.ask names no served model")
    url = spec.base_url.rstrip("/") + "/chat/completions"

    def score(premise: str, hypothesis: str) -> float:
        prompt = (
            "Is the statement fully supported by the passage? Every fact, number"
            " and name of the statement must be in the passage. Answer yes or no."
            f"\n\nPassage:\n{premise}\n\nStatement: {hypothesis}"
        )
        body = {
            "model": spec.model or spec.name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1,
            "temperature": 0,
            "logprobs": True,
            "top_logprobs": 10,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        got = json.loads(urllib.request.urlopen(req, timeout=300).read())
        top = got["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        p = calibration.yes_probability([(t["token"], t["logprob"]) for t in top])
        return float(p) if p is not None else 0.5

    return score


def score(out: Path, checker: str, lettuce: Path | None) -> None:
    rows = _read(out / "pairs.jsonl")
    target = out / f"scores-{checker}.jsonl"
    done = {r["id"] for r in _read(target)}
    if checker == "hhem":
        fn = _hhem()
    elif checker == "lettuce":
        if lettuce is None:
            raise SystemExit("--lettuce: the unpacked wheel's directory")
        fn = _lettuce(lettuce)
    else:
        fn = _judge()
    began = time.time()
    with target.open("a", encoding="utf-8") as f:
        for n, r in enumerate(rows, 1):
            if r["id"] in done:
                continue
            t = time.time()
            p = fn(r["premise"], r["hypothesis"])
            f.write(
                json.dumps(
                    {"id": r["id"], "p": round(p, 4), "s": round(time.time() - t, 3)}
                )
                + "\n"
            )
            if n % 50 == 0:
                print(
                    f"{n}/{len(rows)}  {(time.time() - began) / n:.2f} s a pair",
                    flush=True,
                )
    print(f"done in {time.time() - began:.0f} s")


# ------------------------------------------------------------------ report


def report(out: Path) -> None:
    rows = {r["id"]: r for r in _read(out / "pairs.jsonl")}
    labels = {r["id"]: r["label"] for r in _read(out / "labels.jsonl")}
    originals = sum(1 for r in rows.values() if r["variant"] == "original")
    print(f"{len(rows)} pairs; {originals} sentences")
    for path in sorted(out.glob("scores-*.jsonl")):
        name = path.stem.removeprefix("scores-")
        got = {s["id"]: s for s in _read(path)}
        each = sum(s["s"] for s in got.values()) / max(1, len(got))
        print(f"\n## {name}  ({each:.2f} s a pair)")
        print(
            "| passages | originals, mean | originals ≥ 0.5"
            " | planted errors < 0.5 | paraphrases ≥ 0.5 |"
        )
        print("|---|---|---|---|---|")
        for lang in ("en", "de", "all"):
            keep = [
                r
                for r in rows.values()
                if r["id"] in got and (lang == "all" or r["lang"] == lang)
            ]
            orig = [got[r["id"]]["p"] for r in keep if r["variant"] == "original"]
            plant = [
                got[r["id"]]["p"]
                for r in keep
                if r["variant"] in ("number", "negation", "name")
            ]
            para = [got[r["id"]]["p"] for r in keep if r["variant"] == "paraphrase"]

            def share(xs: list[float], ok: Any) -> str:
                return f"{sum(1 for x in xs if ok(x))}/{len(xs)}" if xs else "—"

            mean = f"{sum(orig) / len(orig):.2f}" if orig else "—"
            held = share(orig, lambda x: x >= 0.5)
            caught = share(plant, lambda x: x < 0.5)
            kept = share(para, lambda x: x >= 0.5)
            print(f"| {lang} | {mean} | {held} | {caught} | {kept} |")
        for kind in ("number", "negation", "name"):
            xs = [
                got[r["id"]]["p"]
                for r in rows.values()
                if r["id"] in got and r["variant"] == kind
            ]
            if xs:
                print(f"- {kind}: {sum(1 for x in xs if x < 0.5)}/{len(xs)} caught")
        # paired, whatever the threshold and whether the original was true:
        # a planted error below its own original, and a paraphrase of an
        # accepted original staying within 0.2 of it
        pl = [
            (got[r["id"]]["p"], got[r["of"]]["p"])
            for r in rows.values()
            if r["id"] in got
            and r["of"] in got
            and r["variant"] in ("number", "negation", "name")
        ]
        pa = [
            (got[r["id"]]["p"], got[r["of"]]["p"])
            for r in rows.values()
            if r["id"] in got
            and r["of"] in got
            and r["variant"] == "paraphrase"
            and got[r["of"]]["p"] >= 0.5
        ]
        if pl:
            lower = sum(1 for v, o in pl if v < o - 0.05)
            print(f"- planted below its original: {lower}/{len(pl)}")
            # an error is only a catch when the sentence it was planted in passed
            within = [v for v, o in pl if o >= 0.5]
            hit = sum(1 for v in within if v < 0.5)
            print(f"- planted in an accepted original, caught: {hit}/{len(within)}")
        if pa:
            near = sum(1 for v, o in pa if v >= o - 0.2)
            print(f"- paraphrase of an accepted original, within 0.2: {near}/{len(pa)}")
        gold = [(got[i]["p"], y) for i, y in labels.items() if i in got]
        if gold:
            tp = sum(1 for p, y in gold if p >= 0.5 and y)
            tn = sum(1 for p, y in gold if p < 0.5 and not y)
            pos = sum(1 for _, y in gold if y)
            neg = len(gold) - pos
            bal = ((tp / pos if pos else 0) + (tn / neg if neg else 0)) / 2
            print(
                f"- gold: balanced accuracy {bal:.3f} on {len(gold)} labelled sentences"
            )


def labels(out: Path) -> None:
    """A person's labels out of ``to-label.md`` (each item's ``label:``
    line: yes, partly or no; partly counts as no) into ``labels.jsonl``."""
    text = (out / "to-label.md").read_text(encoding="utf-8")
    got = []
    for block in re.split(r"(?m)^## ", text)[1:]:
        head, _, body = block.partition(chr(10))
        m = re.search(r"(?m)^label:\s*(yes|partly|no)(?![a-z])", body, re.IGNORECASE)
        if head.strip().isdigit() and m:
            got.append({"id": int(head), "label": int(m.group(1).lower() == "yes")})
    _write(out / "labels.jsonl", got)
    print(len(got), "labels")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=("collect", "pairs", "score", "report", "labels"))
    ap.add_argument("out", type=Path)
    ap.add_argument("--checker", choices=("hhem", "lettuce", "judge"), default="hhem")
    ap.add_argument("--lettuce", type=Path, help="the unpacked lettucedetect wheel")
    ap.add_argument("--no-paraphrase", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    a.out.mkdir(parents=True, exist_ok=True)
    if a.step == "collect":
        collect(a.out)
    elif a.step == "pairs":
        pairs(a.out, paraphrase=not a.no_paraphrase)
    elif a.step == "labels":
        labels(a.out)
    elif a.step == "score":
        score(a.out, a.checker, a.lettuce)
    else:
        report(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
