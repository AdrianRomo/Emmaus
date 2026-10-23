from __future__ import annotations

from datetime import datetime

from django import template
from django.utils import timezone
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

from .. import charts
from ..views.common import initials as _initials

register = template.Library()

STATUS_CHIPS = {
    "pendiente": ("chip--pending", "Sin entrevista"),
    "en_curso": ("chip--progress", "En curso"),
    "completada": ("chip--done", "Completada"),
    "descartada": ("chip--pending", "Descartada"),
}
FOLLOW_UP_CHIPS = {
    "pendiente": ("chip--alert", "Pendiente"),
    "en_proceso": ("chip--progress", "En proceso"),
    "atendido": ("chip--done", "Atendido"),
    "cancelado": ("chip--pending", "Cancelado"),
}


@register.filter
def pct(value):
    if value is None or value == "":
        return "—"
    return f"{value}\u00a0%"  # never split "45 %" across lines


@register.simple_tag
def status_chip(status, label=None):
    css, default = STATUS_CHIPS.get(status, ("chip--pending", status))
    return format_html('<span class="chip {}">{}</span>', css, label or default)


@register.simple_tag
def follow_up_chip(status):
    css, label = FOLLOW_UP_CHIPS.get(status, ("chip--pending", status))
    return format_html('<span class="chip {}">{}</span>', css, label)


@register.simple_tag
def status(status, label=None):
    """Rótulo status: a small painted mark plus the word (colour is never the only signal)."""
    css, default = STATUS_CHIPS.get(status, ("chip--pending", status))
    return format_html(
        '<span class="status status--{}"><span class="status__mark" aria-hidden="true"></span>{}</span>',
        css.removeprefix("chip--"),
        label or default,
    )


@register.simple_tag
def follow_up_status(status):
    css, label = FOLLOW_UP_CHIPS.get(status, ("chip--pending", status))
    return format_html(
        '<span class="status status--{}"><span class="status__mark" aria-hidden="true"></span>{}</span>',
        css.removeprefix("chip--"),
        label,
    )


def _progress_text(completed, in_progress, total):
    remaining = max(total - completed - in_progress, 0)
    if not total:
        return "Sin camperos todavía"
    return f"{completed} de {total} completadas, {in_progress} en curso, {remaining} sin iniciar"


@register.simple_tag
def tally(completed, in_progress, total, limit=80):
    """One mark per campero (completed, in progress, not started). Above `limit` people a ruler reads better."""
    completed, in_progress, total = int(completed or 0), int(in_progress or 0), int(total or 0)
    if total > limit:
        return ruler(completed, in_progress, total)
    remaining = max(total - completed - in_progress, 0)
    marks = (
        '<span class="tally__mark tally__mark--done"></span>' * completed
        + '<span class="tally__mark tally__mark--progress"></span>' * in_progress
        + '<span class="tally__mark"></span>' * remaining
    )
    return format_html('<div class="tally" role="img" aria-label="{}">{}</div>', _progress_text(completed, in_progress, total), mark_safe(marks))


@register.simple_tag
def ruler(completed, in_progress, total, marker=None, marker_label=""):
    """Stacked bar: completed, in progress, remaining track. Optional marker (a percent) such as the event average."""
    completed, in_progress, total = int(completed or 0), int(in_progress or 0), int(total or 0)
    segs = []
    if total and completed:
        segs.append(f'<span class="ruler__seg ruler__seg--done" style="width:{100 * completed / total:.2f}%"></span>')
    if total and in_progress:
        segs.append(f'<span class="ruler__seg ruler__seg--progress" style="width:{100 * in_progress / total:.2f}%"></span>')
    tick = ""
    if marker is not None and marker != "":
        tick = f'<span class="ruler__marker" style="left:{float(marker):.2f}%" title="{escape(marker_label)}"></span>'
    return format_html(
        '<div class="ruler" role="img" aria-label="{}">{}{}</div>',
        _progress_text(completed, in_progress, total),
        mark_safe("".join(segs)),
        mark_safe(tick),
    )


@register.filter
def initials(name):
    return _initials(str(name or ""))


@register.filter
def as_datetime(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return value


@register.filter
def since(value):
    """'hace 5 min', 'hace 2 h', or a date."""
    value = as_datetime(value)
    if not value:
        return ""
    delta = timezone.now() - value
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "hace un momento"
    if minutes < 60:
        return f"hace {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"hace {hours} h"
    return timezone.localtime(value).strftime("%d/%m %H:%M")


@register.filter
def get_item(mapping, key):
    if mapping is None:
        return None
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def signed(value):
    if value is None:
        return "—"
    if value > 0:
        return f"+{value}"
    if value < 0:
        return f"−{abs(value)}"
    return "sin cambio"


@register.simple_tag
def meter(completed, in_progress, total, size=""):
    """Completed / in progress / remaining, with a visually hidden text equivalent."""
    completed, in_progress, total = int(completed or 0), int(in_progress or 0), int(total or 0)
    if total:
        done_w = 100 * completed / total
        prog_w = 100 * in_progress / total
    else:
        done_w = prog_w = 0
    remaining = max(total - completed - in_progress, 0)
    segs = []
    if done_w:
        segs.append(f'<span class="meter__seg meter__seg--done" style="width:{done_w:.2f}%"></span>')
    if prog_w:
        segs.append(f'<span class="meter__seg meter__seg--progress" style="width:{prog_w:.2f}%"></span>')
    text = (
        f"{completed} de {total} completadas, {in_progress} en curso, {remaining} sin iniciar"
        if total
        else "Sin participantes todavía"
    )
    return mark_safe(
        f'<div class="meter {"meter--lg" if size == "lg" else ""}">'
        f'<div class="meter__track" role="img" aria-label="{text}">{"".join(segs)}</div></div>'
    )


@register.simple_tag
def cumulative_chart(points, total, chart_id, title, deck=False, eta_minutes=None, bucket_minutes=60, projection_label="", compact=False):
    return charts.cumulative_chart(
        points,
        int(total or 0),
        chart_id=chart_id,
        title=title,
        deck=bool(deck),
        compact=bool(compact),
        eta_minutes=eta_minutes,
        bucket_minutes=int(bucket_minutes or 60),
        projection_label=projection_label,
    )


@register.simple_tag
def columns_chart(points, chart_id, title, deck=False, partial_last=False, compact=False):
    return charts.columns_chart(points, chart_id=chart_id, title=title, deck=bool(deck), partial_last=bool(partial_last), compact=bool(compact))


@register.inclusion_tag("partials/ui/brand_mark.html")
def brand_mark(size="1.75rem"):
    return {"size": size}


@register.filter
def duration(minutes):
    from ..services.metrics import format_duration

    return format_duration(minutes)


MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _local(value):
    value = as_datetime(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        return timezone.localtime(value) if timezone.is_aware(value) else value
    return value


@register.filter
def fecha_larga(value):
    """'Viernes 23 de octubre' (Spanish style: lowercase month)."""
    value = _local(value)
    if value is None:
        return ""
    text = f"{DIAS[value.weekday()]} {value.day} de {MESES[value.month - 1]}"
    return text[0].upper() + text[1:]


@register.filter
def fecha_corta(value):
    """'23 oct, 14:05' for datetimes; '23 oct 2026' for dates."""
    value = _local(value)
    if value is None:
        return ""
    base = f"{value.day} {MESES[value.month - 1][:3]}"
    if isinstance(value, datetime):
        return f"{base}, {value:%H:%M}"
    return f"{base} {value.year}"


@register.filter
def cuarto(value):
    """Rooms are often numbers ('12') but sometimes names ('Cabaña 4')."""
    text = str(value or "").strip()
    if not text:
        return "Sin cuarto"
    return f"Cuarto {text}" if text[0].isdigit() else text
