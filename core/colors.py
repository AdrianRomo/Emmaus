"""Colour arithmetic for a church's own palette: WCAG 2.x contrast and the hover shade.

Used by core.forms.ChurchSettingsForm (it refuses colours that would make the app hard to read, audit A11Y-06) and
by the emmaus template filter theme_hover (layout/skeleton.html, audit UI-18).
"""

from __future__ import annotations

import re

_HEX = re.compile(r"#?([0-9a-fA-F]{6})")
WHITE = (255, 255, 255)


def parse_hex(value) -> tuple[int, int, int] | None:
    """(r, g, b) for "#RRGGBB" (or "RRGGBB"); None for anything else."""
    match = _HEX.fullmatch(str(value or "").strip())
    if not match:
        return None
    digits = match.group(1)
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def contrast_with_white(value) -> float | None:
    rgb = parse_hex(value)
    return None if rgb is None else contrast_ratio(rgb, WHITE)


HOVER_MIN_CONTRAST = 1.2  # against the colour itself: a hover the eye can see
HOVER_STEPS = (0.12, 0.16, 0.2, 0.25, 0.3, 0.4)


def hover_shade(value) -> str:
    """The hover of a button colour, as "#rrggbb": about 12 % darker, darker still if needed to reach 1.2:1 against
    the colour itself, and lighter for a colour too dark to darken visibly (#111111, black). Anything that is not
    "#RRGGBB" comes back unchanged.
    """
    rgb = parse_hex(value)
    if rgb is None:
        return str(value or "")
    shade = rgb
    for toward_white in (False, True):
        for amount in HOVER_STEPS:
            if toward_white:
                shade = tuple(round(c + (255 - c) * amount) for c in rgb)
            else:
                shade = tuple(round(c * (1 - amount)) for c in rgb)
            if contrast_ratio(shade, rgb) >= HOVER_MIN_CONTRAST:
                return "#{:02x}{:02x}{:02x}".format(*shade)
    return "#{:02x}{:02x}{:02x}".format(*shade)  # the lightest step: no colour gets this far
