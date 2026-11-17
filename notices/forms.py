from django import forms

from core.forms import TuErrorMessagesMixin

from . import services
from .models import Announcement

WHOLE_CHURCH = "iglesia"
MATERIAL, DETAIL = "si", "no"
normalize_body = services.clean_body


class FirstRadio(forms.RadioSelect):
    """A radio group whose label address is its first option, so the error summary's link lands on it."""

    def id_for_label(self, id_, index=None):
        return super().id_for_label(id_, "0" if index is None else index)


class NoticeForm(TuErrorMessagesMixin, forms.ModelForm):
    """A notice's words, level and days. ``audiences`` (value, label) is given when publishing a new
    one: only what this person may write to. Who a notice is for does not change afterwards.
    ``allow_urgent``: who the church's policy lets publish urgent notices (today the
    administration); anybody else is offered "Normal" and "Importante", and "Urgente" is not a valid
    answer from them. A guide correcting a notice that the administration made urgent is not asked
    for the level: it stays.

    ``material`` ("¿Cambió la información?") is shown when correcting an urgent notice that people
    may have read (``services.asks_on_change``) and must be answered when the title or the text
    changed and the notice stays urgent. ``material_answer`` is what ``services.update`` takes."""

    para = forms.ChoiceField(label="Para quién")
    priority = forms.ChoiceField(label="Nivel")
    material = forms.ChoiceField(
        label="¿Cambió la información?",
        required=False,
        widget=FirstRadio,
        choices=[
            (MATERIAL, "Sí, cambió algo importante: pedir «Enterado» otra vez"),
            (DETAIL, "No, solo corregí un detalle: se conservan los «Enterado»"),
        ],
        help_text=(
            "Contesta si cambiaste el título o el texto. Si pides «Enterado» otra vez, el aviso vuelve hasta arriba"
            " de Mi iglesia para todas las personas, también para quienes ya habían respondido, y la cuenta empieza de nuevo."
        ),
    )

    class Meta:
        model = Announcement
        fields = ["title", "body", "publish_on", "expires_on"]
        labels = {"expires_on": "Último día en que se ve"}
        help_texts = {
            "body": f"Solo texto, hasta {Announcement.BODY_MAX} caracteres. No pongas datos privados de nadie.",
            "publish_on": "Si eliges un día que todavía no llega, nadie lo verá hasta entonces.",
            "expires_on": "Déjalo vacío si el aviso no caduca.",
        }
        widgets = {
            "body": forms.Textarea(attrs={"rows": 8}),
            "publish_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "expires_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    field_order = ["para", "title", "priority", "body", "material", "publish_on", "expires_on"]

    def __init__(self, *args, audiences=None, allow_urgent=False, **kwargs):
        super().__init__(*args, **kwargs)
        Priority = Announcement.Priority
        # As the notice is stored: validating the form writes the new words on ``instance``.
        self._stored = (self.instance.title, self.instance.body, self.instance.priority) if self.instance.pk else None
        if not (self.instance.pk and services.asks_on_change(self.instance)):
            del self.fields["material"]
        if not allow_urgent and self.instance.pk and self.instance.is_urgent:
            del self.fields["priority"]
        else:
            level = self.fields["priority"]
            level.choices = [choice for choice in Priority.choices if allow_urgent or choice[0] != Priority.URGENT]
            level.initial = self.instance.priority
            level.error_messages.update(
                required="Elige el nivel del aviso.", invalid_choice="Elige el nivel del aviso entre los que aparecen."
            )
            level.help_text = "Un aviso importante lleva la palabra «Importante» junto al título y aparece antes que los demás."
            if allow_urgent:
                level.help_text += (
                    " Uno urgente, además, se queda hasta arriba de Mi iglesia hasta que cada persona responde «Enterado» o el aviso vence."
                )
        if audiences is None:
            del self.fields["para"]
        else:
            self.fields["para"].choices = audiences
            self.fields["para"].error_messages.update(
                required="Elige para quién es el aviso.", invalid_choice="Elige para quién es el aviso."
            )
        self.fields["title"].error_messages["required"] = "Escribe un título para el aviso."
        self.fields["body"].error_messages["required"] = "Escribe el aviso."
        self.fields["publish_on"].error_messages["required"] = "Elige el día en que se publica."

    def clean_body(self):
        body = normalize_body(self.cleaned_data["body"])
        if len(body) > Announcement.BODY_MAX:
            raise forms.ValidationError(
                f"El aviso puede tener hasta {Announcement.BODY_MAX} caracteres y este tiene {len(body)}. Acórtalo."
            )
        return body

    def clean(self):
        data = super().clean()
        start, end = data.get("publish_on"), data.get("expires_on")
        if start and end and end < start:
            self.add_error("expires_on", "El último día no puede ser antes del día en que se publica.")
        if "material" in self.fields and not data.get("material") and "material" not in self.errors:
            title, body, level = self._stored
            words = (services.clean_title(data.get("title", title)), data.get("body", body))
            stays_urgent = data.get("priority", level) == Announcement.Priority.URGENT
            if stays_urgent and "title" in data and "body" in data and words != (title, body):
                self.add_error(
                    "material", "Cambiaste lo que dice el aviso. Elige si cambió la información o si solo corregiste un detalle."
                )
        return data

    @property
    def material_answer(self) -> bool | None:
        """True: ask for "Enterado" again. False: a detail. None: not asked or not answered."""
        return {MATERIAL: True, DETAIL: False}.get(self.cleaned_data.get("material"))
