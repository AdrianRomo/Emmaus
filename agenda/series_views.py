"""The screens of a series (issue #93): the administration writes and ends it and calls a day off; the
people of the church read its dates. The rules live in ``agenda.series``.

Every view goes through ``church_admin_view`` (church ``manage``) or ``community_view``, and a series is
always fetched inside ``request.church``: the number of another church's series is a 404."""

from zoneinfo import available_timezones

from django import forms
from django.contrib import messages
from django.core.validators import MaxLengthValidator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.forms import TuErrorMessagesMixin
from events.forms import MEXICO_TIME_ZONES
from groups.views import church_admin_view, community_view

from . import calendar, series, services
from .models import EventPage, EventRegistration, EventSeries, SeriesOccurrence

INTERVALS = [(1, "Cada semana"), (2, "Cada 2 semanas"), (3, "Cada 3 semanas"), (4, "Cada 4 semanas")]
PAST_SHOWN = 8


class SeriesForm(TuErrorMessagesMixin, forms.ModelForm):
    weekdays = forms.TypedMultipleChoiceField(
        label="Qué días de la semana", choices=EventSeries.WEEKDAYS, coerce=int, widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "Elige al menos un día de la semana."},
    )
    interval_weeks = forms.TypedChoiceField(label="Cada cuánto", choices=INTERVALS, coerce=int, initial=1)
    time_zone = forms.ChoiceField(
        label="Zona horaria", choices=MEXICO_TIME_ZONES,
        help_text="La hora es la de este lugar todo el año, también donde cambia el horario.",
    )

    class Meta:
        model = EventSeries
        fields = [
            "name", "weekdays", "interval_weeks", "starts_at", "duration_minutes", "location", "time_zone", "starts_on",
            "ends_on", "description", "published", "public", "registration", "capacity",
        ]
        help_texts = {
            "name": "Por ejemplo: Servicio dominical, Reunión de jóvenes.",
            "starts_at": "En formato de 24 horas, por ejemplo 10:00.",
            "starts_on": "La primera fecha es el primer día de la semana elegido a partir de este día.",
            "ends_on": "Déjalo vacío si no tiene fin.",
            "description": "Dos o tres frases. Cada fecha las muestra en su página.",
            "published": "Las personas de la iglesia verán cada fecha en Eventos. Mientras esté apagado, solo las ve la administración.",
            "public": (
                "Cada fecha tiene su página pública y aparece en el calendario público de la iglesia, con su nombre, "
                "día, hora y lugar. Nunca muestra quién va. Solo funciona mientras las fechas estén publicadas."
            ),
            "registration": "Un servicio de cada semana normalmente no necesita inscripción.",
            "capacity": "Cuántas personas se pueden inscribir a cada fecha. Déjalo vacío si no hay límite.",
        }
        widgets = {
            "starts_at": forms.TimeInput(format="%H:%M", attrs={"inputmode": "numeric", "placeholder": "10:00"}),
            "duration_minutes": forms.NumberInput(attrs={"inputmode": "numeric", "min": 15, "max": 720}),
            "starts_on": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "ends_on": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 3, "maxlength": 800}),
            "registration": forms.RadioSelect,
            "capacity": forms.NumberInput(attrs={"inputmode": "numeric", "min": 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].error_messages["required"] = "Escribe el nombre de la serie."
        self.fields["starts_at"].error_messages.update(required="Escribe a qué hora empieza.", invalid="Escribe la hora así: 10:00.")
        self.fields["starts_on"].error_messages["required"] = "Escribe desde qué día."
        self.fields["description"].validators.append(MaxLengthValidator(800, "Escribe 800 caracteres o menos."))
        self.fields["registration"].choices = EventPage.Registration.choices
        zone = self.instance.time_zone if self.instance.pk else ""
        if zone and zone not in dict(MEXICO_TIME_ZONES) and zone in available_timezones():
            # A series written by code for another country keeps its zone when somebody edits it.
            self.fields["time_zone"].choices = [*MEXICO_TIME_ZONES, (zone, zone)]
        if self.instance.pk and not self.is_bound:
            self.initial["weekdays"] = self.instance.weekday_numbers

    def clean_weekdays(self):
        return ",".join(str(number) for number in sorted(set(self.cleaned_data["weekdays"])))

    def clean_duration_minutes(self):
        minutes = self.cleaned_data["duration_minutes"]
        if not 15 <= minutes <= 720:
            raise forms.ValidationError("Escribe cuántos minutos dura, entre 15 y 720.")
        return minutes

    def clean_capacity(self):
        capacity = self.cleaned_data["capacity"]
        if capacity is not None and capacity < 1:
            raise forms.ValidationError("Escribe cuántos lugares hay, o deja el campo vacío si no hay límite.")
        return capacity

    def clean(self):
        cleaned = super().clean()
        first, last = cleaned.get("starts_on"), cleaned.get("ends_on")
        if first and last and last < first:
            self.add_error("ends_on", "El último día no puede ser antes del primero.")
        return cleaned


def _series(request, series_id: int) -> EventSeries:
    return get_object_or_404(EventSeries, church=request.church, pk=series_id)


def _occurrence(request, series_id: int, occurrence_id: int) -> SeriesOccurrence:
    return get_object_or_404(SeriesOccurrence.objects.select_related("series", "event"), pk=occurrence_id,
                             series_id=series_id, series__church=request.church)


def _form_page(request, form, item):
    return render(request, "agenda/series_form.html", {"form": form, "series": item, "weeks": series.WINDOW_WEEKS})


# ---- the administration -------------------------------------------------------------------------
@church_admin_view
def create(request):
    form = SeriesForm(request.POST or None, initial={"starts_on": timezone.localdate(), "duration_minutes": 90})
    if request.method == "POST" and form.is_valid():
        item = series.create_series(request.church, request.user, **form.cleaned_data)
        messages.success(request, f"Listo. Ya están escritas las fechas de las próximas {series.WINDOW_WEEKS} semanas; las siguientes se agregan solas.")
        return redirect("agenda:series_admin", series_id=item.pk)
    return _form_page(request, form, None)


@church_admin_view
def edit(request, series_id: int):
    item = _series(request, series_id)
    form = SeriesForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        applied = series.update_series(item, request.user, **form.cleaned_data)
        text = "Guardamos la serie y actualizamos las fechas que vienen."
        if any(row.has_people for row in applied.kept):
            text += " No cambiamos las fechas que ya tienen personas inscritas: revísalas abajo."
        messages.success(request, text)
        return redirect("agenda:series_admin", series_id=item.pk)
    return _form_page(request, form, item)


@church_admin_view
def admin_detail(request, series_id: int):
    item = _series(request, series_id)
    today = timezone.localdate()
    signed = Count("event__registrations", filter=Q(event__registrations__status=EventRegistration.Status.ACTIVE))
    rows = SeriesOccurrence.objects.filter(series=item).select_related("event", "event__page").annotate(signed=signed)
    coming = list(rows.filter(Q(event__ends_on__gte=today) | Q(event__ends_on__isnull=True, event__starts_on__gte=today))
                  .exclude(status=series.OFF_RULE).order_by("event__starts_on", "pk"))
    for row in coming:
        page = getattr(row.event, "page", None)
        row.is_published = page is not None and page.published
    past = list(rows.filter(event__ends_on__lt=today).exclude(status=series.OFF_RULE).order_by("-event__starts_on")[:PAST_SHOWN])
    return render(request, "agenda/series_admin.html", {
        "series": item, "rule": series.rule_words(item), "coming": coming, "past": past,
        "review": series.to_review(item, today), "weeks": series.WINDOW_WEEKS,
    })


@church_admin_view
@require_POST
def end(request, series_id: int):
    item = _series(request, series_id)
    series.set_active(item, request.user, False)
    messages.success(request, "La serie terminó: ya no se agregan fechas. Las que ya existen siguen como están.")
    return redirect("agenda:series_admin", series_id=item.pk)


@church_admin_view
@require_POST
def resume(request, series_id: int):
    item = _series(request, series_id)
    series.set_active(item, request.user, True)
    messages.success(request, "La serie sigue: ya están escritas las fechas que faltaban.")
    return redirect("agenda:series_admin", series_id=item.pk)


@church_admin_view
@require_POST
def call_off(request, series_id: int, occurrence_id: int):
    row = _occurrence(request, series_id, occurrence_id)
    series.call_off(row, request.user)
    text = "Listo: ese día no hay. Ya no aparece en Eventos ni en el calendario público."
    if EventRegistration.objects.filter(event_id=row.event_id, status__in=series.SIGNED_UP).exists():
        text += " Hay personas inscritas: avísales tú, Emmaus no les envía nada."
    messages.success(request, text)
    return redirect("agenda:series_admin", series_id=series_id)


@church_admin_view
@require_POST
def restore(request, series_id: int, occurrence_id: int):
    row = _occurrence(request, series_id, occurrence_id)
    series.restore(row, request.user)
    messages.success(request, "Listo: ese día sí hay.")
    return redirect("agenda:series_admin", series_id=series_id)


def admin_context(request) -> dict:
    """What "Administrar los eventos" says about series: each one with its next date, and the address of
    the church's public calendar while it answers."""
    today = timezone.localdate()
    items = list(EventSeries.objects.filter(church=request.church).order_by("-active", "name", "pk"))
    for item in items:
        item.rule = series.rule_words(item)
        item.next = (
            SeriesOccurrence.objects.filter(series=item, status=series.ACTIVE, event__starts_on__gte=today)
            .select_related("event").order_by("event__starts_on").first()
        )
    feed = request.build_absolute_uri(calendar.feed_path(request.church)) if calendar.church_pages(request.church) else ""
    return {"series_list": items, "calendar_link": feed}


# ---- the person ---------------------------------------------------------------------------------
@community_view
def member_detail(request, series_id: int):
    """The dates of one series this person may see. A series with none (nothing published, or all of it
    over) does not exist for them."""
    item = _series(request, series_id)
    events = [event for event in services.upcoming_for(request.person, request.church)
              if (row := series.occurrence_of(event)) is not None and row.series_id == item.pk]
    if not events:
        raise Http404
    for event in events:
        event.called_off = event.series_occurrence.cancelled
    return render(request, "agenda/series.html", {"series": item, "rule": series.rule_words(item), "events": events})
