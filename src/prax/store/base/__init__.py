"""The connection, the lock, the archive and the index files.

Nothing here knows about documents or the graph beyond the record of an
edge's ending and which document a node is, which both write: it is what
every other module of the store stands on. One process-wide re-entrant lock serializes
writes (invariant 4); reads in the door take a connection per thread
(``thread_connection``). Originals and parsed text are content-addressed
files under ``data/archive`` (invariant 2), and the vector index files are
opened and cached here. So are the shapes a read hands out that more
than one module of the store builds (``SearchHit``).

A package of parts, each importing only from the ones before it in
``ORDER``; this module re-exports every name, private ones too, so a
caller writes ``store.base._serialized`` as before. A test patches a
name in the part that reads it (``store.base.locks.LOCK_WAIT_SLOW``)."""

from __future__ import annotations

from prax.errors import NotFound  # noqa: F401  (the store's miss, store.NotFound)

ORDER = (
    "common",
    "locks",
    "endings",
    "nodes",
    "wall",
    "indexes",
    "schema",
    "tokens",
)

from .common import (  # noqa: F401
    _ASIDE,
    _NOW,
    ASIDE_KINDS,
    CLIENT_META,
    DOOR_WRITTEN_META,
    ID_BATCH,
    LIST_PAGE,
    LIVE,
    VEC_DIM,
    Checks,
    Document,
    DocumentMeta,
    MetaLike,
    SearchHit,
    _meta_logged,
    ago,
    check_meta,
    checks,
    door_written,
    first_fresh_id,
    live,
    mark_ids,
    marks,
    meta_of,
    moment,
    now,
    rows_in,
    run_name,
    undeclared_meta,
)
from .endings import (  # noqa: F401
    EDGE_FACT,
    EDGE_TIMES,
    END_BATCH,
    _end_edges,
    _insert_edge,
    _move_references,
    _restore_run,
)
from .indexes import (  # noqa: F401
    _INDEX_LOCK,
    FACE_INDEX,
    IndexFile,
    _add_to_delta,
    _delta,
    _delta_path,
    _doc_index_path,
    _drop_index_views,
    _get_vector,
    _get_vectors,
    _has_vectors,
    _index,
    _index_dim,
    _index_path,
    _indexes,
    _knn,
    _open_index,
    chunk_index,
    document_index,
    face_index,
    vectors_available,
)
from .locks import (  # noqa: F401
    _HOLDER,
    _LOCK,
    _SURROGATE,
    _TOKEN,
    BUSY_TIMEOUT,
    CACHE_MB,
    JOURNAL_MB,
    LOCK_HOLD_SLOW,
    LOCK_RETRIES,
    LOCK_WAIT_SLOW,
    MMAP_MB,
    F,
    P,
    R,
    _committing,
    _depth,
    _guarded,
    _lock_log,
    _NoLock,
    _note_hold,
    _note_wait,
    _reading,
    _serialized,
    _take_back,
)
from .nodes import (  # noqa: F401
    _MEDIA_NODE,
    _claim_node,
    _migration_69_nodes,
    _node_doc,
    media_node_name,
    node_name_of,
)
from .schema import (  # noqa: F401
    _DIGEST,
    AFTER_MIGRATION,
    DOCUMENT_NODES,
    _archive_bytes,
    _drop_legacy_vec_table,
    _like_prefix,
    _read_archive,
    archive_path,
    init_db,
    is_digest,
    migrations,
    schema_version,
    sha256_file,
)
from .tokens import (  # noqa: F401
    TOKEN_NAME,
    TOKEN_ROLES,
    TOKEN_USE_EVERY,
    _check_role,
    _token_hash,
    _token_used,
    _tokens,
    _viewer_of,
    _write_token_use,
    add_token,
    list_tokens,
    named_viewer,
    note_token_use,
    remove_token,
    set_token_role,
    token_viewer,
)
from .wall import (  # noqa: F401
    DOC_KEYS,
    UNASSIGNED,
    VIEWER,
    Connection,
    K,
    Memo,
    V,
    Viewer,
    _fold,
    _guards,
    _hidden_memo,
    _hidden_read,
    _local,
    _scrubbed,
    chunk_hidden,
    connect,
    decode_domains,
    doc_visible,
    document_hidden,
    domain_clause,
    domain_sets,
    hidden_documents,
    holds_domain,
    personal_sql,
    scrub,
    store_state,
    thread_connection,
)
