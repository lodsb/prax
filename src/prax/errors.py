"""The errors a caller is meant to see. The door turns them into HTTP
answers in one place (``prax.api``), so a route does not catch them."""

from __future__ import annotations


class NotFound(KeyError):
    """What the caller named is not there: no such document, entity, page
    or run, or one the viewer may not see (the wall answers as if absent).
    The door answers 404 with the message. A KeyError, so a caller that
    catches one still works; unlike KeyError its message reads without
    quotes. A KeyError that is not a NotFound is a bug, and stays a 500."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""
