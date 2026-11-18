"""Configuración, "Duplicar como plantilla" (issue #51): the next camp starts from this one's configuration.

Only somebody who administers the church creates an event in it (like "Crear evento"). Everybody else is refused
the way every screen of a camp refuses them (``event_view``): 403 for another church's administrator, as
people/tests/test_tenant_isolation.py pins for every address under an event; 404 for a simple event's address."""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse

from core.services import send_email_safely

from ..access import administered_churches
from ..forms import EventForm
from ..models import Event
from ..services import team as team_svc
from ..services.duplicate import duplicate_camp, suggest_name
from .admin import _activation_link, _can_issue_activation_link
from .common import ADMIN, event_view


class DuplicateEventForm(EventForm):
    """The name (suggested) and the days of the copy. Its address forms itself from the name, and takes the next
    number when it is taken (EventForm.clean_slug). Also the base of the agenda's form for a simple event."""

    required_messages = {"name": "Escribe el nombre del evento nuevo."}
    RESERVED_SLUGS = EventForm.RESERVED_SLUGS | {"administrar"}  # agenda/urls.py routes it before an event's address

    class Meta(EventForm.Meta):
        fields = ["name", "starts_on", "ends_on", "slug"]
        help_texts = {
            "starts_on": "Si todavía no la sabes, déjala vacía: la escribes después en Configuración.",
        }

    def __init__(self, *args, source: Event, **kwargs):
        kwargs.setdefault("initial", {}).setdefault("name", suggest_name(source.name))
        super().__init__(*args, **kwargs)
        self.fields["slug"].widget = forms.HiddenInput()
        self.fields["name"].label = "Nombre del evento nuevo"
        # EventForm.__init__ already asked for self["name"] (the address mirrors it), and a bound field keeps the label
        # it was made with: forget that one so the page shows the new label.
        self._bound_fields_cache.pop("name", None)

    def clean(self):
        cleaned = super().clean()
        first, last = cleaned.get("starts_on"), cleaned.get("ends_on")
        if last and not first:
            self.add_error("starts_on", "Escribe también la fecha de inicio, o deja las dos vacías.")
        elif first and last and last < first:
            self.add_error("ends_on", "La fecha de término no puede ser anterior a la de inicio.")
        return cleaned


class DuplicateCampForm(DuplicateEventForm):
    copy_team = forms.BooleanField(
        label="Copiar también al equipo",
        required=False,
        help_text="Las mismas personas con el mismo rol, y quién supervisa a quién. El acceso a información "
                  "confidencial no se copia: se otorga de nuevo. A cada persona le avisamos por correo.",
    )


def can_duplicate(user, event: Event) -> bool:
    return administered_churches(user).filter(pk=event.church_id).exists()


def _invite(request, event: Event, memberships) -> tuple[int, int]:
    """Tell each person again, for the new event: (how many e-mails went out, how many people got none).
    A person who never created a password gets a new link to create it; nothing of an earlier message is reused."""
    configured = team_svc.email_is_configured()
    sent = missed = 0
    sign_in = request.build_absolute_uri(reverse("login"))
    for membership in memberships:
        user = membership.user
        if user.pk == request.user.pk:
            continue
        if not (configured and user.email):
            missed += 1
            continue
        if _can_issue_activation_link(event, user):
            how = f"Crea tu contraseña aquí:\n{_activation_link(request, user)}"
        else:
            how = f"Entra con tu contraseña de siempre:\n{sign_in}"
        ok = send_email_safely(
            f"Te invitaron a {event.name} en Emmaus",
            f"Hola {user.first_name}:\n\nTe agregaron al equipo de {event.name}, como "
            f"{membership.get_role_display().lower()}. {how}\n\nSi no esperabas este correo, ignóralo.",
            [user.email],
        )
        sent, missed = (sent + 1, missed) if ok else (sent, missed + 1)
    return sent, missed


@event_view(ADMIN)
def duplicate(request, event, access):
    if not can_duplicate(request.user, event):
        raise PermissionDenied
    form = DuplicateCampForm(request.POST or None, source=event)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        result = duplicate_camp(
            event, request.user, name=data["name"], slug=data["slug"], starts_on=data["starts_on"],
            ends_on=data["ends_on"], copy_team=data["copy_team"],
        )
        copy = result.event
        messages.success(
            request,
            f"Listo. {copy.name} ya está creado, en preparación, con la configuración de {event.name}. "
            "Confirma los procedimientos de la iglesia y registra a sus camperos.",
        )
        if data["copy_team"]:
            sent, missed = _invite(request, copy, result.members)
            people = len(result.members)
            text = "Copiamos a 1 persona del equipo." if people == 1 else f"Copiamos a {people} personas del equipo."
            if sent:
                text += " Le avisamos por correo a 1." if sent == 1 else f" Les avisamos por correo a {sent}."
            if missed:
                text += (
                    f" A {missed} no le{'s' if missed != 1 else ''} llegó correo: avísale{'s' if missed != 1 else ''} tú. "
                    "Quien todavía no tiene contraseña necesita un enlace para crearla: lo generas en Equipo, desde su ficha."
                )
            messages.info(request, text)
        return redirect("events:admin_home", slug=copy.slug)
    return render(request, "events/admin/duplicate.html", {"form": form})
