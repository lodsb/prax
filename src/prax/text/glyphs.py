"""Glyphs the extractors leave behind, put back into letters.

A PDF's text comes out of its fonts, and two kinds of font leave marks
that are not characters:

* **Ligatures.** A typeset "fi" is one glyph, U+FB01, and MuPDF hands it
  over as such. Search does not match "ﬁlter" against "filter", and a
  webfont without the presentation forms shows a box. The five common
  ones (ff, fi, fl, ffi, ffl) and the two long-s forms are spelled out.
* **Symbol-font code points.** A glyph without a Unicode name is mapped
  by MuPDF into the private use area, and a Word-era PDF's formulas set
  in Adobe's Symbol font come out as U+F020–U+F0FE: the font's own byte
  behind U+F000. That encoding is a known table (= is F03D, ∈ is F0CE,
  … is F0BC, the Greek alphabet on the Latin letters), so those are
  translated. A bullet from Wingdings shares the range; one at the
  start of a line is taken for a bullet. Other private-use ranges
  (F1xx, F2xx: other symbol fonts) are left alone — a box is honest,
  a wrong symbol is not — and so is U+FFFD, which is a glyph the font
  named nothing at all.

* **Detached accents.** A LaTeX-made PDF sets an accent as a glyph of
  its own over its letter, and the text layer hands both over side by
  side: "f¨ur", "Universit`a", "B´ezier", "Erd˝os". A search for "für"
  cannot match it, and an entity is named after the broken word. The
  accent goes back onto its letter (``accents``). It may stand before
  the letter or after it, and both happen in one text ("Greˇsa´kova"),
  so where only one side takes that accent it goes there, and where
  both could (a vowel on each side of an umlaut) the order the rest of
  the text uses for that accent decides, and failing that the letter
  the accent is more often found on. Each accent combines only with
  the letters it is used on in Latin script, so an apostrophe written
  as ´ ("Wobbrock´s") stays, and a grave beside a space (a Markdown
  code span) is never taken for one.

``clean`` runs on every text as it is indexed (``store.index_text``); the
``unmapped-glyphs`` ailment of ``store.repair`` finds the documents
indexed before it did and re-indexes them, chunks with unchanged text
keeping their vectors.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",  # long s + t
    "ﬆ": "st",
}

# Adobe Symbol encoding, byte -> character; MuPDF puts the byte at U+F000+b.
_SYMBOL_LOW = (
    " !∀#∃%&∋()∗+,−./0123456789:;<=>?≅ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ[∴]⊥_‾"
    "αβχδεφγηιϕκλμνοπθρστυϖωξψζ{|}∼"
)  # 0x20 .. 0x7E
_SYMBOL_HIGH = (
    "€ϒ′≤⁄∞ƒ♣♦♥♠↔←↑→↓°±″≥×∝∂•÷≠≡≈…⏐⎯↵ℵℑℜ℘⊗⊕∅∩∪⊃⊇⊄⊂⊆∈∉∠∇®©™∏√⋅¬∧∨⇔⇐⇑⇒⇓◊〈®©™∑"
    "⎛⎜⎝⎡⎢⎣⎧⎨⎩⎪〉∫⌠⎮⌡⎞⎟⎠⎤⎥⎦⎫⎬⎭"
)  # 0xA0 .. 0xFE (0xF0 is unassigned; kept as it came)
SYMBOL: dict[str, str] = {}
for _i, _ch in enumerate(_SYMBOL_LOW):
    SYMBOL[chr(0xF020 + _i)] = _ch
for _i, _ch in enumerate(_SYMBOL_HIGH):
    if _ch != "":
        SYMBOL[chr(0xF0A0 + _i)] = _ch
BULLETS = {"": "•", "": "▪", "": "•", "": "➢", "": "✓"}

_LIG = re.compile("[ﬀ-ﬆ]")
_SYM = re.compile("[-]")
_BULLET_AT_LINE_START = re.compile(r"(?m)^([ \t*-]*)([])")
DAMAGED = re.compile("[ﬀ-ﬆ-]")


# a spacing accent -> its combining mark, and the letters it goes on, the
# most frequent first (the tie-break when both neighbours could take it)
ACCENTS: dict[str, tuple[str, str]] = {
    "¨": ("̈", "uoaeiyUOAEIY"),
    "´": ("́", "eaiouyEAIOUYnszcNSZC"),
    "`": ("̀", "aeiouAEIOU"),
    "ˆ": ("̂", "eoaiuEOAIU"),
    "˜": ("̃", "naoNAO"),
    "ˇ": ("̌", "scrzenltdSCRZENLTD"),
    "˝": ("̋", "ouOU"),
    "˘": ("̆", "agAG"),
    "˚": ("̊", "auAU"),
    "¸": ("̧", "csCS"),
    "˙": ("̇", "zZ"),
}
_ACCENT = re.compile("[" + "".join(ACCENTS) + "]")
# the two that are also mathematics (a hat, a tilde over a variable) are
# only taken inside a word, with a letter on both sides
_INSIDE_ONLY = {"ˆ", "˜"}
_GAP = re.compile(r"\s")
WORD_REACH = 80  # characters a word is looked for on each side of a mark


def _on(letter: str, accent: str) -> str | None:
    """The letter with the accent on it, when that is one character."""
    mark, letters = ACCENTS[accent]
    if letter == "ı":  # the dotless i the accent was set over
        letter = "i"
    if letter not in letters:
        return None
    joined = unicodedata.normalize("NFC", letter + mark)
    return joined if len(joined) == 1 else None


def _sides(text: str, i: int) -> tuple[str | None, str | None]:
    """What the accent at ``i`` would make of the letter before it and of
    the letter after it (None where it cannot go)."""
    accent = text[i]
    prev = text[i - 1] if i > 0 else ""
    nxt = text[i + 1] if i + 1 < len(text) else ""
    before = _on(nxt, accent) if nxt else None  # the accent before its letter
    after = _on(prev, accent) if prev else None  # the accent after its letter
    if accent == "`":
        # a Markdown code span opens after a space and closes after a
        # letter: only a grave inside a lowercase word, before its vowel,
        # with no other backtick in the word, is an accent
        # the word around it, looked for within WORD_REACH characters: a
        # search over the whole text per backtick took a book of 3.4 million
        # characters half an hour (2026-10-09)
        left = text[max(0, i - WORD_REACH) : i]
        right = text[i : i + WORD_REACH]
        gap = _GAP.search(right)
        word = re.split(r"\s", left)[-1] + (right[: gap.start()] if gap else right)
        if not (prev.islower() and nxt.islower()) or word.count("`") > 1:
            return None, None
        after = None
    if accent in _INSIDE_ONLY:
        # in a word of lowercase letters on both sides, as in "extrˆemes",
        # never in a formula or a phonetic transcription
        after_next = text[i + 2] if i + 2 < len(text) else ""
        if not (prev.isalpha() and nxt.islower() and after_next.isalpha()):
            before = None
        if not (prev.isalpha() and nxt.islower() and i >= 2 and text[i - 2].isalpha()):
            after = None
    if nxt == "ı" and before:
        after = None  # a dotless i is one the accent stood on
    if accent == "´" and before and nxt.lower() in "nszc":
        # "Raczy´nski", "´swiat"; but "Wobbrock´s" is an apostrophe
        following = text[i + 2] if i + 2 < len(text) else ""
        if not following.isalpha():
            before = None
    if before and not prev:
        before = None  # a passage's first character: what stood before it is not here
    if before and prev and not (prev.isalpha() or prev.isspace() or prev in "-(\"'"):
        before = None  # "**´**", "<sup>¨" are marks, not letters
    return after, before


def accents(text: str) -> str:
    """The text with each detached accent put back on its letter."""
    if not _ACCENT.search(text):
        return text
    found = [m.start() for m in _ACCENT.finditer(text)]
    # which side each accent stands on where only one side can take it
    order: Counter[tuple[str, str]] = Counter()
    for i in found:
        after, before = _sides(text, i)
        if after and not before:
            order[(text[i], "after")] += 1
        elif before and not after:
            order[(text[i], "before")] += 1
    out: list[str] = []
    last = 0
    skip_to = -1
    for i in found:
        if i < skip_to:
            continue
        after, before = _sides(text, i)
        if not after and not before:
            continue
        accent = text[i]
        if after and before:
            a, b = order[(accent, "after")], order[(accent, "before")]
            # no evidence either way: before its letter, which is where a
            # LaTeX-made PDF most often leaves it ("B´ezier", "Raczy´nski")
            side = "after" if a > b else "before"
        else:
            side = "after" if after else "before"
        if side == "after":
            out.append(text[last : i - 1])
            out.append(after or "")
            last = i + 1
            # "Einfu¨ hrung": the space the accent's glyph left behind
            if (
                accent == "¨"
                and text[i + 1 : i + 2] == " "
                and text[i + 2 : i + 3].islower()
            ):
                last = i + 2
        else:
            out.append(text[last:i])
            out.append(before or "")
            last = i + 2
            skip_to = i + 2
    out.append(text[last:])
    return "".join(out)


def clean(text: str) -> str:
    """The text with ligatures spelled out, Symbol-font code points
    translated and detached accents put back on their letters; a text
    without any of these comes back as it is."""
    if DAMAGED.search(text):
        text = _LIG.sub(lambda m: LIGATURES[m.group(0)], text)
        text = _BULLET_AT_LINE_START.sub(
            lambda m: m.group(1) + BULLETS[m.group(2)], text
        )
        text = _SYM.sub(lambda m: SYMBOL.get(m.group(0), m.group(0)), text)
    return accents(text)


def damaged(text: str) -> bool:
    """Whether ``clean`` would change the text. Asked of the cleaning
    itself: a Symbol-font code point it leaves as it is (U+F0F0, which the
    font leaves unassigned) is no damage it can mend, and counting it as
    one re-indexed the same documents on every heal (2026-10-09)."""
    if DAMAGED.search(text) is None and _ACCENT.search(text) is None:
        return False
    return clean(text) != text
