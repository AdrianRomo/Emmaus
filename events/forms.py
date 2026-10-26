from __future__ import annotations

from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.text import slugify

from .models import (
    Event,
    EventMembership,
    EventParticipant,
    FollowUp,
    InterviewStation,
    MembershipRole,
    Question,
    QuestionOption,
    QuestionnaireVersion,
    Section,
    Sede,
)


class SpanishErrorsMixin:
    """Plain-language required messages, one per field (GOV.UK style)."""

    required_messages: dict[str, str] = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name in self.required_messages:
                field.error_messages["required"] = self.required_messages[name]
            field.error_messages.setdefault("invalid", "Revisa este dato.")


class EventForm(SpanishErrorsMixin, forms.ModelForm):
    required_messages = {"name": "Escribe el nombre del evento.", "slug": "Escribe un identificador corto."}

    class Meta:
        model = Event
        fields = ["name", "kind", "slug", "location", "starts_on", "ends_on", "status"]
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "ends_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        help_texts = {
            "slug": "Solo minúsculas, números y guiones. Por ejemplo: jovenes-2026.",
        }

    def clean_slug(self):
        slug = slugify(self.cleaned_data["slug"])
        if not slug:
            raise ValidationError("Usa letras, números o guiones.")
        clash = Event.objects.filter(slug=slug)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError("Ya existe un evento con ese identificador. Prueba con otro.")
        return slug


MEXICO_TIME_ZONES = [
    ("America/Mexico_City", "Centro (Ciudad de México, Guadalajara, Monterrey)"),
    ("America/Cancun", "Sureste (Quintana Roo)"),
    ("America/Chihuahua", "Chihuahua"),
    ("America/Ciudad_Juarez", "Ciudad Juárez"),
    ("America/Mazatlan", "Pacífico (Sinaloa, Nayarit, Baja California Sur)"),
    ("America/Hermosillo", "Sonora"),
    ("America/Tijuana", "Noroeste (Baja California)"),
]


class EventSettingsForm(forms.ModelForm):
    time_zone = forms.ChoiceField(label="Zona horaria", choices=MEXICO_TIME_ZONES, help_text="Define a qué hora caen los cortes y cómo se muestran las horas.")

    class Meta:
        model = Event
        fields = [
            "time_zone",
            "questionnaire_version",
            "reports_enabled",
            "report_interval_minutes",
            "report_window_start",
            "report_window_end",
            "adult_age",
            "requires_guardian_authorization",
            "min_group_size",
            "retention_days",
            "privacy_notice",
            "safeguarding_instructions",
        ]
        widgets = {
            "report_window_start": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "report_window_end": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "privacy_notice": forms.Textarea(attrs={"rows": 5}),
            "safeguarding_instructions": forms.Textarea(attrs={"rows": 5}),
        }
        help_texts = {
            "questionnaire_version": "Solo aparecen versiones publicadas. Las entrevistas ya hechas conservan su versión.",
            "report_window_start": "Los cortes se generan solo dentro de este horario.",
            "retention_days": "Después de este tiempo se borran las respuestas individuales. Los resúmenes se conservan.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["questionnaire_version"].queryset = QuestionnaireVersion.objects.filter(
            questionnaire__church=self.instance.church, status=QuestionnaireVersion.Status.PUBLISHED
        ).select_related("questionnaire")
        self.fields["questionnaire_version"].empty_label = "Sin cuestionario"


class ProceduresForm(forms.Form):
    confirm = forms.BooleanField(
        label="Confirmo que la iglesia definió y aprobó sus procedimientos de autorización, protección de menores y atención de situaciones de riesgo.",
        error_messages={"required": "Marca la casilla para confirmar."},
    )


class StationChoiceForm(forms.Form):
    station = forms.ModelChoiceField(
        queryset=InterviewStation.objects.none(),
        label="¿En qué computadora estás?",
        empty_label=None,
        widget=forms.RadioSelect,
        error_messages={"required": "Elige la computadora en la que estás."},
    )

    def __init__(self, *args, event: Event, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["station"].queryset = event.stations.filter(is_active=True)


class ParticipantForm(SpanishErrorsMixin, forms.ModelForm):
    required_messages = {
        "full_name": "Escribe el nombre completo del campero.",
        "age": "Escribe la edad del campero.",
        "room": "Escribe el cuarto.",
        "church_group": "Escribe la iglesia o grupo.",
        "coordinator": "Elige al coordinador.",
    }
    guardian_authorization = forms.BooleanField(
        required=False,
        label="Ya entregó la autorización firmada de su padre, madre o tutor legal",
        help_text="Solo para menores de edad.",
    )
    not_duplicate = forms.BooleanField(required=False, label="Sí, es otra persona con el mismo nombre")

    class Meta:
        model = EventParticipant
        fields = ["full_name", "age", "room", "church_group", "sede", "coordinator"]
        widgets = {
            "full_name": forms.TextInput(attrs={"autocomplete": "off", "autocapitalize": "words", "spellcheck": "false"}),
            "age": forms.NumberInput(attrs={"inputmode": "numeric", "min": 5, "max": 99}),
            "room": forms.TextInput(attrs={"autocomplete": "off"}),
            "church_group": forms.TextInput(attrs={"autocomplete": "off", "list": "church-groups"}),
        }
        labels = {"full_name": "Nombre completo del campero"}

    def __init__(self, *args, event: Event, can_choose_coordinator: bool, show_guardian: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.duplicates = []
        if can_choose_coordinator:
            self.fields["coordinator"].queryset = EventMembership.objects.filter(
                event=event, role=MembershipRole.COORDINATOR, is_active=True
            ).select_related("user")
            self.fields["coordinator"].label_from_instance = lambda m: m.display_name
            self.fields["coordinator"].required = False
            self.fields["coordinator"].empty_label = "Sin coordinador por ahora"
        else:
            self.fields.pop("coordinator")
        if not show_guardian or not event.requires_guardian_authorization:
            self.fields.pop("guardian_authorization")
        self._limit_sedes(event)

    def _limit_sedes(self, event: Event):
        """Only when the church has sedes: the event's (or all active ones), plus the one already saved."""
        allowed = event.allowed_sedes()
        if self.instance.sede_id:  # a sede deactivated since keeps its campero, and saving keeps it too
            allowed = Sede.objects.filter(Q(pk__in=allowed.values("pk")) | Q(pk=self.instance.sede_id))
        if not allowed.exists():
            self.fields.pop("sede")
            return
        self.fields["sede"].queryset = allowed
        self.fields["sede"].required = False
        self.fields["sede"].empty_label = "Sin sede"

    def clean_full_name(self):
        name = " ".join(self.cleaned_data["full_name"].split())
        if len(name.split()) < 2:
            raise ValidationError("Escribe nombre y apellido.")
        return name

    def clean(self):
        cleaned = super().clean()
        from .services.participants import possible_duplicates

        name = cleaned.get("full_name")
        if name:
            self.duplicates = possible_duplicates(self.event, name, exclude_id=self.instance.pk)
            if self.duplicates and not cleaned.get("not_duplicate"):
                self.add_error(
                    "not_duplicate",
                    "Ya hay un campero registrado con este nombre. Revisa la lista y confirma si es otra persona.",
                )
        return cleaned


class ParticipantAdminForm(ParticipantForm):
    class Meta(ParticipantForm.Meta):
        fields = ["full_name", "age", "room", "church_group", "sede", "coordinator", "is_active", "withdrawn_reason"]
        labels = {"full_name": "Nombre completo del campero", "is_active": "Sigue participando en el evento"}


class InterviewStartForm(forms.Form):
    mode = forms.ChoiceField(
        label="¿Cómo se va a responder?",
        choices=[
            ("asistida", "Entrevista asistida: yo hago las preguntas y anoto las respuestas"),
            ("supervisada", "Entrevista a solas: el campero responde solo en esta computadora"),
        ],
        widget=forms.RadioSelect,
        initial="asistida",
        error_messages={"required": "Elige cómo se va a responder la entrevista."},
    )

    def __init__(self, *args, requirements, **kwargs):
        super().__init__(*args, **kwargs)
        self.requirements = requirements
        for requirement in requirements:
            if requirement.confirm_now:
                self.fields[f"consent_{requirement.key}"] = forms.BooleanField(
                    label=requirement.label,
                    help_text=requirement.help,
                    required=True,
                    error_messages={"required": "Para comenzar, confirma esta autorización."},
                )

    def confirmed(self) -> set[str]:
        return {r.key for r in self.requirements if r.confirm_now and self.cleaned_data.get(f"consent_{r.key}")}


class DiscardForm(forms.Form):
    reason = forms.ChoiceField(
        label="¿Por qué se descarta?",
        widget=forms.RadioSelect,
        choices=[
            ("campero_equivocado", "Se abrió la entrevista de otro campero por error"),
            ("no_desea", "El campero decidió no participar por ahora"),
            ("repetir", "Hay que empezar de nuevo"),
            ("otro", "Otro motivo"),
        ],
        error_messages={"required": "Elige un motivo."},
    )


class SafetyReportForm(forms.Form):
    details = forms.CharField(
        label="¿Qué pasó?",
        help_text="Escribe solo lo necesario para que el equipo de cuidado autorizado pueda actuar. Es confidencial: solo ese equipo podrá leerlo.",
        widget=forms.Textarea(attrs={"rows": 6}),
        max_length=4000,
        error_messages={"required": "Describe brevemente la situación."},
    )


class FollowUpForm(forms.ModelForm):
    class Meta:
        model = FollowUp
        fields = ["status", "assigned_to", "resolution_note"]
        labels = {"assigned_to": "Líder responsable"}
        widgets = {"resolution_note": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, event: Event, can_write_notes: bool, **kwargs):
        super().__init__(*args, **kwargs)
        leader_ids = EventMembership.objects.filter(event=event, is_active=True).exclude(
            role=MembershipRole.COORDINATOR
        ).values_list("user_id", flat=True)
        coordinator_id = getattr(getattr(self.instance.participant, "coordinator", None), "user_id", None)
        self.fields["assigned_to"].queryset = User.objects.filter(id__in=[*leader_ids, coordinator_id]).order_by("first_name")
        self.fields["assigned_to"].label_from_instance = lambda u: u.get_full_name() or u.email
        self.fields["assigned_to"].required = False
        self.fields["assigned_to"].empty_label = "Sin asignar"
        if not can_write_notes:
            self.fields.pop("resolution_note")


class MemberForm(forms.Form):
    full_name = forms.CharField(label="Nombre completo", max_length=150, error_messages={"required": "Escribe el nombre completo."})
    email = forms.EmailField(
        label="Correo electrónico",
        help_text="Lo usará para entrar a Emmaus.",
        error_messages={"required": "Escribe el correo electrónico.", "invalid": "Escribe un correo válido, como nombre@ejemplo.com."},
    )
    role = forms.ChoiceField(label="Rol en este evento", choices=MembershipRole.choices, widget=forms.RadioSelect,
                             error_messages={"required": "Elige un rol."})
    team_name = forms.CharField(label="Nombre del equipo", max_length=80, required=False,
                                help_text="Solo para tutores. Por ejemplo: Equipo Monte Sion.")
    tutor = forms.ModelChoiceField(queryset=EventMembership.objects.none(), required=False, label="Tutor que lo supervisa",
                                   empty_label="Sin tutor por ahora", help_text="Solo para coordinadores.")
    can_view_sensitive = forms.BooleanField(required=False, label="Puede leer información confidencial",
                                            help_text="Solo para el equipo de cuidado autorizado. Cada consulta queda registrada.")

    def __init__(self, *args, event: Event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.fields["tutor"].queryset = EventMembership.objects.filter(event=event, role=MembershipRole.TUTOR, is_active=True).select_related("user")
        self.fields["tutor"].label_from_instance = lambda m: f"{m.display_name}, {m.team_label}"

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        user = User.objects.filter(email__iexact=email).first()
        if user and EventMembership.objects.filter(event=self.event, user=user).exists():
            raise ValidationError("Esta persona ya forma parte del equipo de este evento. Para cambiar su rol, toca “Editar” junto a su nombre en Equipo.")
        return email


JOIN_NOT_FOUND = "No encontramos ese evento. Revisa lo que escribiste o pregunta a quien organiza."


class JoinForm(forms.Form):
    """/unirme/: the code on the sign or the event's name. One field, one button."""

    clave = forms.CharField(
        label="Código o nombre del evento",
        max_length=160,
        error_messages={"required": "Escribe el código o el nombre del evento.", "max_length": JOIN_NOT_FOUND},
        widget=forms.TextInput(attrs={"class": "input search__input", "autocomplete": "off", "spellcheck": "false"}),
    )


class AccessRequestForm(forms.Form):
    """Asking to join an event team: who you are and what you will do. Approval is the administration's."""

    ROLE_CHOICES = [
        (MembershipRole.TUTOR.value, "Tutor: acompaño a un grupo de coordinadores"),
        (MembershipRole.COORDINATOR.value, "Coordinador: entrevisto a mis camperos"),
    ]

    # 150: the longest username and name an account can have (approving creates one from these)
    full_name = forms.CharField(label="Nombre completo", max_length=150,
                                error_messages={"required": "Escribe tu nombre completo.", "max_length": "Escribe un nombre de 150 caracteres o menos."},
                                widget=forms.TextInput(attrs={"autocomplete": "name"}))
    email = forms.EmailField(
        label="Correo",
        max_length=150,
        error_messages={"required": "Escribe tu correo.", "invalid": "Escribe un correo válido, como nombre@ejemplo.com.",
                        "max_length": "Escribe un correo de 150 caracteres o menos."},
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    requested_role = forms.ChoiceField(label="¿Qué vas a hacer en el evento?", choices=ROLE_CHOICES, widget=forms.RadioSelect,
                                       error_messages={"required": "Elige qué vas a hacer en el evento.",
                                                       "invalid_choice": "Elige qué vas a hacer en el evento."})
    note = forms.CharField(label="Algo que la administración deba saber", max_length=300, required=False,
                           error_messages={"max_length": "Escribe 300 caracteres o menos."},
                           widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, account_email: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.account_email = account_email
        if account_email:  # signed in: the request is for this account, whatever the page sends
            self.fields["email"].widget.attrs["readonly"] = True
            self.fields["email"].help_text = "Es el correo de tu cuenta."

    def clean_email(self):
        if self.account_email:
            return self.account_email
        return self.cleaned_data["email"].strip().lower()


class MembershipEditForm(forms.ModelForm):
    tutor = forms.ModelChoiceField(queryset=EventMembership.objects.none(), required=False, label="Tutor que lo supervisa",
                                   empty_label="Sin tutor")

    class Meta:
        model = EventMembership
        fields = ["role", "team_name", "can_view_sensitive", "is_active"]
        widgets = {"role": forms.RadioSelect}
        labels = {"is_active": "Puede entrar y trabajar en este evento"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = MembershipRole.choices  # a membership always has a role: no blank choice
        event = self.instance.event
        self.fields["tutor"].queryset = EventMembership.objects.filter(event=event, role=MembershipRole.TUTOR, is_active=True).exclude(pk=self.instance.pk).select_related("user")
        self.fields["tutor"].label_from_instance = lambda m: f"{m.display_name}, {m.team_label}"
        assignment = getattr(self.instance, "tutor_assignment", None)
        if assignment:
            self.initial["tutor"] = assignment.tutor_id


class ImportForm(forms.Form):
    file = forms.FileField(
        label="Archivo CSV",
        help_text="Columnas: Nombre, Edad, Cuarto, Iglesia o grupo. Opcionales: Coordinador (correo o nombre), Autorización (sí/no) y Sede.",
        error_messages={"required": "Elige un archivo CSV."},
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > 2 * 1024 * 1024:
            raise ValidationError("El archivo es muy grande. El máximo es 2 MB.")
        if not upload.name.lower().endswith((".csv", ".txt")):
            raise ValidationError("El archivo debe ser .csv. Guarda la hoja de cálculo como CSV (UTF-8) y súbela otra vez.")
        return upload


class SectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ["title", "description", "order"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class QuestionForm(forms.ModelForm):
    condition_codes = forms.CharField(
        required=False,
        label="Respuestas que la muestran",
        help_text="Los identificadores internos de esas respuestas, separados por comas. Están junto a cada opción en el cuestionario. Por ejemplo: si",
    )

    class Meta:
        model = Question
        fields = ["text", "help_text", "kind", "sensitivity", "required", "condition_question", "order"]
        labels = {"condition_question": "Mostrar solo si en esta pregunta se respondió…"}

    def __init__(self, *args, version: QuestionnaireVersion, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Question.objects.filter(section__version=version, kind__in=[Question.Kind.SINGLE, Question.Kind.MULTIPLE])
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        self.fields["condition_question"].queryset = qs
        self.fields["condition_question"].required = False
        self.fields["condition_question"].empty_label = "Siempre se muestra"
        self.initial.setdefault("condition_codes", ", ".join(self.instance.condition_option_codes or []))

    def clean(self):
        cleaned = super().clean()
        parent = cleaned.get("condition_question")
        codes = [c.strip() for c in (cleaned.get("condition_codes") or "").split(",") if c.strip()]
        if parent:
            valid = set(parent.options.values_list("code", flat=True))
            unknown = [c for c in codes if c not in valid]
            if not codes:
                self.add_error("condition_codes", "Escribe el identificador interno de al menos una respuesta de la pregunta elegida.")
            elif unknown:
                self.add_error(
                    "condition_codes",
                    f"Esa pregunta no tiene respuestas con estos identificadores: {', '.join(unknown)}. Usa uno de estos: {', '.join(sorted(valid))}.",
                )
        cleaned["condition_codes_list"] = codes if parent else []
        return cleaned


class OptionForm(forms.ModelForm):
    class Meta:
        model = QuestionOption
        fields = ["label", "order", "is_exclusive", "is_decline", "suggests_follow_up"]


class ReportScopeForm(forms.Form):
    scope = forms.ChoiceField(label="Alcance", choices=())

    def __init__(self, *args, scopes, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["scope"].choices = [(s["key"], s["label"]) for s in scopes]


class SedeForm(SpanishErrorsMixin, forms.ModelForm):
    """A campus of the church. The identifier comes from the name unless someone writes one."""

    required_messages = {"name": "Escribe el nombre de la sede.", "order": "Escribe un número. Las sedes con 0 van primero."}

    class Meta:
        model = Sede
        fields = ["name", "slug", "city", "order", "active"]
        labels = {"active": "Activa"}
        help_texts = {
            "slug": "Se forma solo con el nombre si lo dejas vacío. Solo minúsculas, números y guiones, por ejemplo: zona-esmeralda.",
            "order": "Las sedes se muestran de menor a mayor.",
            "active": "Si la desactivas, ya no aparece al registrar camperos. Los que ya la tienen la conservan.",
        }
        widgets = {"order": forms.NumberInput(attrs={"inputmode": "numeric", "min": 0})}

    def __init__(self, *args, church, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.instance.church = church
        self.fields["slug"].required = False
        self.fields["slug"].error_messages["invalid"] = "Usa solo minúsculas, números y guiones."

    def _others(self):
        return Sede.objects.filter(church=self.church).exclude(pk=self.instance.pk)

    def clean_name(self):
        # The import finds a sede by its name or its identifier, read without case or accents (slugify):
        # every value it can read must lead to one sede only.
        name = " ".join(self.cleaned_data["name"].split())
        key = slugify(name)
        if not key:
            raise ValidationError("Escribe un nombre con letras o números.")
        for other in self._others():
            if slugify(other.name) == key:
                raise ValidationError("Ya hay una sede con ese nombre.")
            if slugify(other.slug) == key:  # slugify: a row made outside this form may hold "centro_"
                raise ValidationError(f"Ese nombre es el identificador de la sede «{other.name}». Usa otro nombre o cambia ese identificador.")
        return name

    def clean(self):
        cleaned = super().clean()
        name = cleaned.get("name")
        if not name or "slug" in self.errors:
            return cleaned
        # Cut to the column, then drop a separator the cut left at the end: slugify() would drop it when reading.
        slug = slugify(cleaned.get("slug") or name)[:40].strip("-_")
        others = list(self._others())
        same_name = next((o for o in others if slugify(o.name) == slug), None)
        if not slug:
            self.add_error("slug", "Usa letras, números o guiones.")
        elif any(o.slug == slug or slugify(o.slug) == slug for o in others):
            self.add_error("slug", "Ya hay una sede con ese identificador. Prueba con otro.")
        elif same_name:
            self.add_error("slug", f"Ese identificador se lee como el nombre de la sede «{same_name.name}». Prueba con otro.")
        else:
            cleaned["slug"] = slug
        return cleaned


class EventSedesForm(forms.Form):
    """Which sedes take part in this event. None marked: the whole church."""

    sedes = forms.ModelMultipleChoiceField(
        queryset=Sede.objects.none(),
        required=False,
        label="Sedes que participan",
        help_text="Si no marcas ninguna, participan todas las sedes activas.",
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, event: Event, **kwargs):
        super().__init__(*args, **kwargs)
        chosen = event.sedes.values("pk")
        # An inactive sede stays listed while the event still has it, so it can be unmarked.
        self.fields["sedes"].queryset = Sede.objects.filter(church_id=event.church_id).filter(Q(active=True) | Q(pk__in=chosen))
        self.fields["sedes"].label_from_instance = lambda s: s.name if s.active else f"{s.name}, inactiva"
        self.initial.setdefault("sedes", list(event.sedes.values_list("pk", flat=True)))
