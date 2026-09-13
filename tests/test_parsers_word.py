"""Word documents: .docx read here (a zip of XML, no dependency), and the
older formats through LibreOffice when a machine has it."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from prax import parsers

BODY = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>
      <w:r><w:t>Ökonomie der Wissenschaft</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="berschrift2"/></w:pPr>
      <w:r><w:t>Erster Teil</w:t></w:r></w:p>
    <w:p><w:r><w:t xml:space="preserve">A paragraph in </w:t></w:r>
      <w:r><w:t>two runs.</w:t></w:r></w:p>
    <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr>
      <w:r><w:t>first item</w:t></w:r></w:p>
    <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr>
      <w:r><w:t>second item</w:t></w:r></w:p>
    <w:p/>
    <w:p><w:pPr><w:outlineLvl w:val="2"/></w:pPr>
      <w:r><w:t>By outline level</w:t></w:r></w:p>
    <w:tbl>
      <w:tr><w:tc><w:p><w:r><w:t>part</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>mm</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>bolt</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>12</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
    <w:sdt><w:sdtContent>
      <w:p><w:r><w:t>inside a content control</w:t></w:r></w:p>
    </w:sdtContent></w:sdt>
  </w:body>
</w:document>
"""


def docx_bytes(document_xml: str = BODY) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("[Content_Types].xml", "<Types/>")
    return buffer.getvalue()


def test_docx_becomes_markdown() -> None:
    text = parsers.by_name("docx")(docx_bytes())
    assert text.startswith("# Ökonomie der Wissenschaft")
    assert "## Erster Teil" in text  # a German heading style
    assert "### By outline level" in text  # the outline level wins
    assert "A paragraph in two runs." in text
    assert "- first item\n- second item" in text  # one list, not two paragraphs
    assert "| part | mm |\n|---|---|\n| bolt | 12 |" in text
    assert "inside a content control" in text


def test_a_file_that_is_not_a_word_document_says_so() -> None:
    with pytest.raises(ValueError, match="not a Word document"):
        parsers.by_name("docx")(b"PK\x03\x04 nonsense")


def test_the_office_types_are_known_even_where_python_is_not_told() -> None:
    assert parsers.guess_mime("notes.docx") == parsers.DOCX_MIME
    assert parsers.guess_mime("old.doc") == "application/msword"
    assert parsers.guess_mime("open.odt").endswith("opendocument.text")
    assert parsers.guess_mime("mystery.zzz") == "application/octet-stream"


def test_a_docx_upload_is_picked_up_by_the_docx_extractor() -> None:
    found = parsers.candidates(parsers.guess_mime("notes.docx"))
    assert [e.name for e in found] == ["docx"]


needs_libreoffice = pytest.mark.skipif(
    parsers.soffice_path() is None, reason="LibreOffice is not installed"
)


def test_an_rtf_is_read_without_libreoffice() -> None:
    rtf = (
        rb"{\rtf1\ansi{\fonttbl\f0 Times;}\f0 Plain words from an old format."
        rb"\par Second paragraph.\par}"
    )
    text = parsers.by_name("rtf")(rtf)
    assert "Plain words from an old format." in text and "Second paragraph." in text
    assert parsers.candidates("application/rtf")[0].name == "rtf"
    with pytest.raises(ValueError, match="not an RTF"):
        parsers.by_name("rtf")(b"plain text")


def _odt(content_xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        zf.writestr("content.xml", content_xml)
    return buf.getvalue()


ODT = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
  xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">
 <office:body><office:text>
  <text:h text:outline-level="1">A Title</text:h>
  <text:p>First<text:s text:c="2"/>paragraph with a
   <text:span>span</text:span>.</text:p>
  <text:h text:outline-level="2">Parts</text:h>
  <text:list><text:list-item><text:p>bolt</text:p></text:list-item>
   <text:list-item><text:p>nut</text:p></text:list-item></text:list>
  <table:table><table:table-row><table:table-cell><text:p>part</text:p></table:table-cell>
   <table:table-cell><text:p>mm</text:p></table:table-cell></table:table-row>
   <table:table-row><table:table-cell><text:p>bolt</text:p></table:table-cell>
   <table:table-cell><text:p>12</text:p></table:table-cell></table:table-row></table:table>
  <text:p>Last<text:line-break/>line.</text:p>
 </office:text></office:body></office:document-content>
"""


def test_an_odt_becomes_markdown_without_libreoffice() -> None:
    text = parsers.by_name("odt")(_odt(ODT))
    head = "# A Title\n\nFirst  paragraph with a span.\n\n## Parts\n\n- bolt\n- nut\n\n"
    assert text.startswith(head)
    assert "| part | mm |\n|---|---|\n| bolt | 12 |" in text
    assert text.endswith("Last\nline.")
    assert (
        parsers.candidates("application/vnd.oasis.opendocument.text")[0].name == "odt"
    )
    with pytest.raises(ValueError, match="not an OpenDocument"):
        parsers.by_name("odt")(b"nope")


@needs_libreoffice
def test_an_old_doc_goes_through_libreoffice(tmp_path: Path) -> None:
    # RTF bytes under a .doc name: LibreOffice reads the content, not the suffix
    rtf = rb"{\rtf1\ansi Plain words from an old format.\par}"
    text = parsers.by_name("office")(rtf, filename="old.doc")
    assert "Plain words from an old format." in text


def test_office_without_libreoffice_is_simply_not_offered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parsers, "soffice_path", lambda: None)
    assert parsers.candidates("application/msword") == []
    with pytest.raises(RuntimeError, match="LibreOffice is not installed"):
        parsers.by_name("office")(b"anything", filename="x.doc")
    # the formats that never needed it are still offered
    assert parsers.candidates("application/rtf")[0].name == "rtf"
    assert (
        parsers.candidates("application/vnd.oasis.opendocument.text")[0].name == "odt"
    )
