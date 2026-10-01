"""scripts/check_private.py, the guard against the owner's private data in
a commit: invented values only, never the owner's."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_private.py"


@pytest.fixture()
def guard():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("check_private", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_finds_a_value_of_a_personal_document_and_names_no_value(
    guard, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "prax.yaml").write_text(
        "private:\n  paths: [/Belege/Steuer]\n", encoding="utf-8"
    )
    text = (
        "Rechnung\nIBAN DE02 1203 0000 0000 2020 51\n"
        "Erika Quendel, Zinnoberweg 731, 99481 Quarzlingen\n"
        "Tel. 030 1234 5678, erika@example.org\n"
    )
    h = "ab" + "0" * 62
    (tmp_path / "archive" / "ab").mkdir(parents=True)
    (tmp_path / "archive" / "ab" / h).write_text(text, encoding="utf-8")
    con = sqlite3.connect(tmp_path / "prax.db")
    con.execute("CREATE TABLE documents (text_hash TEXT, sensitivity TEXT)")
    con.execute("INSERT INTO documents VALUES (?, 'personal')", (h,))
    con.commit()
    con.close()
    values = guard.private_values(tmp_path)
    assert values["iban"] == {"DE02120300000000202051"}
    assert "Zinnoberweg 731" in values["street"]
    lines = [
        ("a.md", 1, "pay to DE02120300000000202051"),
        ("b.md", 4, "ships to Zinnoberweg 731, door 2"),
        ("c.js", 9, 'paths: ["/belege/steuer/"]'),
        ("d.md", 2, "an invented DE89 3704 0044 0532 0130 00"),
        ("e.md", 3, "mail erika@example.org"),
    ]
    found = guard.findings(lines, values)
    assert found == [
        ("a.md", 1, "iban"),
        ("b.md", 4, "street"),
        ("c.js", 9, "private path"),
        ("e.md", 3, "email"),
    ]
    assert all("DE02" not in str(f) for f in found)  # the report holds no value
