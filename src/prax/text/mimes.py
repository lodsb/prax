"""What a document's type says about it, where the type alone misleads.

``image/*`` means a picture almost everywhere: the vision model reads it,
the graph extraction leaves it out, the document field calls it an image.
DjVu is the exception. Its registered type is ``image/vnd.djvu``, but a
DjVu file is a document, usually a scanned book with a text layer, and is
read by its own parser like a PDF. Code that asks "is this a picture?"
asks it here, so the exception is said once.
"""

from __future__ import annotations

import mimetypes

DJVU = "image/vnd.djvu"
# what a phone, a camera or a music library names that a machine's own
# table may lack (Python's has no HEIC; some have no FLAC or Matroska)
MEDIA = {
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".opus": "audio/ogg",
    ".ogg": "audio/ogg",
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}
# image types that are documents, not pictures
DOCUMENT_IMAGES = (DJVU,)


def is_picture(mime: str | None) -> bool:
    """Whether a document of this type is a picture (a photo, a scan saved
    as an image), not a document that happens to have an image type."""
    m = (mime or "").lower()
    return m.startswith("image/") and m not in DOCUMENT_IMAGES


def is_recording(mime: str | None) -> bool:
    """Whether a document of this type is a recording, sound or picture."""
    return (mime or "").lower().startswith(("audio/", "video/"))


def of_name(name: str, fallback: str = "application/octet-stream") -> str:
    """The type of a file by its name: ``MEDIA`` first, then the machine's
    table."""
    dot = name.rfind(".")
    suffix = name[dot:].lower() if dot >= 0 else ""
    return MEDIA.get(suffix) or mimetypes.guess_type(name)[0] or fallback


def picture_sql(column: str = "mime") -> str:
    """The same question as an SQL condition on ``column``."""
    kept = ", ".join(f"'{m}'" for m in DOCUMENT_IMAGES)
    return f"({column} LIKE 'image/%' AND {column} NOT IN ({kept}))"
