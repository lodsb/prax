"""Where a quote stands in its text (``prax.text.quotes``): the ways a
model's quote differs from the text's characters and is still found."""

from __future__ import annotations

from prax.text import quotes

TEXT = (
    "Grainlet synthesis is a variation of granular\n"
    "synthesis in which the duration of each grain is tied to its fre-\n"
    "quency. The **phase vocoder** is used for _time stretching_ in\n"
    "many tools, but the authors prefer a wavelet approach for transients."
)


def _at(quote: str) -> str | None:
    got = quotes.place(TEXT, quote)
    return TEXT[got[0] : got[1]] if got else None


def test_as_written_and_with_its_whitespace_and_case_loose() -> None:
    assert (
        _at("Grainlet synthesis is a variation") == "Grainlet synthesis is a variation"
    )
    assert _at("variation of granular synthesis") == "variation of granular\nsynthesis"
    assert _at("GRAINLET SYNTHESIS") == "Grainlet synthesis"
    assert _at("tied to its frequency") == "tied to its fre-\nquency"


def test_quotation_marks_around_it_and_markdown_in_the_text() -> None:
    assert (
        _at('"Grainlet synthesis is a variation"')
        == "Grainlet synthesis is a variation"
    )
    assert _at("“the phase vocoder is used for time stretching”") == (
        "The **phase vocoder** is used for _time stretching"
    )


def test_a_quote_with_an_ellipsis_is_placed_by_its_parts() -> None:
    got = _at("Grainlet synthesis is a variation ... the duration of each grain")
    assert got is not None and got.startswith("Grainlet") and got.endswith("each grain")
    assert _at("The phase vocoder […] prefer a wavelet approach") is not None
    # parts out of order, or one not in the text, place nothing
    assert _at("a wavelet approach ... Grainlet synthesis is") is None
    assert _at("Grainlet synthesis is a variation ... of something else here") is None


def test_what_is_not_there_or_is_no_quote_places_nothing() -> None:
    assert _at("additive synthesis is a variation") is None
    assert _at("tiny") is None  # too short to place anything
    assert _at("x" * (quotes.MAX_QUOTE + 1)) is None
    assert quotes.place("", "Grainlet synthesis") is None
