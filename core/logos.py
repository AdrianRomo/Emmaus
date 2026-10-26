"""The church's own logo (Church.logo, Church.logo_dark): PNG only.

An upload is checked by its bytes, never by its name, with no regular expression on anything it contains:

1. At most 512 KB, measured on the bytes read.
2. A chunk walk (byte arithmetic): the PNG signature; IHDR first, 13 bytes, once; every length within the
   file (a 2 GB claim stops at the first read); every CRC right; no acTL (an animated PNG); IEND empty and
   the last byte of the file (nothing appended after the image).
3. IHDR's size: 32 to 2000 px on the long side (the largest use is the PDF's 660 px; 2000 leaves room for
   retina screens), at most 10 times wider than tall or taller than wide.
4. Pillow: verify(), then load() of every pixel, with Image.MAX_IMAGE_PIXELS left in force.

SVG is not accepted: a vector file is a document, and making it safe to parse and to draw was not worth it
for a logo. The stored-file readers below never raise: whatever is on disk, a page draws the wordmark
instead (a warning names the stored file, never what it contains).
"""

from __future__ import annotations

import functools
import logging
import math
import os
import struct
import zlib
from dataclasses import dataclass
from io import BytesIO

from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

MAX_BYTES = 512 * 1024
MAX_PNG_SIDE = 2000
MIN_PNG_LONG_SIDE = 32
MAX_RATIO = 10  # a logo is at most 10 times wider than tall, or taller than wide
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
CONTENT_TYPE = "image/png"

UPLOAD_HELP = "Sube el logotipo en PNG con fondo transparente (hasta 512 KB, lado mayor de 32 a 2000 px)."
UNREADABLE = "No pudimos abrir este PNG. Expórtalo otra vez desde tu programa de diseño."


@dataclass(frozen=True)
class LogoInfo:
    width: int
    height: int


@dataclass(frozen=True)
class LogoMeta:
    """A stored logo, as the templates and the PDF need it."""

    width: int
    height: int
    version: str  # "<bytes>-<mtime in ns>": a new upload changes the URL, so no browser keeps the old one

    content_type = CONTENT_TYPE


def proportions_ok(width, height) -> bool:
    """Finite, positive and no more than MAX_RATIO to 1 either way."""
    if not all(isinstance(side, (int, float)) and math.isfinite(side) and side > 0 for side in (width, height)):
        return False
    return max(width, height) / min(width, height) <= MAX_RATIO


def _unreadable(message: str = UNREADABLE) -> ValidationError:
    return ValidationError(message, code="logo_unreadable")


def _walk_chunks(data: bytes) -> tuple[int, int]:
    """IHDR's width and height, once every chunk has been walked and found in order."""
    if not data.startswith(PNG_MAGIC):
        raise ValidationError(UPLOAD_HELP, code="logo_type")  # SVG, JPEG, WebP, text: one answer
    position = len(PNG_MAGIC)
    size = None
    while True:
        if position + 12 > len(data):
            raise _unreadable()  # the file ends before IEND
        length = int.from_bytes(data[position : position + 4], "big")
        kind = data[position + 4 : position + 8]
        end = position + 12 + length
        if end > len(data):
            raise _unreadable()  # a length the file does not have: a 2 GB claim stops here, before any slicing
        body = data[position + 8 : position + 8 + length]
        if int.from_bytes(data[end - 4 : end], "big") != zlib.crc32(kind + body):
            raise _unreadable()
        if size is None:
            if kind != b"IHDR" or length != 13:
                raise _unreadable()
            size = struct.unpack(">II", body[:8])
        elif kind == b"IHDR":
            raise _unreadable()
        if kind == b"acTL":
            raise ValidationError(
                "Este PNG es animado. Sube el logotipo como un PNG fijo con fondo transparente.", code="logo_animated"
            )
        position = end
        if kind == b"IEND":
            if length != 0:
                raise _unreadable()
            if position != len(data):
                raise _unreadable("Este PNG trae datos de más después de la imagen. Expórtalo otra vez desde tu programa de diseño.")
            return size


def _check_size(width: int, height: int) -> None:
    if not (width and height):
        raise _unreadable()
    if max(width, height) > MAX_PNG_SIDE:
        raise ValidationError(
            f"Esta imagen mide {width} × {height} píxeles. Sube un PNG de hasta {MAX_PNG_SIDE} píxeles por lado.",
            code="logo_pixels",
        )
    if max(width, height) < MIN_PNG_LONG_SIDE:
        raise ValidationError(
            f"Esta imagen mide {width} × {height} píxeles y es demasiado chica. Sube un PNG de al menos "
            f"{MIN_PNG_LONG_SIDE} píxeles en su lado más largo.",
            code="logo_pixels",
        )
    if not proportions_ok(width, height):
        raise ValidationError(
            f"Esta imagen mide {width} × {height} píxeles. Un logotipo puede ser hasta 10 veces más ancho que alto, "
            "o más alto que ancho: recórtalo o sube otro.",
            code="logo_ratio",
        )


def _inspect_png(data: bytes) -> LogoInfo:
    from PIL import Image, UnidentifiedImageError

    width, height = _walk_chunks(data)
    _check_size(width, height)
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG" or image.size != (width, height):
                raise _unreadable()
            image.verify()
        with Image.open(BytesIO(data)) as image:
            image.load()  # every pixel: image data that does not decode is refused here
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
        ValueError,
        EOFError,
        struct.error,
        zlib.error,
    ):
        raise _unreadable() from None
    return LogoInfo(width, height)


def inspect_logo_upload(file) -> LogoInfo:
    """Read an upload (or any file object) and say what it is; ValidationError if it cannot be a logo."""
    size = getattr(file, "size", None)
    if size is not None and size > MAX_BYTES:
        raise ValidationError(
            f"Este archivo pesa {round(size / 1024)} KB y el máximo es 512 KB. {UPLOAD_HELP}", code="logo_size"
        )
    file.seek(0)
    data = file.read(MAX_BYTES + 1)
    file.seek(0)
    if len(data) > MAX_BYTES:
        raise ValidationError(f"Este archivo pesa más de 512 KB. {UPLOAD_HELP}", code="logo_size")
    return _inspect_png(data)  # not a PNG signature (SVG, JPEG, WebP, text): one answer, from _walk_chunks


def validate_church_logo(file) -> None:
    """Model validator shared by Church.logo and Church.logo_dark.

    A file already in storage was checked when it was uploaded: it is not opened again each time the
    church settings are saved (a lost media volume must not lock the settings form).
    """
    if getattr(file, "_committed", False):
        return
    inspect_logo_upload(file)


@functools.lru_cache(maxsize=64)
def _meta_for(path: str, size: int, mtime_ns: int) -> LogoMeta | None:
    """The stored file walked again (cheap: no decoding); anything wrong with it means no logo."""
    try:
        if size > MAX_BYTES:
            raise ValueError("larger than an upload can be")
        with open(path, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
        width, height = _walk_chunks(data)
        _check_size(width, height)
    except Exception:  # noqa: BLE001 - a stored file must never break a page: the wordmark stands in
        logger.warning("The church logo %s cannot be shown as a logo; showing the wordmark instead.", os.path.basename(path))
        return None
    return LogoMeta(width, height, f"{size}-{mtime_ns}")


def logo_meta(fieldfile) -> LogoMeta | None:
    """Size and cache-busting version of a stored logo; None if there is none, it went missing or is not one."""
    try:
        if not fieldfile:
            return None
        path = fieldfile.path
        stat = os.stat(path)
    except FileNotFoundError:
        logger.warning("The church logo %s is missing from MEDIA_ROOT; showing the wordmark instead.", fieldfile.name)
        return None
    except Exception:  # noqa: BLE001 - same rule: never a 500 because of a stored file
        logger.warning("The church logo %s cannot be read; showing the wordmark instead.", getattr(fieldfile, "name", ""))
        return None
    return _meta_for(path, stat.st_size, stat.st_mtime_ns)
