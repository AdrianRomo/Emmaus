from django import forms

from core.forms import TuErrorMessagesMixin

from .models import Announcement

WHOLE_CHURCH = "iglesia"


def normalize_body(text: str) -> str:
    """A browser sends each line break as two characters; a person counts one."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


class NoticeForm(TuErrorMessagesMixin, forms.ModelForm):
    """A notice's words, level and days. ``audiences`` (value, label) is given when publishing a new
    one: only what this person may write to. Who a notice is for does not change afterwards.
    ``allow_urgent``: the administration; anybody else is offered "Normal" and "Importante", and
    "Urgente" is not a valid answer from them. A guide correcting a notice that the administration
    made urgent is not asked for the level: it stays."""

    para = forms.ChoiceField(label="Para quién")
    priority = forms.ChoiceField(label="Nivel")

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

    field_order = ["para", "title", "priority", "body", "publish_on", "expires_on"]

    def __init__(self, *args, audiences=None, allow_urgent=False, **kwargs):
        super().__init__(*args, **kwargs)
        Priority = Announcement.Priority
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
        return data
