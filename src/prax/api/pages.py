"""Pages and the standing questions: read, write, append, project members;
the questions pass as a job, and the registry of what it is answering."""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from prax import (
    blocks,
    questions,
    store,
)

from ._base import _con

router = APIRouter()


# ------------------------------------------------------------------ pages


class PageReq(BaseModel):
    text: str
    title: str | None = None
    kind: str = "topic"  # addendum | project | topic (creation only)
    author: str = "human"
    note: str | None = None
    annotates: list[int] | None = None
    part_of: str | None = None
    force: bool = False


class AppendReq(BaseModel):
    section: str
    heading: str | None = None
    author: str = "agent"
    note: str | None = None


class MemberReq(BaseModel):
    doc_id: int


@router.get("/pages")
def pages(request: Request, kind: str | None = None) -> list[dict[str, Any]]:
    """Pages, most recently revised first."""
    return store.list_pages(_con(request), kind=kind)


@router.get("/page/{slug}")
def page(slug: str, request: Request) -> dict[str, Any]:
    """The page, with what the questions pass is doing to it right now:
    ``asking`` the ids of the ask blocks being answered, ``asking_page``
    when the page is a standing question being asked again."""
    p = store.get_page(_con(request), store.slugify(slug))
    if p is None:
        raise HTTPException(404, "no such page")
    p["asking"] = _asking(p["slug"], [b["id"] for b in p["blocks"]])
    p["asking_page"] = p["kind"] == questions.KIND and _asking_page(p["slug"])
    return p


@router.get("/page/{slug}/revision/{revision}")
def page_revision(slug: str, revision: int, request: Request) -> dict[str, Any]:
    try:
        text = store.page_revision_text(_con(request), slug, revision)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"slug": slug, "revision": revision, "text": text}


@router.put("/page/{slug}")
def put_page(slug: str, req: PageReq, request: Request) -> dict[str, Any]:
    """Create the page or add a revision. An agent revision over a human
    one is refused with 409 unless ``force``; use append. A page saved
    with an ask block not yet answered has the questions pass started
    for it (``job`` in the answer)."""
    try:
        con = _con(request)
        written = store.write_page(
            con,
            slug,
            req.text,
            title=req.title,
            kind=req.kind,
            author=req.author,
            note=req.note,
            annotates=req.annotates,
            part_of=req.part_of,
            force=req.force,
        )
        job = _answer_new_blocks(con, written["slug"], req.text)
        return {**written, "job": job} if job is not None else written
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/page/{slug}/append")
def append_page(slug: str, req: AppendReq, request: Request) -> dict[str, Any]:
    try:
        return store.append_page(
            _con(request),
            slug,
            req.section,
            heading=req.heading,
            author=req.author,
            note=req.note,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/project/{slug}/members")
def add_member(slug: str, req: MemberReq, request: Request) -> dict[str, Any]:
    """``part_of`` edge from a document to a project page."""
    try:
        eid = store.add_to_project(_con(request), slug, req.doc_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"edge_id": eid, "existing": eid is None}


# -------------------------------------------------------------- questions


class QuestionReq(BaseModel):
    result: dict[str, Any]  # the result of POST /ask
    slug: str | None = None
    options: dict[str, Any] | None = None  # what the ask was asked with


class QuestionsRunReq(BaseModel):
    slug: str | None = None  # one question (a page, or a block as slug#id), else all
    force: bool = False  # ask again even when nothing is new
    briefing: bool = False  # write the day's briefing after
    release: bool = False  # a block edited by hand: ask it again all the same


# the last standing listing, keyed by the store's change stamp (the door's
# writes and any other connection's commits, as /changes reports it): the
# check runs a search per question, a second on a large library, and a
# listing asked twice of an unchanged store is the same listing
_standing: tuple[str, list[dict[str, Any]]] | None = None


def _stamp(request: Request) -> str:
    state = request.app.state
    with state.con_lock:
        return f"{store.data_version(state.con)}-{state.writes}"


@router.get("/questions")
def questions_list(request: Request) -> list[dict[str, Any]]:
    """The standing questions — the question pages, then the ask blocks
    of other pages as ``slug#id`` — with what each remembers and whether
    the library has learned something since (the check runs a search
    per question; no model). Served again as it was while the store's
    change stamp stands."""
    global _standing
    stamp = _stamp(request)
    if _standing is not None and _standing[0] == stamp:
        rows = [dict(r) for r in _standing[1]]
    else:
        rows = questions.standing(_con(request))
        _standing = (stamp, [dict(r) for r in rows])
    for row in rows:  # what the pass is doing to each right now
        slug, _, block = row["slug"].partition("#")
        row["asking"] = bool(_asking(slug, [block])) if block else _asking_page(slug)
    return rows


@router.post("/questions")
def questions_create(req: QuestionReq, request: Request) -> dict[str, Any]:
    """Keep an answer as a standing question: a page the door asks again
    when the library learns something (``schedule: questions``)."""
    try:
        return questions.create(
            _con(request), req.result, slug=req.slug, options=req.options
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/questions/run")
def questions_run(req: QuestionsRunReq, request: Request) -> dict[str, Any]:
    """Check every question (or one) and ask again what is due, as a job;
    with ``briefing`` the day's page after."""
    return _start_questions(
        _con(request),
        only=req.slug,
        force=req.force,
        briefing=req.briefing,
        release=req.release,
    )


# what the questions pass has in hand: the ``only`` of each running job
# ("" for every question, a slug, a slug#id) and its job id, so a page
# and a listing can say "asking…" instead of leaving a click unanswered,
# and a second click while the first runs joins that job instead of
# starting another (a run is a model call; a page once took twenty-six
# in four minutes from one person's clicks)
_answering: dict[str, int] = {}
_answering_lock = threading.Lock()


def _asking_page(slug: str) -> bool:
    with _answering_lock:
        return "" in _answering or slug in _answering


def _asking(slug: str, block_ids: list[str]) -> list[str]:
    """The ask blocks of a page being answered right now."""
    with _answering_lock:
        if "" in _answering or slug in _answering:
            return list(block_ids)
        return [b for b in block_ids if f"{slug}#{b}" in _answering]


def _start_questions(
    con: Any,
    *,
    only: str | None = None,
    force: bool = False,
    briefing: bool = True,
    release: bool = False,
) -> dict[str, Any]:
    key = only or ""
    with _answering_lock:
        running = _answering.get(key)
        if running is not None:
            return {"job": running, "running": True}
        job = store.Job(con, "questions", note=only or "every question")
        _answering[key] = job.id

    def run() -> None:
        con = store.connect()
        try:
            with store.Job.existing(con, job.id) as mine:
                rep = questions.refresh_all(
                    con, force=force, only=only, job=mine, release=release
                )
                note = f"{rep['refreshed']} of {rep['checked']} asked again"
                if briefing:
                    b = questions.briefing(con, job=mine)
                    note += f"; briefing: {b['documents']} documents"
                mine.update(note=note)
        except Exception:  # the job row carries the error
            logging.getLogger("prax.questions").exception("the questions failed")
        finally:
            con.close()
            with _answering_lock:
                _answering.pop(key, None)

    threading.Thread(target=run, name="questions", daemon=True).start()
    return {"job": job.id}


def _answer_new_blocks(con: Any, slug: str, text: str) -> int | None:
    """A page saved with an ask block this page has not asked yet — one
    not filled, or one pasted in from another page, filled there — the
    pass runs for that page now, as a job, so the answer is there within
    a moment rather than at the clock's hour. One job per page at a
    time. Returns the job id, or None when there was nothing to ask."""
    if not blocks.blocks(text):
        return None
    page = store.get_page(con, slug)
    if page is None or not any(
        b["asked_at"] is None and not b["held"] for b in page["blocks"]
    ):
        return None  # (a held block is the person's until released)
    if _asking_page(slug):
        return None
    return _start_questions(con, only=slug, briefing=False)["job"]
