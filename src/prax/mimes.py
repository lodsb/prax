"""What a document's type says about it, where the type alone misleads.

``image/*`` means a picture almost everywhere: the vision model reads it,
the graph extraction leaves it out, the document field calls it an image.
DjVu is the exception. Its registered type is ``image/vnd.djvu``, but a
DjVu file is a document, usually a scanned book with a text layer, and is
read by its own parser like a PDF. Code that asks "is this a picture?"
asks it here, so the exception is said once.
"""

from __future__ import annotations

DJVU = "image/vnd.djvu"
# image types that are documents, not pictures
DOCUMENT_IMAGES = (DJVU,)


def is_picture(mime: str | None) -> bool:
    """Whether a document of this type is a picture (a photo, a scan saved
    as an image), not a document that happens to have an image type."""
    m = (mime or "").lower()
    return m.startswith("image/") and m not in DOCUMENT_IMAGES


def picture_sql(column: str = "mime") -> str:
    """The same question as an SQL condition on ``column``."""
    kept = ", ".join(f"'{m}'" for m in DOCUMENT_IMAGES)
    return f"({column} LIKE 'image/%' AND {column} NOT IN ({kept}))"
