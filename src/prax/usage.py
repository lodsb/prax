"""What a call to a paid model used, carried back to the door.

A reading is done by a worker and the worker never writes to the store
(invariant 4), so what it spends has to travel home with its result.
Extraction does that already — its answer carries ``usage`` — but a
reading's answer is text, and the tokens behind it used to be dropped on
the floor. On 2026-09-23 a worker read 30 figures with claude-sonnet-5
and the ledger showed nothing at all.

So a client that talks to a paid model records what it used here, the
worker takes it when a document is done and posts it with the result,
and the door writes the ledger row (``prax.budget``). The accumulator is
thread-local because a worker reads several documents at once, and it is
cleared by the taking, so nothing is ever counted twice.
"""

from __future__ import annotations

import threading
from typing import Any

_local = threading.local()

FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "cached_tokens",
)


def _bag() -> dict[str, dict[str, int]]:
    bag = getattr(_local, "bag", None)
    if bag is None:
        bag = _local.bag = {}
    return bag


def record(model: str, usage: Any) -> None:
    """What one call to ``model`` used. ``usage`` is the API's own object
    or a mapping; anything it does not say counts as nothing."""
    if not model or usage is None:
        return
    into = _bag().setdefault(model, {})
    for field in FIELDS:
        got = (
            usage.get(field) if isinstance(usage, dict) else getattr(usage, field, None)
        )
        if got:
            into[field] = into.get(field, 0) + int(got)


def take() -> dict[str, dict[str, int]]:
    """Everything recorded on this thread since the last taking, and
    clear it. ``{model: {input_tokens: …}}``, empty when nothing was
    paid for."""
    bag = _bag()
    out = {model: dict(tokens) for model, tokens in bag.items() if tokens}
    bag.clear()
    return out


def clear() -> None:
    """Forget what was recorded without reading it: what a caller does
    before a piece of work, so nothing earlier lands on it."""
    _bag().clear()
