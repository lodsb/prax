"""What a captured page carries that is not the document: the comment
section, the advertising, and a recipe's ingredient list."""

from __future__ import annotations

from prax import chunking, furniture, ingredients

SPONSOR_READ = """# How a transformer works

Transformers read a sentence all at once, which is what makes them fast.
Attention is the part that decides which words matter to which, and the
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.
The point of the encoder is that every token sees every other one.


This is why I'm happy to work together with Brilliant, whose mission is
to help you learn science in the easiest way possible.

You can try Brilliant yourself for free if you use my link
brilliant.org/sabine. You will get 20% off the annual premium
subscription.

## Comments

Nice video! But you got the attention formula wrong at 4:12.

I have been waiting for this one all week.
"""


def test_an_advertisement_needs_a_sponsor_and_an_offer() -> None:
    """A sponsor's mark with something to act on, so that a funding
    acknowledgement stays part of the paper."""
    yes, why = furniture.looks_paid(
        "You can try Brilliant for free with my link brilliant.org/x, 20% off."
    )
    assert yes and why
    assert furniture.looks_paid("today's sponsor, Sockdoc, makes this simple")[0]
    assert furniture.looks_paid("Anzeige")[0]  # the label a German page uses
    for innocent in (
        "Research sponsored by the U.S. Department of Energy under contract DE-01.",
        "We use the code in [48] to generate the matrices, see https://x.org/psd.",
        (
            "The authors thank InterFace AG (http://interface-ag.com/) for"
            " sponsoring the iPads used in this research."
        ),
        "If you sign up for our list at eff.org we will write when a law moves.",
        "Die Anzeige zeigt die eingestellte Frequenz in Hertz.",
    ):
        assert furniture.looks_paid(innocent)[0] is False, innocent


def test_a_sponsor_read_runs_while_it_names_the_brand() -> None:
    pieces = [
        "Transformers read a sentence all at once.",
        "This is why I am happy to work together with Brilliant.",
        "All courses on Brilliant have interactive visualisations.",
        "Try Brilliant for free with my link brilliant.org/sabine, 20% off.",
        "Back to the attention formula.",
    ]
    runs = furniture.ad_runs(pieces)
    assert len(runs) == 1
    first, last, data = runs[0]
    assert (first, last) == (1, 3) and data["brand"] == "brilliant"


def test_the_comment_section_is_one_chunk_set_aside() -> None:
    chunks = chunking.chunk(SPONSOR_READ)
    kinds = [c.kind for c in chunks]
    assert kinds.count("comment") == 1 and kinds.count("ad") == 1
    comment = next(c for c in chunks if c.kind == "comment")
    assert comment.text.startswith("## Comments")
    assert "waiting for this one" in comment.text  # to the end of the page
    ad = next(c for c in chunks if c.kind == "ad")
    assert "brilliant.org/sabine" in ad.text
    assert ad.data and ad.data["brand"] == "brilliant"
    # the invariant holds for the new kinds too
    for c in chunks:
        assert c.text == SPONSOR_READ[c.char_start : c.char_end]


def test_a_long_comment_section_is_chunked_per_block() -> None:
    """A page's readers can write more than a chunk holds. The section is
    grouped like text, cut where its own lines end so a comment is never
    torn in half, and the pictures before its heading — the first
    commenter's avatar — belong to it rather than to the document."""
    page = (
        "# A piece about AI\n\n"
        + ("Some prose about the argument. " * 90)
        + "\n\n![Avatar of unfinished view](figure:"
        + "a" * 64
        + ")\n\n"
        + "## Comments\n\n"
        + "74 Kommentare\n"
        + "\n".join(
            f"reader{i}\nWhat reader {i} wrote about it. " * 6 for i in range(12)
        )
        + "\n"
    )
    chunks = chunking.chunk(page)
    comments = [c for c in chunks if c.kind == "comment"]
    assert len(comments) > 3  # per block, not one blob
    assert all(len(c.text) <= 2 * chunking.TARGET_CHARS for c in comments)
    assert "Avatar of unfinished view" in comments[0].text
    assert not [c for c in chunks if c.kind == "figure"]  # the avatar is not one
    # the section is contiguous, and every chunk is the region it names
    first = chunks.index(comments[0])
    assert [c.kind for c in chunks[first:]] == ["comment"] * len(comments)
    for c in chunks:
        assert c.text == page[c.char_start : c.char_end]
    # no line is torn: every block starts where a line does
    for c in comments[1:]:
        assert page[c.char_start - 1] == "\n"


def test_a_papers_discussion_is_not_a_comment_section() -> None:
    """Only the last section of a page, under a document of some size."""
    paper = (
        "# A paper\n\n"
        + ("Some prose about the method. " * 90)
        + "\n\n## Discussion\n\nWe have shown that it works.\n"
        + "\n\n## Comments\n\nA remark on the algorithm above.\n"
        + "\n\n## References\n\nSomebody, 2019.\n"
    )
    assert not [c for c in chunking.chunk(paper) if c.kind == "comment"]
    short = "# A note\n\nOne line.\n\n## Comments\n\nWell said.\n"
    assert not [c for c in chunking.chunk(short) if c.kind == "comment"]


RECIPE = """# Tacos mit Sellerie

### Zutaten

Für 4 Personen
1. 2 Sellerieknollen (mittelgroß, insgesamt 1,2 kg)
2. 60 ml Olivenöl
3. ½ TL Salz

#### Für die Soße

1. 120 g Sour Cream
2. ¼ Bund Koriander (fein gehackt)

Falls Sie für die kühleren Monate eine ungewöhnliche Füllung suchen,
könnte der Knollensellerie Ihre Lösung sein. Er ist fest genug, um bei
großer Hitze seine Form zu wahren.
"""


def test_the_ingredient_list_is_one_chunk_with_what_it_says() -> None:
    chunks = chunking.chunk(RECIPE)
    box = [c for c in chunks if c.kind == "ingredients"]
    assert len(box) == 1
    c = box[0]
    assert c.text.startswith("### Zutaten") and "¼ Bund Koriander" in c.text
    assert "Falls Sie" not in c.text  # the prose after it is prose
    data = c.data or {}
    assert data["servings"] == {"text": "Für 4 Personen", "n": 4}
    assert data["count"] == 5
    assert [g["name"] for g in data["groups"]] == [None, "Für die Soße"]
    first = data["groups"][0]["items"]
    assert first[1] == {
        "text": "60 ml Olivenöl",
        "amount": 60.0,
        "unit": "ml",
        "item": "Olivenöl",
    }
    assert first[2]["amount"] == 0.5 and first[2]["unit"] == "TL"
    assert first[0]["note"].startswith("mittelgroß")
    # and it is a chunk like any other: the region of the artifact it names
    assert c.text == RECIPE[c.char_start : c.char_end]


def test_an_ingredient_line_read_in_the_two_languages() -> None:
    assert ingredients.parse_item("1. 2 cups plain flour") == {
        "text": "2 cups plain flour",
        "amount": 2.0,
        "unit": "cups",
        "item": "plain flour",
    }
    assert ingredients.parse_item("- ½ tsp salt")["amount"] == 0.5
    assert ingredients.parse_item("3. schwarzer Pfeffer (grob gemahlen)") == {
        "text": "schwarzer Pfeffer (grob gemahlen)",
        "note": "grob gemahlen",
        "item": "schwarzer Pfeffer",
    }
    assert ingredients.is_heading("Ingredients") and ingredients.is_heading("Zutaten")
    assert not ingredients.is_heading("Instructions")
