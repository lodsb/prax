"""Images as text: a model describes a picture and transcribes what it says.

A schematic, a photo of a device, a plot or a whiteboard holds information
that classical OCR misses (handwriting, component values, what the drawing
is of). This extractor sends the image to a vision model with a prompt
shaped for a research library and returns Markdown: what the image shows,
every piece of printed or handwritten text transcribed, components and
values, and anything notable. The Markdown becomes the document's text
artifact like any parser's output, so the image gets chunks, embeddings
and a context column. Explicit-only in the registry — a model call per
image, money when the model is Claude — so it runs when named:

    python scripts/parse_pending.py --pending --mime image/ --extractor vision

The ``vision`` step of ``prax.yaml`` picks the model: a Claude model
(``PRAX_VISION`` or ``PRAX_VISION_MODEL`` override it; Haiku 4.5 misread a
compressor schematic's identity where Sonnet got everything), or an
``openai`` model — llama-server started with the model's multimodal
projector (``scripts/llama_server.ps1 -Mmproj``), which costs nothing per
image. The model's name is in the artifact's first line and in the
extractor's stamp for provenance. ``claude-vision`` is the same extractor
pinned to Claude, the name the earlier descriptions carry.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable
from typing import Any

DEFAULT_MODEL = "claude-sonnet-5"  # Haiku misread a schematic; Sonnet read it
MAX_BYTES = 5 * 1024 * 1024  # the API's limit per image
MEDIA_TYPES = {
    b"\x89PNG": "image/png",
    b"\xff\xd8": "image/jpeg",
    b"GIF8": "image/gif",
    b"RIFF": "image/webp",
}

PROMPT = """\
This image is a document in a personal research library about audio,
signal processing, music and electronics. Write Markdown that makes the
image findable and useful without seeing it:

## What it shows
One paragraph: the kind of image (schematic, block diagram, plot, photo,
screenshot, whiteboard), its subject, and what a reader learns from it.

## Text in the image
Every piece of printed or handwritten text, transcribed verbatim, one item
per line, including labels, titles, annotations, axis labels and legends.
Mark handwritten items with "(handwritten)". Keep the original spelling.

## Components and values
For schematics and diagrams: the components, blocks and signal path with
their values and designators (R12 100k, C3 10uF, op-amp stages, tubes,
transformers). For plots: what is on the axes, ranges, notable points.
Omit this section if nothing applies.

## Notes
Anything a reader would want to know: what device or paper it belongs to
if the image says so, oddities, what is unreadable.
"""

# Replaceable in tests: a callable returning an object with messages.create.
CLIENT_FACTORY: Callable[[], Any] | None = None


def media_type(data: bytes) -> str | None:
    for magic, mt in MEDIA_TYPES.items():
        if data.startswith(magic):
            return mt
    return None


def _client() -> Any:
    if CLIENT_FACTORY is not None:
        return CLIENT_FACTORY()
    import anthropic

    return anthropic.Anthropic(timeout=180.0, max_retries=3)


def model_name() -> str:
    """What the ``vision`` step resolves to, for the extractor's stamp."""
    from prax import models

    spec = models.resolve("vision")
    return spec.runtime_name if spec is not None else "none"


def describe(
    data: bytes,
    *,
    filename: str | None = None,
    model: str | None = None,
    claude_only: bool = False,
    previous: str | None = None,
) -> str:
    """Markdown describing the image, headed by its filename and the model.
    ``model`` names a Claude model outright; otherwise the ``vision`` step
    says which model, Claude or a vision-capable server (``claude_only``
    refuses the latter, for the ``claude-vision`` name). ``previous`` is
    the document's current text: when it is an earlier reading of the
    image, the new reading is put first and the earlier ones kept after
    it, each section headed with its model, so a second model adds to
    the picture instead of replacing it (a re-run of the same model does
    replace its own reading)."""
    fresh = _describe(
        data, filename=filename, model=model, claude_only=claude_only
    )
    return merge_readings(fresh, previous) if previous else fresh


def _describe(
    data: bytes,
    *,
    filename: str | None,
    model: str | None,
    claude_only: bool,
) -> str:
    from prax.parsers import ExtractionError

    mt = media_type(data)
    if mt is None:
        raise ExtractionError("not a PNG, JPEG, GIF or WebP image")
    if len(data) > MAX_BYTES:
        raise ExtractionError(f"image larger than {MAX_BYTES // (1024 * 1024)} MB")
    if model is None:
        from prax import models

        spec = models.resolve("vision")
        if spec is None or spec.kind not in ("claude", "openai"):
            raise RuntimeError(
                "images need a Claude model or a vision-capable server"
                " (llama-server --mmproj): steps.vision.model in prax.yaml"
                " or PRAX_VISION"
            )
        if spec.kind == "openai":
            if claude_only:
                raise RuntimeError(
                    "claude-vision wants a Claude model; the vision step names"
                    f" {spec.name!r} — use the 'vision' extractor for it"
                )
            text = _describe_server(models.runtime(spec), data, mt)
            return _artifact(filename, spec.runtime_name, text)
        model = spec.model or DEFAULT_MODEL
    response = _client().messages.create(
        model=model,
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mt,
                            "data": base64.b64encode(data).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    return _artifact(filename, model, text)


def _describe_server(runtime: Any, data: bytes, mt: str) -> str:
    """The same prompt through an OpenAI-shaped server; a little temperature
    keeps a local model out of the repetition loops greedy decoding falls
    into on long transcriptions."""
    text, _usage = runtime.chat(
        "", PROMPT, images=[(data, mt)], max_tokens=2000, temperature=0.2
    )
    return text


def _artifact(filename: str | None, model: str, text: str) -> str:
    from prax.parsers import ExtractionError

    if not text.strip():
        raise ExtractionError("the model returned no text")
    head = f"# {filename}" if filename else "# Image"
    return f"{head}\n\n*Image described by {model}.*\n\n{text.strip()}\n"


_BY_LINE = re.compile(r"^\*Image described by (.+?)\.\*$", re.MULTILINE)
_SECTION = re.compile(r"^## (.+?)(?: \(([^()]+)\))?$", re.MULTILINE)


def readings(text: str) -> list[tuple[str, str]]:
    """The readings in an image artifact as ``(model, body)`` pairs, the
    body being that model's sections with plain headings; ``[]`` when the
    text is not an image description."""
    m = _BY_LINE.search(text)
    if not m:
        return []
    models = [s.strip() for s in re.split(r";\s*read again by\s*", m.group(1))]
    first = models[0]
    out: dict[str, list[str]] = {}
    body = text[m.end() :]
    found = list(_SECTION.finditer(body))
    if not found:
        return [(first, body.strip())] if body.strip() else []
    for i, s in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(body)
        section = f"## {s.group(1)}\n" + body[s.end() : end].strip("\n")
        out.setdefault(s.group(2) or first, []).append(section.strip())
    return [(who, "\n\n".join(parts)) for who, parts in out.items()]


def merge_readings(fresh: str, previous: str) -> str:
    """The fresh reading first, then the earlier readings of other models,
    every section headed with its model; the same model's earlier reading
    is dropped. ``previous`` that is no image description is dropped too
    (the image had OCR text, say)."""
    new = readings(fresh)
    old = [r for r in readings(previous) if r[0] not in {m for m, _ in new}]
    if not new or not old:
        return fresh
    head = fresh.split("\n", 1)[0]
    allr = new + old
    by = "; read again by ".join(m for m, _ in allr)
    parts = [head, "", f"*Image described by {by}.*"]
    for model, body in allr:
        parts += ["", _SECTION.sub(lambda s, m=model: f"## {s.group(1)} ({m})", body)]
    return "\n".join(parts).rstrip() + "\n"
