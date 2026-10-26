from __future__ import annotations

import re
from datetime import datetime

from django import template
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

from core.colors import hover_shade

from .. import charts
from ..services.interviews import KIOSK_KEY
from ..views.common import initials as _initials
from ..views.session import idle_seconds_left

register = template.Library()

# Status words and their mark. The three stage marks mean only a stage: not started (outline), in progress (medium
# petróleo), done or in use (petróleo). A state that is not a stage has a mark of its own (audit UI-07): "closed" (filled
# grey) for what ended without being done (descartada, cancelado, vencida, retirada) and "review" (half grey) for what
# waits for someone to look at it or decide. Colour is never the only signal: the word always goes with the mark.
STATUS_MARKS = {
    "pendiente": ("pending", "Sin entrevista"),
    "en_curso": ("progress", "En curso"),
    "completada": ("done", "Completada"),
    "descartada": ("closed", "Descartada"),  # also "Vencida" (invitations) and the discipleship "Cancelado", "Rechazado", "Cerrada"
    "revisar": ("review", "Revisar"),
    "por_evaluar": ("review", "Por decidir"),  # Interview.follow_up_decision
    "pasada_al_pastor": ("review", "Pasada al pastor"),  # a discipleship question waiting for the pastor
    # Questionnaire versions (QuestionnaireVersion.Status)
    "borrador": ("progress", "Borrador"),
    "publicada": ("done", "Publicada"),
    "retirada": ("closed", "Retirada"),
    "archivado": ("closed", "Archivado"),  # Event.Status
}
FOLLOW_UP_MARKS = {
    "pendiente": ("alert", "Pendiente"),
    "en_proceso": ("progress", "En proceso"),
    "atendido": ("done", "Atendido"),
    "cancelado": ("closed", "Cancelado"),
}


@register.filter
def pct(value):
    if value is None or value == "":
        return "—"
    return f"{value}\u00a0%"  # never split "45 %" across lines


@register.simple_tag
def status(status, label=None):
    """Rótulo status: a small painted mark plus the word (colour is never the only signal)."""
    kind, default = STATUS_MARKS.get(status, ("pending", status))
    return format_html(
        '<span class="status status--{}"><span class="status__mark" aria-hidden="true"></span>{}</span>', kind, label or default
    )


@register.simple_tag
def follow_up_status(status):
    kind, label = FOLLOW_UP_MARKS.get(status, ("pending", status))
    return format_html('<span class="status status--{}"><span class="status__mark" aria-hidden="true"></span>{}</span>', kind, label)


def _progress_text(completed, in_progress, total):
    remaining = max(total - completed - in_progress, 0)
    if not total:
        return "Sin datos todavía"
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
def described(field):
    """The widget with aria-describedby naming its hint and its error, so screen readers announce both."""
    ids = []
    if field.help_text:
        ids.append(f"{field.auto_id}_helptext")
    if field.errors:
        ids.append(f"{field.auto_id}-error")
    attrs = {"aria-describedby": " ".join(ids)} if ids else {}
    if field.errors:
        attrs["aria-invalid"] = "true"
    return field.as_widget(attrs=attrs)


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
    """'hace 5 minutos', 'hace 2 horas', or after a day the date: '23 oct, 14:05'."""
    value = as_datetime(value)
    if not value:
        return ""
    delta = timezone.now() - value
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "hace un momento"
    if minutes < 60:
        return f"hace {minutes} {'minuto' if minutes == 1 else 'minutos'}"
    hours = minutes // 60
    if hours < 24:
        return f"hace {hours} {'hora' if hours == 1 else 'horas'}"
    return fecha_corta(value)


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
def delta_sentence(delta, since):
    """'+11 entrevistas y +3 % de avance desde el corte de las 20:00.' The same sentence as the PDF."""
    from ..services.metrics import delta_sentence as sentence

    return sentence(delta or {}, as_datetime(since))


_OLD_DAY = re.compile(r"^(\d{1,2})/(\d{1,2})$")  # "25/10", what cutoffs saved before the copy pass hold


@register.filter
def chart_day(point):
    """'25 oct' for a point of a timeline that spans days, '' otherwise.

    Read from the bucket's time ("t") rather than the stored text, so saved cutoffs that hold "25/10" read
    like new ones; the presentation's timeline has no "t", so there the old text is converted.
    """
    day = (point or {}).get("day") or ""
    if not day:
        return ""
    from ..services.metrics import SHORT_MONTHS, short_day

    stamp = as_datetime(point.get("t"))
    if isinstance(stamp, datetime):
        return short_day(stamp)  # no localtime(): "t" carries the event's offset, like the hour next to it
    old = _OLD_DAY.match(day)
    if old and 1 <= int(old.group(2)) <= 12:
        return f"{int(old.group(1))} {SHORT_MONTHS[int(old.group(2)) - 1]}"
    return day


@register.simple_tag
def cumulative_chart(points, total, chart_id, title, deck=False, eta_minutes=None, bucket_minutes=60, projection_label="", compact=False):
    # charts.py writes "{day} {label}" under the first point: "25 oct 18:00". No comma there, unlike fecha_corta and
    # the data table: the axis has no room for it (measured on the pastor's chart, where a projection squeezes the axis).
    points = [{**point, "day": chart_day(point)} for point in points or []]
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


@register.simple_tag
def stale_minutes():
    """The minutes after which an interview counts as "interrumpida" (metrics.STALE_MINUTES), so no page says another number."""
    from ..services.metrics import STALE_MINUTES

    return STALE_MINUTES


@register.filter
def duration(minutes):
    from ..services.metrics import format_duration

    return format_duration(minutes)


@register.filter
def duration_short(minutes):
    """'6 h 15 min', '2 h', '45 min': the compact form, only for a big figure (.figures__value), where "6 horas 15
    minutos" at 36 px overflowed its tile (audit UI-01). Sentences keep the words (duration). A no-break space joins
    each number to its unit, so a narrow tile wraps between "6 h" and "15 min", never inside them."""
    if minutes is None or minutes == "":
        return ""
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours}\u00a0h {mins}\u00a0min"
    if hours:
        return f"{hours}\u00a0h"
    return f"{mins}\u00a0min"


@register.simple_tag(takes_context=True)
def staff_idle_attrs(context):
    """The <body> attributes emmaus.js reads for the "¿Sigues aquí?" notice before the staff idle logout (audit A11Y-04):
    the idle limit events.middleware.StaffIdleLogoutMiddleware applies, the seconds really left (from the session's
    last touch, which can be up to 30 s older than this page), and the URL that touches the session. Nothing for
    someone signed out, for a browser confined to the campero's screens (its 404 and CSRF pages use base.html), or
    when the idle logout is off."""
    user = context.get("user")
    request = context.get("request")
    seconds = int(getattr(settings, "EMMAUS_IDLE_LOGOUT_SECONDS", 0) or 0)
    if seconds <= 0 or not getattr(user, "is_authenticated", False):
        return ""
    session = getattr(request, "session", None)
    if session is not None and KIOSK_KEY in session:
        return ""
    left = idle_seconds_left(request) if session is not None else seconds
    return format_html(
        'data-idle-seconds="{}" data-idle-remaining="{}" data-idle-ping-url="{}"', seconds, left, reverse("events:still_here")
    )


@register.filter
def theme_hover(color):
    """The church's own primary colour, about 12 % darker, for --petroleo-hover (audit UI-18: it was the same colour)."""
    return hover_shade(color)


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
