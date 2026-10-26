"""The church's logo in the wordmark's slot (docs/design-system.md, "Marca de la iglesia").

    {% load brand %}
    {% church_brand church variant="claro" size="bar" href=home_url %}

With a logo (a PNG, core.logos): an <img> whose alt is the church's name and whose width and height come from
the file, fitted to the slot without changing its proportions, so nothing moves when it loads. Without one
(or with the stored file gone or unreadable): exactly the painted wordmark that was there before, a link
when ``href`` is given. Emmaus is the product, the church is the brand: with a logo uploaded nothing on a
screen or a report says "Emmaus" any more (``brand_name`` gives the <title> and the PDF the church's name too).

``variant="oscuro"`` is for dark backgrounds (the "Noche" deck): the church's white version if it uploaded
one; otherwise its usual logo on a small white plate, because a black logo on a dark slide would vanish.
"""

from __future__ import annotations

import math

from django import template
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core.logos import logo_meta

register = template.Library()

# (max width, max height) in CSS px: the logo fills the box as far as its proportions allow, never past either side.
SIZES = {
    "bar": (180, 32),
    "auth": (220, 72),
    "mark": (400, 40),  # 40 px tall; 400 is what the widest logo allowed (10:1, core.logos) needs
    "deck": (320, 120),  # the title slide; on a 1920 px slide, drawn in cqw, so it scales with the slide like everything on it
    "deck-top": (220, 48),  # the top row of every other slide, beside the event's name
}
DECK_SLIDE_WIDTH = 1920
VARIANTS = ("claro", "oscuro")


def fit(width: float, height: float, size: str) -> tuple[int, int] | None:
    """The logo scaled into the slot's box, proportions kept, rounded only at the end; None if it has no size."""
    try:
        if not all(isinstance(side, (int, float)) and math.isfinite(side) and side > 0 for side in (width, height)):
            return None
        max_width, max_height = SIZES[size]
        scale = min(max_width / width, max_height / height)
        return max(1, round(width * scale)), max(1, round(height * scale))
    except Exception:  # noqa: BLE001 - whatever the stored file claimed, the page draws (with the wordmark)
        return None


def choose(church, variant: str):
    """(URL variant, stored logo, needs the white plate) or None for the wordmark."""
    if not church:
        return None
    if variant == "oscuro":
        meta = logo_meta(church.logo_dark)
        if meta:
            return "oscuro", meta, False
    meta = logo_meta(church.logo)
    if meta:
        return "claro", meta, variant == "oscuro"
    return None


def logo_url(church, variant: str, meta) -> str:
    return f"{reverse('church_logo', args=[church.slug, variant])}?v={meta.version}"


@register.filter
def has_logo(church, variant="claro") -> bool:
    """For slots that only exist with a logo (above the sign-in title, the title slide): the file is really there."""
    return choose(church, variant) is not None


@register.simple_tag
def brand_name(church) -> str:
    """Who the page speaks for where only words fit (the <title>, the PDF's author): the church's name once it has
    a logo, "Emmaus" until then. The same rule as the logo itself: with one uploaded, Emmaus steps back everywhere."""
    return church.name if choose(church, "claro") else "Emmaus"


@register.simple_tag
def church_brand(church, variant="claro", size="bar", href=""):
    if size not in SIZES:
        raise ValueError(f"church_brand: unknown size {size!r}")
    if variant not in VARIANTS:
        raise ValueError(f"church_brand: unknown variant {variant!r}")
    if href:
        wordmark = format_html('<a class="wordmark" href="{}">Emmaus</a>', href)
    else:
        wordmark = mark_safe('<span class="wordmark">Emmaus</span>')
    chosen = choose(church, variant)
    if chosen is None:
        return wordmark
    url_variant, meta, plate = chosen
    fitted = fit(meta.width, meta.height, size)
    if fitted is None:
        return wordmark
    width, height = fitted
    style = mark_safe(f' style="width:{width * 100 / DECK_SLIDE_WIDTH:.2f}cqw"') if size.startswith("deck") else ""
    html = format_html(
        '<img class="brand__logo brand__logo--{}" src="{}" alt="{}" width="{}" height="{}"{}>',
        size,
        logo_url(church, url_variant, meta),
        church.name,
        width,
        height,
        style,
    )
    if plate:
        html = format_html('<span class="brand__plate">{}</span>', html)
    if href:
        html = format_html('<a class="brand brand--{}" href="{}">{}</a>', size, href, html)
    return html
