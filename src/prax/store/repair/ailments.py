"""The list of ailments, and the two passes over it: ``health`` looks,
``heal`` repairs."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..base import _NOW, _reading
from ..jobs import Job
from .common import (
    CAP,
    EXAMPLES,
    Ailment,
    _repair_edges,
    _repair_entities,
    _repair_jobs,
    _repair_review,
)
from .documents import (
    _edges_of_retired,
    _extraction_failed,
    _glyph_documents,
    _labelled_summaries,
    _not_documents,
    _repair_extraction_failed,
    _repair_glyphs,
    _repair_labelled_summaries,
    _repair_not_documents,
    _repair_stale_extractions,
    _repair_stale_parses,
    _repair_twins,
    _repair_uncounted_pages,
    _repair_untyped,
    _review_of_retired,
    _stale_extractions,
    _stale_jobs,
    _stale_parses,
    _thin_texts,
    _twin_documents,
    _uncounted_pages,
    _unembedded_chunks,
    _unparsable_documents,
    _unpolished_transcripts,
    _unread_figures,
    _unread_formulas,
    _unreadable_documents,
    _untyped_documents,
)
from .graph import (
    _backwards_part_of,
    _container_citations,
    _document_twins,
    _duplicate_facts,
    _functional_conflicts,
    _mangled_names,
    _not_venues,
    _placeholder_entities,
    _reference_entities,
    _repair_document_twins,
    _repair_duplicate_facts,
    _repair_names,
    _repair_not_venues,
    _repair_part_of,
    _repair_spacing_twins,
    _repair_stray_versions,
    _repair_wire_labels,
    _repair_wire_names,
    _self_edges,
    _slow_walks,
    _spacing_twins,
    _split_names,
    _stray_version_modules,
    _unnamed_entities,
    _wire_labels,
    _wire_names,
)

AILMENTS: tuple[Ailment, ...] = (
    Ailment(
        name="functional-conflicts",
        what=(
            "a subject with two values of a relation the ontology calls"
            " functional (a paper published in two venues)"
        ),
        fix=(
            "look at the evidence of each: end the wrong edge, or say it is"
            " not functional in the ontology's YAML; nothing is changed here"
        ),
        find=_functional_conflicts,
    ),
    Ailment(
        name="not-venues",
        what=(
            'a paper "published in" what is no venue: a publisher, a company,'
            " a university, a semester or an exercise sheet the extraction"
            " typed as one"
        ),
        fix=(
            "end the edge and write what it meant beside it: published_by the"
            " publisher or company, written_at the institution, nothing for a"
            " date (INFERRED, producer heal:not-venues)"
        ),
        find=_not_venues,
        repair=_repair_not_venues,
    ),
    Ailment(
        name="backwards-part-of",
        what=(
            "a part_of whose names say it is wrong: the wrong way round (a"
            " university part of its chair, a course part of its exam), one"
            " that holds neither way (a university part of a project, a paper"
            ' part of a publisher, anything part of "course"), or, by a cue'
            " alone, doubtful"
        ),
        fix=(
            "turn the reversed ones round and end the misfits (history kept,"
            " INFERRED, producer heal:part_of-direction); the doubtful ones"
            " are listed for a person and not changed. The rule pass stands"
            " on none of them either way"
        ),
        find=_backwards_part_of,
        repair=_repair_part_of,
    ),
    Ailment(
        name="stray-version-modules",
        what=(
            "live facts and open review items whose ontology version names a"
            " file beside the modules (genres, subjects, the lexicon) as if it"
            " were one: a door started before the file existed read it so"
        ),
        fix=(
            "take that name out of the version; the fact is the same, only its"
            " stamp was wrong (a retired fact keeps its history)"
        ),
        find=_stray_version_modules,
        repair=_repair_stray_versions,
    ),
    Ailment(
        name="container-citations",
        what=(
            "citations that point at the container a work appeared in — a"
            " proceedings volume, a journal — rather than at the work, so the"
            " citation names no paper and the volume becomes a hub"
        ),
        fix="end the citation; the container itself is left alone",
        find=_container_citations,
        repair=_repair_edges,
    ),
    Ailment(
        name="placeholder-entities",
        what=(
            "entities named after the prompt rather than after a thing"
            " ('source name', 'unknown', 'n/a'), carrying live edges"
        ),
        fix="end every edge they carry; the entity stays as the record",
        find=_placeholder_entities,
        repair=_repair_entities,
    ),
    Ailment(
        name="reference-number-entities",
        what=(
            "entities whose name is a reference, figure or table number"
            " ('[12]', 'fig. 3'), which the ontology says is never a name"
        ),
        fix="end every edge they carry",
        find=_reference_entities,
        repair=_repair_entities,
    ),
    Ailment(
        name="mangled-names",
        what=(
            "entity names with markup or line breaks in them, as a citation"
            " importer leaves them ('<i>The Origins of Music</i>')"
        ),
        fix=(
            "clean the name, or merge into the entity that already carries"
            " the clean one"
        ),
        find=_mangled_names,
        repair=_repair_names,
    ),
    Ailment(
        name="wire-names",
        what=(
            "entity names an extractor's own wire syntax got glued to"
            " ('chord dst_type=concept(confidence=EXTRACTED evidence=…',"
            " 'no_correspondence(dst=Sundberg)'): a line the model wrote"
            " in the triple format and the parser took whole"
        ),
        fix=(
            "cut the name at the syntax and clean it, or merge into the"
            " entity that already carries it; a name that was nothing but"
            " syntax has its edges ended"
        ),
        find=_wire_names,
        repair=_repair_wire_names,
    ),
    Ailment(
        name="wire-labels",
        what=(
            "labels an extractor's wire syntax got glued to ('ARP 2600"
            " dst_type=tool confidence=…'); a query that added one to its"
            " words failed until 2026-10-05"
        ),
        fix=(
            "set the label aside (kind 'wire', kept) and write the words"
            " before the syntax as a label where the entity has none such;"
            " run wire-names first"
        ),
        find=_wire_labels,
        repair=_repair_wire_labels,
    ),
    Ailment(
        name="unnamed-entities",
        what=(
            "entities with no name at all, or with a whole citation or"
            " paragraph as one (a claim is a sentence and is left alone)"
        ),
        fix="end every edge they carry",
        find=_unnamed_entities,
        repair=_repair_entities,
    ),
    Ailment(
        name="split-names",
        what=(
            "one name held by things of unrelated types (SuperCollider a tool"
            " and a method, TU Munich an organization and an author): mostly"
            " one thing typed differently from document to document,"
            " sometimes two things"
        ),
        fix=(
            "a person's call: merge the parts that are one thing"
            " (POST /graph/merge with across_types), or leave two things apart;"
            " a traverse from the name already walks one and names the others"
        ),
        find=_split_names,
    ),
    Ailment(
        name="duplicate-facts",
        what=(
            "one fact live twice from one document and one reader: a later"
            " run stated it again, or its ends were merged since"
        ),
        fix=(
            "end the later edges, the oldest kept; a fact two readers state"
            " (a record and a model) is two pieces of evidence and stays"
        ),
        find=_duplicate_facts,
        repair=_repair_duplicate_facts,
    ),
    Ailment(
        name="document-twins",
        what=(
            "one library document known to the graph as entities of two"
            " document types (the paper X and the article X): a page link or a"
            " citation typed it a paper, its own extraction otherwise"
        ),
        fix=(
            "fold the others into the most connected (the type"
            " store.document_node gives), one signed run unmerge_run takes back"
            "; only a title one open document carries, never a page"
        ),
        find=_document_twins,
        repair=_repair_document_twins,
    ),
    Ailment(
        name="spacing-twins",
        what=(
            "entities of one type whose names differ only in spacing"
            " ('Valhalla DSP' and 'ValhallaDSP', 'sub-pattern' and"
            " 'subpattern'): the resolution's sure tier keeps word boundaries"
        ),
        fix=(
            "fold each into its clean spelling (no detached accents, the"
            " fewest words the library's text does not use, not all capitals,"
            " then the most edges), one signed run unmerge_run takes back;"
            " only names of six characters or more joined up, same numbers"
        ),
        find=_spacing_twins,
        repair=_repair_spacing_twins,
    ),
    Ailment(
        name="twin-documents",
        what=(
            "two live documents with one title and the same text — a PDF"
            " downloaded twice, a book kept in two prints; different bytes,"
            " so the hash did not fold them"
        ),
        fix=(
            "retire the twin into the keeper (more live edges, then the older"
            " one): what it holds and the keeper lacks moves over first"
        ),
        find=_twin_documents,
        repair=_repair_twins,
    ),
    Ailment(
        name="self-edges",
        what=(
            "live edges from a thing to itself, usually what is left after"
            " two names were merged into one"
        ),
        fix="end them; they say nothing about the neighbourhood",
        find=_self_edges,
        repair=_repair_edges,
    ),
    Ailment(
        name="edges-of-retired-documents",
        what="live edges whose source document was retired (a duplicate capture)",
        fix="end them; the surviving capture's reading stands",
        find=_edges_of_retired,
        repair=_repair_edges,
    ),
    Ailment(
        name="review-of-retired-documents",
        what="open review items from documents that were retired",
        fix="resolve them as dropped",
        find=_review_of_retired,
        repair=_repair_review,
    ),
    Ailment(
        name="slow-graph-walks",
        what=(
            "walks from the most connected entities slower than SLOW_WALK_MS"
            " (50 ms for one hop, 500 for two): the graph's threshold in"
            " CLAUDE.md"
        ),
        fix=(
            "first an index for the query the walk spends its time in (as"
            " migration 0031 did); past that, the edges in Kuzu"
            " (docs/rationale.md), not a server database"
        ),
        find=_slow_walks,
    ),
    Ailment(
        name="stale-jobs",
        what="jobs still marked running whose heartbeat stopped a day ago",
        fix="close them as failed (the door reaps its own host within minutes)",
        find=_stale_jobs,
        repair=_repair_jobs,
    ),
    Ailment(
        name="documents-without-an-extractor",
        what=(
            "documents waiting for text of a kind nothing here can read"
            " (a .doc without LibreOffice, a zip, a video)"
        ),
        fix=(
            "install what reads them (howto 3b) or retire them; nothing to"
            " repair in the store"
        ),
        find=_unparsable_documents,
    ),
    Ailment(
        name="untyped-documents",
        what=(
            "documents taken in as unknown bytes whose file name says what they"
            " are (a .djvu before prax knew the type), which no parser was offered"
        ),
        fix="give each its type; the parse queue then reads it",
        find=_untyped_documents,
        repair=_repair_untyped,
    ),
    Ailment(
        name="extraction-failed",
        what=(
            "documents the extract step could not read under the current"
            " ontology (the error is kept): left out of the passes until the"
            " ontology moves or a reading succeeds"
        ),
        fix=(
            "a bigger slot on the model server, or the promote step for the"
            " few that matter. Repairing forgets only the errors that were the"
            " server's (loading its model, down, refused: a pass ran while"
            " llama-server was paused), so those are selected again"
        ),
        find=_extraction_failed,
        repair=_repair_extraction_failed,
    ),
    Ailment(
        name="not-documents",
        what=(
            "originals that cannot be what their type says — a macOS resource"
            " fork (._file), a Windows shortcut, a program, an empty file, a"
            " PDF without its header — registered from a folder that held"
            " them beside the real files; nothing will ever read them"
        ),
        fix=(
            "retire them: row and bytes stay, index and graph forget them,"
            " and the unreadable list is the scans again"
        ),
        find=_not_documents,
        repair=_repair_not_documents,
    ),
    Ailment(
        name="uncounted-pages",
        what=(
            "PDFs with text whose page count no parse recorded (read before"
            " the worker kept one): thin-texts cannot weigh them"
        ),
        fix=(
            "count them: each PDF opened once, meta.pages written, zero for"
            " a truncated original MuPDF opens with no pages at all (needs"
            " pymupdf on the door; the worker records it for every parse since)"
        ),
        find=_uncounted_pages,
        repair=_repair_uncounted_pages,
    ),
    Ailment(
        name="labelled-summaries",
        what=(
            "summaries that begin with the words of the message that asked"
            ' for them ("Document title: …", "Description: …"): a model'
            " told to answer with the translation and nothing else filled in"
            " the form instead, and the document field indexes the label"
        ),
        fix=(
            "take the label off (summaries.parse), which is what the check"
            " refuses now; no model call, and the English underneath is"
            " usually right"
        ),
        find=_labelled_summaries,
        repair=_repair_labelled_summaries,
    ),
    Ailment(
        name="thin-texts",
        what=(
            "PDFs of five pages or more with under 100 bytes of text a page:"
            " scans whose text layer is the cover's, read as if it were the"
            " book (the front matter of a Google Books scan, say); only the"
            " PDFs with a page count are weighed (uncounted-pages)"
        ),
        fix=(
            "ask for OCR over all of them (`prax reread --extractor"
            " pymupdf4llm-ocr --thin`; the OCR page budget, parse.ocr_max_pages,"
            " must cover the longest) or the vision model; nothing to repair"
            " in the store"
        ),
        find=_thin_texts,
        offers=(
            {
                "label": "ask OCR for all of them",
                "extractor": "pymupdf4llm-ocr",
                "thin": 100,
            },
        ),
    ),
    Ailment(
        name="unreadable-documents",
        what=(
            "documents every extractor here has tried and found no text in"
            " (scans without a text layer): they wait, and are not tried again"
        ),
        fix=(
            "ask for OCR or the vision model on the document's page ('read"
            " again…', howto 3b) or over all of them at once (`prax reread"
            " --unreadable`), or retire it; nothing to repair in the store"
        ),
        find=_unreadable_documents,
        offers=(
            {
                "label": "ask OCR for all of them",
                "extractor": "pymupdf4llm-ocr",
                "unreadable": True,
            },
            {
                "label": "ask the vision model for their scanned pages",
                "extractor": "vision-pages",
                "mode": "scans",
                "unreadable": True,
            },
        ),
    ),
    Ailment(
        name="stale-extractions",
        what=(
            "documents whose extraction was made from a text a later read has"
            " replaced (marker over a pymupdf4llm text, OCR over a scan): the"
            " graph speaks of a text that is gone, and the extract step does"
            " not select them again"
        ),
        fix=(
            "move the stamp to the history so the extract step reads the new"
            " text (`prax work --steps extract --scope all`, or the nightly"
            " pass); the old reading's edges are retired when the new one is"
            " applied. A read that replaces the text does this on the way in"
            " now; these are from before"
        ),
        find=_stale_extractions,
        repair=_repair_stale_extractions,
    ),
    Ailment(
        name="unread-formulas",
        what=(
            "documents holding a display equation no model has read: the LaTeX"
            " is there and embeds to noise, and nothing says in words what the"
            " equation is, so a search for it by meaning finds nothing"
        ),
        fix=(
            "ask the formulas model for them — on one document's page ('read"
            " again… → formulas'), or over all of them at once (`prax reread"
            " --extractor formulas --unread-formulas`, howto 3h). steps.formulas"
            " in prax.yaml names the model; a local one costs only time"
        ),
        find=_unread_formulas,
        offers=(
            {
                "label": "read the formulas",
                "extractor": "formulas",
                "unread_formulas": True,
            },
        ),
    ),
    Ailment(
        name="unpolished-transcripts",
        what=(
            "videos whose transcript is the automatic one as it came — no"
            " sentences, no capitals, every filler — and the polish has not"
            " written it yet (captured before the step existed, or while its"
            " model was away)"
        ),
        fix=(
            "ask the polish for them (`prax reread --extractor polish"
            " --unpolished`; steps.polish in prax.yaml names the model — a"
            " local one costs only seconds a talk); the door asks for it by"
            " itself for every new capture"
        ),
        find=_unpolished_transcripts,
        offers=(
            {
                "label": "polish all of them",
                "extractor": "polish",
                "unpolished": True,
            },
        ),
    ),
    Ailment(
        name="unread-figures",
        what=(
            "documents holding a figure no model has read: the text has the"
            " picture and its caption, but nothing that says what it shows,"
            " so a search cannot find it and an answer cannot use it"
        ),
        fix=(
            "ask the vision model for them — on one document's page ('read"
            " again… → figures'), or over all of them at once (`prax reread"
            " --extractor figures --unread-figures`, howto 3b). A local"
            " model costs only time, about four seconds a figure; nothing"
            " is wrong in the store"
        ),
        find=_unread_figures,
        offers=(
            {
                "label": "read the captioned ones",
                "extractor": "figures",
                "unread_figures": True,
            },
            {
                "label": "read every image",
                "extractor": "figures",
                "mode": "all",
                "unread_figures": True,
            },
        ),
    ),
    Ailment(
        name="unmapped-glyphs",
        what=(
            "documents whose text holds ligature glyphs (ﬁ, ﬂ) or Symbol-font"
            " code points (=, ∈, α as private-use characters) from before"
            " every text was cleaned: boxes on screen, words search cannot match"
        ),
        fix=(
            "re-index each from its own text, cleaned (prax.text.glyphs); unchanged"
            " chunks keep their vectors"
        ),
        find=_glyph_documents,
        repair=_repair_glyphs,
    ),
    Ailment(
        name="stale-parses",
        what=(
            "documents whose text came from an extractor prax has revised"
            " since (the figures it finds now, a cleaner reading): a re-read"
            " would produce something new, or say 'same'"
        ),
        fix=(
            "moves the stamp where an annotation in the history already"
            " made the revision's change (figure references placed); the"
            " rest a backlog pass reads a few at a time (`prax work --scope"
            " all`, nightly)"
        ),
        find=_stale_parses,
        repair=_repair_stale_parses,
    ),
    Ailment(
        name="chunks-without-vectors",
        what="chunks the current embedding model has no vector for",
        fix="run a worker (`prax work`); nothing to repair in the store",
        find=_unembedded_chunks,
    ),
)


BY_NAME = {a.name: a for a in AILMENTS}


def _chosen(only: list[str] | None) -> list[Ailment]:
    if not only:
        return list(AILMENTS)
    unknown = [n for n in only if n not in BY_NAME]
    if unknown:
        raise ValueError(
            f"no such ailment: {', '.join(unknown)}; known: {', '.join(BY_NAME)}"
        )
    return [BY_NAME[n] for n in only]


@_reading
def health(
    con: sqlite3.Connection, *, only: list[str] | None = None, examples: int = EXAMPLES
) -> dict[str, Any]:
    """What is wrong with the store right now: every ailment, how many rows
    it finds and a few of them to look at. Reads only."""
    found = []
    for ailment in _chosen(only):
        rows = ailment.find(con)
        found.append(
            {
                "name": ailment.name,
                "what": ailment.what,
                "fix": ailment.fix,
                "repairable": ailment.repairable,
                "offers": list(ailment.offers),
                "count": len(rows),
                "capped": len(rows) >= CAP,
                "examples": rows[:examples],
            }
        )
    return {
        "ailments": found,
        "found": sum(1 for f in found if f["count"]),
        "checked_at": con.execute(f"SELECT {_NOW}").fetchone()[0],
    }


def heal(con: sqlite3.Connection, *, only: list[str] | None = None) -> dict[str, Any]:
    """Repair what the named ailments find, through the store's own
    functions: edges are invalidated (never deleted), review items are
    resolved, job rows are closed. Announces itself as a job.

    ``only`` names the ailments to repair; without it every repairable one
    runs. A report-only ailment is skipped and says so."""
    chosen = _chosen(only)
    out: dict[str, Any] = {}
    done = 0
    with Job(con, "heal", note=", ".join(a.name for a in chosen)) as job:
        for ailment in chosen:
            rows = ailment.find(con)
            if not rows:
                continue
            if ailment.repair is None:  # a report: it says what to do
                out[ailment.name] = f"{len(rows)} to look at — {ailment.fix}"
                continue
            repaired = ailment.repair(con, rows)
            done += repaired
            out[ailment.name] = {"found": len(rows), "repaired": repaired}
            if repaired < len(rows):
                out[ailment.name]["left alone"] = len(rows) - repaired
            job.update(done=done, note=ailment.name)
    return out
