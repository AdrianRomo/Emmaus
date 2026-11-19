"""What a short text typed by (or uploaded for) a person may not contain, wherever it is stored.

Control characters are refused, never cleaned away: a NUL is an error on PostgreSQL and passes on
SQLite, and a bidi control makes a name read as something else. One rule for a tag's name and for
every cell of an import, so the preview and the database agree.
"""

from __future__ import annotations

from events.models import normalize_name

_BIDI = frozenset({0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)})
_LINE = frozenset({0x2028, 0x2029})


def has_control(text: str) -> bool:
    """Anything below U+0020, U+007F to U+009F, a bidi control, or a line or paragraph separator."""
    return any(code < 0x20 or 0x7F <= code <= 0x9F or code in _BIDI or code in _LINE for code in map(ord, text or ""))


def normalized_fits(text: str, limit: int) -> bool:
    """Compatibility normalisation can make a text LONGER ("ﬃ" is three letters): the normalised form is
    what the column holds, so it is the one measured."""
    return len(normalize_name(text or "")) <= limit
