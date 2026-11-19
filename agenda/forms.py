import re

from django import forms
from django.core.validators import MaxLengthValidator

from core.forms import TuErrorMessagesMixin

from . import services
from people.models import Person

from .models import EventFaq, EventNotice, EventPage, EventRegistration, EventScheduleItem, GuardianConsent

DATE_FORMATS = ["%d/%m/%Y", "%Y-%m-%d"]


class EventPageForm(TuErrorMessagesMixin, forms.ModelForm):
    """The page of one event. The name, the dates and the place are the event's own and are not
    here: they are changed in the event's configuration."""

    class Meta:
        model = EventPage
        fields = [
            "published", "public", "notice_mail", "description", "starts_at", "registration", "capacity", "waiting_list", "registration_closes_on",
            "requires_guardian_consent", "visitor_signup", "visitor_phone",
        ]
        labels = {"description": "Descripción", "registration": "¿Se inscriben desde Emmaus?"}
        help_texts = {
            # Protected words (decision 0010, issue #75): what the public page shows and what it never shows.
            "public": (
                "Para compartir el evento con mamás, papás y tutores que no tienen cuenta. La página pública dice de qué "
                "se trata, cuándo, dónde, el programa y las preguntas frecuentes. Nunca muestra quién se inscribió ni "
                "cuántos lugares quedan, y solo funciona mientras el evento esté publicado."
            ),
            "notice_mail": (
                "En la página pública, quien quiera deja su correo para que Emmaus le avise cuando publiques un aviso "
                "público de este evento. Solo se pide el correo; la persona lo confirma desde su bandeja y se da de baja "
                "cuando quiera. El correo dice que hay un aviso y lleva el enlace: nunca su título ni su texto. Nadie ve "
                "la lista de direcciones, tampoco la administración, y no se usan para nada más. Solo funciona mientras "
                "la página pública esté disponible. Si lo apagas, o la página deja de ser pública, no se envía nada y las "
                "direcciones que ya había se conservan sin recibir correos hasta que se borran solas, 14 días después "
                "de que termine el evento."
            ),
            # A business rule of the waiting list (decision 0011): nobody is signed up without confirming it.
            "waiting_list": (
                "Cuando se libera un lugar, Emmaus se lo ofrece a la primera persona de la lista y ella decide si lo "
                "acepta: nadie queda inscrito sin confirmarlo."
            ),
            # Protected words: the minors' safeguard.
            "requires_guardian_consent": (
                "La inscripción de un menor queda pendiente hasta que la administración registre la autorización, y la "
                "de quien no tiene la edad confirmada por el equipo, hasta que la confirmes. Al encenderlo se revisan "
                "todas las inscripciones que ya hay."
            ),
            # Protected words (issue #93): what a visitor's sign-up is and what it never is.
            "visitor_signup": (
                "En la página pública, una persona mayor de edad se inscribe con su nombre y su correo, sin crear cuenta. "
                "Emmaus le envía un correo y solo cuando lo confirma ocupa un lugar (o entra a la lista de espera). Una "
                "persona por formulario. Su edad es lo que ella dice: nadie la verificó. Solo funciona con la página "
                "pública disponible y la inscripción desde Emmaus abierta, y nunca en un evento que cuida a los menores "
                "de edad."
            ),
            "visitor_phone": "Si lo enciendes, el teléfono es obligatorio para quien se inscribe sin cuenta. Lo ve solo la administración.",
            "published": "Aparece en Eventos. Apagado, solo lo ve la administración.",
            "starts_at": "24 horas, por ejemplo 19:30.",
            "capacity": "Vacío: sin límite.",
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

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("visitor_signup"):
            # In this order: the minors' rule first, because nothing else on the page can lift it.
            if cleaned.get("requires_guardian_consent"):
                self.add_error("visitor_signup", services.MINORS_NO_VISITORS)
            elif not cleaned.get("public") or not cleaned.get("published"):
                self.add_error("visitor_signup", "Para encenderlo, el evento tiene que estar publicado y tener su página pública.")
            elif cleaned.get("registration") != EventPage.Registration.OPEN:
                self.add_error("visitor_signup", "Para encenderlo, abre primero la inscripción desde Emmaus.")
        return cleaned

    def clean_registration_closes_on(self):
        day = self.cleaned_data["registration_closes_on"]
        last = self.event.ends_on or self.event.starts_on
        if day and last and day > last:
            raise forms.ValidationError("El último día para inscribirse no puede ser después de que termine el evento.")
        return day


class BirthDateForm(TuErrorMessagesMixin, forms.Form):
    """The one thing a sign-up may ask: the date of birth, when the event needs to know who is a
    minor. It may be left empty: the team asks for it when it confirms the age."""

    birth_date = forms.DateField(
        label="Tu fecha de nacimiento",
        help_text="Por ejemplo: 14/03/2009. Si prefieres no escribirla, déjala vacía: el equipo de la iglesia te la pedirá.",
        required=False,
        input_formats=DATE_FORMATS,
        widget=forms.DateInput(format="%d/%m/%Y", attrs={"inputmode": "numeric", "autocomplete": "bday"}),
    )

    # What happened and how to fix it, one line each (review 2026-11-18): a date written the right way that the
    # calendar does not have (31/02/2009) is not a format problem, and is not answered as one.
    NOT_UNDERSTOOD = "No entendimos esa fecha. Escríbela con día, mes y año, por ejemplo 14/03/2009."
    NO_SUCH_DAY = "Ese día no existe en el calendario. Revisa el día y el mes, por ejemplo 14/03/2009."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["birth_date"].error_messages.update({"invalid": self.NOT_UNDERSTOOD})

    def clean(self):
        cleaned = super().clean()
        typed = str(self.data.get("birth_date", "")).strip()
        if "birth_date" in self.errors and re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", typed):
            self.errors["birth_date"] = self.error_class([self.NO_SUCH_DAY])
        return cleaned


class EventFaqForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = EventFaq
        fields = ["question", "answer", "position"]
        help_texts = {"position": "Se muestran de menor a mayor."}
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


class EventNoticeForm(TuErrorMessagesMixin, forms.ModelForm):
    """One public notice of an event. Whoever has the link reads it, so the form says what never to write."""

    class Meta:
        model = EventNotice
        fields = ["title", "body"]
        help_texts = {
            "title": "Lo más importante en pocas palabras, por ejemplo: «El camión sale a las 7:00».",
            "body": (
                "Cualquiera con el enlace lo puede leer, sin cuenta. No escribas nombres ni datos personales de nadie, "
                "sobre todo de menores de edad."
            ),
        }
        widgets = {"body": forms.Textarea(attrs={"rows": 5, "maxlength": 1200})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["title"].error_messages["required"] = "Escribe el título del aviso."
        self.fields["body"].error_messages["required"] = "Escribe el aviso."
        # The message is the field's own "max_length" one (core.forms.use_tu_error_messages), as for the title.
        self.fields["body"].validators.append(MaxLengthValidator(1200))

    def _written(self, name: str) -> str:
        """The field, refused when nothing in it can be seen (only zero-width spaces, for example)."""
        value = self.cleaned_data[name]
        if not services.visible_text(value):
            raise forms.ValidationError(self.fields[name].error_messages["required"], code="required")
        return value

    def clean_title(self) -> str:
        return self._written("title")

    def clean_body(self) -> str:
        return self._written("body")


class EventScheduleItemForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = EventScheduleItem
        fields = ["title", "day", "starts_at", "ends_at", "place"]
        help_texts = {"starts_at": "24 horas, por ejemplo 19:30."}
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


PARTICIPATION_NEEDED = "Marca que autoriza que participe en este evento: sin eso no hay autorización que registrar."
METHOD_HELP = (
    "Solo el formato firmado o la verificación en persona confirman la inscripción. Una llamada o un mensaje se "
    "guarda como nota de contacto y la inscripción sigue pendiente."
)


class ConsentForm(TuErrorMessagesMixin, forms.ModelForm):
    """The authorization of a mother, father or guardian for one registration. Closed options, a
    name and one choice per purpose: there is no place to write anything else, and nothing medical."""

    class Meta:
        model = GuardianConsent
        fields = ["guardian_name", "relationship", "method", "guardian_phone", "allows_participation", "allows_transport",
                  "allows_photos"]
        widgets = {"relationship": forms.RadioSelect, "method": forms.RadioSelect}
        help_texts = {"method": METHOD_HELP}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["relationship"].choices = GuardianConsent.Relationship.choices
        self.fields["method"].choices = GuardianConsent.Method.choices
        self.fields["allows_participation"].required = True
        self.fields["allows_participation"].error_messages["required"] = PARTICIPATION_NEEDED
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
    # For a visitor at an event that asks: an explicit choice, nothing preselected. No age is assumed.
    age = forms.ChoiceField(
        label="¿Es mayor o menor de edad?", choices=[("adulto", "Es mayor de edad"), ("menor", "Es menor de edad")],
        widget=forms.RadioSelect, help_text="Dilo por lo que ves en la entrada. Si dudas, pregunta su edad.",
    )
    guardian_name = forms.CharField(label="Nombre de su madre, padre o tutor", max_length=160, required=False)
    relationship = forms.ChoiceField(
        label="¿Quién autoriza?", choices=GuardianConsent.Relationship.choices, widget=forms.RadioSelect, required=False
    )
    method = forms.ChoiceField(
        label="¿Cómo autorizó?", choices=GuardianConsent.Method.choices, widget=forms.RadioSelect, required=False
    )
    guardian_phone = forms.CharField(label="Teléfono de su madre, padre o tutor", max_length=40, required=False)
    allows_participation = forms.BooleanField(label="Autoriza que participe en este evento", required=False)
    allows_transport = forms.BooleanField(label="Autoriza el transporte organizado por la iglesia", required=False)
    allows_photos = forms.BooleanField(label="Autoriza que aparezca en fotografías del evento", required=False)
    override = forms.BooleanField(label="Registrar de todos modos", required=False)
    override_full = forms.BooleanField(
        label="El evento está lleno: registrar de todos modos, aunque ya no haya lugares", required=False
    )
    override_reason = forms.ChoiceField(
        label="¿Por qué la registras de todos modos?", choices=services.WalkupOverrideReason.choices,
        widget=forms.RadioSelect, required=False,
    )

    CONSENT = ("guardian_name", "relationship", "method", "guardian_phone", "allows_participation", "allows_transport",
               "allows_photos")
    OVERRIDE = ("override", "override_full", "override_reason")

    def __init__(self, *args, person=None, minor_known=None, requires_consent=False, blocks=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.person, self.minor_known, self.requires_consent = person, minor_known, requires_consent
        # The rules of the page this walk-up meets (``services.walkup_blocks``): each one needs its checkbox.
        self.blocks = list(blocks)
        self.page_blocks = [rule for rule in self.blocks if rule != services.FULL_RULE]
        if not self.page_blocks:
            del self.fields["override"]
        if services.FULL_RULE not in self.blocks:
            del self.fields["override_full"]
        if not self.blocks:
            del self.fields["override_reason"]
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
        if person is None and requires_consent:
            del self.fields["is_minor"]
            self.fields["age"].error_messages["required"] = "Elige si es mayor o menor de edad."
        else:
            del self.fields["age"]
        if not requires_consent or minor_known is False:
            for name in self.CONSENT:
                del self.fields[name]
        else:
            self.fields["method"].help_text = METHOD_HELP
            self.fields["guardian_name"].widget.attrs["autocomplete"] = "off"
            self.fields["guardian_phone"].widget.attrs.update({"inputmode": "tel", "autocomplete": "off"})

    @property
    def person_fields(self):
        return [self[name] for name in ("first_name", "last_name", "phone", "is_minor", "age") if name in self.fields]

    @property
    def consent_fields(self):
        return [self[name] for name in self.CONSENT if name in self.fields]

    @property
    def override_fields(self):
        return [self[name] for name in self.OVERRIDE if name in self.fields]

    def clean(self):
        cleaned = super().clean()
        cleaned["is_minor"] = self.minor_known if self.minor_known is not None else bool(cleaned.get("is_minor"))
        cleaned["seen_adult"] = False
        if "age" in self.fields:
            cleaned["is_minor"], cleaned["seen_adult"] = cleaned.get("age") == "menor", cleaned.get("age") == "adulto"
        if self.page_blocks and not cleaned.get("override"):
            self.add_error("override", "Marca «Registrar de todos modos» si decides registrarla aunque la página del evento diga otra cosa.")
        if services.FULL_RULE in self.blocks and not cleaned.get("override_full"):
            self.add_error("override_full", "El evento está lleno. Marca esta casilla si decides registrarla aunque ya no haya lugares.")
        if self.blocks and not cleaned.get("override_reason"):
            self.add_error("override_reason", "Elige por qué la registras de todos modos.")
        cleaned["overrides"] = self.blocks
        given = [name for name in ("guardian_name", "relationship", "method") if cleaned.get(name)]
        purposes = [name for name in ("allows_participation", "allows_transport", "allows_photos") if cleaned.get(name)]
        cleaned["consent"] = None
        if given or purposes or cleaned.get("guardian_phone"):
            if not cleaned["is_minor"]:
                where = "is_minor" if "is_minor" in self.fields else "age" if "age" in self.fields else None
                if where != "age" or "age" not in self.errors:
                    self.add_error(where, "La autorización es solo para menores de edad. Marca «Es menor de edad» o deja la autorización vacía.")
            elif len(given) < 3:
                missing = {
                    "guardian_name": "Escribe el nombre de quien autoriza.",
                    "relationship": "Elige si es su madre, su padre o su tutor legal.",
                    "method": "Elige cómo autorizó.",
                }
                for name, message in missing.items():
                    if name not in given and name not in self.errors:
                        self.add_error(name, message)
            elif not cleaned.get("allows_participation"):
                self.add_error("allows_participation", PARTICIPATION_NEEDED)
            else:
                cleaned["consent"] = {name: cleaned.get(name, "") for name in self.CONSENT}
        return cleaned


class ConfirmAgeForm(TuErrorMessagesMixin, forms.Form):
    """"Confirmar edad": how the team verified the age and the date it saw. Nothing about a document
    is asked or kept: no image, no number."""

    birth_date = forms.DateField(
        label="Fecha de nacimiento",
        help_text="Por ejemplo: 14/03/2009. Escribe la que viste; si ya había una, corrígela si no coincide.",
        input_formats=DATE_FORMATS,
        widget=forms.DateInput(format="%d/%m/%Y", attrs={"inputmode": "numeric", "autocomplete": "off"}),
    )
    method = forms.ChoiceField(
        label="¿Cómo confirmaste su edad?", choices=Person.BirthDateMethod.choices, widget=forms.RadioSelect,
        help_text="No guardes fotos ni números de ningún documento: aquí solo queda cómo lo confirmaste.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["birth_date"].error_messages.update({
            "required": "Escribe su fecha de nacimiento.",
            "invalid": "Escribe la fecha así: día/mes/año, por ejemplo 14/03/2009.",
        })
        self.fields["method"].error_messages["required"] = "Elige cómo confirmaste su edad."


class AnnulForm(TuErrorMessagesMixin, forms.Form):
    """"Anular registro": one reason, from a closed list."""

    reason = forms.ChoiceField(
        label="¿Por qué anulas este registro?", choices=EventRegistration.AnnulReason.choices, widget=forms.RadioSelect
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].error_messages["required"] = "Elige por qué anulas este registro."


class GuestAgeForm(TuErrorMessagesMixin, forms.Form):
    """What the team saw of a visitor whose age nobody said when they were written down."""

    age = forms.ChoiceField(
        label="¿Es mayor o menor de edad?",
        choices=[("adulto", "La vi en la puerta: es mayor de edad"), ("menor", "Es menor de edad")], widget=forms.RadioSelect,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["age"].error_messages["required"] = "Elige si es mayor o menor de edad."
