from django import forms
from django.core.validators import MaxLengthValidator

from core.forms import TuErrorMessagesMixin

from . import services
from .models import EventFaq, EventPage, EventScheduleItem, GuardianConsent

DATE_FORMATS = ["%d/%m/%Y", "%Y-%m-%d"]


class EventPageForm(TuErrorMessagesMixin, forms.ModelForm):
    """The page of one event. The name, the dates and the place are the event's own and are not
    here: they are changed in the event's configuration."""

    class Meta:
        model = EventPage
        fields = [
            "published", "description", "starts_at", "registration", "capacity", "registration_closes_on",
            "requires_guardian_consent",
        ]
        help_texts = {
            "requires_guardian_consent": (
                "La inscripción de un menor queda pendiente hasta que la administración registre la autorización."
            ),
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


class BirthDateForm(TuErrorMessagesMixin, forms.Form):
    """The one thing a sign-up may ask: the date of birth, when the event needs to know who is a minor."""

    birth_date = forms.DateField(
        label="Tu fecha de nacimiento",
        help_text="Por ejemplo: 14/03/2009.",
        input_formats=DATE_FORMATS,
        widget=forms.DateInput(format="%d/%m/%Y", attrs={"inputmode": "numeric", "autocomplete": "bday"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["birth_date"].error_messages.update({
            "required": "Escribe tu fecha de nacimiento.",
            "invalid": "Escribe la fecha así: día/mes/año, por ejemplo 14/03/2009.",
        })


class EventFaqForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = EventFaq
        fields = ["question", "answer", "position"]
        help_texts = {"position": "Las preguntas se muestran de menor a mayor."}
        widgets = {
            "answer": forms.Textarea(attrs={"rows": 4, "maxlength": 1200}),
            "position": forms.NumberInput(attrs={"inputmode": "numeric", "min": 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["question"].error_messages["required"] = "Escribe la pregunta."
        self.fields["answer"].error_messages["required"] = "Escribe la respuesta."
        self.fields["answer"].max_length = 1200
        self.fields["answer"].validators.append(MaxLengthValidator(1200, "Escribe 1200 caracteres o menos."))
        self.fields["position"].min_value = 1


class EventScheduleItemForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = EventScheduleItem
        fields = ["day", "starts_at", "ends_at", "title", "place"]
        help_texts = {"starts_at": "En formato de 24 horas, por ejemplo 19:30."}
        widgets = {
            "day": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "starts_at": forms.TimeInput(format="%H:%M", attrs={"inputmode": "numeric", "placeholder": "19:30"}),
            "ends_at": forms.TimeInput(format="%H:%M", attrs={"inputmode": "numeric", "placeholder": "20:30"}),
        }

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        first, last = event.starts_on or event.ends_on, services.last_day(event)
        if first:
            self.fields["day"].widget.attrs.update({"min": first.isoformat(), "max": last.isoformat()})
            if not self.instance.pk and "day" not in self.initial:
                self.initial["day"] = first
        self.fields["day"].error_messages.update({"required": "Elige el día.", "invalid": "Elige el día."})
        self.fields["title"].error_messages["required"] = "Escribe qué es."
        for name in ("starts_at", "ends_at"):
            self.fields[name].error_messages["invalid"] = "Escribe la hora así: 19:30."
        self.fields["starts_at"].error_messages["required"] = "Escribe a qué hora empieza."

    def clean_day(self):
        day = self.cleaned_data["day"]
        error = services.schedule_day_error(self.event, day)
        if error:
            raise forms.ValidationError(error)
        return day

    def clean(self):
        cleaned = super().clean()
        starts_at, ends_at = cleaned.get("starts_at"), cleaned.get("ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            self.add_error("ends_at", "La hora en que termina tiene que ser después de la hora en que empieza.")
        return cleaned


class ConsentForm(TuErrorMessagesMixin, forms.ModelForm):
    """The authorization of a mother, father or guardian for one registration. Closed options and a
    name: there is no place to write anything else."""

    class Meta:
        model = GuardianConsent
        fields = ["guardian_name", "relationship", "method", "guardian_phone"]
        widgets = {"relationship": forms.RadioSelect, "method": forms.RadioSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["relationship"].choices = GuardianConsent.Relationship.choices
        self.fields["method"].choices = GuardianConsent.Method.choices
        self.fields["guardian_name"].error_messages["required"] = "Escribe el nombre de quien autoriza."
        self.fields["relationship"].error_messages["required"] = "Elige si es su madre, su padre o su tutor legal."
        self.fields["method"].error_messages["required"] = "Elige cómo autorizó."
        self.fields["guardian_name"].widget.attrs["autocomplete"] = "off"
        self.fields["guardian_phone"].widget.attrs.update({"inputmode": "tel", "autocomplete": "off"})


class WalkUpForm(TuErrorMessagesMixin, forms.Form):
    """Somebody arrived. For a visitor: the name, and a phone if they give one. For a person of the
    church (``person``) the name is not asked. When the event asks for a guardian's authorization,
    it can be recorded here in the same step; left empty, the registration waits for it."""

    first_name = forms.CharField(label="Nombre", max_length=120)
    last_name = forms.CharField(label="Apellidos", max_length=120, required=False)
    phone = forms.CharField(label="Teléfono", max_length=40, required=False)
    is_minor = forms.BooleanField(label="Es menor de edad", required=False)
    guardian_name = forms.CharField(label="Nombre de su madre, padre o tutor", max_length=160, required=False)
    relationship = forms.ChoiceField(
        label="¿Quién autoriza?", choices=GuardianConsent.Relationship.choices, widget=forms.RadioSelect, required=False
    )
    method = forms.ChoiceField(
        label="¿Cómo autorizó?", choices=GuardianConsent.Method.choices, widget=forms.RadioSelect, required=False
    )
    guardian_phone = forms.CharField(label="Teléfono de su madre, padre o tutor", max_length=40, required=False)

    CONSENT = ("guardian_name", "relationship", "method", "guardian_phone")

    def __init__(self, *args, person=None, minor_known=None, requires_consent=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.person, self.minor_known, self.requires_consent = person, minor_known, requires_consent
        if person is not None:
            for name in ("first_name", "last_name", "phone"):
                del self.fields[name]
            if minor_known is not None:  # the date of birth already says it
                del self.fields["is_minor"]
        else:
            self.fields["first_name"].error_messages["required"] = "Escribe el nombre de quien llegó."
            for name in ("first_name", "last_name"):
                self.fields[name].widget.attrs["autocomplete"] = "off"
            self.fields["phone"].widget.attrs.update({"inputmode": "tel", "autocomplete": "off"})
        if not requires_consent or minor_known is False:
            for name in self.CONSENT:
                del self.fields[name]
        else:
            self.fields["guardian_name"].widget.attrs["autocomplete"] = "off"
            self.fields["guardian_phone"].widget.attrs.update({"inputmode": "tel", "autocomplete": "off"})

    @property
    def person_fields(self):
        return [self[name] for name in ("first_name", "last_name", "phone", "is_minor") if name in self.fields]

    @property
    def consent_fields(self):
        return [self[name] for name in self.CONSENT if name in self.fields]

    def clean(self):
        cleaned = super().clean()
        cleaned["is_minor"] = self.minor_known if self.minor_known is not None else bool(cleaned.get("is_minor"))
        given = [name for name in ("guardian_name", "relationship", "method") if cleaned.get(name)]
        cleaned["consent"] = None
        if given or cleaned.get("guardian_phone"):
            if not cleaned["is_minor"]:
                self.add_error("is_minor" if "is_minor" in self.fields else None,
                               "La autorización es solo para menores de edad. Marca «Es menor de edad» o deja la autorización vacía.")
            elif len(given) < 3:
                missing = {
                    "guardian_name": "Escribe el nombre de quien autoriza.",
                    "relationship": "Elige si es su madre, su padre o su tutor legal.",
                    "method": "Elige cómo autorizó.",
                }
                for name, message in missing.items():
                    if name not in given and name not in self.errors:
                        self.add_error(name, message)
            else:
                cleaned["consent"] = {name: cleaned.get(name, "") for name in self.CONSENT}
        return cleaned
