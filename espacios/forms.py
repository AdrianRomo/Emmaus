from datetime import timedelta

from django import forms
from django.core.validators import MaxLengthValidator, MinValueValidator
from django.db.models import Q
from django.utils import timezone

from core.forms import TuErrorMessagesMixin
from events.models import Event

from . import services
from .models import Space

BUFFERS = [(0, "Nada"), (15, "15 minutos"), (30, "30 minutos"), (60, "1 hora"), (90, "1 hora y media"), (120, "2 horas")]
TIME_HELP = "En formato de 24 horas, por ejemplo 18:30."
MAX_REPORT_DAYS = 366


def _time_field(label: str, required_message: str) -> forms.TimeField:
    return forms.TimeField(
        label=label, input_formats=["%H:%M", "%H.%M", "%H"], help_text=TIME_HELP,
        widget=forms.TimeInput(format="%H:%M", attrs={"inputmode": "numeric", "placeholder": "18:30", "autocomplete": "off"}),
        error_messages={"required": required_message, "invalid": "Escribe la hora así: 18:30."},
    )


class SpaceForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = Space
        fields = ["name", "kind", "capacity", "needs_approval", "active", "notes"]
        help_texts = {
            "name": "Por ejemplo: Salón 2, Cocina, Proyector portátil.",
            "capacity": "Déjalo vacío si no aplica, como en un proyector.",
            "needs_approval": "Si lo apagas, la reserva de quien puede pedirla queda aprobada en el momento.",
            "active": "Si lo apagas, deja de aparecer en la lista y nadie puede pedirlo. Sus reservas no se borran.",
            "notes": "Solo las lee la administración. Por ejemplo, dónde está la llave.",
        }
        widgets = {"notes": forms.Textarea(attrs={"rows": 3, "maxlength": 600})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].error_messages["required"] = "Escribe el nombre del espacio o del equipo."
        self.fields["notes"].max_length = 600
        self.fields["notes"].validators.append(MaxLengthValidator(600, "Escribe 600 caracteres o menos."))
        self.fields["capacity"].min_value = 1
        self.fields["capacity"].validators.append(MinValueValidator(1, "Usa un número igual o mayor que 1."))
        if self.instance.pk is None:
            del self.fields["active"]  # a new space can be booked; it is switched off later, when it stops being lent


class BookingForm(TuErrorMessagesMixin, forms.Form):
    """One use of a space on one day: the hours in the church's own zone. ``with_event`` only for the
    administration, which is who reads every event of the church."""

    title = forms.CharField(
        label="¿Para qué es?", max_length=120,
        help_text="Por ejemplo: Ensayo de alabanza. Lo leen la administración y tú; los demás solo ven «Ocupado».",
        error_messages={"required": "Escribe para qué es la reserva."},
    )
    day = forms.DateField(
        label="Día", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        error_messages={"required": "Elige el día.", "invalid": "Elige un día del calendario."},
    )
    starts = _time_field("Empieza a las", "Escribe a qué hora empieza.")
    ends = _time_field("Termina a las", "Escribe a qué hora termina.")
    setup_minutes = forms.TypedChoiceField(
        label="Tiempo para preparar antes", choices=BUFFERS, coerce=int, initial=0,
        help_text="El espacio queda apartado también ese rato, para acomodar sillas o conectar el equipo.",
    )
    teardown_minutes = forms.TypedChoiceField(
        label="Tiempo para recoger después", choices=BUFFERS, coerce=int, initial=0,
    )
    event = forms.ModelChoiceField(
        label="Evento de la iglesia", queryset=Event.objects.none(), required=False, empty_label="Ninguno",
        help_text="Si la reserva es para un evento de la iglesia, elígelo. Solo lo ve la administración.",
    )

    def __init__(self, *args, church, tz, with_event: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.tz = tz
        today = timezone.now().astimezone(tz).date()
        self.fields["day"].widget.attrs["min"] = today.isoformat()
        for name in ("setup_minutes", "teardown_minutes", "event"):
            self.fields[name].error_messages["invalid_choice"] = "Elige una opción de la lista."
        if with_event:
            self.fields["event"].queryset = (
                Event.objects.filter(church=church, status__in=(Event.Status.DRAFT, Event.Status.ACTIVE))
                .filter(Q(ends_on__gte=today - timedelta(days=1)) | Q(ends_on__isnull=True)).order_by("starts_on", "name", "id")
            )
        else:
            del self.fields["event"]

    def clean(self):
        data = super().clean()
        day, starts, ends = data.get("day"), data.get("starts"), data.get("ends")
        if day and starts and ends:
            # Moments, not hours on a wall clock: the night the clock changes they are not in the same order.
            for name, hour in (("starts", starts), ("ends", ends)):
                try:
                    data[f"{name}_at"] = services.local_instant(day, hour, self.tz)
                except services.SpaceError as error:
                    self.add_error(name, error.message)
            if self.errors:
                return data
            if data["ends_at"] <= data["starts_at"]:
                self.add_error("ends", "La hora en que termina tiene que ser después de la hora en que empieza.")
            elif data["starts_at"] <= timezone.now():
                self.add_error("starts" if day == timezone.now().astimezone(self.tz).date() else "day",
                               "Esa hora ya pasó. Elige un día y una hora que todavía no lleguen.")
        return data


class RejectForm(TuErrorMessagesMixin, forms.Form):
    reason = forms.CharField(
        label="Motivo", max_length=200,
        help_text="Solo lo lee la administración. Emmaus no le envía nada a quien pidió la reserva: avísale tú.",
        error_messages={"required": "Escribe el motivo: lo lee la administración cuando alguien pregunta."},
    )


class UsageForm(TuErrorMessagesMixin, forms.Form):
    desde = forms.DateField(label="Desde", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
                            error_messages={"required": "Elige desde qué día.", "invalid": "Elige un día del calendario."})
    hasta = forms.DateField(label="Hasta", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
                            error_messages={"required": "Elige hasta qué día.", "invalid": "Elige un día del calendario."})

    def clean(self):
        data = super().clean()
        first, last = data.get("desde"), data.get("hasta")
        if first and last:
            if last < first:
                self.add_error("hasta", "Elige un día igual o posterior al primero.")
            elif (last - first).days > MAX_REPORT_DAYS:
                self.add_error("hasta", "Elige un periodo de un año o menos.")
        return data
