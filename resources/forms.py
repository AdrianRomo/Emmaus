from django import forms

from core.forms import TuErrorMessagesMixin

from . import services
from .models import Devotional, MediaItem

DATE_INPUT = {"type": "date"}


class DevotionalForm(TuErrorMessagesMixin, forms.ModelForm):
    """The devotional of one day. ``church`` is needed to say that a day already has one."""

    publish = forms.BooleanField(
        label="Publicarlo",
        required=False,
        help_text="Las personas de la iglesia lo leerán desde su día. Sin marcar se queda como borrador y solo lo ve la administración.",
    )

    class Meta:
        model = Devotional
        fields = ["date", "title", "passage", "body", "author_name"]
        help_texts = {
            "date": "Un devocional por día. Si eliges un día que todavía no llega, nadie lo leerá hasta entonces.",
            "passage": "Solo la cita, por ejemplo Juan 15:1-8. Emmaus no trae el texto de la Biblia: cada quien lo lee en la suya.",
            "body": f"Solo texto, hasta {Devotional.BODY_MAX} caracteres.",
            "author_name": "El nombre que aparece junto al devocional.",
        }
        widgets = {
            "date": forms.DateInput(attrs=DATE_INPUT, format="%Y-%m-%d"),
            "body": forms.Textarea(attrs={"rows": 14}),
        }

    def __init__(self, *args, church, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        if self.instance.pk:
            self.fields["publish"].initial = self.instance.is_published
        self.fields["date"].error_messages["required"] = "Elige el día del devocional."
        self.fields["title"].error_messages["required"] = "Escribe un título para el devocional."
        self.fields["body"].error_messages["required"] = "Escribe el devocional."

    def clean_date(self):
        day = self.cleaned_data["date"]
        if Devotional.objects.filter(church=self.church, date=day).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(services.DAY_TAKEN)
        return day

    def clean_body(self):
        body = self.cleaned_data["body"].replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(body) > Devotional.BODY_MAX:
            raise forms.ValidationError(
                f"El devocional puede tener hasta {Devotional.BODY_MAX} caracteres y este tiene {len(body)}. Acórtalo."
            )
        return body


class MediaItemForm(TuErrorMessagesMixin, forms.ModelForm):
    """A resource: what it is and where it lives. The address is the only thing Emmaus keeps of it."""

    url = forms.URLField(
        label="Enlace",
        max_length=500,
        assume_scheme="https",
        help_text="Pega la dirección de YouTube, Spotify o el sitio donde está. Debe empezar con https://. "
        "Emmaus solo guarda el enlace: el recurso se abre en ese sitio.",
        error_messages={"required": "Pega el enlace del recurso.", "invalid": services.BAD_URL},
    )

    class Meta:
        model = MediaItem
        fields = ["kind", "title", "url", "date", "speaker", "series", "description"]
        help_texts = {
            "date": "El día en que se predicó, se enseñó o se publicó.",
            "speaker": "Quien predicó, enseñó o canta.",
            "series": "Si es parte de una serie, escribe su nombre igual en cada recurso.",
            "description": f"Una o dos frases, hasta {MediaItem.DESCRIPTION_MAX} caracteres.",
        }
        widgets = {
            "date": forms.DateInput(attrs=DATE_INPUT, format="%Y-%m-%d"),
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["title"].error_messages["required"] = "Escribe el título del recurso."
        self.fields["date"].error_messages["required"] = "Elige la fecha del recurso."
        self.fields["kind"].error_messages.update(required="Elige qué tipo de recurso es.", invalid_choice="Elige qué tipo de recurso es.")

    def clean_url(self):
        try:
            return services.clean_url(self.cleaned_data["url"])
        except services.ResourceError as error:
            raise forms.ValidationError(error.message) from None

    def clean_description(self):
        text = self.cleaned_data["description"].replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(text) > MediaItem.DESCRIPTION_MAX:
            raise forms.ValidationError(
                f"La descripción puede tener hasta {MediaItem.DESCRIPTION_MAX} caracteres y esta tiene {len(text)}. Acórtala."
            )
        return text
