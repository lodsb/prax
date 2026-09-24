"""Titles for documents that have none worth the name.

Half the library's titles are filenames: standalone Zotero attachments
arrive as ``<md5>-slides.pdf``, items without metadata as ``Unknown - 2002
- No Title.pdf``, and conference papers as the ALL CAPS line their PDF
prints. A title is what a search hit, a citation in an answer and a
``paper`` entity are called, so these are repaired in a batch pass
(the worker's titles step) that goes through ``store.retitle``, which
keeps the old title in ``meta.title_history`` and moves the paper entity
along.

``needs_title`` says why a title should go. For ALL CAPS titles ``recase``
is a rule (title case with stopwords and known acronyms), no model. For
filenames the beginning of the document goes to the titles step's model
(``prax.models``; a local server keeps private material on the machine,
and the call is a few seconds) for one line: the title as printed when there is
one, otherwise a short
descriptive name in the document's language, which for a course sheet or
a manual is what a person would type to find it. PDF metadata and the
first Markdown heading are offered to the model as hints, since each is
right about half the time and junk otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from prax import answers, extraction, markup

HEAD_CHARS = 1500
TITLE_MAX = 160
HASH_PREFIX = re.compile(r"^[0-9a-f]{32}-")
FILE_EXT = re.compile(
    r"\.(pdf|html?|txt|md|djvu|epub|docx?|pptx?|xlsx?|tex|rtf|ps|gif|png|jpe?g|webp)\.?$",
    re.IGNORECASE,
)
ZOTERO_AUTO = re.compile(r"(^unknown -|- no title\b|^no title\b)", re.IGNORECASE)
JUNK_PDF_META = re.compile(
    r"(microsoft (word|powerpoint)|powerpoint|\.(docx?|dvi|indb|pptx?|tex|qxd|fm)$"
    r"|^untitled|^\s*$|^\d+$)",
    re.IGNORECASE,
)
SECTION_HEADING = re.compile(
    r"^(\d+(\.\d+)*\.?\s|abstract|introduction|contents|table of contents|theorem"
    r"|summary|zusammenfassung|inhalt|references|aufgabe \d|chapter \d)",
    re.IGNORECASE,
)
PAGE_MARK = markup.PAGE_MARK_ANY
PICTURE = re.compile(
    r"<!-- Start of picture text -->.*?<!-- End of picture text -->", re.DOTALL
)
MARKUP = re.compile(r"(\*\*|__|~~|</?u>|</?i>|</?b>|<br>)")

STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "for",
    "in",
    "on",
    "at",
    "to",
    "by",
    "with",
    "from",
    "as",
    "into",
    "via",
    "vs",
    "versus",
    "using",
    "over",
    "under",
    "between",
    "without",
    "through",
    "towards",
    "toward",
    "its",
    "der",
    "die",
    "das",
    "und",
    "für",
    "von",
    "mit",
    "im",
    "zu",
    "des",
    "de",
    "la",
    "le",
    "les",
    "du",
    "et",
    "el",
    "los",
    "las",
    "y",
    "del",
}
ACRONYMS = {
    "DSP",
    "FFT",
    "STFT",
    "DFT",
    "MDCT",
    "MIDI",
    "OSC",
    "VST",
    "AU",
    "LTI",
    "IIR",
    "FIR",
    "FDN",
    "GPU",
    "CPU",
    "CNN",
    "RNN",
    "LSTM",
    "GRU",
    "GAN",
    "VAE",
    "NMF",
    "HRTF",
    "SVM",
    "PCA",
    "ICA",
    "MFCC",
    "DAFX",
    "ICASSP",
    "ISMIR",
    "AES",
    "IEEE",
    "ACM",
    "SIGGRAPH",
    "NIME",
    "SMC",
    "ICMC",
    "DAW",
    "EQ",
    "FM",
    "AM",
    "PWM",
    "ADC",
    "DAC",
    "BBD",
    "VCA",
    "VCO",
    "VCF",
    "LFO",
    "ADSR",
    "SNR",
    "THD",
    "EMG",
    "EEG",
    "ECG",
    "MRI",
    "RF",
    "UI",
    "UX",
    "API",
    "SDK",
    "OS",
    "IO",
    "AI",
    "ML",
    "NLP",
    "LLM",
    "DNN",
    "MLP",
    "HMM",
    "DTW",
    "RMS",
    "PLL",
    "DC",
    "AC",
    "MP3",
    "WAV",
    "FLAC",
    "RGB",
    "HDR",
    "PDE",
    "ODE",
    "SVD",
    "KL",
    "MSE",
    "ASR",
    "TTS",
    "MIR",
    "GUI",
    "CLI",
    "SQL",
    "HTML",
    "CSS",
    "JSON",
    "XML",
    "USB",
    "HDMI",
    "LED",
    "LCD",
    "OLED",
    "PCB",
    "IC",
    "ROM",
    "RAM",
    "SSD",
    "HDD",
    "TCP",
    "IP",
    "UDP",
    "HTTP",
    "URL",
    "DOI",
    "II",
    "III",
    "IV",
    "VI",
    "VII",
    "VIII",
    "IX",
    "XI",
    "XII",
    "USA",
    "UK",
    "EU",
    "MIT",
    "CCRMA",
    "IRCAM",
    "TU",
    "ETH",
    "UC",
    "NYU",
}


def needs_title(title: str | None, meta: dict[str, Any] | None = None) -> str | None:
    """Why a title should be replaced, or None: ``empty``, ``filename``,
    ``zotero-auto`` (an ``Unknown - No Title`` name) or ``caps``. A title the
    pass or a person already wrote (``meta.title_source``) is kept."""
    meta = meta or {}
    src = meta.get("title_source")
    if src and src != "zotero":
        return None
    t = (title or "").strip()
    if not t:
        return "empty"
    if HASH_PREFIX.match(t) or FILE_EXT.search(t):
        return "filename"
    if ZOTERO_AUTO.search(t):
        return "zotero-auto"
    if len(t) > 20 and t == t.upper() and re.search(r"[A-Z]{4}", t):
        return "caps"
    return None


def _cap(word: str) -> str:
    if word.upper() in ACRONYMS or any(ch.isdigit() for ch in word):
        return word.upper()
    return "-".join(part[:1].upper() + part[1:].lower() for part in word.split("-"))


def recase(title: str) -> str:
    """ALL CAPS to title case: stopwords lower (not the first word), known
    acronyms and tokens with digits upper, the rest capitalized."""
    words = title.split()
    out = []
    for i, w in enumerate(words):
        core = w.strip("():,;.!?'\"“”‘’")
        if not core:
            out.append(w)
            continue
        lead = w[: w.index(core)]
        trail = w[w.index(core) + len(core) :]
        after_colon = i > 0 and words[i - 1].endswith(":")
        if i and not after_colon and core.lower() in STOPWORDS:
            cased = core.lower()
        else:
            cased = _cap(core)
        out.append(lead + cased + trail)
    return " ".join(out)


def head(text: str, chars: int = HEAD_CHARS) -> str:
    """The beginning of a text artifact, without page markers, picture
    transcriptions and Markdown emphasis, collapsed to single lines."""
    s = PICTURE.sub(" ", text[: chars * 3])
    s = PAGE_MARK.sub("\n", s)
    s = MARKUP.sub("", s)
    lines = [" ".join(line.split()) for line in s.splitlines()]
    s = "\n".join(line for line in lines if line)
    return s[:chars]


def first_heading(text: str) -> str | None:
    """The first Markdown heading when it could be a title (not a numbered
    section, not Abstract or Contents), cleaned of emphasis."""
    for line in text[:20000].splitlines():
        if line.startswith("#"):
            h = MARKUP.sub("", line.lstrip("#")).strip()
            h = " ".join(h.split())
            if 4 <= len(h) <= TITLE_MAX and not SECTION_HEADING.match(h):
                return h
            return None
    return None


def pdf_meta_title(value: str | None) -> str | None:
    """A PDF's metadata title when it is not an application's file name."""
    t = " ".join((value or "").split())
    if len(t) < 6 or JUNK_PDF_META.search(t) or FILE_EXT.search(t):
        return None
    return t


# ------------------------------------------------------------ the model
# No grammar here: a forced "title" prefix makes a 7B model add a label of
# its own and, with nothing ending the line early, ramble to the length cap
# (the first sample run). A plain instruction, a stop at the line break and
# a short token budget give a clean line; confidence is computed from the
# text instead of asked for.

SYSTEM = """\
You name documents for a personal research library. You see the beginning of
a document, its file name, and sometimes hints. Answer with the document's
title and nothing else, on one line: the title as printed when the document
has one (a paper, article, manual, thesis, report, book chapter). When it
has none, give a short descriptive name in the document's own language
saying what it is and for what, using only names, courses, products,
events or organisations that appear in the text; invent nothing and add no
placeholder words. Never a file name, never a section heading such as
Introduction or Table of Contents, never a person's name alone, no quotes,
no label, under 120 characters, in normal title or sentence case (fix
broken capitalisation from scanning, no ALL CAPS)."""

LABEL = answers.LABEL  # kept as a name: the guess prompt refers to it
WORD = re.compile(r"[^\W\d_]{4,}", re.UNICODE)


@dataclass
class Guess:
    title: str
    confidence: str
    usage: dict[str, int]


def user_message(
    text: str,
    *,
    filename: str | None,
    heading: str | None = None,
    pdf_title: str | None = None,
) -> str:
    parts = []
    if filename:
        parts.append(f"File name: {filename}")
    if heading:
        parts.append(f"First heading in the text: {heading}")
    if pdf_title:
        parts.append(f"Title in the PDF metadata (often wrong): {pdf_title}")
    parts += ["", "Beginning of the document:", "", head(text), "", "Title:"]
    return "\n".join(parts)


def parse(text: str) -> str | None:
    """The first line of the model's answer as a title: labels, quotes and
    trailing chatter removed; None when nothing usable is left."""
    for line in text.splitlines():
        t = " ".join(answers.unwrap(line.split("</")[0]).split())
        if len(t) >= 3:
            return t[:TITLE_MAX].rstrip(" ,;:-")
    return None


def printed(title: str, text: str) -> str:
    """``high`` when most of the title's words occur at the beginning of the
    text (the title was read, not invented), else ``low``."""
    words = {w.lower() for w in WORD.findall(title)}
    if not words:
        return "low"
    body = head(text, chars=HEAD_CHARS * 2).lower()
    found = sum(1 for w in words if w in body)
    return "high" if found / len(words) >= 0.6 else "low"


_ODD_CASE = re.compile(r"[a-zäöüß][A-ZÄÖÜ]")


def tidy(title: str) -> str:
    """Scanned text arrives with capitals inside words ("IntervIew MIt");
    two such words and the title is recased by rule."""
    odd = sum(
        1 for w in title.split() if _ODD_CASE.search(w) and w.upper() not in ACRONYMS
    )
    return recase(title) if odd >= 2 else title


def acceptable(title: str, *, filename: str | None) -> bool:
    """Not a file name, not the old file name, not an obvious non-title."""
    if FILE_EXT.search(title) or HASH_PREFIX.match(title) or ZOTERO_AUTO.search(title):
        return False
    if filename and title.lower() == filename.lower():
        return False
    if title.lower() in ("unset", "unknown", "none", "n/a", "untitled"):
        return False
    return not SECTION_HEADING.match(title) or len(title) > 40


def guess_title(
    runtime: extraction.Runtime,
    text: str,
    *,
    filename: str | None,
    heading: str | None = None,
    pdf_title: str | None = None,
) -> Guess | None:
    out, usage = runtime.chat(
        SYSTEM,
        user_message(text, filename=filename, heading=heading, pdf_title=pdf_title),
        max_tokens=60,
        temperature=0.0,
        stop=["\n"],
    )
    title = parse(out)
    if title is None or not acceptable(title, filename=filename):
        return None
    title = tidy(title)
    return Guess(title=title, confidence=printed(title, text), usage=usage)
