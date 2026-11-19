"""How "Panorama" draws what ``overview.services.build`` returns. Presentation only: nothing here
counts, rounds or decides what may be shown. A value arrives already as the page may tell it (a
number, words, or ``None``) and leaves as the same value, drawn."""

from django import template
from django.utils.html import format_html

register = template.Library()

PILL = '<span class="chip chip--plain">{}</span>'
# The words of ``services.share`` that say "half or more came"; every other band says "fewer than half".
HIGH = frozenset({"la mitad o más", "casi todas"})


@register.filter
def told(value, minimum):
    """A total as the page may tell it. A number stays a number. Words (a band such as "Casi todas")
    go on a neutral pill, and so does a total the service kept back (``None``), which reads
    "Menos de N": never a blank, never a guess."""
    if value is None:
        return format_html(PILL, f"Menos de {minimum}")
    text = str(value)
    if text.isdigit():
        return text
    return format_html(PILL, text)


@register.filter
def trend_mark(label) -> str:
    """One meeting of a group's trend, as a mark: "exact" (the label is the number itself, written as
    it is), "high" (half or more came: "la mitad o más", "casi todas") or "low" (fewer than half:
    "menos de la mitad", "menos de N"). Two heights for four bands, so a mark never says more than its
    band does; the words themselves are beside it for a screen reader."""
    text = str(label).strip().lower()
    if text.isdigit():
        return "exact"
    return "high" if text in HIGH else "low"
