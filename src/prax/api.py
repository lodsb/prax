"""FastAPI app — the HTTP door. Thin wrappers over prax.store."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import store

app = FastAPI(title="prax", version="0.0.1")
_con = None


def con():
    global _con
    if _con is None:
        _con = store.connect()
        store.init_db(_con)
    return _con


class IngestText(BaseModel):
    text: str
    title: str | None = None
    source_url: str | None = None


class LinkReq(BaseModel):
    src: str
    src_type: str
    rel: str
    dst: str
    dst_type: str
    confidence: str = "EXTRACTED"
    source_doc: int | None = None


@app.post("/ingest")
def ingest(req: IngestText):
    return store.ingest_text(
        con(), req.text, title=req.title, source_url=req.source_url
    )


@app.get("/get/{doc_id}")
def get(doc_id: int):
    doc = store.get_document(con(), doc_id)
    if doc is None:
        raise HTTPException(404, "no such document")
    return doc


@app.get("/search")
def search(q: str, limit: int = 10):
    return store.search(con(), q, limit)


@app.post("/link")
def link(req: LinkReq):
    edge = store.Edge(req.src, req.src_type, req.rel, req.dst, req.dst_type)
    eid = store.link(con(), edge, confidence=req.confidence,
                     source_doc=req.source_doc)
    return {"edge_id": eid}


@app.get("/traverse")
def traverse(entity: str, hops: int = 1):
    return store.traverse(con(), entity, hops)
