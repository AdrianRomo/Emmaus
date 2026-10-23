from __future__ import annotations

from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
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


class EventSettingsForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = [
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
        fields = ["full_name", "age", "room", "church_group", "coordinator"]
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
        fields = ["full_name", "age", "room", "church_group", "coordinator", "is_active", "withdrawn_reason"]
        labels = {"full_name": "Nombre completo del campero", "is_active": "Sigue participando en el evento"}


class InterviewStartForm(forms.Form):
    mode = forms.ChoiceField(
        label="¿Cómo se va a responder?",
        choices=[
            ("asistida", "Yo hago las preguntas y anoto las respuestas"),
            ("supervisada", "El campero responde solo en esta computadora"),
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
                    error_messages={"required": "Confirma esta autorización para poder comenzar."},
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
        help_text="Escribe solo lo necesario para que el equipo responsable pueda actuar. Esto es confidencial.",
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
                                            help_text="Solo para el equipo de cuidado. Cada consulta queda registrada.")

    def __init__(self, *args, event: Event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.fields["tutor"].queryset = EventMembership.objects.filter(event=event, role=MembershipRole.TUTOR, is_active=True).select_related("user")
        self.fields["tutor"].label_from_instance = lambda m: f"{m.display_name} · {m.team_label}"

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        user = User.objects.filter(email__iexact=email).first()
        if user and EventMembership.objects.filter(event=self.event, user=user).exists():
            raise ValidationError("Esta persona ya forma parte del equipo de este evento.")
        return email


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
        event = self.instance.event
        self.fields["tutor"].queryset = EventMembership.objects.filter(event=event, role=MembershipRole.TUTOR, is_active=True).exclude(pk=self.instance.pk).select_related("user")
        self.fields["tutor"].label_from_instance = lambda m: f"{m.display_name} · {m.team_label}"
        assignment = getattr(self.instance, "tutor_assignment", None)
        if assignment:
            self.initial["tutor"] = assignment.tutor_id


class ImportForm(forms.Form):
    file = forms.FileField(
        label="Archivo CSV",
        help_text="Columnas: Nombre, Edad, Cuarto, Iglesia o grupo. Opcionales: Coordinador (correo o nombre) y Autorización (sí/no).",
        error_messages={"required": "Elige un archivo CSV."},
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > 2 * 1024 * 1024:
            raise ValidationError("El archivo es muy grande. El máximo es 2 MB.")
        if not upload.name.lower().endswith((".csv", ".txt")):
            raise ValidationError("El archivo debe ser .csv. En Excel usa Archivo > Guardar como > CSV UTF-8.")
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
        help_text="Claves de opción separadas por coma. Por ejemplo: si",
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
                self.add_error("condition_codes", "Escribe al menos una clave de opción de la pregunta elegida.")
            elif unknown:
                self.add_error("condition_codes", f"Estas claves no existen en esa pregunta: {', '.join(unknown)}.")
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
