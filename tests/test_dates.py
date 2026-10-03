"""When a document says it was published (``prax.text.dates``)."""

from __future__ import annotations

import pytest

from prax.text import dates


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("2019", ("2019", "year")),
        ("2019-07", ("2019-07", "month")),
        ("2019/7/3", ("2019-07-03", "day")),
        ("2019-07-03T10:00:00Z", ("2019-07-03", "day")),
        ("3 July 2019", ("2019-07-03", "day")),
        ("July 3, 2019", ("2019-07-03", "day")),
        ("Jul. 2019", ("2019-07", "month")),
        ("3. März 2021", ("2021-03-03", "day")),
        ("septembre 2020", ("2020-09", "month")),
        ("  2019 ", ("2019", "year")),
    ],
)
def test_a_date_as_written_and_its_precision(text: str, want: tuple[str, str]) -> None:
    assert dates.parse(text) == want


@pytest.mark.parametrize(
    "text",
    [
        "",
        None,
        "soon",
        "1066",
        "2019-13",
        "2019-02-40",
        "page 2019 of 3000",
        "Foo 2019",
    ],
)
def test_what_is_not_clearly_a_date_is_none(text: str | None) -> None:
    assert dates.parse(text) is None


def test_an_arxiv_id_says_the_month_of_its_first_version() -> None:
    assert dates.from_arxiv("2310.08560") == ("2023-10", "month")
    assert dates.from_arxiv("2512.13564v2") == ("2025-12", "month")
    assert dates.from_arxiv("hep-th/9901001") == ("1999-01", "month")
    assert dates.from_arxiv("math.GT/0309136") == ("2003-09", "month")
    assert dates.from_arxiv("not an id") is None


def test_a_page_says_it_in_its_tags_and_its_markup() -> None:
    page = b"""<html><head>
      <meta property="article:published_time" content="2024-02-11T08:00:00+00:00">
      <meta name="citation_publication_date" content="2023/11/05">
      <meta name="citation_date" content="2022">
      <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Recipe",
         "datePublished": "2021-06-01", "author": {"@type": "Person"}}
      </script></head><body>...</body></html>"""
    got = dates.from_html(page)
    assert got["citation"] == ("2023-11-05", "day")  # the first citation tag
    assert got["jsonld"] == ("2021-06-01", "day")
    assert got["generic"] == ("2024-02-11", "day")
    assert dates.from_html(b"<html><head><title>x</title></head></html>") == {}
    graph = b"""<script type='application/ld+json'>{"@graph": [
      {"@type": "WebSite"}, {"@type": "Article", "datePublished": "May 2018"}]}</script>
      <script type="application/ld+json">{not json</script>"""
    assert dates.from_html(graph) == {"jsonld": ("2018-05", "month")}
