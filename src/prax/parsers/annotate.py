"""A model's reading under a marked line, said once.

A figure's line and a display equation's line each get the readings of a
model under them (``markup.read_by``: ``*Figure, as read by …:*``,
``*Formula, as read by …:*``). The vision pass (``parsers.figures``) and
the formulas pass (``parsers.formulas``) wrote the same steps twice: the
mode a host chose, the text around the line a model is shown, the
reading put under the line (a model's earlier one replaced on ``again``,
another model's kept), and the readings carried over a replacing read
(structure review finding 6). The kinds differ only in their anchor, the
line a reading belongs under, and in what they ask; that stays in each.
``parsers.readings`` is the other side: the extractors a reading request
names, which call these passes.
"""

from __future__ import annotations

from collections.abc import Callable

from prax.text import chunking, markup


def mode(dotted: str, env: str, default: str, which: tuple[str, ...]) -> str:
    """The reading mode a host chose (``parse.figures``,
    ``parse.formula_readings``), refused when it is not one of ``which``."""
    from prax import config
    from prax.parsers import ExtractionError

    chosen = str(config.setting(dotted, env, default))
    if chosen not in which:
        raise ExtractionError(f"{dotted} must be one of {which}, not {chosen!r}")
    return chosen


def nearby(
    lines: list[str],
    at: int,
    *,
    each: int,
    chars: int,
    skip: Callable[[str], bool] = lambda line: False,
) -> str:
    """The text on either side of the line at ``at``, ``each`` lines a side
    and at most ``chars`` characters a side, without any reading (a model
    must not be shown its own earlier work, or another's) and without the
    lines ``skip`` names (the other figures, the other equations)."""
    keep = [
        ln
        for ln in lines[: max(0, at)][-each:] + ["…"] + lines[at + 1 :][:each]
        if not markup.READ_BY.match(ln.strip()) and not skip(ln.strip())
    ]
    text = " ".join(" ".join(keep).split())
    if len(text) > 2 * chars:
        text = text[:chars] + " … " + text[-chars:]
    return text


def is_by(line: str, who: str) -> bool:
    """Is this line ``who``'s reading, of any kind?"""
    m = markup.READ_BY.match(line.strip())
    return bool(m and m.group("model").strip() == who)


def place(
    lines: list[str], at: int, kind: str, who: str, text: str, *, again: bool
) -> None:
    """Put ``who``'s reading of the ``kind`` on line ``at`` right under it,
    inside its paragraph. With ``again`` it goes through the readings
    under the line first: this model's earlier one goes, another model's
    stays, and the new one follows them."""
    at += 1
    if again:
        while at < len(lines) and markup.READ_BY.match(lines[at].strip()):
            if is_by(lines[at], who):
                del lines[at]
            else:
                at += 1
    lines.insert(at, markup.read_by(kind, who, text))


def anchor_of(line: str) -> str | None:
    """What a line holds that a model reads under it: a figure, by its
    hash, or a display equation, by its LaTeX with the spacing collapsed
    (two reads of one paper write the same equation spaced differently)."""
    ref = markup.figure_line_of(line)
    if ref is not None:
        return ref
    stripped = line.strip()
    if chunking.is_formula(stripped):
        return "formula:" + "".join(stripped[2:-2].split())
    return None


def carry(previous: str | list[str], text: str) -> str:
    """``text`` with the readings ``previous`` held carried over: a
    replacing read (marker, OCR, the parser again) writes a text of its
    own, and the readings were lines of the old one, under each figure's
    or equation's line (``anchor_of``). One still in the new text gets the
    readings it had, after any the new text already gives it, a model's
    line once. Until 2026-11-07 only figures' were carried, so a marker
    read dropped every formula reading of the paper.
    ``previous`` may be several texts, newest first (the current one and
    the earlier ones, ``store.earlier_texts``): a model's newest reading of
    a figure is the one carried. On 2026-10-06 a marker evening dropped
    every figure reading of the papers it read.

    An anchor that comes several times (an equation repeated, a figure
    shown twice) is matched by its turn: the second in the old text is the
    second in the new one. By the anchor alone, the second's readings went
    under the first, or were lost (the safety review of 2026-11-11)."""
    texts = [previous] if isinstance(previous, str) else previous
    held: dict[tuple[str, int], list[str]] = {}
    for one in texts:
        if "(figure:" not in one and "$$" not in one:
            continue
        old = one.split("\n")
        turns: dict[str, int] = {}
        for i, line in enumerate(old):
            anchor = anchor_of(line)
            if anchor is None:
                continue
            turns[anchor] = turns.get(anchor, 0) + 1
            ref = (anchor, turns[anchor])
            have = {
                m.group("model")
                for t in held.get(ref, [])
                if (m := markup.READ_BY.match(t))
            }
            at = i + 1
            while at < len(old) and (m := markup.READ_BY.match(old[at])):
                if m.group("model") not in have:
                    held.setdefault(ref, []).append(old[at])
                    have.add(m.group("model"))
                at += 1
    if not held:
        return text
    lines = text.split("\n")
    out: list[str] = []
    seen: dict[str, int] = {}
    i = 0
    while i < len(lines):
        out.append(lines[i])
        anchor = anchor_of(lines[i])
        i += 1
        if anchor is None:
            continue
        seen[anchor] = seen.get(anchor, 0) + 1
        ref = (anchor, seen[anchor])
        if ref not in held:
            continue
        there: list[str] = []
        while i < len(lines) and markup.READ_BY.match(lines[i]):
            there.append(lines[i])
            i += 1
        models = {m.group("model") for t in there if (m := markup.READ_BY.match(t))}
        out += there
        for reading in held.pop(ref):
            m = markup.READ_BY.match(reading)
            if m and m.group("model") not in models:
                out.append(reading)
                models.add(m.group("model"))
    return "\n".join(out)
