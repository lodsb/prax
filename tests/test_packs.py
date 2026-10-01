"""Packs (docs/packs.md): every domain's knowledge in one package, its
capability for the hosts that name it."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from prax import packs, steps, store
from prax.graph import ontology
from prax.packs.base import Pack

PACKS_DIR = Path(packs.__file__).parent


def test_the_knowledge_of_every_pack_is_composed() -> None:
    """The eight modules that moved into packs on 2026-10-01 compose to the
    version string they had in one folder; a golden run compared the whole
    ontology, the sameness rule and the lexicon before and after."""
    assert ontology.current().version == (
        "core3+computing2+craft1+electronics1+kitchen2+research9+society1+studio5+workshop2"
    )
    files = ontology.module_files()
    assert files["research"].parent == PACKS_DIR / "research"
    assert files["core"].parent.name == "ontology"


def test_the_sameness_cases_keep_their_order() -> None:
    """A module's cases follow the general ones in the order they stood in
    one file, so the rule a calibrated judge reads did not move."""
    names = [name for name, _, _ in ontology.sameness().modules]
    assert names == ["kitchen", "studio", "electronics"]


def test_a_manifest_imports_nothing_but_the_pack_type() -> None:
    for init in sorted(PACKS_DIR.glob("*/__init__.py")):
        tree = ast.parse(init.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                pytest.fail(f"{init}: imports {[a.name for a in node.names]}")
            if isinstance(node, ast.ImportFrom) and node.module not in (
                "__future__",
                "prax.packs.base",
            ):
                pytest.fail(f"{init}: imports from {node.module}")


def test_no_pack_opens_a_database() -> None:
    """A pack keeps what it needs in a chunk's data or a document's meta,
    through the store's functions (invariant 3)."""
    for py in sorted(PACKS_DIR.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "connect":
                pytest.fail(f"{py}: calls connect")


def test_no_pack_repeats_a_name() -> None:
    assert packs.duplicates({"steps": steps.CORE_STEPS}) == []


@pytest.mark.parametrize("name", [p.name for p in packs.PACKS if p.ontology])
def test_a_packs_suggested_rules_are_rules(name: str) -> None:
    path = packs.rules_file(name)
    assert path is not None and path.exists()
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert rules
    own = {Path(f).stem for f in packs.by_name(name).ontology}
    for rule in rules:
        store.documents.domains._check_rule(rule)
        assert own & set(rule["domains"]), rule  # it sends documents to the pack


def test_a_packs_capability_reaches_the_registries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a fake pack: its names reach the lookups the core's lists
    read, and its code is imported only for a host that runs it."""
    (tmp_path / "fakepack_code.py").write_text(
        "EXTRACTORS = ['an extractor']\n"
        "def tool(con, s, arg):\n    return 'answered ' + arg\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    fake = Pack(
        name="fake",
        extractors=("fakepack_code:EXTRACTORS",),
        readings={"sympy": "formulas"},
        kinds=("score",),
        aside=("score",),
        steps={"scan": "fakepack_code"},
        watched=("scan",),
        tools={"math": "fakepack_code:tool"},
        tool_help={"math": "math: <x>   the fake tool"},
        settings="fake",
    )
    monkeypatch.setattr(packs, "PACKS", (*packs.PACKS, fake))
    assert packs.step_homes()["scan"] == "fakepack_code"
    assert packs.watched() == ("scan",)
    assert packs.readings() == {"sympy": "formulas"}
    assert packs.kinds() == ("score",) and packs.aside() == ("score",)
    assert packs.setting_sections()[-1] == "fake"
    assert "fakepack_code" not in sys.modules  # names only, so far
    assert packs.extractors([]) == [] and packs.tools([]) == {}
    assert packs.extractors(["fake"]) == ["an extractor"]
    assert packs.tools(["fake"])["math"](None, None, "x") == "answered x"
    assert packs.tool_help(["fake"]) == ["math: <x>   the fake tool"]
    with pytest.raises(KeyError, match="no pack named"):
        packs.running(["nope"])
    twice = Pack(name="twice", steps={"parse": "elsewhere"})
    monkeypatch.setattr(packs, "PACKS", (*packs.PACKS, twice))
    assert packs.duplicates({"steps": steps.CORE_STEPS}) == ["twice: steps 'parse'"]


def test_a_pack_step_is_found_by_its_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """``steps.get`` imports a pack's step by the module's full name."""
    import importlib

    module = importlib.import_module("prax.steps.parse")
    monkeypatch.setattr(steps, "STEPS", (*steps.STEPS, "parse-again"))
    monkeypatch.setitem(steps._HOMES, "parse-again", "prax.steps.parse")
    monkeypatch.setitem(module.REGISTERED, "parse-again", "the step")
    assert steps.get("parse-again") == "the step"


def test_a_packs_suggested_rules_are_tried_by_name(client: TestClient) -> None:
    """``POST /domains/dry-run`` with ``pack``: the rules the pack suggests,
    counted and written nowhere; an unknown pack is a 404."""
    got = client.post("/domains/dry-run", json={"pack": "craft"}).json()
    assert [r["domains"] for r in got["rules"]] == [["kitchen"], ["kitchen"]]
    assert client.post("/domains/dry-run", json={"pack": "nope"}).status_code == 404


def test_the_surfers_prompt_shows_a_packs_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pack's tool line stands before ``answer`` in the step prompt, and
    only on a host that runs the pack."""
    from prax.answering import surf

    assert surf.system() == surf.SYSTEM or "maths:" in surf.system()
    monkeypatch.setattr(surf, "PACK_HELP", ["maths: same [n] == <latex>   check"])
    text = surf.system()
    assert text.index("maths: same") < text.index("\nanswer  ")
    monkeypatch.setattr(surf, "PACK_HELP", [])
    assert surf.system() == surf.SYSTEM
