"""prax.yaml and the model registry: names, precedence, runtimes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prax import models

YAML = """
models:
  sonnet: {kind: claude, model: claude-sonnet-5, effort: low}
  tiny: {kind: openai, base_url: http://127.0.0.1:1/v1, model: tiny, n_ctx: 4096}
  server:
    kind: openai
    base_url: http://gpu-box:8080/v1
    model: qwen-32b
    price: [0.5, 1.5]
steps:
  extract: {model: sonnet, max_triples: 12}
  ask: {model: server}
  titles: {model: tiny}
"""


@pytest.fixture()
def cfg(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    p = data_dir / models.CONFIG_NAME
    p.write_text(YAML, encoding="utf-8")
    for var in (
        "PRAX_EXTRACT",
        "PRAX_EXTRACT_MODEL",
        "PRAX_ASK",
        "PRAX_ASK_MODEL",
        "PRAX_TITLES",
        "PRAX_VISION",
        "PRAX_VISION_MODEL",
        "PRAX_ADJUDICATE",
        "PRAX_CONFIG",
    ):
        monkeypatch.delenv(var, raising=False)
    models.reset()
    return p


def test_file_names_and_steps(cfg: Path) -> None:
    assert models.names() == ["sonnet", "tiny", "server"]
    s = models.resolve("extract")
    assert s is not None and s.kind == "claude" and s.model == "claude-sonnet-5"
    assert s.effort == "low" and models.settings("extract") == {"max_triples": 12}
    a = models.resolve("ask")
    assert (
        a is not None
        and a.kind == "openai"
        and a.runtime_name == "qwen-32b@gpu-box:8080"
    )
    t = models.resolve("titles")
    assert t is not None and t.kind == "openai" and t.runtime_name == "tiny@127.0.0.1:1"
    assert t.n_ctx == 4096
    assert models.resolve("vision").model == "claude-sonnet-5"  # the default
    assert models.resolve("adjudicate") is None
    assert models.price_of("qwen-32b@gpu-box:8080") == (0.5, 1.5)
    assert models.price_of("claude-sonnet-5") is None


def test_environment_overrides_the_file(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAX_EXTRACT", "stub")
    assert models.resolve("extract").kind == "stub"
    monkeypatch.setenv("PRAX_EXTRACT", "tiny")
    assert models.resolve("extract").kind == "openai"
    monkeypatch.setenv("PRAX_EXTRACT", "none")
    assert models.resolve("extract") is None
    monkeypatch.setenv("PRAX_EXTRACT", "nope")
    with pytest.raises(models.ConfigError, match="no model named 'nope'"):
        models.resolve("extract")
    monkeypatch.delenv("PRAX_EXTRACT")
    # the legacy model id swap applies to a Claude step
    monkeypatch.setenv("PRAX_EXTRACT_MODEL", "claude-opus-5")
    assert models.resolve("extract").model == "claude-opus-5"
    monkeypatch.setenv("PRAX_EXTRACT_EFFORT", "high")
    assert models.settings("extract")["effort"] == "high"


def test_implicit_names_without_a_file(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("PRAX_ASK", "PRAX_EXTRACT", "PRAX_EXTRACT_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert models.load() == {}
    assert models.resolve("extract").model == "claude-opus-5"
    assert models.resolve("ask") is None  # nobody answers unless the file says
    assert models.resolve("titles") is None
    assert models.names() == []
    monkeypatch.setenv("PRAX_ASK", "claude")  # legacy spelling
    assert models.resolve("ask").model == "claude-sonnet-5"
    monkeypatch.setenv("PRAX_ASK_MODEL", "claude-opus-5")
    assert models.resolve("ask").model == "claude-opus-5"
    assert models.spec("claude-haiku-4-5").kind == "claude"
    assert models.spec("nothing") is None


def test_bad_files_are_refused(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    p = data_dir / models.CONFIG_NAME
    p.write_text("steps: {frobnicate: {model: x}}", encoding="utf-8")
    with pytest.raises(models.ConfigError, match="unknown step"):
        models.load()
    p.write_text("models: {bad: {kind: openai, model: m}}", encoding="utf-8")
    with pytest.raises(models.ConfigError, match="needs 'base_url'"):
        models.spec("bad")
    p.write_text("models: {bad: {kind: laser}}", encoding="utf-8")
    with pytest.raises(models.ConfigError, match="kind must be"):
        models.spec("bad")
    monkeypatch.setenv("PRAX_CONFIG", str(data_dir / "elsewhere.yaml"))
    assert models.load() == {}


def test_openai_runtime_posts_and_parses(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_post(url: str, body: dict[str, Any], key: str | None) -> dict[str, Any]:
        seen.update(url=url, body=body, key=key)
        return {
            "choices": [{"message": {"content": "  a title\n"}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 3},
        }

    monkeypatch.setattr(models, "post_json", fake_post)
    monkeypatch.setenv("GPU_KEY", "secret")
    rt = models.OpenAIRuntime(
        "http://gpu-box:8080/v1/", "qwen-32b", api_key_env="GPU_KEY"
    )
    text, usage = rt.chat(
        "sys", "user", max_tokens=50, stop=["\n"], grammar="root ::= x"
    )
    assert text == "a title" and usage == {"input_tokens": 40, "output_tokens": 3}
    assert seen["url"] == "http://gpu-box:8080/v1/chat/completions"
    assert seen["key"] == "secret" and seen["body"]["stop"] == ["\n"]
    assert seen["body"]["grammar"] == "root ::= x" and seen["body"]["max_tokens"] == 50
    assert rt.name == "qwen-32b@gpu-box:8080"


def test_runtimes_are_shared(cfg: Path) -> None:
    s = models.resolve("ask")
    assert models.runtime(s) is models.runtime(s)
    assert isinstance(models.runtime(s), models.OpenAIRuntime)
    assert isinstance(models.runtime(models.spec("stub")), models.StubRuntime)
    assert isinstance(models.runtime(models.spec("sonnet")), models.ClaudeRuntime)


def test_claude_runtime_refuses_grammars() -> None:
    rt = models.ClaudeRuntime("claude-sonnet-5")
    with pytest.raises(ValueError, match="grammar"):
        rt.chat("s", "u", grammar="root ::= x")


def test_describe(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = models.describe("ask")
    assert d["model"] == "server" and d["kind"] == "openai" and "server" in d["models"]
    assert d["config"] == str(cfg) and d["error"] is None
    monkeypatch.setenv("PRAX_ASK", "ghost")
    assert "ghost" in models.describe("ask")["error"]


def test_an_image_rides_along_as_a_data_url(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_post(url: str, body: dict[str, Any], key: str | None) -> dict[str, Any]:
        seen.update(body=body)
        return {"choices": [{"message": {"content": "a photo"}}], "usage": {}}

    monkeypatch.setattr(models, "post_json", fake_post)
    rt = models.OpenAIRuntime("http://gpu-box:8080/v1", "qwen-vl")
    text, _ = rt.chat("", "describe", images=[(b"\x89PNG....", "image/png")])
    assert text == "a photo"
    messages = seen["body"]["messages"]
    assert [m["role"] for m in messages] == ["user"]  # no empty system turn
    image, prompt = messages[0]["content"]
    assert image["type"] == "image_url"
    assert image["image_url"]["url"] == "data:image/png;base64,iVBORy4uLi4="
    assert prompt == {"type": "text", "text": "describe"}


METRICS = """\
# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
# TYPE llamacpp:prompt_tokens_total counter
llamacpp:prompt_tokens_total 12345
llamacpp:prompt_tokens_cached_total 9000
llamacpp:tokens_predicted_total 678
llamacpp:prompt_tokens_seconds 1500.5
llamacpp:predicted_tokens_seconds 92.25
llamacpp:requests_processing 2
llamacpp:requests_deferred 0
llamacpp:n_busy_slots_per_decode 2
"""


def test_server_status_reads_props_and_metrics(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = {
        "http://gpu-box:8080/props": (
            '{"model_alias": "local-server", "total_slots": 3,'
            ' "endpoint_metrics": true, "modalities": {"vision": true},'
            ' "model_path": "C:\\\\models\\\\q.gguf"}'
        ),
        "http://gpu-box:8080/metrics": METRICS,
    }

    def fake_get(url: str, timeout: float = 1.0) -> str:
        if url not in pages:
            raise OSError("connection refused")
        return pages[url]

    monkeypatch.setattr(models, "get_text", fake_get)
    servers = models.servers()
    assert [s.name for s in servers] == ["tiny", "server"]  # one per base URL
    up = models.server_status(models.spec("server"))
    assert up["reachable"] and up["slots"] == 3 and up["vision"] is True
    assert up["file"] == "q.gguf" and up["alias"] == "local-server"
    assert up["metrics"]["prompt_tps"] == 1500.5
    assert up["metrics"]["busy_slots"] == 2
    assert up["metrics"]["prompt_tokens_cached"] == 9000
    assert up["metrics"]["processing"] == 2 and up["metrics"]["deferred"] == 0
    assert up["metrics"]["predicted_tokens_total"] == 678
    down = models.server_status(models.spec("tiny"))
    assert down == {
        "name": "tiny",
        "model": "tiny",
        "url": "http://127.0.0.1:1",
        "reachable": False,
        "error": "connection refused",
    }
    # started without --metrics: reachable, no load figures
    pages["http://gpu-box:8080/props"] = '{"total_slots": 1, "endpoint_metrics": false}'
    quiet = models.server_status(models.spec("server"))
    assert quiet["reachable"] and quiet["metrics"] is None and quiet["vision"] is False
