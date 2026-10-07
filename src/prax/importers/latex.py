"""Papers with their LaTeX source: the PDF is the original, the text comes
from the source (``prax import latex``).

A PDF's text layer loses the mathematics: MuPDF reads a display equation
as a scatter of symbols, and marker recovers LaTeX only at the cost of
hours on the card and its own OCR's mistakes. Where the source is at
hand, it says the mathematics exactly. Each manuscript is a folder with
its PDF and the LaTeX that built it; the source is flattened (its
``\\input`` and ``\\include`` files read in), its citations numbered and
its bibliography written out as a References section, its numbered
displays tagged, and pandoc turns it into the Markdown prax indexes: a
display equation alone on its line as ``$$…$$`` (a ``formula`` chunk), a
section a heading, the theorems and proofs as paragraphs.

The door keeps the PDF under its hash and the text as its text artifact,
stamped ``latex-source/<pandoc version>`` (``POST /ingest/file`` with
``text``), so the PDF's pages and figures are served as for any paper and
the parse queue leaves the text alone. Nothing here opens the store: this
runs where the ``prax`` command runs.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SOURCE = "latex"
STAMP = "latex-source"
# what the conversion does, by revision: 2 writes each figure as its
# caption line, where the figure-crops reading puts the picture
REVISION = 2


def stamp(pandoc_version: str) -> str:
    """The text's stamp: ``latex-source/3.9-r2``."""
    return f"{STAMP}/{pandoc_version}-r{REVISION}"


MAX_DEPTH = 12  # \input within \input, at most
PANDOC_SECONDS = 180

_COMMENT = re.compile(r"(?<!\\)%.*")
_INPUT = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}")
_BEGIN_DOCUMENT = re.compile(r"\\begin\{document\}")
_TITLE = re.compile(r"^#\s*\[(?P<title>[^\]]+)\]\((?P<pdf>[^)]+\.pdf)\)", re.MULTILINE)
_DATE = re.compile(
    r"-(January|February|March|April|May|June|July|August|September|October"
    r"|November|December)-(\d{1,2})-(\d{4})$"
)
_DATE_LINE = re.compile(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$")
_AUTHORS = re.compile(r",\s*|\s+and\s+")
_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


@dataclass(frozen=True)
class Manuscript:
    folder: Path
    pdf: Path
    main: Path
    title: str
    date: str | None  # YYYY-MM-DD, from the folder's name
    authors: tuple[str, ...] = ()  # the README's line under the title

    @property
    def key(self) -> str:
        """The folder's name: what a later run knows the manuscript by."""
        return self.folder.name


def manuscripts(root: Path) -> list[Manuscript]:
    """Every folder under ``root`` with one PDF and a LaTeX file that
    holds ``\\begin{document}``, the shallowest first; a folder inside a
    manuscript found already (a figure built on its own) is part of it. A
    folder's README names its PDF, title and authors where it has one
    (``# [Title](paper.pdf)``, the authors on the next line)."""
    out: list[Manuscript] = []
    for pdf in sorted(root.rglob("*.pdf"), key=lambda p: len(p.parts)):
        folder = pdf.parent
        # a figure built on its own inside a manuscript is part of it
        if any(m.folder == folder or m.folder in folder.parents for m in out):
            continue
        found = _manuscript(folder)
        if found is not None:
            out.append(found)
    return out


def _manuscript(folder: Path) -> Manuscript | None:
    title = None
    pdf = None
    authors: tuple[str, ...] = ()
    readme = folder / "README.md"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8", errors="replace")
        m = _TITLE.search(text)
        if m and (folder / m.group("pdf")).is_file():
            title, pdf = m.group("title").strip(), folder / m.group("pdf")
            after = [x.strip() for x in text[m.end() :].splitlines() if x.strip()]
            if after and not after[0].startswith("#"):
                authors = author_line(after[0])
    pdfs = sorted(folder.glob("*.pdf"))
    if pdf is None:
        if len(pdfs) != 1:
            return None
        pdf = pdfs[0]
    main = main_tex(folder)
    if main is None:
        return None
    return Manuscript(
        folder, pdf, main, title or folder.name, folder_date(folder.name), authors
    )


def author_line(line: str) -> tuple[str, ...]:
    """The authors a README's line names: "**Author:** OpenAI", "OpenAI ·
    September 26, 2026", "A. Writer and B. Writer"; a date is not one."""
    line = re.sub(r"[*_]", "", line)
    line = re.sub(r"^\s*authors?\s*:\s*", "", line, flags=re.IGNORECASE)
    line = re.split(r"\s+[·|–—]\s+", line)[0]
    return tuple(
        a.strip()
        for a in _AUTHORS.split(line)
        if a.strip() and not _DATE_LINE.match(a.strip()) and not a.strip().isdigit()
    )


def main_tex(folder: Path) -> Path | None:
    """The file that builds the paper: the one with ``\\begin{document}``,
    a ``main.tex`` or ``paper.tex`` first when there are several."""
    found = [
        p
        for p in sorted(folder.rglob("*.tex"))
        if _BEGIN_DOCUMENT.search(p.read_text(encoding="utf-8", errors="replace"))
    ]
    if not found:
        return None
    for name in ("main.tex", "paper.tex", "article.tex"):
        for p in found:
            if p.name == name:
                return p
    return min(found, key=lambda p: (len(p.parts), p.name))


def folder_date(name: str) -> str | None:
    """``…-September-25-2026`` or ``…-2026-09-25`` as ``2026-09-25``."""
    iso = re.search(r"-(\d{4})-(\d{2})-(\d{2})$", name)
    if iso:
        return "-".join(iso.groups())
    m = _DATE.search(name)
    if not m:
        return None
    month = _MONTHS.index(m.group(1)) + 1
    return f"{m.group(3)}-{month:02d}-{int(m.group(2)):02d}"


# ---------------------------------------------------------------- the source


def strip_comments(tex: str) -> str:
    """Every ``%`` comment gone, an escaped ``\\%`` kept."""
    return "\n".join(_COMMENT.sub("", line) for line in tex.splitlines())


def flatten(main: Path, *, depth: int = 0) -> str:
    """The source with every ``\\input``, ``\\include`` and ``\\subfile``
    read in, relative to the main file's folder (and then to the file's
    own), ``.tex`` added where the name has none; a file not found is left
    out with a comment in its place."""
    text = strip_comments(main.read_text(encoding="utf-8", errors="replace"))
    if depth >= MAX_DEPTH:
        return text
    roots = [main.parent]

    def inline(m: re.Match[str]) -> str:
        name = m.group(1).strip()
        for root in roots:
            for candidate in (root / name, root / f"{name}.tex"):
                if candidate.is_file() and candidate.suffix in (".tex", ""):
                    return flatten(candidate, depth=depth + 1)
        return ""

    return _INPUT.sub(inline, text)


# ------------------------------------------------------------- the references

_THEBIB = re.compile(
    r"\\begin\{thebibliography\}\{[^}]*\}(?P<body>.*?)\\end\{thebibliography\}",
    re.DOTALL,
)
_BIBITEM = re.compile(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
_BIBLIOGRAPHY = re.compile(r"\\bibliography\s*\{([^}]+)\}")
_BIBSTYLE = re.compile(r"\\bibliographystyle\s*\{([^}]+)\}")
_CITE = re.compile(
    r"\\(?:cite|citep|citet|citealp|citealt|parencite|textcite|autocite)\*?"
    r"\s*(?:\[(?P<pre>[^\]]*)\])?\s*(?:\[(?P<post>[^\]]*)\])?\s*\{(?P<keys>[^}]+)\}"
)


def bibliography(tex: str, folder: Path) -> tuple[str, list[tuple[str, str]]]:
    """The source without its bibliography, and the entries it had as
    ``(key, LaTeX of the entry)``, in the order the paper numbers them:
    a ``thebibliography`` as written; a ``.bib`` file by ``plain``'s order
    (authors' surnames, then year) or, for any other style, by first
    citation."""
    m = _THEBIB.search(tex)
    if m:
        parts = _BIBITEM.split(m.group("body"))
        entries = [
            (parts[i].strip(), parts[i + 1].strip())
            for i in range(1, len(parts) - 1, 2)
        ]
        return tex[: m.start()] + tex[m.end() :], entries
    m = _BIBLIOGRAPHY.search(tex)
    if not m:
        return tex, []
    records: dict[str, dict[str, str]] = {}
    for name in m.group(1).split(","):
        path = _find(folder, name.strip(), ".bib")
        if path is not None:
            records.update(bibtex(path.read_text(encoding="utf-8", errors="replace")))
    style = _BIBSTYLE.search(tex)
    cited = [k for c in _CITE.finditer(tex) for k in _keys(c.group("keys"))]
    used = [k for k in dict.fromkeys(cited) if k in records]
    if style and style.group(1).strip() in ("plain", "alpha", "amsplain", "abbrv"):
        used.sort(key=lambda k: _sort_key(records[k]))
    rest = _BIBSTYLE.sub("", _BIBLIOGRAPHY.sub("", tex))
    return rest, [(k, _entry_latex(records[k])) for k in used]


def _find(folder: Path, name: str, suffix: str) -> Path | None:
    for candidate in (folder / name, folder / f"{name}{suffix}"):
        if candidate.is_file():
            return candidate
    hits = sorted(folder.rglob(f"{Path(name).name}{suffix}"))
    return hits[0] if hits else None


_BIB_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.IGNORECASE)


def bibtex(text: str) -> dict[str, dict[str, str]]:
    """A ``.bib`` file's entries by key, each a dict of its fields (lower
    case names, values without their outer braces or quotes)."""
    out: dict[str, dict[str, str]] = {}
    for m in _BIB_ENTRY.finditer(text):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        body = _balanced(text, m.start() + m.group(0).index("{"))
        if body is None:
            continue
        fields = {"type": m.group(1).lower()}
        for name, value in _fields(body[body.find(",") + 1 :]):
            fields[name] = value
        out[m.group(2)] = fields
    return out


def _balanced(text: str, at: int) -> str | None:
    """The text from the brace at ``at`` to its partner, without them."""
    depth = 0
    for j in range(at, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[at + 1 : j]
    return None


_FIELD = re.compile(r"\s*,?\s*(\w+)\s*=\s*")
_FIELD_END = re.compile(r"[,\n]")


def _fields(body: str) -> Iterable[tuple[str, str]]:
    """A bibtex entry's fields: braced, quoted or bare values."""
    i = 0
    while i < len(body):
        m = _FIELD.match(body, i)
        if not m:
            break
        name, j = m.group(1).lower(), m.end()
        if j < len(body) and body[j] == "{":
            value = _balanced(body, j) or ""
            j += len(value) + 2
        elif j < len(body) and body[j] == '"':
            q = body.find('"', j + 1)
            value, j = body[j + 1 : q], q + 1
        else:
            stop = _FIELD_END.search(body, j)
            end = stop.start() if stop else len(body)
            value, j = body[j:end], end
        yield name, " ".join(value.split())
        i = j


def _surnames(authors: str) -> str:
    names = [a.strip() for a in re.split(r"\s+and\s+", authors) if a.strip()]
    return " ".join(
        (n.split(",")[0] if "," in n else n.split()[-1]).strip("{}").lower()
        for n in names
        if n.split()
    )


def _sort_key(record: dict[str, str]) -> tuple[str, str, str]:
    return (
        _surnames(record.get("author") or record.get("editor") or ""),
        record.get("year", ""),
        record.get("title", "").lower(),
    )


def _entry_latex(r: dict[str, str]) -> str:
    """One entry written out the way ``plain`` would: authors, title,
    where, year, and the DOI or URL."""
    authors = ", ".join(
        " ".join(reversed([p.strip() for p in a.split(",", 1)])) if "," in a else a
        for a in re.split(r"\s+and\s+", r.get("author") or r.get("editor") or "")
        if a.strip()
    )
    where = (
        r.get("journal")
        or r.get("booktitle")
        or r.get("howpublished")
        or r.get("publisher")
        or r.get("school")
        or r.get("institution")
        or ""
    )
    volume = f" {r['volume']}" if r.get("volume") else ""
    pages = f", {r['pages']}" if r.get("pages") else ""
    parts = [
        authors,
        f"\\emph{{{r.get('title', '')}}}",
        f"{where}{volume}{pages}".strip(),
        r.get("year", ""),
    ]
    text = ", ".join(p for p in parts if p) + "."
    if r.get("doi"):
        text += f" doi:{r['doi']}"
    elif r.get("eprint"):
        text += f" arXiv:{r['eprint']}"
    elif r.get("url"):
        text += f" \\url{{{r['url']}}}"
    return text


def _keys(keys: str) -> list[str]:
    return [k.strip() for k in keys.split(",") if k.strip()]


def number_citations(tex: str, order: list[str]) -> str:
    """Every ``\\cite{a,b}`` (and its kin) as ``[1, 2]``, by the order of
    the bibliography; a key it does not hold is kept as written. A note
    after the keys (``\\cite[Thm. 2]{a}``) stays beside the number."""
    number = {k: i for i, k in enumerate(order, 1)}

    def cite(m: re.Match[str]) -> str:
        keys = [str(number.get(k, k)) for k in _keys(m.group("keys"))]
        note = m.group("post") if m.group("post") is not None else m.group("pre")
        inside = ", ".join(keys) + (f", {note}" if note else "")
        # in braces: a citation inside an optional argument,
        # \begin{theorem}[Smith \cite{a}], would end it at its own "]"
        return f"{{[{inside}]}}"

    return _CITE.sub(cite, tex)


def references_section(entries: list[tuple[str, str]]) -> str:
    """The bibliography as a References section prax's reference parser
    reads: a heading, then one paragraph an entry, ``[n]`` first."""
    if not entries:
        return ""
    lines = ["\\section*{References}", ""]
    for i, (_key, text) in enumerate(entries, 1):
        lines += [f"[{i}] {text}", ""]
    return "\n".join(lines)


# --------------------------------------------------------- numbered displays

_NUMBERED = ("equation", "align", "gather", "multline", "eqnarray", "flalign")
_ENV = re.compile(
    r"\\begin\{(?P<env>" + "|".join(_NUMBERED) + r")\}(?P<body>.*?)\\end\{(?P=env)\}",
    re.DOTALL,
)
_LABEL = re.compile(r"\\label\{([^}]+)\}")
_NONUMBER = re.compile(r"\\(?:nonumber|notag)\b")
_REF = re.compile(r"\\(eqref|ref|autoref|cref|Cref)\{([^}]+)\}")


def number_displays(tex: str) -> str:
    """Each numbered display tagged with the number the PDF gives it
    (``\\tag{n}``, counted in order through the paper, one a line of an
    ``align``), and every ``\\eqref`` to it written as ``(n)``: what lets
    a passage that says "by (4)" find the formula."""
    count = 0
    numbers: dict[str, str] = {}

    def env(m: re.Match[str]) -> str:
        nonlocal count
        name, body = m.group("env"), m.group("body")
        rows = (
            body.split("\\\\") if name != "equation" and name != "multline" else [body]
        )
        out = []
        for row in rows:
            if _NONUMBER.search(row) or "\\tag" in row or not row.strip():
                out.append(_NONUMBER.sub("", row))
                continue
            count += 1
            for label in _LABEL.findall(row):
                numbers[label] = str(count)
            out.append(_LABEL.sub("", row).rstrip() + f" \\tag{{{count}}}")
        return f"\\begin{{{name}*}}" + "\\\\".join(out) + f"\\end{{{name}*}}"

    tex = _ENV.sub(env, tex)

    def ref(m: re.Match[str]) -> str:
        label = m.group(2)
        if label not in numbers:
            return m.group(0)
        return f"({numbers[label]})" if m.group(1) == "eqref" else numbers[label]

    return _REF.sub(ref, tex)


# ------------------------------------------------------------------ pandoc


def pandoc() -> str | None:
    """The pandoc to run: one on the PATH, else the one ``pypandoc_binary``
    brings (``pip install prax[latex]``); None without either."""
    found = shutil.which("pandoc")
    if found:
        return found
    try:
        import pypandoc  # type: ignore[import-not-found,import-untyped,unused-ignore]

        return str(pypandoc.get_pandoc_path())
    except (ImportError, OSError):
        return None


def pandoc_version(binary: str) -> str:
    out = subprocess.run(
        [binary, "--version"], capture_output=True, text=True, timeout=30, check=False
    ).stdout
    m = re.search(r"pandoc(?:\.exe)?\s+([\d.]+)", out)
    return m.group(1) if m else "unknown"


def to_markdown(tex: str, binary: str, *, cwd: Path) -> str:
    """Pandoc's Markdown of a flattened source: mathematics in dollars, no
    raw HTML, no attributes, lines unwrapped."""
    done = subprocess.run(
        [
            binary,
            "-f",
            "latex",
            "-t",
            "markdown_strict+tex_math_dollars+pipe_tables+backtick_code_blocks",
            "--wrap=none",
        ],
        input=tex,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        timeout=PANDOC_SECONDS,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"pandoc: {done.stderr.strip()[:300]}")
    return done.stdout


_DISPLAY = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_ANCHOR = re.compile(r"\[([^\]]*)\]\(#[^)]*\)")
_HTML = re.compile(r"</?(?:div|span|a)\b[^>]*>")
_TAG = re.compile(r"\s*\\tag\{([^{}]*)\}")
_SINGLE = re.compile(r"\\begin\{equation\*\}(.*)\\end\{equation\*\}")
# a citation pandoc escaped, "\[3, 7\]": the brackets are text, not maths
_CITED = re.compile(r"\\\[(\d[^\]\\$]*)\\\]")


def tidy(markdown: str) -> str:
    """Pandoc's Markdown as prax reads it: each display equation one line
    of its own, ``$$…$$``, without the labels or the starred environment a
    single equation was wrapped in, and its number at its end (a ``\\tag``
    inside ``aligned`` is moved out, which KaTeX asks for, and several
    rows' numbers are joined, ``\\tag{4–6}``); citations ``[3, 7]``
    unescaped; links to an anchor in the paper as their text; what HTML is
    left, gone."""

    def display(m: re.Match[str]) -> str:
        latex = " ".join(m.group(1).split())
        latex = _LABEL.sub("", latex)
        single = _SINGLE.fullmatch(latex)
        if single:
            latex = single.group(1).strip()
        tags = _TAG.findall(latex)
        if tags:
            latex = _TAG.sub("", latex).rstrip()
            number = tags[0] if len(tags) == 1 else f"{tags[0]}–{tags[-1]}"
            latex += f" \\tag{{{number}}}"
        return f"\n\n$${latex}$$\n\n"

    text = _DISPLAY.sub(display, markdown)
    text = _CITED.sub(r"[\1]", text)
    text = _ANCHOR.sub(r"\1", text)
    text = _HTML.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


_TYPED_LABEL = re.compile(r"\\label\s*\[[^\]]*\]\s*\{")
# a wrapper that stitches PDFs built on their own: "\includepdf[…]{part-1.pdf}"
_INCLUDEPDF = re.compile(
    r"\\includepdf\b[^\n]*\{([^{}]+?)(?:\.pdf)?\}\s*$", re.MULTILINE
)


def parts(main: Path) -> list[Path]:
    """The sources of the PDFs a wrapper includes (``\\includepdf``), in
    order, where each is there as ``.tex``; empty for a paper that is its
    own source."""
    found = []
    for name in _INCLUDEPDF.findall(flatten(main)):
        tex = main.parent / f"{name}.tex"
        if tex.is_file():
            found.append(tex)
    return found


def convert(ms: Manuscript, binary: str) -> str:
    """A manuscript's text, from its source: a wrapper's parts one after
    the other."""
    pieces = parts(ms.main)
    if pieces:
        return "\n".join(
            _convert_one(Manuscript(ms.folder, ms.pdf, p, ms.title, ms.date), binary)
            for p in pieces
        )
    return _convert_one(ms, binary)


_FIGURE = re.compile(r"\\begin\{figure\*?\}(?P<body>.*?)\\end\{figure\*?\}", re.DOTALL)
_CAPTION_CMD = re.compile(r"\\caption\s*(?:\[[^\]]*\])?\s*(?=\{)")
_SECTION = re.compile(r"\\section\s*(?=\{)|\\appendix\b")
_BY_SECTION = re.compile(r"\\numberwithin\s*\{figure\}\s*\{section\}")


def figure_captions(tex: str) -> str:
    """Each figure environment as its caption line, ``Figure N: …``, the
    number the PDF prints (in order, or by section where the paper says
    ``\\numberwithin{figure}{section}``), and every ``\\ref`` to it as N.
    The drawing itself (TikZ, an included graphic) is not text and goes;
    the caption line is where the ``figure-crops`` reading puts the
    picture it cuts from the PDF."""
    by_section = bool(_BY_SECTION.search(tex))
    marks = [(m.start(), m.group(0)) for m in _SECTION.finditer(tex)]
    numbers: dict[str, str] = {}
    count = 0
    last_section = ""

    def section_at(pos: int) -> str:
        n, appendix = 0, False
        for at, what in marks:
            if at > pos:
                break
            if what.startswith("\\appendix"):
                appendix, n = True, 0
            else:
                n += 1
        if appendix and n:
            return chr(ord("A") + n - 1)
        return str(n)

    def figure(m: re.Match[str]) -> str:
        nonlocal count, last_section
        body = m.group("body")
        if by_section:
            section = section_at(m.start())
            if section != last_section:
                count, last_section = 0, section
            count += 1
            number = f"{section}.{count}"
        else:
            count += 1
            number = str(count)
        for label in _LABEL.findall(body):
            numbers[label] = number
        cap = _CAPTION_CMD.search(body)
        caption = ""
        if cap:
            caption = _balanced(body, cap.end()) or ""
        return f"\n\n\\noindent Figure {number}: {_LABEL.sub('', caption)}\n\n"

    tex = _FIGURE.sub(figure, tex)

    def ref(m: re.Match[str]) -> str:
        return numbers.get(m.group(2), m.group(0))

    return _REF.sub(ref, tex)


def _convert_one(ms: Manuscript, binary: str) -> str:
    # cleveref's \label[lemma]{x}, which pandoc does not read
    tex = _TYPED_LABEL.sub(r"\\label{", flatten(ms.main))
    tex, entries = bibliography(tex, ms.main.parent)
    tex = number_citations(tex, [k for k, _ in entries])
    tex = number_displays(tex)
    tex = figure_captions(tex)
    section = references_section(entries)
    if section:
        if "\\end{document}" in tex:
            tex = tex.replace("\\end{document}", section + "\n\\end{document}", 1)
        else:
            tex += "\n" + section
    return tidy(to_markdown(tex, binary, cwd=ms.main.parent))
