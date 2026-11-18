"""Duplicate a simple event of the church (issue #51): "Eventos de la iglesia", "Duplicar".

The copy is a simple event too (decision 0015: a simple event and a camp do not convert into each other; a camp is
duplicated in its Configuración, ``events/services/duplicate.py``). It takes the event's basics, its page (not
published), its frequent questions and its programme, moved to the new first day. Without a day the programme is not
copied, because each of its moments is on a day. It never takes who signed up, the authorizations, the opinions or
the attendance.

``RELATIONS`` names every model that points at ``Event``: the tests compare it with the models, so a table added
later fails them until somebody decides whether a copy inherits it.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from events.models import Event
from events.services.duplicate import COPIED, NEVER
from events.views.duplicate import DuplicateEventForm
from groups.views import church_admin_view
from people.audit import record_church

from .models import EventFaq, EventPage, EventScheduleItem

WITH_DAY = "with_day"  # copied only when the copy has a first day to hang it from

RELATIONS = {
    "page": COPIED,  # never published: somebody reads it again first
    "faqs": COPIED,
    "schedule_items": WITH_DAY,
    "registrations": NEVER,  # who signed up, who came, and with them the guardians' authorizations
    "feedback": NEVER,
    "feedback_given": NEVER,
    # A simple event has none of the camp's rows; the copy cannot have them either.
    "memberships": NEVER,
    "tutor_assignments": NEVER,
    "membership_aliases": NEVER,  # nicknames of a camp's coordinators (issue #50): a simple event has no camp team
    "access_requests": NEVER,
    "stations": NEVER,
    "participants": NEVER,
    "questionnaire_links": NEVER,
    "interviews": NEVER,
    "follow_ups": NEVER,
    "snapshots": NEVER,
    "audit_events": NEVER,  # the church's log records the copy (people.audit)
    "person_links": NEVER,
}

EVENT_FIELDS = ("kind", "location", "time_zone")
PAGE_FIELDS = ("description", "starts_at", "registration", "capacity", "requires_guardian_consent")


@dataclass
class Duplicated:
    event: Event
    faqs: int = 0
    schedule_items: int = 0
    schedule_skipped: int = 0  # moments of the original left out because the copy has no day yet


class DuplicateSimpleForm(DuplicateEventForm):
    class Meta(DuplicateEventForm.Meta):
        help_texts = {
            "starts_on": "Si lo dejas vacío, el programa no se copia: cada momento del programa necesita un día.",
            "ends_on": "Déjalo vacío para que dure lo mismo que el original.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["starts_on"].label = "Día"
        self.fields["ends_on"].label = "Último día"


@transaction.atomic
def duplicate_simple_event(source: Event, actor, *, name: str, slug: str, starts_on=None, ends_on=None) -> Duplicated:
    if source.camp_tools:
        raise ValueError("Un campamento se duplica desde su Configuración.")
    first = _first_day(source)
    shift = starts_on - first if starts_on and first else None
    if starts_on and ends_on is None and source.starts_on and source.ends_on and source.ends_on > source.starts_on:
        ends_on = starts_on + (source.ends_on - source.starts_on)
    copy = Event(
        church=source.church, created_by=actor, camp_tools=False, join_by_name=False, status=Event.Status.ACTIVE,
        name=name, slug=slug, starts_on=starts_on, ends_on=ends_on,
        **{field: getattr(source, field) for field in EVENT_FIELDS},
    )
    copy.save()
    copy.sedes.set(source.sedes.all())

    page = EventPage.objects.filter(event=source).first()
    values = {field: getattr(page, field) for field in PAGE_FIELDS} if page else {}
    if page and page.registration_closes_on and shift is not None:
        values["registration_closes_on"] = page.registration_closes_on + shift
    EventPage.objects.create(event=copy, created_by=actor, published=False, **values)

    result = Duplicated(event=copy)
    faqs = [EventFaq(event=copy, question=f.question, answer=f.answer, position=f.position) for f in source.faqs.all()]
    EventFaq.objects.bulk_create(faqs)
    result.faqs = len(faqs)

    items = list(source.schedule_items.all())
    if shift is None:
        result.schedule_skipped = len(items)
    else:
        EventScheduleItem.objects.bulk_create([
            EventScheduleItem(event=copy, day=item.day + shift, starts_at=item.starts_at, ends_at=item.ends_at,
                              title=item.title, place=item.place)
            for item in items
        ])
        result.schedule_items = len(items)

    record_church(source.church, actor, "evento_sencillo_creado", f"Creó el evento {copy.name} como copia de {source.name}",
                  copy, source="duplicado", event_id=source.pk)
    return result


def _first_day(source: Event):
    """The day the programme hangs from: the event's first day, or its earliest moment when it has no date."""
    if source.starts_on:
        return source.starts_on
    return source.schedule_items.order_by("day").values_list("day", flat=True).first()


@church_admin_view
def duplicate(request, slug: str):
    source = get_object_or_404(Event, church=request.church, slug=slug, camp_tools=False)
    form = DuplicateSimpleForm(request.POST or None, source=source)
    moments = source.schedule_items.count()
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        result = duplicate_simple_event(
            source, request.user, name=data["name"], slug=data["slug"], starts_on=data["starts_on"], ends_on=data["ends_on"]
        )
        copy = result.event
        text = f"Listo. {copy.name} ya está creado, sin publicar. Revisa su página antes de publicarlo."
        if result.schedule_skipped:
            text += " El programa no se copió porque el evento todavía no tiene día."
        messages.success(request, text)
        return redirect("agenda:page_edit", slug=copy.slug)
    return render(request, "agenda/duplicate.html", {"form": form, "source": source, "moments": moments})
