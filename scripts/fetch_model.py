"""Fetch the model files this store's configuration names, once.

    python scripts/fetch_model.py server-35b     # a models entry with repo and file
    python scripts/fetch_model.py --all          # every entry that names one
    python scripts/fetch_model.py --embed        # the embedder (and reranker if set)
    python scripts/fetch_model.py --url https://... --to <data dir>/models/x.gguf

Files land in <data dir>/models/<repo>/<file> (PRAX_MODELS_DIR elsewhere)
and an interrupted download resumes. The printed path is what
`serve.path` under the model takes (or leave it out: `prax up` looks here).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import embeddings, fetch, models, rerank


def _progress() -> object:
    last = [0.0]

    def show(done: int, total: int | None) -> None:
        now = time.monotonic()
        if now - last[0] < 0.5 and (total is None or done < total):
            return
        last[0] = now
        pct = f" {100 * done // total}%" if total else ""
        print(
            f"\r  {fetch.human(done)} of {fetch.human(total)}{pct}", end="", flush=True
        )

    return show


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="*", help="models entries of prax.yaml")
    ap.add_argument("--all", action="store_true", help="every entry with repo and file")
    ap.add_argument("--embed", action="store_true", help="the embedder and reranker")
    ap.add_argument("--url", help="any file by URL")
    ap.add_argument("--to", help="where --url goes")
    a = ap.parse_args()
    show = _progress()
    todo: list[tuple[str, str, str]] = []  # (label, repo, file)
    if a.url:
        if not a.to:
            ap.error("--url needs --to")
        print(a.url)
        p = fetch.download(a.url, Path(a.to), progress=show)  # type: ignore[arg-type]
        print(f"\n{p}")
        return 0
    names = list(a.names)
    if a.all:
        names += [n for n in models.names() if n not in names]
    for name in names:
        s = models.spec(name)
        if s is None:
            print(f"{name}: no such model in prax.yaml", file=sys.stderr)
            return 2
        if not (s.repo and s.file):
            if a.all:
                continue
            print(f"{name}: names no repo and file to fetch", file=sys.stderr)
            return 2
        todo.append((name, s.repo, s.file))
    if a.embed:
        emb = embeddings.MODELS[embeddings.chosen()]
        for f in (*emb.files.values(), emb.tokenizer):
            todo.append((emb.name, emb.repo, f))
        rr = rerank.current_spec()
        if rr is not None:
            for f in (*rr.files.values(), "tokenizer.json"):
                todo.append((rr.name, rr.repo, f))
    if not todo:
        ap.error("nothing to fetch: name a model, --all or --embed")
    for label, repo, file in todo:
        print(f"{label}: {repo}/{file}")
        p = fetch.model_file(repo, file, progress=show)  # type: ignore[arg-type]
        print(f"\n  {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
