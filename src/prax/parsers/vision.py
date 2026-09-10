"""Images as text: Claude describes a picture and transcribes what it says.

A schematic, a photo of a device, a plot or a whiteboard holds information
that classical OCR misses (handwriting, component values, what the drawing
is of). This extractor sends the image to Claude with a prompt shaped for
a research library and returns Markdown: what the image shows, every piece
of printed or handwritten text transcribed, components and values, and
anything notable. The Markdown becomes the document's text artifact like
any parser's output, so the image gets chunks, embeddings and a context
column. Explicit-only in the registry: each call costs money (a few cents
with Sonnet 5), so it runs when named:

    python scripts/parse_pending.py --pending --mime image/ --extractor claude-vision

The ``vision`` step of ``prax.yaml`` picks the model, ``PRAX_VISION`` or
``PRAX_VISION_MODEL`` override it (default ``claude-sonnet-5``; Haiku 4.5
misread a compressor schematic's identity where Sonnet got everything); the
model name is written into the artifact's first line for provenance.
"""

from __future__ import annotations

import base64
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


def describe(
    data: bytes, *, filename: str | None = None, model: str | None = None
) -> str:
    """Markdown describing the image, headed by its filename and the model."""
    from prax.parsers import ExtractionError

    mt = media_type(data)
    if mt is None:
        raise ExtractionError("not a PNG, JPEG, GIF or WebP image")
    if len(data) > MAX_BYTES:
        raise ExtractionError(f"image larger than {MAX_BYTES // (1024 * 1024)} MB")
    if model is None:
        from prax import models

        spec = models.resolve("vision")
        if spec is None or spec.kind != "claude":
            raise RuntimeError(
                "images need a Claude model: steps.vision.model in prax.yaml"
                " or PRAX_VISION"
            )
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
    if not text.strip():
        raise ExtractionError("the model returned no text")
    head = f"# {filename}" if filename else "# Image"
    return f"{head}\n\n*Image described by {model}.*\n\n{text.strip()}\n"
