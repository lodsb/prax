"""Model files fetched once, resumed when interrupted, taken from the
Hugging Face cache when present, refused offline."""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from prax import fetch

PAYLOAD = bytes(range(256)) * 40  # 10 KB, distinct at every offset


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].rstrip("-"))
            body = PAYLOAD[start:]
            self.send_response(206)
            self.send_header(
                "Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}"
            )
        else:
            body = PAYLOAD
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def server() -> Iterator[str]:
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}/repo/resolve/main/model.bin"
    finally:
        srv.shutdown()


def test_download_streams_and_resumes(server: str, tmp_path: Path) -> None:
    dest = tmp_path / "models" / "repo" / "model.bin"
    seen: list[tuple[int, int | None]] = []
    fetch.download(server, dest, progress=lambda d, t: seen.append((d, t)))
    assert dest.read_bytes() == PAYLOAD
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    # an interrupted run left a part: the rest is fetched with a Range
    dest.unlink()
    part = dest.with_name("model.bin.part")
    part.write_bytes(PAYLOAD[:3000])
    fetch.download(server, dest)
    assert dest.read_bytes() == PAYLOAD and not part.exists()


def test_model_file_uses_the_data_dir_and_the_hf_cache(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAX_OFFLINE", "1")
    monkeypatch.delenv("PRAX_MODELS_DIR", raising=False)
    hub = tmp_path / "hub"
    snap = hub / "models--Xenova--tiny" / "snapshots" / "abc" / "onnx"
    snap.mkdir(parents=True)
    (snap / "model.onnx").write_bytes(b"onnx!")
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    p = fetch.model_file("Xenova/tiny", "onnx/model.onnx")
    assert p == data_dir / "models" / "Xenova" / "tiny" / "onnx" / "model.onnx"
    assert p.read_bytes() == b"onnx!"
    # once there, neither the cache nor the network is consulted
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "nowhere"))
    assert fetch.model_file("Xenova/tiny", "onnx/model.onnx") == p
    with pytest.raises(fetch.Offline):
        fetch.model_file("Xenova/tiny", "onnx/other.onnx")


def test_hub_url() -> None:
    assert (
        fetch.hub_url("Xenova/bge-small-en-v1.5", "onnx/model.onnx")
        == "https://huggingface.co/Xenova/bge-small-en-v1.5/resolve/main/onnx/model.onnx"
    )
