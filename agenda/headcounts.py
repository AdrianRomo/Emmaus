""""¿Cuántos vinimos?": the headcount of one gathering (issue #93, wave 12).

Somebody counts the room and the administration types three numbers at most: the total and, if the church
counts them, adults and children. No name, no list, nothing per person. It is a different fact from the named
attendance of the registrations ("Inscritos", "Guardar la asistencia"): neither is worked out from the other and
neither changes the other. An ordinary service has a headcount and no registrations at all.

What is shown. The total of a gathering open to the church is not about identifiable people and is shown as it
was typed. The children's number is: three children in a small church are three known children. So wherever a
count is READ (the series' table, Panorama) the two parts go through ``told``: both parts are shown only when each
one (typed, or what the total leaves) is at least the church's minimum reporting size (``people.policy``, floor
5); otherwise the adults are not shown (the total minus the adults would give the children) and the children
read "Menos de N" when it is their number that is small. The form that types the count shows it back exactly, to who typed it: the
church's administration, so that it can be corrected.

No trend per person, no "who stopped coming": there is no person here to follow.
"""

from __future__ import annotations

from django import forms
from django.db import transaction
from django.utils import timezone

from core.forms import TuErrorMessagesMixin
from events.models import Event
from people.audit import record_church

from . import services
from .models import EventHeadcount, SeriesOccurrence

TREND_DATES = 12
MAX_TOTAL = 100000


class HeadcountForm(TuErrorMessagesMixin, forms.Form):
    total = forms.IntegerField(label="¿Cuántas personas vinieron?", min_value=0, max_value=MAX_TOTAL,
                               widget=forms.NumberInput(attrs={"inputmode": "numeric", "min": 0}))
    adults = forms.IntegerField(label="Personas adultas", required=False, min_value=0, max_value=MAX_TOTAL,
                                widget=forms.NumberInput(attrs={"inputmode": "numeric", "min": 0}))
    children = forms.IntegerField(label="Niñas y niños", required=False, min_value=0, max_value=MAX_TOTAL,
                                  widget=forms.NumberInput(attrs={"inputmode": "numeric", "min": 0}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["total"].error_messages.update(required="Escribe cuántas personas vinieron.", invalid="Escribe un número, por ejemplo 84.")
        for name in ("adults", "children"):
            self.fields[name].error_messages["invalid"] = "Escribe un número, o deja el campo vacío."

    def clean(self):
        cleaned = super().clean()
        total, adults, children = cleaned.get("total"), cleaned.get("adults"), cleaned.get("children")
        if total is None:
            return cleaned
        if adults is not None and children is not None and adults + children != total:
            self.add_error("total", f"Las personas adultas y las niñas y niños suman {adults + children}: el total tiene que ser ese número.")
        else:
            for name, value in (("adults", adults), ("children", children)):
                if value is not None and value > total:
                    self.add_error(name, "No puede ser más que el total.")
        return cleaned


def record(event: Event, actor, *, total: int, adults: int | None = None, children: int | None = None) -> EventHeadcount:
    """Write or correct the count of ``event``, from its first day on. The audit row says that a count was written
    for which event, never the numbers: a children's count of 3 in the log is the same 3."""
    if not services.has_started(event):
        raise services.AgendaError("El evento todavía no empieza. El conteo se registra desde su primer día.", code="not_started")
    for value in (total, adults, children):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_TOTAL):
            raise services.AgendaError("Escribe un número.", code="number")
    if (adults is not None and adults > total) or (children is not None and children > total) or (
            adults is not None and children is not None and adults + children != total):
        raise services.AgendaError("Las personas adultas y las niñas y niños no pueden sumar otro número que el total.", code="parts")
    with transaction.atomic():
        row = EventHeadcount.objects.select_for_update().filter(event=event).first()
        created = row is None
        row = row or EventHeadcount(event=event)
        row.total, row.adults, row.children = total, adults, children
        row.recorded_by, row.recorded_at = actor, timezone.now()
        row.save()
        verb = "Registró" if created else "Corrigió"
        record_church(event.church, actor, "evento_conteo", f"{verb} cuántas personas vinieron a {event.name}", row, event_id=event.pk)
    return row


def told(row: EventHeadcount, minimum: int) -> dict:
    """One count as it may be READ: ``total`` always; ``adults`` and ``children`` as numbers only when BOTH parts
    (typed, or what the total leaves for the one that was not) are at least ``minimum``; else ``adults`` None and
    ``children_words`` "Menos de N" when the children's number is the small one (empty when nothing was typed
    about the parts, or when it is the adults' number that is small)."""
    typed = row.adults is not None or row.children is not None
    # A part that was not typed is still known to whoever reads the other one beside the total.
    adults = row.adults if row.adults is not None else (row.total - row.children if typed else None)
    children = row.children if row.children is not None else (row.total - row.adults if typed else None)
    exact = typed and adults >= minimum and children >= minimum
    few_children = typed and children < minimum
    return {
        "total": row.total,
        "adults": row.adults if exact else None,
        "children": row.children if exact else None,
        "children_words": f"Menos de {minimum}" if few_children else "",
    }


def series_trend(series, minimum: int, today=None) -> list[dict]:
    """The last ``TREND_DATES`` dates of a series that have a count, oldest first: ``day``, ``event`` and ``told``,
    plus ``share`` (the total against the largest of them, 0 to 100) for the bar."""
    rows = list(
        SeriesOccurrence.objects.filter(series=series, event__headcount__isnull=False)
        .select_related("event", "event__headcount").order_by("-event__starts_on", "-pk")[:TREND_DATES]
    )
    return _trend([(row.event.starts_on or row.day, row.event, row.event.headcount) for row in reversed(rows)], minimum)


def _trend(items, minimum: int) -> list[dict]:
    largest = max((count.total for _, _, count in items), default=0)
    return [
        {"day": day, "event": event, **told(count, minimum), "share": round(count.total * 100 / largest) if largest else 0}
        for day, event, count in items
    ]


def church_trends(church, minimum: int) -> list[dict]:
    """For Panorama: each series of ``church`` that has counts, with its last ``TREND_DATES`` of them. One query,
    whatever the number of series; no name of a person exists anywhere near it."""
    rows = (
        SeriesOccurrence.objects.filter(series__church=church, event__headcount__isnull=False)
        .select_related("series", "event", "event__headcount").order_by("series__name", "series_id", "-event__starts_on", "-pk")
    )
    by_series: dict[int, list] = {}
    names: dict[int, str] = {}
    for row in rows[: TREND_DATES * 50]:
        names[row.series_id] = row.series.name
        mine = by_series.setdefault(row.series_id, [])
        if len(mine) < TREND_DATES:
            mine.append((row.event.starts_on or row.day, row.event, row.event.headcount))
    return [{"name": names[pk], "dates": _trend(list(reversed(items)), minimum)} for pk, items in by_series.items()]
