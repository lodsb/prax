"""Pages: the notes that are documents too.

A page is a document with a slug and a revision history; the agent may add
to what a person wrote but never replace it. Writing one goes through the
same ingest as any other document, and its ``annotates`` and ``part_of``
edges go through the same graph.

A package of parts, each importing only from the ones before it in
``ORDER``; this module re-exports every name, so a caller outside writes
``store.pages.<name>`` or ``from .pages import name`` as before."""

from __future__ import annotations

ORDER = (
    "texts",
    "pointers",
    "tree",
    "reads",
    "writes",
    "moves",
    "splits",
    "projects",
)

from .moves import (  # noqa: F401
    _MOVED,
    _bookkeeping_revision,
    _move_note,
    _move_page,
    _rename_slug,
    move_page,
    rename_page,
)
from .pointers import (  # noqa: F401
    PROJECT_SOURCE,
    page_links,
    pointer_of,
    project_documents,
    resolve_pointer,
)
from .projects import (  # noqa: F401
    _project_row,
    list_projects,
    note_project_sync,
    project_at,
    project_named,
    save_project,
)
from .reads import (  # noqa: F401
    get_page,
    list_pages,
    open_answer_documents,
    page_revision_text,
    pages_with_chunks_of,
)
from .splits import (  # noqa: F401
    SPLIT_DEPTH,
    _split_page,
    cited_place,
    split_page,
)
from .texts import (  # noqa: F401
    _DOC_LINK,
    _LINK_EVIDENCE,
    PAGE_AUTHORS,
    PAGE_KINDS,
    PAGE_LARGE,
    _page_original,
    _slug_of,
    linked_documents,
    page_blocks,
    page_titles,
    slugify,
)
from .tree import (  # noqa: F401
    NOT_SHOWN,
    PAGE_SOURCES,
    PROJECT_MEMBERS,
    _aliases,
    _ancestors,
    _annotated,
    _children,
    _is_project_page,
    _lifecycle,
    _members,
    _parent_of,
    _project_edges,
    _projects_of,
    _refuse_cycle,
    _refuse_elsewhere,
    _seen_project_edges,
    _slug_at,
    _slug_for_all,
    _slug_shown,
    page_id,
    page_subtree,
    scope_documents,
)
from .writes import (  # noqa: F401
    _annotate,
    _link_edges,
    _link_project,
    _new_page,
    _next_revision,
    _page_link,
    _refuse_write,
    _rename_page,
    _revision_after,
    _set_page_parent,
    _sized,
    _write_page,
    add_to_project,
    append_page,
    fill_blocks,
    mark_open_answer,
    set_page_parent,
    update_section,
    write_page,
)
