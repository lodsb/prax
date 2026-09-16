"""Which model does which step: ``prax.yaml``, the registry, the runtimes.

Every AI-assisted step (``extract``, ``ask``, ``titles``, ``vision``,
``adjudicate``) picks its model here instead of reading its own
environment variables. ``prax.yaml`` in the data directory (or
``PRAX_CONFIG``) names models and assigns them to steps::

    models:
      sonnet:    {kind: claude, model: claude-sonnet-5, effort: medium}
      server-35b: {kind: openai, base_url: http://127.0.0.1:8080/v1, model: qwen3.6-35b}
    steps:
      extract:   {model: server-35b, max_triples: 20}
      ask:       {model: server-35b}
      titles:    {model: server-35b}
      vision:    {model: sonnet}
      adjudicate: {model: none}

Three kinds: ``claude`` (the API), ``openai`` (any OpenAI-compatible
server: llama-server or vLLM on this or another machine, or a hosted
API; ``api_key_env`` names the variable holding its key), ``stub``
(tests). A local model always lives in its own server process
(``prax up`` starts it from the model's ``serve:`` block), never in the
process that runs the step:
the door stays lean and the worker stays small. Some names need no file:
any ``claude-*`` id, ``stub``, ``none``. Precedence for a step:
``PRAX_<STEP>`` in the environment (a model name or ``none``), then the
file, then the step's default. ``PRAX_<STEP>_MODEL``
swaps the Claude model id when the step resolves to Claude, as it always
did. A runtime is loaded once per process however many steps share it.

Steps build their own objects from the spec: extraction wants a JSON
schema from Claude and a grammar from llama-server, vision sends an image,
so the registry hands out a ``ModelSpec`` and, for chat-shaped work, a
``Runtime`` (``chat(system, user, ...)``) of the right kind.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from prax import config

CONFIG_NAME = "prax.yaml"
KINDS = ("claude", "openai", "stub")
STEPS = ("extract", "promote", "ask", "titles", "vision", "adjudicate", "typing")
STEP_DEFAULTS = {
    "extract": "claude-opus-5",
    "promote": "claude-sonnet-5",  # the expensive pass over flagged documents
    "ask": "none",  # the caller's own model answers unless the file says otherwise
    "titles": "none",
    "vision": "claude-sonnet-5",
    "adjudicate": "none",
    "typing": "none",  # the model typing pass over the review queue
}
DEFAULT_CTX = 8192  # what a step may assume of a server's context per slot
OPENAI_TIMEOUT = 600.0
_CLAUDE_TIMEOUT = 180.0


@dataclass(frozen=True)
class ModelSpec:
    name: str
    kind: str
    model: str | None = None  # the API's model id (claude, openai)
    base_url: str | None = None  # the server (openai)
    api_key_env: str | None = None
    repo: str | None = None  # where the file comes from (scripts/fetch_model.py)
    file: str | None = None
    n_ctx: int = DEFAULT_CTX  # the server's context per slot (openai)
    effort: str | None = None
    price: tuple[float, float] | None = None  # USD per million in, out
    params: tuple[tuple[str, Any], ...] = field(default_factory=tuple)
    # how `prax up` starts the server for this model (openai kind, on this
    # machine): the slots, the projector, what stays in RAM (prax.up)
    serve: tuple[tuple[str, Any], ...] = field(default_factory=tuple)

    @property
    def runtime_name(self) -> str:
        if self.kind == "openai":
            host = urlparse(self.base_url or "").netloc or "server"
            return f"{self.model}@{host}"
        return self.model or self.name


ConfigError = config.ConfigError  # the file says something we cannot follow


# ------------------------------------------------------------------ file


def config_path() -> Path:
    return config.config_path()


def load() -> dict[str, Any]:
    """The parsed file, ``{}`` when there is none; validated shape."""
    path = config_path()
    data = config.document()
    for section in ("models", "steps"):
        if section in data and not isinstance(data[section], dict):
            raise ConfigError(f"{path}: '{section}' must be a mapping")
    for step in data.get("steps", {}):
        if step not in STEPS:
            raise ConfigError(f"{path}: unknown step {step!r}; steps are {STEPS}")
    return data


def _spec_from(name: str, raw: dict[str, Any]) -> ModelSpec:
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ConfigError(f"model {name!r}: kind must be one of {KINDS}")
    known = {
        "kind",
        "model",
        "base_url",
        "repo",
        "file",
        "api_key_env",
        "n_ctx",
        "effort",
        "price",
        "serve",
    }
    if kind == "openai" and not (raw.get("base_url") and raw.get("model")):
        raise ConfigError(
            f"model {name!r}: an openai model needs 'base_url' and 'model'"
        )
    if kind == "claude" and not raw.get("model"):
        raise ConfigError(f"model {name!r}: a claude model needs 'model'")
    price = raw.get("price")
    serve = raw.get("serve") or {}
    if not isinstance(serve, dict):
        raise ConfigError(f"model {name!r}: 'serve' must be a mapping")
    if serve and kind != "openai":
        raise ConfigError(f"model {name!r}: only an openai model has a server to serve")
    return ModelSpec(
        name=name,
        kind=kind,
        model=raw.get("model"),
        base_url=raw.get("base_url"),
        api_key_env=raw.get("api_key_env"),
        repo=raw.get("repo"),
        file=raw.get("file"),
        n_ctx=int(raw.get("n_ctx", DEFAULT_CTX)),
        effort=raw.get("effort"),
        price=(float(price[0]), float(price[1])) if price else None,
        params=tuple(sorted((k, v) for k, v in raw.items() if k not in known)),
        serve=tuple(sorted(serve.items())),
    )


def spec(name: str) -> ModelSpec | None:
    """The model behind a name: from the file, or one of the implicit
    names (``claude-*``, ``stub``); ``none`` and unknown names are None."""
    if name in ("none", ""):
        return None
    models = load().get("models", {})
    if name in models:
        return _spec_from(name, models[name] or {})
    if name == "stub":
        return ModelSpec(name="stub", kind="stub")
    if name.startswith("claude-"):
        return ModelSpec(name=name, kind="claude", model=name)
    return None


def names() -> list[str]:
    """Model names a caller may pick: the file's."""
    return list(load().get("models", {}))


# ----------------------------------------------------------------- steps


def _env_step(step: str) -> str | None:
    return os.environ.get(f"PRAX_{step.upper()}")


def resolve(step: str) -> ModelSpec | None:
    """The model for a step, or None when the step is off (``none``)."""
    if step not in STEPS:
        raise ValueError(f"step must be one of {STEPS}")
    chosen = _env_step(step)
    source = "environment"
    if chosen is None:
        chosen = (load().get("steps", {}).get(step) or {}).get("model")
        source = "prax.yaml"
    if chosen is None:
        model_id = os.environ.get(f"PRAX_{step.upper()}_MODEL")
        if model_id:  # the legacy way of naming a Claude model for a step
            chosen = model_id
        else:
            chosen = STEP_DEFAULTS[step]
        source = "default"
    if chosen == "claude":  # legacy PRAX_ASK=claude
        chosen = os.environ.get(f"PRAX_{step.upper()}_MODEL") or _claude_default(step)
    result = spec(chosen)
    if result is None and chosen not in ("none", ""):
        raise ConfigError(
            f"step {step!r}: no model named {chosen!r} ({source}); known: {names()}"
        )
    if result is not None and result.kind == "claude":
        model_id = os.environ.get(f"PRAX_{step.upper()}_MODEL")
        if model_id and model_id != result.model:
            result = ModelSpec(**{**result.__dict__, "model": model_id})
    return result


def _claude_default(step: str) -> str:
    return (
        STEP_DEFAULTS[step]
        if STEP_DEFAULTS[step].startswith("claude-")
        else "claude-sonnet-5"
    )


def settings(step: str) -> dict[str, Any]:
    """The step's own settings from the file (everything but ``model``),
    with the legacy environment overrides applied."""
    out = {
        k: v
        for k, v in (load().get("steps", {}).get(step) or {}).items()
        if k != "model"
    }
    if step == "extract" and os.environ.get("PRAX_EXTRACT_EFFORT"):
        out["effort"] = os.environ["PRAX_EXTRACT_EFFORT"]
    return out


def describe(step: str) -> dict[str, Any]:
    """For a UI: what the step resolves to and what it could use."""
    try:
        chosen = resolve(step)
        error = None
    except ConfigError as exc:
        chosen, error = None, str(exc)
    return {
        "step": step,
        "model": chosen.name if chosen else "none",
        "kind": chosen.kind if chosen else None,
        "runtime": chosen.runtime_name if chosen else None,
        "models": names(),
        "config": str(config_path()) if config_path().is_file() else None,
        "error": error,
    }


def price_of(runtime_name: str) -> tuple[float, float] | None:
    """USD per million tokens for a runtime name the file prices; None when
    the file says nothing (the caller falls back to its own table)."""
    for name, raw in load().get("models", {}).items():
        s = _spec_from(name, raw or {})
        if s.runtime_name == runtime_name and s.price:
            return s.price
    return None


# -------------------------------------------------------------- runtimes


class StubRuntime:
    """Tests: echoes a fixed line."""

    name = "stub"

    def __init__(self, reply: str = "stub\n") -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def chat(self, system: str, user: str, **kw: Any) -> tuple[str, dict[str, int]]:
        self.calls.append({"system": system, "user": user, **kw})
        return self.reply, {"input_tokens": len(user) // 4, "output_tokens": 4}


@dataclass
class OpenAIRuntime:
    """A chat completion against an OpenAI-compatible server. llama-server
    honours ``grammar`` and ``repeat_penalty`` as extra fields; vLLM ignores
    them (its structured outputs are a different field), so use the JSON
    path there. The key, when the server wants one, comes from the variable
    named in the spec at call time and is never stored."""

    base_url: str
    model: str
    api_key_env: str | None = None
    timeout: float = OPENAI_TIMEOUT

    @property
    def name(self) -> str:
        host = urlparse(self.base_url).netloc or "server"
        return f"{self.model}@{host}"

    def chat(
        self,
        system: str,
        user: str,
        *,
        grammar: str | None = None,
        max_tokens: int = 2000,
        temperature: float = 0.0,
        repeat_penalty: float = 1.0,
        stop: list[str] | None = None,
        images: list[tuple[bytes, str]] | None = None,
    ) -> tuple[str, dict[str, int]]:
        """``images`` are ``(bytes, media type)`` pairs sent before the
        user text as data URLs; the server needs a multimodal projector
        (llama-server ``--mmproj``) or answers about text alone."""
        content: Any = user
        if images:
            content = [
                {"type": "image_url", "image_url": {"url": _data_url(data, mt)}}
                for data, mt in images
            ] + [{"type": "text", "text": user}]
        messages = [{"role": "user", "content": content}]
        if system:
            messages.insert(0, {"role": "system", "content": system})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stop:
            body["stop"] = stop
        if repeat_penalty != 1.0:
            body["repeat_penalty"] = repeat_penalty
        if grammar:
            body["grammar"] = grammar
        data = post_json(
            self.base_url.rstrip("/") + "/chat/completions", body, self._key()
        )
        text = (data["choices"][0]["message"].get("content") or "").strip()
        u = data.get("usage") or {}
        return text, {
            "input_tokens": int(u.get("prompt_tokens", 0) or 0),
            "output_tokens": int(u.get("completion_tokens", 0) or 0),
        }

    def _key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None


def _data_url(data: bytes, media_type: str) -> str:
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def post_json(url: str, body: dict[str, Any], key: str | None) -> dict[str, Any]:
    """One POST; replaced in tests."""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=OPENAI_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"{url}: HTTP {exc.code}: {detail}") from exc


# ------------------------------------------------------- server status

STATUS_TIMEOUT = 1.5  # a status call must not hold the door's request thread


def servers() -> list[ModelSpec]:
    """The ``openai`` models of the file, one per server (the same base
    URL under two names is one server)."""
    out: list[ModelSpec] = []
    seen: set[str] = set()
    for name in names():
        s = spec(name)
        if s is None or s.kind != "openai" or not s.base_url:
            continue
        root = _server_root(s.base_url)
        if root in seen:
            continue
        seen.add(root)
        out.append(s)
    return out


def _server_root(base_url: str) -> str:
    """``http://host:port`` from an OpenAI base URL (``…/v1`` stripped)."""
    root = base_url.rstrip("/")
    return root.removesuffix("/v1")


def get_text(url: str, timeout: float = STATUS_TIMEOUT) -> str:
    """One GET; replaced in tests."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_metrics(text: str) -> dict[str, float]:
    """Prometheus text as ``{name: value}``; the ``llamacpp:`` prefix
    dropped so the names read as the server documents them."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0].split("{", 1)[0]
        name = name.removeprefix("llamacpp:")
        try:
            out[name] = float(parts[1])
        except ValueError:
            continue
    return out


def server_status(s: ModelSpec) -> dict[str, Any]:
    """What a llama-server (or another OpenAI-shaped server) says about
    itself: its model and slots from ``/props``, and when it was started
    with ``--metrics``, the load from ``/metrics`` — tokens per second,
    busy slots, requests running and waiting, prompt tokens served from
    the cache. Unreachable is a state, not an error."""
    root = _server_root(s.base_url or "")
    out: dict[str, Any] = {
        "name": s.name,
        "model": s.model,
        "url": root,
        "reachable": False,
    }
    try:
        props = json.loads(get_text(root + "/props"))
    except Exception as exc:  # noqa: BLE001 — any failure is "not reachable"
        out["error"] = str(exc)[:120]
        return out
    out["reachable"] = True
    out["alias"] = props.get("model_alias")
    out["slots"] = props.get("total_slots")
    modalities = props.get("modalities") or {}
    out["vision"] = bool(modalities.get("vision"))
    path = str(props.get("model_path") or "")
    out["file"] = path.replace("\\", "/").rsplit("/", 1)[-1] or None
    if not props.get("endpoint_metrics"):
        out["metrics"] = None  # the server was started without --metrics
        return out
    try:
        m = parse_metrics(get_text(root + "/metrics"))
    except Exception as exc:  # noqa: BLE001
        out["metrics"] = None
        out["error"] = str(exc)[:120]
        return out
    out["metrics"] = {
        "prompt_tps": m.get("prompt_tokens_seconds"),
        "predicted_tps": m.get("predicted_tokens_seconds"),
        "busy_slots": m.get("n_busy_slots_per_decode"),
        "processing": m.get("requests_processing"),
        "deferred": m.get("requests_deferred"),
        "prompt_tokens_total": m.get("prompt_tokens_total"),
        "prompt_tokens_cached": m.get("prompt_tokens_cached_total"),
        "predicted_tokens_total": m.get("tokens_predicted_total"),
    }
    return out


@dataclass
class ClaudeRuntime:
    """Claude as a plain chat runtime for text-in, text-out steps (titles,
    ask). Grammars are llama.cpp's; extraction with Claude uses its JSON
    schema through ``extraction.ClaudeExtractor`` instead."""

    model: str
    effort: str | None = None
    client: Any = None

    @property
    def name(self) -> str:
        return self.model

    def chat(
        self,
        system: str,
        user: str,
        *,
        grammar: str | None = None,
        max_tokens: int = 2000,
        temperature: float = 0.0,
        repeat_penalty: float = 1.0,
        stop: list[str] | None = None,
    ) -> tuple[str, dict[str, int]]:
        if grammar:
            raise ValueError("a grammar needs a llama.cpp runtime, not Claude")
        if self.client is None:
            import anthropic

            self.client = anthropic.Anthropic(timeout=_CLAUDE_TIMEOUT, max_retries=3)
        from prax.extraction import supports_effort

        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if stop:
            params["stop_sequences"] = stop
        if self.effort and supports_effort(self.model):
            params["output_config"] = {"effort": self.effort}
        response = self.client.messages.create(**params)
        text = "".join(b.text for b in response.content if b.type == "text")
        u = response.usage
        return text, {
            "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
        }


@cache
def _runtime(s: ModelSpec) -> Any:
    if s.kind == "openai":
        assert s.base_url is not None and s.model is not None
        return OpenAIRuntime(s.base_url, s.model, s.api_key_env)
    if s.kind == "claude":
        assert s.model is not None
        return ClaudeRuntime(s.model, effort=s.effort)
    return StubRuntime()


def runtime(s: ModelSpec) -> Any:
    """The loaded runtime for a spec, one per process and spec."""
    return _runtime(s)


def reset() -> None:
    """Tests: forget loaded runtimes."""
    _runtime.cache_clear()
