"""Small drawn pieces of the redesigned shell (docs/v2/DESIGN_SYSTEM.md): the navigation icons, the
arch monogram of a person or a group, and the date block of an agenda row."""

import re

from django import template
from django.utils import formats
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

# One family: 24px grid, 1.75px round strokes, no fills. Each names a place of the navigation,
# never an action; every use sits next to its word.
ICONS = {
    # the atrium's door: an arch
    "inicio": '<path d="M5 21V11a7 7 0 0 1 14 0v10"/><path d="M3 21h18"/><path d="M12 21v-6"/>',
    "grupos": '<circle cx="9" cy="9" r="3.25"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 5.9a3.25 3.25 0 0 1 0 6.2"/><path d="M18 14.6a6 6 0 0 1 3 5.4"/>',
    "eventos": '<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17"/><path d="M8 3v4M16 3v4"/>',
    "personas": '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20.5a7 7 0 0 1 14 0"/>',
    "avisos": '<path d="M4 10v4a1 1 0 0 0 1 1h2l5 4V5L7 9H5a1 1 0 0 0-1 1z"/><path d="M16 9a4 4 0 0 1 0 6"/><path d="M18.5 6.5a8 8 0 0 1 0 11"/>',
    "servir": '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
    "devocional": '<path d="M12 6.5C10 5 7.5 4.5 4.5 4.5v13c3 0 5.5.5 7.5 2 2-1.5 4.5-2 7.5-2v-13c-3 0-5.5.5-7.5 2z"/><path d="M12 6.5v13"/>',
    # a bookmark with two lines: a reading plan
    "plan": '<path d="M7 3.5h10a1.5 1.5 0 0 1 1.5 1.5v15.5L12 17l-6.5 3.5V5A1.5 1.5 0 0 1 7 3.5z"/><path d="M9.5 8.5h5M9.5 12h5"/>',
    "recursos": '<circle cx="12" cy="12" r="8.5"/><path d="M10.2 8.8v6.4l5.3-3.2z"/>',
    "equipo": '<path d="M4 20V9l8-5 8 5v11"/><path d="M9 20v-6h6v6"/>',
    "acompanar": '<circle cx="8" cy="8" r="3"/><circle cx="17" cy="9.5" r="2.5"/><path d="M2.5 19a5.5 5.5 0 0 1 11 0"/><path d="M15 19a4.5 4.5 0 0 1 6.5-4"/>',
    "guia": '<path d="M12 3l2.2 5.3 5.8.5-4.4 3.8 1.3 5.6L12 15.2 7.1 18.2l1.3-5.6L4 8.8l5.8-.5z"/>',
    "camino": '<path d="M6 20c0-5 12-4 12-9a3 3 0 0 0-6 0"/><circle cx="6" cy="20" r="1.25"/><circle cx="12" cy="11" r="1.25"/>',
    "ajustes": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    "ayuda": '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.5a2.5 2.5 0 1 1 3.6 2.3c-.8.4-1.2 1-1.2 1.9"/><path d="M12 16.9v.1"/>',
    "cuenta": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="10" r="2.75"/><path d="M6.8 18.2a5.6 5.6 0 0 1 10.4 0"/>',
    "mas": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "salir": '<path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4"/><path d="M10 16l-4-4 4-4M6 12h9"/>',
}


@register.simple_tag
def icon(name: str):
    """A decorative icon (``aria-hidden``): the word beside it is the name."""
    return format_html(
        '<svg class="icon" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" '
        'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">{}</svg>',
        mark_safe(ICONS[name]),
    )


SMALL_WORDS = {"de", "del", "la", "las", "los", "el", "y", "e", "en", "a", "con", "para"}


def initials(text: str) -> str:
    """"Jóvenes del jueves" -> "JJ"; "Ana" -> "A"; an e-mail -> its first letter."""
    words = [w for w in re.split(r"[\s@._-]+", str(text or "").strip()) if w]
    strong = [w for w in words if w.lower() not in SMALL_WORDS] or words
    return "".join(w[0] for w in strong[:2]).upper()


@register.simple_tag
def monogram(text, size=""):
    """The arch with the initials of a person or a group. Decorative: the name is always written
    beside it. ``size``: "" (44px), "sm" (36px) or "lg" (72px)."""
    letters = initials(text)
    if not letters:
        return ""
    css = f"mono mono--{size}" if size else "mono"
    return format_html('<span class="{}" aria-hidden="true">{}</span>', css, letters)


@register.inclusion_tag("partials/ui/date_block.html")
def date_block(value):
    """The date of an agenda row, drawn: "jue", "19", "nov". The row's text says the date in words too."""
    return {
        "weekday": formats.date_format(value, "D").lower().rstrip("."),
        "day": value.day,
        "month": formats.date_format(value, "M").lower().rstrip("."),
    }


@register.filter
def tel(value: str) -> str:
    """A phone number as a ``tel:`` target: digits, with a leading "+" kept."""
    text = str(value or "").strip()
    digits = re.sub(r"\D", "", text)
    return ("+" if text.startswith("+") else "") + digits
