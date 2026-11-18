"""A simple event of the church: a service, a meal, an evening of worship. It is an ``events.Event``
like a camp (one events architecture, decision 0013) with ``camp_tools`` off: no camperos, no
interviews, no stations. It is created and changed here, in the agenda, and never opens the camp's
screens."""

from django import forms
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from events.forms import EventForm
from events.models import Event
from groups.views import church_admin_view
from people import policy
from people.audit import record_church

from .models import EventPage

SIMPLE_KINDS = [Event.Kind.OTHER, Event.Kind.CONFERENCE, Event.Kind.RETREAT, Event.Kind.DISCIPLESHIP]


class SimpleEventForm(EventForm):
    """The name, the kind, the place and the days. The address forms itself from the name."""

    required_messages = {**EventForm.required_messages, "starts_on": "Escribe qué día es."}
    # agenda/urls.py routes these before an event's address.
    RESERVED_SLUGS = EventForm.RESERVED_SLUGS | {"administrar"}

    class Meta(EventForm.Meta):
        fields = ["name", "kind", "location", "starts_on", "ends_on", "slug"]
        help_texts = {"ends_on": "Vacío: dura un solo día."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].widget = forms.HiddenInput()  # formed from the name; nobody needs to type it here
        self.fields["starts_on"].required = True
        self.fields["starts_on"].label = "Día"
        self.fields["ends_on"].label = "Último día"
        self.fields["kind"].choices = [(kind.value, kind.label) for kind in SIMPLE_KINDS]

    def clean(self):
        cleaned = super().clean()
        first, last = cleaned.get("starts_on"), cleaned.get("ends_on")
        if first and last and last < first:
            self.add_error("ends_on", "El último día no puede ser antes del primero.")
        return cleaned


def create_simple_event(church, actor, **fields) -> Event:
    """A simple event of ``church`` and its page, not published yet. The page starts asking for a
    guardian's authorization when the church's policy says new events do."""
    with transaction.atomic():
        event = Event(church=church, created_by=actor, camp_tools=False, join_by_name=False,
                      status=Event.Status.ACTIVE, **fields)
        event.save()
        # The church's policy "Los eventos nuevos piden autorización para menores" is the page's starting value.
        EventPage.objects.create(event=event, created_by=actor,
                                 requires_guardian_consent=policy.minors_consent_default(church))
        record_church(church, actor, "evento_sencillo_creado", f"Creó el evento {event.name}", event)
    return event


def update_simple_event(event: Event, actor, **fields) -> Event:
    with transaction.atomic():
        locked = Event.objects.select_for_update().get(pk=event.pk, camp_tools=False)
        for name, value in fields.items():
            setattr(locked, name, value)
        locked.save()
        record_church(locked.church, actor, "evento_sencillo_datos", f"Modificó los datos del evento {locked.name}", locked)
    return locked


@church_admin_view
def create(request):
    form = SimpleEventForm(request.POST or None, initial={"kind": Event.Kind.OTHER})
    if request.method == "POST" and form.is_valid():
        event = create_simple_event(request.church, request.user, **form.cleaned_data)
        messages.success(request, f"{event.name} ya está creado. Ahora escribe su página y publícalo.")
        return redirect("agenda:page_edit", slug=event.slug)
    return render(request, "agenda/simple_form.html", {"form": form, "event": None})


@church_admin_view
def edit(request, slug: str):
    event = get_object_or_404(Event, church=request.church, slug=slug, camp_tools=False)
    form = SimpleEventForm(request.POST or None, instance=event)
    if request.method == "POST" and form.is_valid():
        update_simple_event(event, request.user, **form.cleaned_data)
        messages.success(request, "Guardado.")
        return redirect("agenda:admin_list")
    return render(request, "agenda/simple_form.html", {"form": form, "event": event})
