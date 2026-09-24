"""Every way a model has wrapped an answer, in one place.

Each case below was met by one module and not by the others: the labels
came from `summaries` (2026-09-24), the `<tool_call>` tail from `titles`,
the fence and the preamble from `vocabulary` and `summaries`, the
"Sure!" from the local extractor.
"""

from __future__ import annotations

import pytest

from prax import answers


@pytest.mark.parametrize(
    ("given", "want"),
    [
        ("olive oil", "olive oil"),
        ('"olive oil"', "olive oil"),
        ("olive oil.", "olive oil"),
        ('"olive oil".', "olive oil"),
        ('"olive oil."', "olive oil"),
        ("“olive oil”", "olive oil"),
        ("„Olivenöl“", "Olivenöl"),
        ("Here is the translation: olive oil", "olive oil"),
        ("Here's the English name — olive oil", "olive oil"),
        ("Sure! Here is the title: olive oil", "olive oil"),
        ("Title: olive oil", "olive oil"),
        ("Answer: olive oil", "olive oil"),
        ("The English name is: olive oil", "olive oil"),
        ("Description, written in German: olive oil", "olive oil"),
        ("Document title: olive oil", "olive oil"),
        ('The English name is "olive oil".', "olive oil"),
        ("```\nolive oil\n```", "olive oil"),
        ("```text\nolive oil\n```", "olive oil"),
        ("olive oil<tool_call>", "olive oil"),
        ("olive oil<|im_end|>", "olive oil"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_unwrap(given: str, want: str) -> None:
    assert answers.unwrap(given) == want


def test_a_sentence_keeps_its_full_stop() -> None:
    text = "This document is a directory of manufacturers."
    assert answers.unwrap(text, sentence=True) == text
    assert answers.unwrap(text) == "This document is a directory of manufacturers"


def test_the_first_line_that_carries_anything() -> None:
    assert answers.first_line("\n\nolive oil\nbecause it is pressed") == "olive oil"
    assert answers.first_line("Title: A Paper\nNotes: none") == "A Paper"
    assert answers.first_line("\n \n") is None


def test_a_label_line_is_recognised() -> None:
    assert answers.is_label_line("Document title: Use Case Diagrams")
    assert answers.is_label_line("Description, written in German:")
    assert not answers.is_label_line("This document introduces use case diagrams")


def test_a_label_in_the_middle_is_left_alone() -> None:
    """Only what wraps the answer comes off; a colon inside it stays."""
    text = "Ravel: a life in music"
    assert answers.unwrap(text) == text


def test_tokens_take_what_follows_them() -> None:
    assert answers.strip_tokens("a title<tool_call>{...}").strip() == "a title"
    assert answers.unwrap("a title\n<think>hmm</think>") == "a title"


def test_answers_imports_nothing_of_prax() -> None:
    import re
    from pathlib import Path

    text = Path(answers.__file__ or "").read_text(encoding="utf-8")
    assert not re.search(r"^from prax|^import prax|^from \.", text, re.MULTILINE)
