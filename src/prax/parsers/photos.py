"""What a photo says about itself, and the two smaller copies prax keeps of
it (``docs/biography.md`` §7, the photo stage; plan item AR).

No model reads anything here. A picture's EXIF block says when it was
taken, where (GPS) and with what camera; a thumbnail of ``THUMB_PX`` on
its long side shows it in a list, and a preview of ``PREVIEW_PX`` shows it
on its page while the share its original lies on is offline. Both are
JPEG, turned the way the camera's orientation tag says, and carry no EXIF
of their own: where a photo was taken is kept in ``meta``, behind the
wall, and never travels with a copy of the picture.

A phone's photos are HEIC, which Pillow reads with ``pillow-heif``. A
worker without it does not take the step (``available``): a HEIC read
as a failure would be marked and never tried again.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

# what a reading is: a later one (another size, another field) is a new
# version, and every photo becomes due for it again
VERSION = 1
THUMB_PX = 320
PREVIEW_PX = 1600
THUMB_QUALITY = 80
PREVIEW_QUALITY = 82
MEDIA = "image/jpeg"

# the EXIF tags read (Pillow's ``getexif`` keys)
_MAKE, _MODEL, _ORIENTATION, _DATETIME = 0x010F, 0x0110, 0x0112, 0x0132
_EXIF_IFD, _GPS_IFD = 0x8769, 0x8825
_TAKEN, _DIGITIZED, _OFFSET, _LENS = 0x9003, 0x9004, 0x9011, 0xA434
_GPS_LAT_REF, _GPS_LAT, _GPS_LON_REF, _GPS_LON = 1, 2, 3, 4
_GPS_ALT_REF, _GPS_ALT = 5, 6


def available() -> bool:
    """Whether this host can read a phone's photos: Pillow and pillow-heif."""
    try:
        import pillow_heif  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def _open(data: bytes) -> Any:
    from PIL import Image

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value or "").strip().strip("\x00").strip()


def _taken(stamp: str, offset: str) -> str | None:
    """An EXIF date (``2019:05:03 14:22:10``) as ISO 8601, with its UTC
    offset when the camera wrote one; None when it is not a date (a
    camera with no clock set writes ``0000:00:00 00:00:00``)."""
    try:
        when = datetime.strptime(stamp[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    if when.year < 1900:
        return None
    iso = when.strftime("%Y-%m-%dT%H:%M:%S")
    if len(offset) == 6 and offset[0] in "+-" and offset[3] == ":":
        iso += offset
    return iso


def _degrees(value: Any, ref: str) -> float | None:
    try:
        d, m, s = (float(v) for v in value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    out = d + m / 60 + s / 3600
    return round(-out if ref.upper() in ("S", "W") else out, 6)


def _gps(gps: Any) -> dict[str, float] | None:
    if not gps:
        return None
    lat = _degrees(gps.get(_GPS_LAT), _text(gps.get(_GPS_LAT_REF)))
    lon = _degrees(gps.get(_GPS_LON), _text(gps.get(_GPS_LON_REF)))
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None
    out: dict[str, float] = {"lat": lat, "lon": lon}
    try:
        alt = float(gps[_GPS_ALT])
        below = gps.get(_GPS_ALT_REF) in (1, b"\x01")
        out["alt"] = round(-alt if below else alt, 1)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    return out


def facts(img: Any) -> dict[str, Any]:
    """What the picture's EXIF says: ``taken`` (ISO 8601, to the second),
    ``camera``, ``lens``, ``gps`` (``lat``, ``lon``, ``alt``), and its
    ``width`` and ``height`` as it is shown (after the orientation)."""
    exif = img.getexif()
    sub = exif.get_ifd(_EXIF_IFD) if exif else {}
    out: dict[str, Any] = {}
    stamp = _text(sub.get(_TAKEN) or sub.get(_DIGITIZED) or exif.get(_DATETIME))
    if stamp:
        taken = _taken(stamp, _text(sub.get(_OFFSET)))
        if taken:
            out["taken"] = taken
    make, model = _text(exif.get(_MAKE)), _text(exif.get(_MODEL))
    if model:
        # "Apple" and "iPhone 12": the model mostly names the make already
        out["camera"] = (
            model if make and model.startswith(make) else f"{make} {model}".strip()
        )
    lens = _text(sub.get(_LENS))
    if lens:
        out["lens"] = lens
    where = _gps(exif.get_ifd(_GPS_IFD) if exif else None)
    if where:
        out["gps"] = where
    w, h = img.size
    if exif.get(_ORIENTATION) in (5, 6, 7, 8):
        w, h = h, w
    out["width"], out["height"] = int(w), int(h)
    return out


def _jpeg(img: Any, side: int, quality: int) -> bytes:
    copy = img.copy()
    copy.thumbnail((side, side))
    buf = io.BytesIO()
    copy.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def read(data: bytes) -> dict[str, Any]:
    """A photo's facts and its two copies: ``{"facts": …, "thumb": bytes,
    "preview": bytes}``. Raises ``ValueError`` for bytes that are not a
    picture Pillow can open."""
    from PIL import ImageOps, UnidentifiedImageError

    try:
        img = _open(data)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError(
            f"not a picture this host reads ({type(exc).__name__})"
        ) from exc
    found = facts(img)
    shown = ImageOps.exif_transpose(img)
    if shown.mode not in ("RGB", "L"):
        shown = shown.convert("RGB")
    return {
        "facts": found,
        "thumb": _jpeg(shown, THUMB_PX, THUMB_QUALITY),
        "preview": _jpeg(shown, PREVIEW_PX, PREVIEW_QUALITY),
    }


def stamp() -> str:
    """Which reader read it, for ``meta.photo.by``."""
    from PIL import __version__ as pil

    try:
        from pillow_heif import __version__ as heif
    except ImportError:
        heif = "none"
    return f"photos/{VERSION} pillow/{pil} pillow-heif/{heif}"
