"""The paths a text refers to (``prax.text.paths``): links, backticks and
bare mentions, with where each may point."""

from __future__ import annotations

from prax.text import paths


def _targets(
    text: str, here: str = "fw/docs/notes.md", prefix: str = "fw"
) -> list[list[str]]:
    return [c for c, _ in paths.references(text, here, prefix)]


def test_a_markdown_link_resolves_against_its_own_folder() -> None:
    got = paths.references(
        "See [the plan](../plan.md#steps) first.", "fw/docs/notes.md", "fw"
    )
    assert got == [(["fw/plan.md"], "[the plan](../plan.md#steps)")]
    assert _targets("[root](/README.md)") == [["README.md", "fw/README.md"]]


def test_what_is_not_a_path_is_left_alone() -> None:
    text = (
        "[site](https://example.org/a.md) [mail](mailto:a@b.c) [top](#intro)"
        " [up](../../../outside.md) `pip install x` `*.md` and a sentence."
    )
    assert _targets(text) == []


def test_backticks_and_bare_mentions_carry_their_candidates() -> None:
    text = "The table is in `docs/baseline.md`; see also fw/CHANGELOG.md."
    assert _targets(text) == [
        ["docs/baseline.md", "fw/docs/baseline.md", "fw/docs/docs/baseline.md"],
        ["fw/CHANGELOG.md", "fw/fw/CHANGELOG.md", "fw/docs/fw/CHANGELOG.md"],
    ]


def test_one_entry_a_target_and_never_the_file_itself() -> None:
    text = "[a](plan.md) and again [b](plan.md), and [me](notes.md)."
    assert _targets(text) == [["fw/docs/plan.md"]]


def test_a_bare_mention_inside_a_link_is_not_counted_twice() -> None:
    assert _targets("[x](docs/a.md)", here="README.md", prefix="") == [["docs/a.md"]]
