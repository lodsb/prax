"""What a document's path and name say about whether it is clutter (stage
X, docs/PLAN.md): the rules that pick a set for a bulk clean-up. Pure
patterns; the store applies them and a person decides.

Measured read-only on 2026-09-28 over 12,977 documents, after the NAS
dump brought in a backed-up system drive and every program's help:

- ``system``: 139 documents from a system or program folder of a copied
  drive (``Windows/``, ``Program Files/``, the inside of a Mac ``.app``),
  the folders the NAS sender now leaves out;
- ``names``: 212 whose file name says licence, EULA, readme, credits,
  changelog. Only the file name: a title with "history" in it is a book;
- ``help``: 896 help files of programs (SuperCollider's above all).
  Reference material, if a noisy one; the user keeps them, and the rule
  is there to find them;
- ``same-text``: documents whose text is another's, byte for byte, under
  a different original (a PDF saved twice): 55. Retired as duplicates,
  so what they hold moves to the first copy.

Two rules read what the store knows rather than a path, added on
2026-10-26 after the first crawl of a photo library: ``unreadable``, a
picture the photos step marked as not a picture it opens (139 files of a
phone's memory card, named ``.jpg``, of a format no reader knows), and
``clips``, a Live Photo's clip made a document before the crawl knew a
downloader's name for it (25 of icloudpd's ``IMG_1234_HEVC.MOV``).
"""

from __future__ import annotations

import re

SYSTEM = re.compile(
    r"/(Windows|Program Files|Program Files \(x86\)|ProgramData|\$Recycle\.Bin"
    r"|AppData|Application Data|Local Settings|System Volume Information)/"
    r"|\.(app|bundle|framework|plugin|component|vst3?)/",
    re.IGNORECASE,
)
NAMES = re.compile(
    r"^(read ?me|licen[cs]e|copying|credits|change ?log|eula|release ?notes"
    r"|what'?s ?new|third ?party ?content)(?![a-z])",
    re.IGNORECASE,
)
HELP = re.compile(r"\.help\.(rtf|html?)$|/(Help|HelpSource)/", re.IGNORECASE)

RULES = {
    "system": "from a system or program folder of a copied drive",
    "names": "a licence, EULA, readme, credits or changelog by its file name",
    "help": "a program's help files",
    "same-text": "the same text as another document (kept as one)",
    "folder": "everything that came from under a folder",
    "unreadable": "a picture file the photos step could not open",
    "clips": "a Live Photo's clip, made a document before it was known as one",
}


def kinds(path: str, title: str = "") -> set[str]:
    """The path-and-name rules a document matches."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1] if path else title
    stem = name.rsplit(".", 1)[0] if "." in name else name
    out = set()
    if path and SYSTEM.search(path.replace("\\", "/")):
        out.add("system")
    if NAMES.search(stem):
        out.add("names")
    if path and HELP.search(path.replace("\\", "/")):
        out.add("help")
    return out
