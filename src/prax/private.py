"""What makes a document look personal (stage V, docs/PLAN.md).

The library keeps everything, a bank statement and a contract as much as a
paper, and the wall of stage U keeps a named token from what is personal.
These rules decide what a document is *suspected* of: they never mark a
document personal, and never touch one a person has decided about (a
person's decision, either way, is ``meta.sensitivity.by == "human"``). A
person confirms or releases a suspicion on the Review page.

Three kinds of cue, looked for in the title, the paths the document came
from and the start of its text (``head`` characters: a statement says
what it is at the top, and a book that mentions a rental contract on page
300 is not one):

- a **strong** cue marks a document alone: a word such as "Kontoauszug" or
  "payslip";
- a **weak** cue counts only with another one: an invoice word, the
  owner's name, a licence key, an identity document, an IBAN whose check
  digits add up. "Rechnung" alone is a paper on calculus; "Rechnung" and
  the owner's name is a bill. An IBAN alone is a donation box on a web
  page, and "Personalausweis" alone an exam leaflet;
- a **path** marks every document that came from under it.

The words below are the defaults, in English and German. ``private:`` in
prax.yaml adds to them, and is the only place the owner's names and
private paths are written: they never enter the repository::

    private:
      names: [Jane Example]        # a weak cue each
      paths: [/volume1/admin/]     # everything under these
      strong: [Nebenkostenabrechnung]
      weak: [Kaution]
      weak_needed: 2
      head: 5000

A cue is a stem, matched from the start of a word and without regard to
case: "kontoauszug" finds "Kontoauszüge" and "Kontoauszug_2019.pdf".
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from prax import config

STRONG = (
    "kontoauszug",
    "bank statement",
    "account statement",
    "mietvertrag",
    "rental agreement",
    "tenancy agreement",
    "lease agreement",
    "steuerbescheid",
    "einkommensteuer",
    "einkommenssteuer",
    "einkommensteuerbescheid",
    "steuererklärung",
    "tax return",
    "tax assessment",
    "lohnabrechnung",
    "gehaltsabrechnung",
    "entgeltabrechnung",
    "payslip",
    "pay slip",
    "arbeitsvertrag",
    "employment contract",
    "arbeitszeugnis",
    "sozialversicherungsnummer",
    "steueridentifikationsnummer",
    "arztbrief",
    "befundbericht",
    "arbeitsunfähigkeitsbescheinigung",
)
# a weak cue is a group of words that say one thing, in either language:
# "Rechnung" and "invoice" on one bill are one cue, not two
WEAK: tuple[tuple[str, ...], ...] = (
    ("rechnung", "invoice", "quittung", "receipt"),
    ("vertrag", "contract"),
    ("versicherung", "insurance", "krankenkasse"),
    ("finanzamt",),
    ("kontonummer", "account number", "bankverbindung"),
    ("kündigung",),
    ("mahnung",),
    ("bewerbung", "lebenslauf", "curriculum vitae"),
    ("zeugnis",),
    ("vollmacht",),
    # an identity document: an exam leaflet says to bring one, a form asks for it
    ("personalausweis", "reisepass", "passport number"),
    (
        "license key",
        "licence key",
        "lizenzschlüssel",
        "product key",
        "registration code",
    ),
)
WEAK_NEEDED = 2
HEAD = 5000

# an IBAN as printed: two letters, two check digits, then up to thirty
# letters and digits, in groups of four or not
_IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?)\b")
_SEPARATORS = re.compile(r"[_\-./\\]+")


def iban_ok(candidate: str) -> bool:
    """Whether the check digits add up (ISO 13616: mod 97 is 1)."""
    s = candidate.replace(" ", "").upper()
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    digits = "".join(str(int(ch, 36)) for ch in moved)
    return int(digits) % 97 == 1


@dataclass(frozen=True)
class Rules:
    strong: tuple[str, ...] = STRONG
    weak: tuple[tuple[str, ...], ...] = WEAK
    names: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    weak_needed: int = WEAK_NEEDED
    head: int = HEAD

    def cues(self, title: str, paths: Iterable[str], text: str) -> list[str]:
        """Every cue the document shows, as ``kind: what`` (``strong:
        kontoauszug``, ``weak: iban``, ``weak: rechnung``, ``name``, ``path:
        /admin/``). The owner's names are reported as ``name``, never
        spelled out: the cues are kept on the document and shown."""
        where = [p for p in paths if p]
        haystack = " ".join([title or "", *where, (text or "")[: self.head]])
        flat = _SEPARATORS.sub(" ", haystack).lower()
        found: list[str] = []
        for cue in self.strong:
            if _stem(cue).search(flat):
                found.append(f"strong: {cue}")
        if any(iban_ok(m) for m in _IBAN.findall(haystack.upper())):
            found.append("weak: iban")
        for group in self.weak:
            said = next((cue for cue in group if _stem(cue).search(flat)), None)
            if said:
                found.append(f"weak: {said}")
        # a name is specific enough to be found anywhere, glued into a file
        # name too ("RE1300NiklasMuster.pdf"), with its spaces or without
        squashed = flat.replace(" ", "")
        if any(
            n.lower() in flat or n.lower().replace(" ", "") in squashed
            for n in self.names
        ):
            found.append("name")
        for p in self.paths:
            if any(p.lower() in w.lower() for w in where):
                found.append(f"path: {p}")
        return found

    def stamp(self) -> str:
        """Which rules these are, in a few characters: a document the same
        rules have looked at is not read again by the nightly pass."""
        import hashlib

        said = repr(
            (
                self.strong,
                self.weak,
                self.names,
                self.paths,
                self.weak_needed,
                self.head,
            )
        )
        return hashlib.sha256(said.encode("utf-8")).hexdigest()[:12]

    def suspect(self, cues: list[str]) -> bool:
        """A strong cue or a path alone; weak ones in number."""
        if any(c.startswith(("strong:", "path:")) for c in cues):
            return True
        return sum(1 for c in cues if c.startswith("weak:") or c == "name") >= (
            self.weak_needed
        )


def _stem(cue: str) -> re.Pattern[str]:
    return _pattern(_SEPARATORS.sub(" ", cue).lower())


_PATTERNS: dict[str, re.Pattern[str]] = {}


def _pattern(cue: str) -> re.Pattern[str]:
    got = _PATTERNS.get(cue)
    if got is None:
        got = _PATTERNS[cue] = re.compile(r"(?<!\w)" + re.escape(cue))
    return got


def rules() -> Rules:
    """The defaults with what ``private:`` in prax.yaml adds."""
    return Rules(
        strong=(*STRONG, *config.words("private.strong")),
        weak=(*WEAK, *((w,) for w in config.words("private.weak"))),
        names=tuple(config.words("private.names")),
        paths=tuple(config.words("private.paths")),
        weak_needed=config.whole("private.weak_needed", default=WEAK_NEEDED),
        head=config.whole("private.head", default=HEAD),
    )
