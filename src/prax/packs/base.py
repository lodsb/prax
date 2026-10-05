"""What a pack is (docs/packs.md): a manifest of names and files, no code.

A manifest is read by the thin client as cheaply as by the door, so it
imports nothing. The strings it holds are files beside it or import
paths, resolved by ``prax.packs`` when a part is first used.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Pack:
    """One domain: its knowledge, composed on every host, and its
    capability, run on a host that names the pack in ``packs:``."""

    name: str  # the package's folder under prax/packs/
    # knowledge: the library's, always composed
    ontology: tuple[str, ...] = ()  # module files beside the manifest
    sameness: str | None = None  # its cases of "the same thing"
    # its words that say what a name is: sections the core lexicon has not
    lexicon: str | None = None
    rules: str | None = None  # the domain rules it suggests, never applied by itself
    # capability: the host's
    extractors: tuple[str, ...] = ()  # "module:NAME", a list of Extractor
    readings: dict[str, str] = field(default_factory=dict)  # reading -> its step
    kinds: tuple[str, ...] = ()  # new chunk kinds
    aside: tuple[str, ...] = ()  # kinds kept out of vectors and search
    steps: dict[str, str] = field(default_factory=dict)  # step -> its module
    watched: tuple[str, ...] = ()  # steps a worker runs unasked
    tools: dict[str, str] = field(default_factory=dict)  # tool -> "module:function"
    # tool -> the line the surfer's prompt shows for it, as the core's actions
    tool_help: dict[str, str] = field(default_factory=dict)
    # tool -> the GBNF rules its step is written to under a local model's
    # grammar, the rule named after the tool; none: one line of text
    tool_grammar: dict[str, str] = field(default_factory=dict)
    # "module:function" run on an answer the surf wrote with tools on: the
    # answer checked and marked, and the checks (the maths pack's numbers)
    answer_check: str | None = None
    # the words the answer is given about the tools' results, used only
    # when an answer has results (the maths pack's calculator)
    answer_prompt: str | None = None
    # what the pack keeps on a chunk, under a key of its ``data`` (written
    # by its step through ``store.set_chunk_marks``), and the
    # "module:function" that says it beside a nearby equation in an
    # answer's bundle, or "" for none (the maths pack's ``check``)
    chunk_marks: dict[str, str] = field(default_factory=dict)
    extra: str | None = None  # the pyproject extra it needs
    settings: str | None = None  # its section in prax.yaml
