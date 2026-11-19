from django import forms

from people import policy

from .models import BODY_MAX, EMERGENCY_LINE_MAX, REPLY_MAX

EVERYBODY = ""


class PrayerForm(forms.Form):
    """One text and, when the church has more than one pastor, who reads it. ``readers``: the
    accounts named on the page (``access.readers``)."""

    body = forms.CharField(
        label="Tu petición", max_length=BODY_MAX, strip=True,
        help_text="Escribe sobre ti. No pongas nombres ni datos de otras personas. Hasta 1,000 caracteres.",
        widget=forms.Textarea(attrs={"rows": 8, "maxlength": BODY_MAX}),
        error_messages={"required": "Escribe tu petición.", "max_length": "Escribe 1,000 caracteres o menos."},
    )
    reader = forms.ChoiceField(
        label="¿Quién la lee?", required=False, widget=forms.RadioSelect,
        error_messages={"invalid_choice": "Elige a alguien de la lista."},
    )

    def __init__(self, *args, readers=(), **kwargs):
        super().__init__(*args, **kwargs)
        if len(readers) > 1:
            self.fields["reader"].choices = [(EVERYBODY, "Todas las personas de la lista de arriba")] + [
                (str(user.pk), f"Solo {user.reader_name}") for user in readers
            ]
        else:
            del self.fields["reader"]

    @property
    def only_reader_id(self) -> int | None:
        chosen = self.cleaned_data.get("reader") or EVERYBODY
        return int(chosen) if chosen else None


class ReplyForm(forms.Form):
    reply = forms.CharField(
        label="Tu respuesta", max_length=REPLY_MAX, strip=True,
        help_text="Una sola, breve. Solo la lee quien escribió la petición, y no se puede cambiar después. Hasta 500 caracteres.",
        widget=forms.Textarea(attrs={"rows": 4, "maxlength": REPLY_MAX}),
        error_messages={"required": "Escribe tu respuesta.", "max_length": "Escribe 500 caracteres o menos."},
    )


class TermsForm(forms.Form):
    """What a pastor may set: the days a new request is kept (only less than the platform's) and the
    church's own line of help. ``accepted`` is asked only to switch it on."""

    retention_days = forms.IntegerField(
        label="Días que se conserva una petición",
        min_value=policy.PRAYER_RETENTION_DAYS_FLOOR, max_value=policy.PRAYER_RETENTION_DAYS,
        help_text="Pasados estos días, Emmaus borra la petición con su respuesta. Emmaus no las guarda más de 90 días; "
                  "puedes elegir menos, desde 7. Cambiarlo vale para las peticiones nuevas.",
        widget=forms.NumberInput(attrs={"inputmode": "numeric"}),
        error_messages={
            "required": "Escribe un número.", "invalid": "Escribe un número entero.",
            "min_value": "Escribe 7 o más, para que haya tiempo de leerla y responder.",
            "max_value": "Escribe 90 o menos: Emmaus no guarda una petición por más tiempo.",
        },
    )
    emergency_line = forms.CharField(
        label="Línea de ayuda de tu iglesia", required=False, max_length=EMERGENCY_LINE_MAX,
        help_text="Una línea, sin enlaces. Si la escribes, aparece en lugar de la Línea de la Vida. La frase del 911 siempre aparece.",
        error_messages={"max_length": "Escribe 160 caracteres o menos."},
    )
    accepted = forms.BooleanField(
        required=False, label="Leí lo que mi iglesia promete y lo acepto como pastor de esta iglesia",
    )
