from django import forms
from django.core.validators import MaxLengthValidator

from core.forms import TuErrorMessagesMixin

from .models import EventPage


class EventPageForm(TuErrorMessagesMixin, forms.ModelForm):
    """The page of one event. The name, the dates and the place are the event's own and are not
    here: they are changed in the event's configuration."""

    class Meta:
        model = EventPage
        fields = ["published", "description", "starts_at", "registration", "capacity", "registration_closes_on"]
        help_texts = {
            "published": "Las personas de la iglesia lo verán en Eventos. Mientras esté apagado, solo lo ve la administración.",
            "description": "Dos o tres frases. Es lo que lee alguien que decide si va.",
            "starts_at": "En formato de 24 horas, por ejemplo 19:30.",
            "capacity": "Cuántas personas se pueden inscribir. Déjalo vacío si no hay límite.",
            "registration_closes_on": "Después de este día ya nadie se puede inscribir. Déjalo vacío si no hay fecha límite.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4, "maxlength": 800}),
            "starts_at": forms.TimeInput(format="%H:%M", attrs={"inputmode": "numeric", "placeholder": "19:30"}),
            "registration": forms.RadioSelect,
            "capacity": forms.NumberInput(attrs={"inputmode": "numeric", "min": 1}),
            "registration_closes_on": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        }

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.fields["description"].max_length = 800
        self.fields["description"].validators.append(MaxLengthValidator(800, "Escribe 800 caracteres o menos."))
        self.fields["registration"].choices = EventPage.Registration.choices
        self.fields["starts_at"].error_messages["invalid"] = "Escribe la hora así: 19:30."

    def clean_capacity(self):
        capacity = self.cleaned_data["capacity"]
        if capacity is not None and capacity < 1:
            raise forms.ValidationError("Escribe cuántos lugares hay, o deja el campo vacío si no hay límite.")
        return capacity

    def clean_published(self):
        published = self.cleaned_data["published"]
        if published and self.event.starts_on is None:
            raise forms.ValidationError(
                "Este evento no tiene fecha de inicio. Ponla en la configuración del evento y después publícalo."
            )
        return published

    def clean_registration_closes_on(self):
        day = self.cleaned_data["registration_closes_on"]
        last = self.event.ends_on or self.event.starts_on
        if day and last and day > last:
            raise forms.ValidationError("El último día para inscribirse no puede ser después de que termine el evento.")
        return day
