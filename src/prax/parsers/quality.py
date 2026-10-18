"""What is wrong with a document's text: the checks the door runs when a
text lands (stage AO), so a garbled paper says so itself instead of
waiting for a person to see it.

``check`` takes the text, the stamp of what wrote it, the document's type
and the original's profile (``parsers.pdf.profile``, for a PDF) and
returns findings. A finding names its check, how often it saw it, up to
``EVIDENCE`` pieces of the text that show it, and the reading that would
put it right where one would (``fix``); a finding without one only flags
the document. No model and no store: the door keeps the result
(``meta.quality``) and decides what to ask for.

The checks, each from a fix a person asked for by hand on 2026-10-17:

- ``ocr-layer``: a scan's text read from the layer an old OCR laid under
  its pictures (``fix``: marker reading every page itself);
- ``maths-as-text``: a PDF that sets formulas in a maths font, read by a
  reader that writes no LaTeX, with lines of loose symbols where the
  formulas were (``fix``: the paper's LaTeX source on arXiv, or marker);
- ``bare-figures``: captions with no picture beside them (``fix``: the
  crops, unless they have just run);
- ``short-text``: a text much shorter than the PDF's own text layer;
- ``leftovers``: what a reader left that is not text (a latex2html
  comment, ``*f**s*`` for a subscript, an HTML entity).

Measured over the library on 2026-10-18
(``docs/eval/quality-checks-2026-10-18.md``).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from prax.text import markup

from . import figures

# 2: a line with code is not a formula's debris; 3: the crops seen in the
# history; 4: a slide's words over its background are no scan
VERSION = 4
EVIDENCE = 3  # pieces of the text a finding shows
SNIPPET = 120  # characters of each

# the readers that write the mathematics as LaTeX
LATEX_READERS = ("marker/", "latex-source/", "latex2html/")
# the reader that reads a PDF's text layer as it stands
LAYER_READERS = ("pymupdf4llm/", "pymupdf/")

# the readings a finding names as its fix (marker-ocr is marker's, in its
# OCR mode)
FIXING = frozenset({"marker", "latex-source", "figure-crops"})

SCANNED = 0.5  # the share of profiled pages that makes a PDF a scan
MATHS_PAGES = 2  # profiled pages in a maths font that make it a paper with formulas
DEBRIS_LINES = 3  # lines of loose symbols that make its maths garbled
SHORT = 0.5  # a text this much shorter a page than the PDF's layer is short

# a line where the formulas were: mostly tokens of one or two characters,
# some of them symbols a formula is made of
_TOKEN = re.compile(r"\S+")
# what only a formula is made of: an operator, a relation, a Greek letter
_OPERATOR = re.compile(r"[=+−×·∑∏∫∂√≤≥≠≈∞±∈∉⊂⊆∪∩∀∃→←↔⇒⇔∆∇⟨⟩α-ωΑ-Ω]")
# and what a formula shares with prose: brackets, a slash, a bar
_BRACKET = re.compile(r"[()\[\]{}|/^]")
# what is not a formula's text: inline code, an address, Markdown's own
# marks (emphasis, a list's bullet, a quote, a footnote's number)
_NOT_MATHS = re.compile(
    r"`[^`]*`|https?://\S+|\*\*|(?<!\S)[*_]|[*_](?!\S)|^(?:[-*>]|\d+[.)]?)\s+"
)
# what one reader left in one document. Not the HTML pymupdf4llm writes
# on purpose (<sup> for a footnote's mark and an exponent, <br> in a
# table's cell and in a picture's text: 7,426 documents on 2026-10-18),
# which is its format, for the page to show, not a fault of the document
_LEFTOVERS = (
    ("a latex2html comment", re.compile(r"\bMATH \$")),
    ("a subscript as emphasis", re.compile(r"\*[A-Za-z]\*\*[A-Za-z0-9]{1,3}\*")),
    ("an HTML entity", re.compile(r"&(?:nbsp|amp|lt|gt|quot|#\d+);")),
)


@dataclass(frozen=True)
class Finding:
    check: str
    count: int
    evidence: tuple[str, ...] = ()
    fix: str | None = None  # a reading: latex-source, marker, marker-ocr, figure-crops
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"check": self.check, "count": self.count}
        if self.evidence:
            out["evidence"] = list(self.evidence)
        if self.fix:
            out["fix"] = self.fix
        if self.detail:
            out["detail"] = self.detail
        return out


def _snippet(text: str) -> str:
    one = " ".join(text.split())
    return one if len(one) <= SNIPPET else one[: SNIPPET - 1] + "…"


def debris_lines(lines: list[str]) -> list[str]:
    """The lines where a formula became loose symbols: three tokens or
    more, most of them one or two characters, one of them an operator or a
    Greek letter and a third of them that or a bracket. Figure lines, page
    marks, tables and code are not prose; inline code, addresses and
    Markdown's marks are taken out first (a manual's ``\\def`` in
    backticks, a footnote's URL, "1 / 28": the library's false alarms of
    2026-10-18)."""
    out = []
    fenced = False
    for line in lines:
        s = line.strip()
        if s.startswith("```"):
            fenced = not fenced
            continue
        if (
            fenced
            or not s
            or s.startswith(("![", "#"))
            or (s.startswith("|") and s.endswith("|"))  # a table's row, not |H(ω)|
            or markup.PAGE_MARK.match(s)
            # code: pymupdf4llm sets monospace in backticks, a formula's
            # debris in italics (a grammar, VHDL, LaTeX3's sources were the
            # most "found" of the library, 2026-10-18)
            or "`" in s
        ):
            continue
        tokens = _TOKEN.findall(_NOT_MATHS.sub(" ", s))
        if len(tokens) < 3:
            continue
        short = sum(1 for t in tokens if len(t) <= 2)
        operators = sum(1 for t in tokens if _OPERATOR.search(t))
        symbols = sum(1 for t in tokens if _OPERATOR.search(t) or _BRACKET.search(t))
        if short >= 0.6 * len(tokens) and operators and symbols >= len(tokens) / 3:
            out.append(s)
    return out


def since_text(history: list[Mapping[str, Any]] | None, stamp: str) -> set[str]:
    """The readings that ran on the current text: the extractors in
    ``meta.parse_history`` after the entry that wrote it, the last that
    ``created`` or ``upgraded`` a text under the reader of ``stamp`` (its
    ``text_source``). An annotating reading (the crops, the formulas)
    leaves ``text_source`` as it was, so only the history says it ran; a
    re-read that came back ``same`` (the stamp moves on, the text stays)
    and an attempt that failed do not end the walk, and a failed one is no
    reading that ran. One that ran on an earlier text says nothing of
    this one, and a history that never wrote the text says nothing at all
    (the review of 2026-10-18)."""
    reader = stamp.split("/", 1)[0]
    out: set[str] = set()
    for h in reversed(history or []):
        name = str(h.get("extractor") or "").split("/", 1)[0]
        if name == reader and h.get("outcome") in ("created", "upgraded"):
            return out
        if "error" not in h:
            out.add(name)
    return set()


def check(
    text: str,
    *,
    stamp: str,
    mime: str | None,
    profile: Mapping[str, Any] | None = None,
    since: Collection[str] = (),
) -> list[Finding]:
    """The findings of every check on one text (the module's docstring);
    ``since``, the readings that ran on it (``since_text``)."""
    lines = text.split("\n")
    pdf = mime == "application/pdf"
    prof = profile or {}
    found: list[Finding] = []
    layer_read = stamp.startswith(LAYER_READERS)

    scan = max(float(prof.get("scanned") or 0), float(prof.get("ocr_layer") or 0))
    if pdf and layer_read and scan >= SCANNED and prof.get("layer"):
        found.append(
            Finding(
                "ocr-layer",
                round(scan * int(prof.get("pages") or 0)) or 1,
                fix="marker-ocr",
                detail={
                    "scanned": prof.get("scanned"),
                    "ocr_layer": prof.get("ocr_layer"),
                },
            )
        )

    if pdf and not stamp.startswith(LATEX_READERS) and scan < SCANNED:
        debris = debris_lines(lines)
        maths = int(prof.get("maths") or 0)
        if maths >= MATHS_PAGES and len(debris) >= DEBRIS_LINES:
            found.append(
                Finding(
                    "maths-as-text",
                    len(debris),
                    tuple(_snippet(x) for x in debris[:EVIDENCE]),
                    fix="latex-source" if prof.get("arxiv") else "marker",
                    detail={
                        "maths_pages": maths,
                        **({"arxiv": prof["arxiv"]} if prof.get("arxiv") else {}),
                    },
                )
            )

    if pdf:
        bare = sorted(figures.bare_captions(lines))
        if bare:
            found.append(
                Finding(
                    "bare-figures",
                    len(bare),
                    tuple(_snippet(lines[i]) for i in bare[:EVIDENCE]),
                    fix=None if "figure-crops" in since else "figure-crops",
                )
            )

    # only a PDF the profile read whole: the first pages of a book are its
    # contents, denser than the rest (a manual of 114 pages, 2026-10-18)
    layer = int(prof.get("layer_chars") or 0)
    pages = int(prof.get("pages") or 0)
    if pdf and layer and pages and prof.get("sampled") == pages and scan < SCANNED:
        ratio = len("".join(text.split())) / layer
        if ratio < SHORT:
            found.append(Finding("short-text", 1, detail={"share": round(ratio, 2)}))

    hits: list[str] = []
    count = 0
    kinds: dict[str, int] = {}
    for name, pattern in _LEFTOVERS:
        for m in pattern.finditer(text):
            count += 1
            kinds[name] = kinds.get(name, 0) + 1
            if len(hits) < EVIDENCE:
                hits.append(_snippet(text[max(0, m.start() - 40) : m.end() + 40]))
    if count:
        found.append(Finding("leftovers", count, tuple(hits), detail={"kinds": kinds}))
    return found


def profile_of(
    data: bytes | None, *, mime: str | None, arxiv: Any = None
) -> dict[str, Any] | None:
    """What the checks know of a PDF's original: its profile and its arXiv
    id, the one given (``meta.arxiv``) when it is one, else the stamp on
    its first page. None for anything else."""
    if mime != "application/pdf" or not data:
        return None
    from . import latexsrc, pdf

    got = pdf.profile(data)
    # the id without its version: the reading's mode takes no other (the
    # review of 2026-10-18, a "2501.13484v2" stopped the follow-ups)
    got["arxiv"] = latexsrc.arxiv_of(arxiv) or latexsrc.arxiv_id_of(data)
    return got


def fixes(findings: list[Finding]) -> dict[str, Finding]:
    """The findings that name a reading, by the reading."""
    return {f.fix: f for f in findings if f.fix}
