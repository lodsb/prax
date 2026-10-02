"""Office documents: Word's .docx and OpenDocument's .odt read from
their XML here, RTF through striprtf, and the rest converted by
LibreOffice.
"""

from __future__ import annotations

import codecs
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

# ------------------------------------------------------------ Word documents
# A .docx is a zip of XML and needs no dependency: word/document.xml holds the
# body in document order. Old .doc files, RTF and OpenDocument go through
# LibreOffice, which a person installs for themselves; prax only calls it.

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
OFFICE_TIMEOUT = 180.0
# Heading styles as Word writes them in the languages this library holds; the
# outline level below is checked first and is language-independent.
_HEADING = re.compile(
    r"^(heading|berschrift|überschrift|titre|titolo|encabezado|rubrik|kop)(\d)$"
)


def _docx_runs(paragraph: Any) -> str:
    """The text of one paragraph: runs joined, tabs and breaks kept. Field
    codes and tracked deletions carry their own tags and are left out."""
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{W}t":
            parts.append(node.text or "")
        elif node.tag == f"{W}tab":
            parts.append("\t")
        elif node.tag in (f"{W}br", f"{W}cr"):
            parts.append("\n")
    return "".join(parts).strip()


def _docx_heading(paragraph: Any) -> int:
    """The heading level of a paragraph, 0 for body text."""
    properties = paragraph.find(f"{W}pPr")
    if properties is None:
        return 0
    outline = properties.find(f"{W}outlineLvl")
    if outline is not None:
        try:
            return min(int(outline.get(f"{W}val", "")) + 1, 6)
        except ValueError:
            pass
    style = properties.find(f"{W}pStyle")
    name = ((style.get(f"{W}val") if style is not None else "") or "").lower()
    flat = re.sub(r"[\s_-]", "", name)
    if flat == "title":
        return 1
    if flat == "subtitle":
        return 2
    found = _HEADING.match(flat)
    return min(int(found.group(2)), 6) if found else 0


def _docx_table(table: Any) -> str:
    """A table as Markdown, which is what the chunker reads as a table."""
    rows: list[list[str]] = []
    for row in table.findall(f"{W}tr"):
        cells = []
        for cell in row.findall(f"{W}tc"):
            texts = [t for t in (_docx_runs(p) for p in cell.findall(f"{W}p")) if t]
            cells.append(" ".join(texts).replace("|", r"\|"))
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, *rest = rows
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * width) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rest]
    return "\n".join(lines)


def _docx_blocks(parent: Any) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for child in parent:
        if child.tag == f"{W}p":
            text = _docx_runs(child)
            if not text:
                continue
            level = _docx_heading(child)
            if level:
                blocks.append(("h", "#" * level + " " + text))
            elif child.find(f"{W}pPr/{W}numPr") is not None:
                blocks.append(("li", "- " + text))
            else:
                blocks.append(("p", text))
        elif child.tag == f"{W}tbl":
            table = _docx_table(child)
            if table:
                blocks.append(("table", table))
        elif child.tag == f"{W}sdt":  # a content control wraps real content
            content = child.find(f"{W}sdtContent")
            if content is not None:
                blocks.extend(_docx_blocks(content))
    return blocks


ZIPPED_XML_MAX = 256 * 1024 * 1024  # an office file's XML inflates far past its zip


def _zipped_xml(archive: Any, member: str) -> bytes:
    """One XML member of an office file, refused before it is inflated when
    the zip's directory says it is larger than ``ZIPPED_XML_MAX``: a
    document that is small on disk can be gigabytes of XML inside."""
    info = archive.getinfo(member)
    if info.file_size > ZIPPED_XML_MAX:
        raise ValueError(
            f"{member} inflates to {info.file_size >> 20} MB, over the"
            f" {ZIPPED_XML_MAX >> 20} MB the parser reads"
        )
    got: bytes = archive.read(member)
    return got


def _docx(data: bytes) -> str:
    """A Word document as Markdown: headings by outline level or style name,
    numbered and bulleted paragraphs as list items, tables as Markdown
    tables, the rest as paragraphs."""
    import io
    import zipfile
    from xml.etree import ElementTree

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = _zipped_xml(archive, "word/document.xml")
    except KeyError as exc:  # a zip, but not a Word one
        raise ValueError("not a Word document (no word/document.xml)") from exc
    except zipfile.BadZipFile as exc:  # .doc and .rtf land here: the office
        raise ValueError(  # extractor converts those first
            "not a Word document (not a zip: an old .doc or .rtf goes"
            " through the office extractor)"
        ) from exc
    body = ElementTree.fromstring(xml).find(f"{W}body")
    if body is None:
        return ""
    return _join_blocks(_docx_blocks(body))


def _join_blocks(blocks: list[tuple[str, str]]) -> str:
    """Blocks as Markdown: a blank line between them, list items adjacent."""
    lines: list[str] = []
    for i, (kind, text) in enumerate(blocks):
        if i and not (kind == "li" and blocks[i - 1][0] == "li"):
            lines.append("")
        lines.append(text)
    return "\n".join(lines).strip()


# ---- OpenDocument text: the same idea as .docx, other namespaces
_ODT_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_ODT_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_ODT_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"


def _odt_runs(node: Any) -> str:
    """The text of one paragraph or heading: spans flattened, ``text:s``
    (n spaces), tabs and line breaks kept. Whitespace inside a text node
    collapses to one space, as ODF reads it; the explicit elements are the
    only way to say more."""
    parts: list[str] = []
    if node.text:
        parts.append(re.sub(r"\s+", " ", node.text))
    for child in node:
        tag = child.tag
        if tag == f"{_ODT_TEXT}s":
            parts.append(" " * int(child.get(f"{_ODT_TEXT}c", "1") or 1))
        elif tag == f"{_ODT_TEXT}tab":
            parts.append("\t")
        elif tag == f"{_ODT_TEXT}line-break":
            parts.append("\n")
        elif tag == f"{_ODT_TEXT}note":  # a footnote: its body, not its number
            body = child.find(f"{_ODT_TEXT}note-body")
            if body is not None:
                parts.append(" (" + " ".join(_odt_runs(p) for p in body) + ")")
        else:
            parts.append(_odt_runs(child))
        if child.tail:
            parts.append(re.sub(r"\s+", " ", child.tail))
    return "".join(parts).strip()


def _odt_blocks(parent: Any) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for child in parent:
        tag = child.tag
        if tag == f"{_ODT_TEXT}h":
            text = _odt_runs(child)
            if text:
                level = min(int(child.get(f"{_ODT_TEXT}outline-level", "1") or 1), 6)
                blocks.append(("h", "#" * level + " " + text))
        elif tag == f"{_ODT_TEXT}p":
            text = _odt_runs(child)
            if text:
                blocks.append(("p", text))
        elif tag == f"{_ODT_TEXT}list":
            for item in child.iter(f"{_ODT_TEXT}list-item"):
                text = " ".join(
                    t
                    for t in (_odt_runs(p) for p in item.findall(f"{_ODT_TEXT}p"))
                    if t
                )
                if text:
                    blocks.append(("li", "- " + text))
        elif tag == f"{_ODT_TABLE}table":
            rows: list[list[str]] = []
            for row in child.iter(f"{_ODT_TABLE}table-row"):
                cells = [
                    " ".join(_odt_runs(p) for p in cell.iter(f"{_ODT_TEXT}p")).replace(
                        "|", r"\|"
                    )
                    for cell in row.findall(f"{_ODT_TABLE}table-cell")
                ]
                if any(cells):
                    rows.append(cells)
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                head, *rest = rows
                table = [
                    "| " + " | ".join(head) + " |",
                    "|" + "|".join(["---"] * width) + "|",
                ]
                table += ["| " + " | ".join(r) + " |" for r in rest]
                blocks.append(("table", "\n".join(table)))
        elif tag in (f"{_ODT_TEXT}section", f"{_ODT_OFFICE}text"):
            blocks.extend(_odt_blocks(child))
    return blocks


def _odt(data: bytes) -> str:
    """An OpenDocument text as Markdown: headings by outline level, lists,
    tables, paragraphs; ``content.xml`` in the zip, nothing installed."""
    import io
    import zipfile
    from xml.etree import ElementTree

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = _zipped_xml(archive, "content.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ValueError("not an OpenDocument text (no content.xml)") from exc
    body = ElementTree.fromstring(xml).find(f"{_ODT_OFFICE}body")
    if body is None:
        return ""
    return _join_blocks(_odt_blocks(body))


# A Mac's RTF names the character set of its Japanese, Chinese, Korean,
# Hebrew or Thai fonts by the Mac's own name, and striprtf asks Python for a
# codec of that name, which Python has not: 24 files of a copied Mac failed
# whole on "unknown encoding: mac_japanese" (2026-09-28). Each is close kin
# of a codec Python has.
_MAC_CODECS = {
    "mac_japanese": "shift_jis",
    "mac_chinesetrad": "big5",
    "mac_korean": "euc_kr",
    "mac_hebrew": "iso8859_8",
    "mac_chinesesimp": "gb2312",
    "mac_rumanian": "mac_romanian",
    "mac_ukrainian": "mac_cyrillic",
    "mac_thai": "cp874",
}


def _mac_codec(name: str) -> codecs.CodecInfo | None:
    alias = _MAC_CODECS.get(name.lower().replace("-", "_"))
    return codecs.lookup(alias) if alias else None


_MAC_CODECS_REGISTERED: list[bool] = []


def _rtf(data: bytes) -> str:
    """Rich Text as plain text: striprtf reads the control words, keeps the
    words; RTF carries little structure worth a heading."""
    from striprtf.striprtf import rtf_to_text

    if not _MAC_CODECS_REGISTERED:
        codecs.register(_mac_codec)
        _MAC_CODECS_REGISTERED.append(True)
    text = data.decode("cp1252", "replace")
    if not text.lstrip().startswith("{\\rtf"):
        raise ValueError("not an RTF file")
    out = rtf_to_text(text, errors="ignore")
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def soffice_path() -> str | None:
    """LibreOffice's binary, when this machine has one."""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for guess in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/local/bin/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if Path(guess).exists():
            return guess
    return None


def _office(data: bytes, *, filename: str | None = None) -> str:
    """An old binary .doc: LibreOffice converts it to .docx in a scratch
    directory with a profile of its own (so it never clashes with the
    person's running LibreOffice) and ``_docx`` reads that. Its output goes
    to a file, not a pipe: soffice.exe hands the work to a child that keeps
    a pipe open past the conversion, and a pipe would make the wait outlive
    the work."""
    import tempfile

    soffice = soffice_path()
    if soffice is None:
        raise RuntimeError(
            "LibreOffice is not installed: it is what converts an old .doc"
            " (libreoffice.org; prax only calls it)"
        )
    suffix = Path(filename or "").suffix.lower() or ".doc"
    with tempfile.TemporaryDirectory(prefix="prax-office-") as tmp:
        work = Path(tmp)
        source = work / f"input{suffix}"
        source.write_bytes(data)
        said_path = work / "soffice.log"
        with said_path.open("wb") as said_file:
            subprocess.run(
                [
                    soffice,
                    f"-env:UserInstallation=file:///{(work / 'profile').as_posix()}",
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    str(work),
                    str(source),
                ],
                stdin=subprocess.DEVNULL,
                stdout=said_file,
                stderr=subprocess.STDOUT,
                timeout=OFFICE_TIMEOUT,
                check=False,
            )
        converted = work / "input.docx"
        if not converted.exists():
            said = said_path.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(
                f"LibreOffice did not convert the file: {said.strip()[:200]}"
            )
        return _docx(converted.read_bytes())
