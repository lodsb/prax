"""The readings a model writes into a document's current text: figures,
formulas, a polished transcript, figure references and crops, and an
image described by the vision model.
"""

from __future__ import annotations

from prax.parsers import figures

from .base import ExtractionError


def _figures(
    data: bytes, *, filename: str | None = None, previous: str | None = None
) -> str:
    """The vision model's reading of every figure the text references,
    written under each; needs the document parsed first."""
    if not previous:
        raise ExtractionError("no text to put readings in: parse the document first")
    return figures.describe(data, previous)


def _formulas(
    data: bytes, *, filename: str | None = None, previous: str | None = None
) -> str:
    """A reading in words under every display equation of the current
    text (``prax.parsers.formulas``); the original is not needed."""
    from prax.parsers import formulas

    if not previous:
        raise ExtractionError("no text to read the formulas of")
    return formulas.describe(previous)


def _polish(
    data: bytes, *, filename: str | None = None, previous: str | None = None
) -> str:
    """The transcript paragraphs of the current text punctuated
    (``prax.parsers.polish``); the original is not needed."""
    from prax.parsers import polish

    if not previous:
        raise ExtractionError("no text to polish: parse the document first")
    text, _counts = polish.polish(previous)
    return text


def _polish_model() -> str:
    from prax import models

    spec = models.resolve("polish")
    return spec.name if spec is not None else "none"


def _formulas_model() -> str:
    from prax.parsers import formulas

    return formulas.model_name()


def _figures_model() -> str:
    from prax.parsers import vision

    return vision.model_name()


def _figure_refs(
    data: bytes, *, filename: str | None = None, previous: str | None = None
) -> str:
    """The original's figures referenced in the current text, nothing else
    changed: the retroactive pass over a library parsed before figures
    were found, without re-reading the pages."""
    if not previous:
        raise ExtractionError("no text to put the figures in: parse the document first")
    return figures.add_refs(data, previous)


def _figure_crops(
    data: bytes, *, filename: str | None = None, previous: str | None = None
) -> str:
    """The picture a caption claims, rendered off the page, where no
    extractor could pull an image object out: a plot drawn with vector
    paths is not an image object, and more than half the store's figure
    chunks were captions with nothing behind them."""
    if not previous:
        raise ExtractionError(
            "no text to put the pictures in: parse the document first"
        )
    return figures.add_crops(data, previous)


def _vision(
    data: bytes, *, filename: str | None = None, previous: str | None = None
) -> str:
    from prax.parsers import vision

    return vision.describe(data, filename=filename, previous=previous)


def _claude_vision(data: bytes, *, filename: str | None = None) -> str:
    from prax.parsers import vision

    return vision.describe(data, filename=filename, claude_only=True)


def _vision_model() -> str:
    from prax.parsers import vision

    return vision.model_name()
