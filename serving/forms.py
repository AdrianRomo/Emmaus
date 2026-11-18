from django import forms
from django.core.validators import MaxLengthValidator
from django.utils import timezone

from core.forms import TuErrorMessagesMixin

from .models import ServiceDate, Team


class TeamForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = Team
        fields = ["name", "description"]
        help_texts = {"description": "Lo lee quien sirve en el equipo."}
        widgets = {"description": forms.Textarea(attrs={"rows": 3, "maxlength": 400})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].error_messages["required"] = "Escribe el nombre del equipo."
        self.fields["description"].max_length = 400
        self.fields["description"].validators.append(MaxLengthValidator(400, "Escribe 400 caracteres o menos."))


class ServiceDateForm(TuErrorMessagesMixin, forms.ModelForm):
    """One date of a team. Who serves that day is chosen on the same page, outside this form."""

    class Meta:
        model = ServiceDate
        fields = ["date", "starts_at", "what", "place"]
        help_texts = {"starts_at": "En formato de 24 horas, por ejemplo 9:30."}
        widgets = {
            "date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "starts_at": forms.TimeInput(format="%H:%M", attrs={"inputmode": "numeric", "placeholder": "9:30"}),
            "what": forms.TextInput(attrs={"placeholder": "Por ejemplo: bienvenida en la reunión del domingo"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].widget.attrs["min"] = timezone.localdate().isoformat()
        self.fields["date"].error_messages.update(required="Elige el día.", invalid="Elige un día del calendario.")
        self.fields["starts_at"].error_messages["invalid"] = "Escribe la hora así: 9:30."
        self.fields["what"].error_messages["required"] = "Escribe qué se necesita ese día."

    def clean_date(self):
        day = self.cleaned_data["date"]
        if day < timezone.localdate():
            raise forms.ValidationError("Elige un día de hoy en adelante.")
        return day


class UnavailableForm(forms.Form):
    dia = forms.DateField(error_messages={"required": "Elige el día que no puedes.", "invalid": "Elige un día del calendario."})
