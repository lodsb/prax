"""What a call to a model costs, and what a Claude model accepts: the
price table, the cache shares, and ``cost_usd`` over what a call used.

Facts about models rather than about any one step. They lived in
``prax.graph.extraction`` until 2026-09-28, where the extraction was the
first paid step; asking, surfing, the pipeline, entity resolution and the
budget all priced their calls through the extraction module.
"""

from __future__ import annotations


def supports_effort(model: str) -> bool:
    """The ``effort`` output setting exists on the Opus and Sonnet lines from
    4.5/4.6 on; Haiku rejects it with a 400."""
    return not model.startswith("claude-haiku")


# USD per million tokens (input, output), from the published table
# (platform.claude.com/docs/en/about-claude/pricing, read 2026-09-22).
# A model id may carry a date (claude-haiku-4-5-20251001); ``price`` looks
# up the longest name that matches, so a dated id is priced as its model.
# An unknown Claude model is charged at Opus rates, which is the safe way
# to be wrong when a budget depends on the number.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5-1": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-opus-4-1": (15.0, 75.0),
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-3-5": (0.8, 4.0),
}
# What a cached input token costs, as a share of the input price. The
# standard is a tenth; two models read their cache cheaper.
CACHE_READ = {
    "claude-fable-5-1": 0.025,
    "claude-mythos-5-1": 0.025,
    "claude-opus-5-5": 0.05,
}
CACHE_READ_SHARE = 0.1
# A cache write: prax asks for the five-minute cache (``ephemeral`` with no
# ttl), which is 1.25 times the input price; the hour's cache would be 2.
CACHE_WRITE_SHARE = 1.25


def _known(model: str) -> str | None:
    """The name of ``PRICES`` this model id is: the longest one it starts
    with, so ``claude-haiku-4-5-20251001`` is Haiku 4.5 and not a stranger."""
    matches = [name for name in PRICES if model.startswith(name)]
    return max(matches, key=len) if matches else None


def price(model: str) -> tuple[float, float]:
    """USD per million input and output tokens: ``prax.yaml`` may price a
    served model; local models and unpriced servers cost nothing; Claude
    models come from the table, unknown ones at Opus rates."""
    from prax import models

    priced = models.price_of(model)
    if priced:
        return priced
    if model.startswith("local:") or model == "stub" or "@" in model:
        return (0.0, 0.0)
    known = _known(model)
    return PRICES[known] if known else (5.0, 25.0)


def cost_usd(model: str, usage: dict[str, int]) -> float:
    """What one call cost, from what it used: the input and output prices
    of the model, a cache write at ``CACHE_WRITE_SHARE`` of the input
    price and a cache read at this model's share of it."""
    price_in, price_out = price(model)
    read_share = CACHE_READ.get(_known(model) or model, CACHE_READ_SHARE)
    plain = usage.get("input_tokens", 0)
    cached = usage.get("cache_read_input_tokens", 0)
    written = usage.get("cache_creation_input_tokens", 0)
    out = usage.get("output_tokens", 0)
    return (
        plain * price_in
        + cached * price_in * read_share
        + written * price_in * CACHE_WRITE_SHARE
    ) / 1e6 + out * price_out / 1e6
