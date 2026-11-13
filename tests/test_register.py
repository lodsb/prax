"""A register of open questions cut into its questions
(``prax.text.register``, plan item AU step 4)."""

from __future__ import annotations

from prax.text import register

REGISTER = """# Open questions

Read this first. Status: `open` / `answered` / `retired` / `retracted`.

- status: a list in the preamble is no question

## Open

### What is the drive's inertia?
- status: open
- asked: 2026-03-02, after the heavier wheel went on
- test: a driven acceleration against
  the inertia from its geometry
- why not a coast: the gear's lash carries torque only when loaded
- a bullet with no key belongs to the field before it

#### A sub-heading of the question

Still the question's.

### Why does the loop ring at high gain?
- asked: 2026-03-04
- test: a guarded sweep

## Answered

### Is the seed stable across power cycles?
- status: answered 2026-03-08 -- **yes, on ten boots**
- answer: the same seed every time

### Does the wheel bend the track?
- status: retracted 2026-03-09, the same day
- what is true: it does not
"""


def test_questions_are_headings_with_a_state() -> None:
    got = register.split(REGISTER)
    assert got is not None
    assert [q.key for q in got.questions] == [
        "What is the drive's inertia?",
        "Why does the loop ring at high gain?",
        "Is the seed stable across power cycles?",
        "Does the wheel bend the track?",
    ]
    first, second, third, fourth = got.questions
    assert (first.state, first.asked, first.since) == ("open", "2026-03-02", None)
    # the section says it when no status line does
    assert (second.state, second.section) == ("open", "open")
    assert (third.state, third.since) == ("answered", "2026-03-08")
    # the status line wins over the section it stands in
    assert (fourth.state, fourth.section) == ("retracted", "answered")
    assert "Still the question's." in first.text
    assert first.field("test") == (
        "a driven acceleration against the inertia from its geometry"
    )
    assert first.why_not == [
        (
            "why not a coast",
            (
                "the gear's lash carries torque only when loaded"
                " a bullet with no key belongs to the field before it"
            ),
        )
    ]
    assert third.field("answer") == "the same seed every time"
    assert all(REGISTER[q.start : q.end] == q.text for q in got.questions)
    # the head is the rest: the preamble and the sections' headings
    assert got.head.startswith("# Open questions")
    assert "## Open" in got.head and "## Answered" in got.head
    assert "inertia" not in got.head
    assert len(got.head) + sum(len(q.text) for q in got.questions) == len(REGISTER)


def test_a_note_with_one_status_is_no_register() -> None:
    note = "# Design\n\n- status: draft\n\n## Option A\n- status: open\n\nWords.\n"
    assert register.split(note) is None
    assert register.split(note, least=1) is not None


def test_a_section_is_a_state_only_by_its_word_alone() -> None:
    assert register.state_of("Answered") == "answered"
    assert register.state_of("Open (3)") == "open"
    assert register.state_of("Open questions") is None
    assert register.state_of("Retired ideas") is None
