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

``clean`` runs on every text as it is indexed (``store.index_text``); the
``unmapped-glyphs`` ailment of ``store.repair`` finds the documents
indexed before it did and re-indexes them, chunks with unchanged text
keeping their vectors.
"""

from __future__ import annotations

import re

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


def clean(text: str) -> str:
    """The text with ligatures spelled out and Symbol-font code points
    translated; a text without either comes back as it is."""
    if not DAMAGED.search(text):
        return text
    text = _LIG.sub(lambda m: LIGATURES[m.group(0)], text)
    text = _BULLET_AT_LINE_START.sub(lambda m: m.group(1) + BULLETS[m.group(2)], text)
    return _SYM.sub(lambda m: SYMBOL.get(m.group(0), m.group(0)), text)


def damaged(text: str) -> bool:
    """Whether ``clean`` would change the text."""
    return DAMAGED.search(text) is not None
