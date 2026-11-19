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
    Interview,
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
    """Create an event, or its "Datos del evento" in Configuración (prefix "basics").

    The short name for the address forms itself from the name when it is left blank (UX-12): on the server here, and
    while typing in emmaus.js (data-slug-from), which never rewrites one that already has a value.
    """

    required_messages = {"name": "Escribe el nombre del evento."}
    # Routed before an event's address (events/urls.py): /eventos/nuevo/ is "Crear evento", /eventos/cuenta/… the
    # session ping. An event with one of these would open that page instead of its own.
    RESERVED_SLUGS = frozenset({"nuevo", "cuenta", "en-curso"})  # the paths under /eventos/ before <slug>/

    class Meta:
        model = Event
        fields = ["name", "kind", "slug", "location", "starts_on", "ends_on", "status"]
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "ends_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        help_texts = {
            "slug": "Se forma con el nombre; puedes cambiarlo. Solo minúsculas, números y guiones.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        slug = self.fields["slug"]
        slug.required = False
        slug.widget.attrs["data-slug-from"] = self["name"].auto_id  # id_name, id_basics-name: whatever the prefix

    def _slug_taken(self, slug: str) -> bool:
        return slug in self.RESERVED_SLUGS or Event.objects.filter(slug=slug).exclude(pk=self.instance.pk).exists()

    def clean_slug(self):
        submitted = self.cleaned_data.get("slug") or ""
        name = self.cleaned_data.get("name")
        max_length = Event._meta.get_field("slug").max_length
        base = None if name is None else slugify(name)[:max_length].strip("-")
        # Typed: a slug that is not the name's. The one emmaus.js mirrors from the name arrives filled in, and counts
        # as formed from the name, like a blank one.
        if submitted and slugify(submitted) != base:
            slug = slugify(submitted)
            if not slug:
                raise ValidationError("Usa letras, números o guiones.")
            if self._slug_taken(slug):
                raise ValidationError("Ya existe un evento con ese nombre corto. Prueba con otro.")
            return slug
        if name is None:
            # The name has its own error and nothing is saved. The bound instance keeps its saved address: the page
            # shown again builds every link from it ("" on create, where no link uses it).
            return self.instance.slug
        if not base:
            raise ValidationError("Escribe un nombre corto con letras, números o guiones.")
        slug, n = base, 2  # taken: the next number, as question_edit does with codes
        while self._slug_taken(slug):
            suffix = f"-{n}"
            slug, n = f"{base[: max_length - len(suffix)].rstrip('-')}{suffix}", n + 1
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
    time_zone = forms.ChoiceField(label="Zona horaria", choices=MEXICO_TIME_ZONES)
    # The event's questionnaires are chosen in Cuestionarios (admin.questionnaires), not here.

    class Meta:
        model = Event
        fields = [
            "time_zone",
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
            "retention_days": "Después de este tiempo se borran las respuestas individuales. Los resúmenes se conservan.",
        }


class QuestionnaireForm(forms.Form):
    """"Nuevo cuestionario": the questionnaire and its first version, a draft (services.questionnaire.create_questionnaire)."""

    name = forms.CharField(label="Nombre", max_length=160, error_messages={"required": "Escribe el nombre del cuestionario."})
    title = forms.CharField(
        label="Título que ven los camperos", max_length=160, required=False, help_text="Si lo dejas vacío se usa el nombre."
    )
    intro_text = forms.CharField(label="Texto de bienvenida", required=False, widget=forms.Textarea(attrs={"rows": 4}))


class QuestionnaireOrderForm(forms.Form):
    questionnaires_in_order = forms.BooleanField(
        required=False,
        label=Event._meta.get_field("questionnaires_in_order").verbose_name,
        help_text=Event._meta.get_field("questionnaires_in_order").help_text,
    )


class ConfidentialLevelForm(forms.Form):
    """Who reads the answers marked "Confidencial" in this event (admin.confidential_level: administración or pastoral)."""

    confidential_level = forms.ChoiceField(
        label=Event._meta.get_field("confidential_level").verbose_name,
        choices=Event.ConfidentialLevel.choices,
        widget=forms.RadioSelect,
        error_messages={
            "required": "Elige quién lee las respuestas confidenciales.",
            "invalid_choice": "Elige una de las opciones de la lista.",
        },
    )


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
        labels = {"full_name": "Nombre completo"}

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
        labels = {"full_name": "Nombre completo", "is_active": "Sigue participando en el evento"}


class IdentityCheckForm(forms.Form):
    """«Sí, es {nombre}» again, for someone who did not start the interview and is about to continue it with the
    campero (owner decision): the same box as on «Antes de empezar», checked on the server."""

    identity = forms.BooleanField(required=True, error_messages={"required": "Para continuar, confirma que es la persona correcta."})

    def __init__(self, *args, participant_name: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["identity"].label = f"Sí, es {participant_name}"


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

    def __init__(self, *args, requirements, participant_name="", questionnaire_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.requirements = requirements
        # «¿Es la persona correcta?» (owner decision): the identity is confirmed before anything else, the one required
        # check of «Antes de empezar» besides the applicable authorizations; no question of the interview is required
        self.fields["identity"] = forms.BooleanField(
            label=f"Sí, es {participant_name}" if participant_name else "Sí, es la persona correcta",
            required=True,
            error_messages={"required": "Para comenzar, confirma que es la persona correcta."},
        )
        # [(str(questionnaire_id), version title), ...]: the questionnaires the campero can start now.
        self.questionnaire_choices = list(questionnaire_choices)
        if len(self.questionnaire_choices) > 1:
            self.fields["questionnaire"] = forms.ChoiceField(
                label="¿Qué cuestionario?",
                choices=self.questionnaire_choices,
                widget=forms.RadioSelect,
                initial=self.questionnaire_choices[0][0],
                error_messages={
                    "required": "Elige el cuestionario.",
                    "invalid_choice": "Elige uno de los cuestionarios de la lista.",
                },
            )
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

    def chosen_questionnaire_id(self) -> int | None:
        """The questionnaire picked, else the only one offered, else None (the service starts the next one)."""
        if "questionnaire" in self.fields:
            value = getattr(self, "cleaned_data", {}).get("questionnaire")
            return int(value) if value else None
        if len(self.questionnaire_choices) == 1:
            return int(self.questionnaire_choices[0][0])
        return None


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


class FollowUpDecisionChangeForm(forms.Form):
    """«Cambiar decisión de seguimiento»: the decision it goes to (the page names it on its button) and why."""

    decision = forms.ChoiceField(
        choices=[(value, label) for value, label in Interview.FollowUpDecision.choices if value != Interview.FollowUpDecision.TO_EVALUATE],
        widget=forms.HiddenInput,
        error_messages={"required": "Elige la nueva decisión.", "invalid_choice": "Elige la nueva decisión."},
    )
    reason = forms.CharField(
        label="¿Por qué cambias la decisión?",
        help_text="Queda en el historial de la entrevista, con tu nombre.",
        max_length=200,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
        error_messages={
            "required": "Escribe brevemente por qué cambias la decisión.",
            "max_length": "Escribe el motivo en 200 caracteres o menos.",
        },
    )


class FollowUpContinueForm(forms.Form):
    """«Continuar seguimiento»: why the task stays open although its interview no longer asks for it."""

    reason = forms.CharField(
        label="¿Por qué continúas el seguimiento?",
        help_text="Lo leen quienes pueden atender este seguimiento, con tu nombre.",
        max_length=200,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
        error_messages={
            "required": "Escribe brevemente por qué continúas el seguimiento.",
            "max_length": "Escribe el motivo en 200 caracteres o menos.",
        },
    )


def _follow_up_leader_ids(event: Event):
    return EventMembership.objects.filter(event=event, is_active=True).exclude(
        role=MembershipRole.COORDINATOR
    ).values_list("user_id", flat=True)


def follow_up_leaders(event: Event):
    """Who can be responsible for any follow-up of the event: every active member who is not a coordinador."""
    return User.objects.filter(id__in=_follow_up_leader_ids(event)).order_by("first_name")


def _could_open_follow_up(membership: EventMembership, follow_up: FollowUp, tutor_id: int | None) -> bool:
    """Whether this person, given the follow-up, could open it: ``EventAccess.follow_ups`` and ``participants`` read for
    one membership, without a query each (a task given to someone who cannot open it disappears for them)."""
    is_reader = follow_up.created_by_id == membership.user_id
    if follow_up.confidential and not (membership.can_view_sensitive or is_reader):
        return False
    if follow_up.kind != FollowUp.Kind.CARE and not (membership.can_view_sensitive or membership.role == MembershipRole.ADMIN or is_reader):
        return False
    if membership.role in (MembershipRole.ADMIN, MembershipRole.PASTOR):
        return True
    coordinator_id = follow_up.participant.coordinator_id
    if membership.role == MembershipRole.TUTOR:
        return bool(tutor_id and tutor_id == membership.pk)
    return membership.role == MembershipRole.COORDINATOR and coordinator_id == membership.pk


def follow_up_staff(event: Event) -> list[EventMembership]:
    """The event's active memberships with their user and tutor, read once per request (``assignable``)."""
    staff = getattr(event, "_follow_up_staff", None)
    if staff is None:
        staff = event._follow_up_staff = list(
            EventMembership.objects.filter(event=event, is_active=True).select_related("user", "tutor_assignment")
        )
    return staff


def assignable(follow_up: FollowUp, event: Event) -> tuple[list[User], User | None]:
    """Who a follow-up can go to: the leaders (every active member who is not a coordinador) and the campero's own
    coordinador, each only if they could open this follow-up (a tutor sees their own coordinadores' camperos; a
    confidential or safety one, fewer people). Returns (the leaders by first name, the coordinador or None). One rule
    for the detail form, the row of the list and what each row offers."""
    staff = follow_up_staff(event)
    coordinator = getattr(follow_up.participant, "coordinator", None)
    tutor_id = None
    if coordinator is not None:
        assignment = next((m for m in staff if m.pk == coordinator.pk), coordinator)
        tutor_id = getattr(getattr(assignment, "tutor_assignment", None), "tutor_id", None)
    leaders = sorted(
        (m.user for m in staff if m.role != MembershipRole.COORDINATOR and _could_open_follow_up(m, follow_up, tutor_id)),
        key=lambda user: (user.first_name, user.pk),
    )
    own = None
    if coordinator is not None and coordinator.is_active and _could_open_follow_up(coordinator, follow_up, tutor_id):
        own = coordinator.user if coordinator.user_id not in {user.pk for user in leaders} else None
    return leaders, own


def _limit_assigned_to(form: forms.ModelForm, event: Event) -> None:
    """The people a follow-up can go to (``assignable``). Someone who has it and no longer qualifies (left the team,
    or could not open it) is refused when sent again; the list shows them selected, so a row sent unchanged cannot
    unassign them by accident."""
    leaders, own = assignable(form.instance, event)
    field = form.fields["assigned_to"]
    field.queryset = User.objects.filter(id__in=[user.pk for user in leaders] + ([own.pk] if own else [])).order_by("first_name")
    field.label_from_instance = lambda u: u.get_full_name() or u.email
    field.required = False
    field.empty_label = "Sin asignar"


class FollowUpForm(forms.ModelForm):
    class Meta:
        model = FollowUp
        fields = ["status", "assigned_to", "resolution_note"]
        labels = {"assigned_to": "Líder responsable"}
        widgets = {"resolution_note": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, event: Event, can_write_notes: bool, **kwargs):
        super().__init__(*args, **kwargs)
        _limit_assigned_to(self, event)
        # Whoever has it and no longer qualifies (left the team, or could not open it) stays chosen here, said why:
        # this form saves a status or a note, and must not take the follow-up from them in silence. Not offered on others.
        current = self.instance.assigned_to if self.instance.pk else None
        field = self.fields["assigned_to"]
        if current is not None and not field.queryset.filter(pk=current.pk).exists():
            in_team = any(membership.user_id == current.pk for membership in follow_up_staff(event))
            why = "no puede ver este seguimiento" if in_team else "ya no está en el equipo"
            field.queryset = User.objects.filter(pk__in=[*field.queryset.values_list("pk", flat=True), current.pk]).order_by("first_name")
            field.label_from_instance = lambda u: f"{u.get_full_name() or u.email} ({why})" if u.pk == current.pk else (u.get_full_name() or u.email)
        if not can_write_notes:
            self.fields.pop("resolution_note")

    def clean_status(self):
        """A cancelled follow-up is not reopened beside another one of its interview that is not cancelled: the database
        allows one (one_open_care_follow_up_per_interview), and saving it would fail. «Cambiar decisión de seguimiento»
        reopens the interview's task instead of creating a second one, so this guards data from before it."""
        status = self.cleaned_data["status"]
        item = self.instance
        if item.pk and item.status == FollowUp.Status.CANCELLED and status != FollowUp.Status.CANCELLED and item.interview_id:
            others = FollowUp.objects.filter(interview_id=item.interview_id, kind=item.kind, confidential=item.confidential)
            if item.kind == FollowUp.Kind.CARE and others.exclude(pk=item.pk).exclude(status=FollowUp.Status.CANCELLED).exists():
                raise ValidationError("Su entrevista ya tiene otro seguimiento. Continúa en ese.")
        return status


class FollowUpAssignForm(forms.ModelForm):
    """Only who attends a follow-up, chosen from its row in the list."""

    class Meta:
        model = FollowUp
        fields = ["assigned_to"]
        labels = {"assigned_to": "Líder responsable"}

    def __init__(self, *args, event: Event, **kwargs):
        super().__init__(*args, **kwargs)
        _limit_assigned_to(self, event)


class MemberForm(forms.Form):
    full_name = forms.CharField(label="Nombre completo", max_length=150, error_messages={"required": "Escribe el nombre completo."})
    email = forms.EmailField(
        label="Correo electrónico",
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
        error_messages={"required": "Elige un archivo CSV."},
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > 2 * 1024 * 1024:
            raise ValidationError("El archivo es muy grande. El máximo es 2 MB.")
        if not upload.name.lower().endswith((".csv", ".txt")):
            raise ValidationError("El archivo debe ser .csv. Guarda la hoja de cálculo como CSV (UTF-8) y súbela otra vez.")
        return upload


class TeamImportForm(forms.Form):
    file = forms.FileField(
        label="Archivo CSV",
        error_messages={"required": "Elige un archivo CSV."},
    )

    def clean_file(self):
        from .services.team_import import MAX_TEAM_BYTES

        upload = self.cleaned_data["file"]
        if upload.size > MAX_TEAM_BYTES:
            raise ValidationError("El archivo es muy grande. El máximo es 1 MB.")
        if not upload.name.lower().endswith((".csv", ".txt")):
            raise ValidationError("El archivo debe ser .csv. Guarda la hoja de cálculo como CSV (UTF-8) y súbela otra vez.")
        return upload


class SectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ["title", "description", "order"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class QuestionForm(forms.ModelForm):
    """A question of a draft. There is no «Obligatoria»: no question requires an answer (services.questionnaire)."""

    condition_codes = forms.CharField(
        required=False,
        label="Respuestas que la muestran",
        help_text="Sus identificadores internos, separados por comas (están en “Datos técnicos”). Por ejemplo: si",
    )

    class Meta:
        model = Question
        fields = ["text", "help_text", "kind", "sensitivity", "condition_question", "order"]
        labels = {"condition_question": "Mostrar solo si en esta pregunta se respondió…"}

    def __init__(self, *args, version: QuestionnaireVersion, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = [("", "Elige un tipo de respuesta"), *Question.Kind.choices]  # not "---------"
        self.fields["kind"].error_messages["required"] = "Elige un tipo de respuesta."  # Django's says "usted"
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
        labels = {"slug": "Nombre corto", "active": "Activa"}  # LANG-17: not "Identificador"
        help_texts = {
            "slug": "Se genera del nombre; puedes cambiarlo.",
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
