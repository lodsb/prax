"""Model files, fetched once: the ONNX embedder and reranker, and the GGUF
a `models` entry of ``prax.yaml`` names with ``repo`` and ``file``. No
client library: one HTTPS GET per file from the Hugging Face hub (or any
URL), streamed to ``<data dir>/models/<repo>/<file>`` through a ``.part``
file that resumes, renamed when complete. A copy already in the Hugging
Face cache (``~/.cache/huggingface/hub``, from an earlier
``huggingface_hub`` install) is taken from there instead of downloaded.

``PRAX_OFFLINE=1`` (or ``HF_HUB_OFFLINE=1``) refuses to download and says
what is missing; ``HF_TOKEN`` is sent when set (gated repositories).
"""

from __future__ import annotations

import os
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from prax import config

HUB = "https://huggingface.co"
CHUNK = 1 << 20


class Offline(RuntimeError):
    """A file is missing and downloading is off."""


def models_dir() -> Path:
    return Path(
        config.setting("paths.models", "PRAX_MODELS_DIR")
        or config.data_dir() / "models"
    )


def hub_url(repo: str, file: str, revision: str = "main") -> str:
    return f"{HUB}/{repo}/resolve/{revision}/{file}"


def model_file(
    repo: str,
    file: str,
    *,
    revision: str = "main",
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """The local path of ``file`` from the hub repository ``repo``, fetched
    on first use. ``progress(done_bytes, total_bytes_or_None)`` is called
    as the download goes."""
    dest = models_dir() / repo / file
    if dest.exists():
        return dest
    cached = from_hf_cache(repo, file)
    if cached is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cached, dest)
        return dest
    download(hub_url(repo, file, revision), dest, progress=progress)
    return dest


def offline() -> bool:
    return os.environ.get("PRAX_OFFLINE", "") not in ("", "0") or os.environ.get(
        "HF_HUB_OFFLINE", ""
    ) not in ("", "0")


def download(
    url: str,
    dest: Path,
    *,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """Stream ``url`` into ``dest`` through ``dest.part``; a partial file
    from an interrupted run is resumed with a Range request when the
    server allows, started over otherwise."""
    if offline():
        raise Offline(f"{dest} is missing and downloading is off (PRAX_OFFLINE)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "prax/0.1"}
    token = os.environ.get("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 416:  # the part is already the whole file
            part.replace(dest)
            return dest
        raise
    with resp:
        resumed = have > 0 and resp.status == 206
        mode = "ab" if resumed else "wb"
        done = have if resumed else 0
        length = resp.headers.get("Content-Length")
        total = (int(length) + done) if length else None
        with open(part, mode) as out:
            while True:
                buf = resp.read(CHUNK)
                if not buf:
                    break
                out.write(buf)
                done += len(buf)
                if progress:
                    progress(done, total)
    part.replace(dest)
    return dest


def hf_cache_dir() -> Path:
    raw = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if raw:
        return Path(raw)
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def from_hf_cache(repo: str, file: str) -> Path | None:
    """A copy the Hugging Face client left in its cache, if any (the
    snapshots hold symlinks into blobs; resolved here)."""
    snapshots = hf_cache_dir() / f"models--{repo.replace('/', '--')}" / "snapshots"
    if not snapshots.is_dir():
        return None
    for snap in sorted(snapshots.iterdir(), reverse=True):
        candidate = snap / file
        if candidate.is_file():
            resolved = candidate.resolve()
            return resolved if resolved.is_file() else candidate
    return None


def human(n: int | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n //= 1024
    return f"{n} GB"
