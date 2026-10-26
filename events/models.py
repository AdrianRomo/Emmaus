"""Event operations domain (Emmaus Campamentos).

Internal names are generic (Event, EventParticipant, Interview) so the same tables can
serve retreats, conferences, or discipleship programs. Spanish appears only in labels.
"""

from __future__ import annotations

import hashlib
import secrets
import unicodedata
import uuid
from datetime import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.utils import timezone


DEFAULT_PRIVACY_NOTICE = (
    "La iglesia organizadora registra tus respuestas para acompañarte durante y después del "
    "campamento. Solo las personas autorizadas del equipo pastoral pueden ver las respuestas "
    "confidenciales. Puedes elegir “Prefiero no responder” en cualquier pregunta. "
    "Tus respuestas no se comparten con terceros ni se procesan con inteligencia artificial."
)

DEFAULT_SAFEGUARDING_INSTRUCTIONS = (
    "Si alguien está en peligro inmediato, llama al 911. Después avisa en persona a la persona "
    "responsable de protección del campamento. No investigues por tu cuenta ni prometas guardar "
    "un secreto que ponga en riesgo a alguien."
)


def normalize_name(value: str) -> str:
    """Lowercase, accent-free, single-spaced name used only for duplicate detection."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.lower().split())


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ImmutableRecordError(Exception):
    """Raised when code tries to modify a record that must stay as it was published."""


# Read off a printed sign by people who type it on a phone: no 0/O, no 1/I/L.
JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
JOIN_CODE_LENGTH = 6
JOIN_CODE_ATTEMPTS = 20


def new_join_code() -> str:
    return "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH))


# --------------------------------------------------------------------------------------
# Event and organizational hierarchy
# --------------------------------------------------------------------------------------


class Event(models.Model):
    class Kind(models.TextChoices):
        CAMP = "campamento", "Campamento"
        RETREAT = "retiro", "Retiro"
        CONFERENCE = "conferencia", "Conferencia"
        DISCIPLESHIP = "discipulado", "Programa de discipulado"
        OTHER = "otro", "Otra actividad"

    class Status(models.TextChoices):
        DRAFT = "borrador", "En preparación"
        ACTIVE = "activo", "En curso"
        CLOSED = "cerrado", "Terminado"
        ARCHIVED = "archivado", "Archivado"

    REPORT_INTERVAL_CHOICES = [
        (60, "Cada hora"),
        (120, "Cada 2 horas"),
        (180, "Cada 3 horas"),
        (240, "Cada 4 horas"),
    ]

    church = models.ForeignKey("core.Church", on_delete=models.CASCADE, related_name="events")
    name = models.CharField("Nombre", max_length=160)
    slug = models.SlugField("Identificador en la dirección web", max_length=80, unique=True)
    kind = models.CharField("Tipo de actividad", max_length=24, choices=Kind.choices, default=Kind.CAMP)
    status = models.CharField("Estado", max_length=16, choices=Status.choices, default=Status.ACTIVE)
    starts_on = models.DateField("Fecha de inicio", null=True, blank=True)
    ends_on = models.DateField("Fecha de término", null=True, blank=True)
    location = models.CharField("Lugar", max_length=160, blank=True)
    time_zone = models.CharField("Zona horaria", max_length=64, default="America/Mexico_City")
    # The code on the printed sign ("Entra a /unirme/ y escribe ABC234"). Generated on save, never typed by
    # an administrator (not in EventForm). Blank only for rows written by code older than this field.
    join_code = models.CharField("Código para unirse", max_length=8, blank=True, default="", db_default="")
    # The sheet also says the event's name. A new code turns this off, so a sheet that leaked stops working whole.
    join_by_name = models.BooleanField("Se puede entrar con el nombre del evento", default=True, db_default=True)

    questionnaire_version = models.ForeignKey(
        "QuestionnaireVersion",
        verbose_name="Cuestionario en uso",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="events_using",
    )

    # Cortes de seguimiento
    reports_enabled = models.BooleanField("Generar cortes automáticos", default=True)
    report_interval_minutes = models.PositiveSmallIntegerField(
        "Frecuencia de los cortes", choices=REPORT_INTERVAL_CHOICES, default=120
    )
    report_window_start = models.TimeField("Primer corte del día", default=time(8, 0))
    report_window_end = models.TimeField("Último corte del día", default=time(22, 0))

    # Privacidad y protección
    min_group_size = models.PositiveSmallIntegerField(
        "Tamaño mínimo de grupo para mostrar datos sensibles",
        default=5,
        validators=[MinValueValidator(3), MaxValueValidator(20)],
        help_text="Las cifras sensibles menores a este número se ocultan en resúmenes y presentaciones.",
    )
    adult_age = models.PositiveSmallIntegerField("Edad de mayoría", default=18)
    requires_guardian_authorization = models.BooleanField(
        "Pedir autorización de padre, madre o tutor legal para menores", default=True
    )
    privacy_notice = models.TextField("Aviso de privacidad", default=DEFAULT_PRIVACY_NOTICE)
    safeguarding_instructions = models.TextField(
        "Qué hacer ante una situación de riesgo", default=DEFAULT_SAFEGUARDING_INSTRUCTIONS
    )
    procedures_approved_at = models.DateTimeField(
        "Procedimientos aprobados por la iglesia", null=True, blank=True
    )
    procedures_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    retention_days = models.PositiveIntegerField(
        "Días que se conservan las respuestas después del evento",
        default=365,
        validators=[MinValueValidator(30), MaxValueValidator(3650)],
    )
    data_purged_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-starts_on", "name"]
        constraints = [
            models.UniqueConstraint(fields=["join_code"], condition=~Q(join_code=""), name="event_join_code_unique"),
        ]
        verbose_name = "evento"
        verbose_name_plural = "eventos"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if self.join_code:
            return super().save(*args, **kwargs)
        return self._save_with_new_join_code(*args, **kwargs)

    def regenerate_join_code(self) -> str:
        """A new code for the sign. The old code and the event's name stop working at once."""
        old_code, old_by_name = self.join_code, self.join_by_name
        self.join_by_name = False
        try:
            self._save_with_new_join_code(avoid=old_code, update_fields=["join_code", "join_by_name", "updated_at"])
        except Exception:
            self.join_code, self.join_by_name = old_code, old_by_name
            raise
        return self.join_code

    def ensure_join_code(self) -> str:
        """Rows written by code older than the field have no code yet: give them one before building a link."""
        if not self.join_code:
            self.save(update_fields=["join_code"])
        return self.join_code

    def _save_with_new_join_code(self, *args, avoid: str = "", **kwargs):
        """Save with a fresh code, retrying on a clash (a callable default could not retry)."""
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "join_code" not in update_fields:
            kwargs["update_fields"] = [*update_fields, "join_code"]
        for _ in range(JOIN_CODE_ATTEMPTS):
            code = new_join_code()
            if code == avoid or self._join_code_taken(code):
                continue
            self.join_code = code
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError:
                if not self._join_code_taken(code):
                    raise  # another constraint (the slug, ...): not ours to retry
                # another worker saved the same code between the check and the insert: draw again
        raise RuntimeError(f"No se encontró un código libre en {JOIN_CODE_ATTEMPTS} intentos.")

    def _join_code_taken(self, code: str) -> bool:
        others = Event.objects.exclude(pk=self.pk) if self.pk else Event.objects.all()
        return others.filter(join_code=code).exists()

    def clean(self):
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": "La fecha de término no puede ser anterior a la de inicio."})
        if self.report_window_end <= self.report_window_start:
            raise ValidationError({"report_window_end": "El último corte debe ser después del primero."})

    FEMININE_KINDS = {"conferencia", "otro"}

    @property
    def kind_noun(self) -> str:
        return self.get_kind_display()

    @property
    def of_kind(self) -> str:
        """'del campamento' / 'de la conferencia'."""
        noun = self.kind_noun.lower()
        return f"de la {noun}" if self.kind in self.FEMININE_KINDS else f"del {noun}"

    @property
    def all_of_kind(self) -> str:
        """'Todo el campamento' / 'Toda la conferencia'."""
        noun = self.kind_noun.lower()
        return f"Toda la {noun}" if self.kind in self.FEMININE_KINDS else f"Todo el {noun}"

    @property
    def procedures_approved(self) -> bool:
        return self.procedures_approved_at is not None

    @property
    def is_open_for_interviews(self) -> bool:
        return self.status == self.Status.ACTIVE and self.procedures_approved and self.questionnaire_version_id


class MembershipRole(models.TextChoices):
    ADMIN = "administrador", "Administrador"
    PASTOR = "pastor", "Pastor"
    TUTOR = "tutor", "Tutor"
    COORDINATOR = "coordinador", "Coordinador"


class EventMembership(models.Model):
    """A user's role inside one event. Roles never leak across events."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_memberships")
    role = models.CharField("Rol", max_length=16, choices=MembershipRole.choices)
    team_name = models.CharField(
        "Nombre del equipo",
        max_length=80,
        blank=True,
        help_text="Solo para tutores. Se usa en presentaciones en lugar de nombres de personas.",
    )
    can_view_sensitive = models.BooleanField(
        "Acceso a información confidencial",
        default=False,
        help_text="Permite leer respuestas confidenciales y reportes de riesgo. Cada consulta queda registrada.",
    )
    is_active = models.BooleanField("Activo", default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["role", "user__first_name", "user__last_name"]
        constraints = [
            models.UniqueConstraint(fields=["event", "user"], name="membership_one_role_per_event"),
        ]
        indexes = [models.Index(fields=["event", "role", "is_active"], name="membership_event_role_idx")]
        verbose_name = "integrante del equipo"
        verbose_name_plural = "integrantes del equipo"

    def __str__(self) -> str:
        return f"{self.display_name} · {self.get_role_display()}"

    @property
    def display_name(self) -> str:
        return self.user.get_full_name() or self.user.email or self.user.username

    @property
    def team_label(self) -> str:
        return self.team_name or f"Equipo de {self.user.first_name or self.display_name}"


class TutorAssignment(models.Model):
    """Explicit tutor -> coordinador supervision link. A coordinador has at most one tutor."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="tutor_assignments")
    tutor = models.ForeignKey(EventMembership, on_delete=models.CASCADE, related_name="supervised_assignments")
    coordinator = models.OneToOneField(EventMembership, on_delete=models.CASCADE, related_name="tutor_assignment")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["event", "tutor"], name="assignment_event_tutor_idx")]
        verbose_name = "asignación de tutor"
        verbose_name_plural = "asignaciones de tutor"

    def clean(self):
        if self.tutor.event_id != self.event_id or self.coordinator.event_id != self.event_id:
            raise ValidationError("El tutor y el coordinador deben pertenecer al mismo evento.")
        if self.tutor.role != MembershipRole.TUTOR:
            raise ValidationError("Solo un tutor puede supervisar coordinadores.")
        if self.coordinator.role != MembershipRole.COORDINATOR:
            raise ValidationError("Solo se pueden asignar coordinadores a un tutor.")

    def save(self, *args, **kwargs):
        self.full_clean(exclude=["created_by"])
        super().save(*args, **kwargs)


# Pastors and administrators are added by hand on "Equipo": a public request can only ask for these two.
REQUESTABLE_ROLES = [(MembershipRole.TUTOR.value, "Tutor"), (MembershipRole.COORDINATOR.value, "Coordinador")]


class AccessRequest(models.Model):
    """Someone asked, on the event's public page, to join its team. Only a name and an e-mail: no account.

    Approving it runs the same path as "Agregar persona" (an account without a password plus a link to
    create one), so a request can never sign anybody in by itself.
    """

    class Status(models.TextChoices):
        PENDING = "pendiente", "Pendiente"
        APPROVED = "aprobada", "Aprobada"
        REJECTED = "rechazada", "Rechazada"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="access_requests")
    full_name = models.CharField("Nombre completo", max_length=160)
    email = models.EmailField("Correo")
    requested_role = models.CharField("Qué va a hacer en el evento", max_length=16, choices=REQUESTABLE_ROLES)
    note = models.CharField("Algo que la administración deba saber", max_length=300, blank=True)
    decision_note = models.CharField("Motivo de la decisión", max_length=200, blank=True)
    status = models.CharField("Estado", max_length=16, choices=Status.choices, default=Status.PENDING, db_default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    membership = models.ForeignKey(
        EventMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="access_requests"
    )

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            # Sending the form twice (or two tabs at once) leaves one request to decide, not two.
            models.UniqueConstraint(
                fields=["event", "email"], condition=Q(status="pendiente"), name="access_request_one_pending"
            ),
        ]
        indexes = [models.Index(fields=["event", "status"], name="access_request_status_idx")]
        verbose_name = "solicitud de acceso"
        verbose_name_plural = "solicitudes de acceso"

    def __str__(self) -> str:
        return f"{self.full_name}, {self.get_requested_role_display().lower()}"

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)


class InterviewStation(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="stations")
    number = models.PositiveSmallIntegerField("Número")
    name = models.CharField("Nombre", max_length=60)
    is_active = models.BooleanField("Disponible", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["number"]
        constraints = [models.UniqueConstraint(fields=["event", "number"], name="station_number_per_event")]
        verbose_name = "computadora de entrevistas"
        verbose_name_plural = "computadoras de entrevistas"

    def __str__(self) -> str:
        return self.name


# --------------------------------------------------------------------------------------
# Camperos (event participants without accounts)
# --------------------------------------------------------------------------------------


class InterviewStatus(models.TextChoices):
    PENDING = "pendiente", "Sin entrevista"
    IN_PROGRESS = "en_curso", "En curso"
    COMPLETED = "completada", "Completada"


class EventParticipant(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="participants")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    number = models.PositiveIntegerField("Número de campero")
    full_name = models.CharField("Nombre completo", max_length=160)
    normalized_name = models.CharField(max_length=160, editable=False, db_index=False)
    age = models.PositiveSmallIntegerField(
        "Edad", validators=[MinValueValidator(5, "Revisa la edad."), MaxValueValidator(99, "Revisa la edad.")]
    )
    room = models.CharField("Cuarto", max_length=40)
    church_group = models.CharField("Iglesia o grupo", max_length=120)
    coordinator = models.ForeignKey(
        EventMembership,
        verbose_name="Coordinador asignado",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_participants",
        limit_choices_to={"role": MembershipRole.COORDINATOR},
    )
    interview_status = models.CharField(
        "Entrevista", max_length=16, choices=InterviewStatus.choices, default=InterviewStatus.PENDING
    )
    is_active = models.BooleanField("Participa en el evento", default=True)
    withdrawn_reason = models.CharField("Motivo de baja", max_length=200, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField("Registro", auto_now_add=True)
    updated_at = models.DateTimeField("Última modificación", auto_now=True)

    class Meta:
        ordering = ["full_name"]
        constraints = [
            models.UniqueConstraint(fields=["event", "number"], name="participant_number_per_event"),
        ]
        indexes = [
            models.Index(fields=["event", "is_active", "interview_status"], name="participant_status_idx"),
            models.Index(fields=["event", "coordinator"], name="participant_coordinator_idx"),
            models.Index(fields=["event", "normalized_name"], name="participant_name_idx"),
            models.Index(fields=["event", "room"], name="participant_room_idx"),
            models.Index(fields=["event", "church_group"], name="participant_group_idx"),
        ]
        verbose_name = "campero"
        verbose_name_plural = "camperos"

    def __str__(self) -> str:
        return self.full_name

    def save(self, *args, **kwargs):
        self.full_name = " ".join((self.full_name or "").split())
        self.room = (self.room or "").strip()
        self.church_group = " ".join((self.church_group or "").split())
        self.normalized_name = normalize_name(self.full_name)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "full_name" in update_fields:
            kwargs["update_fields"] = list(set(update_fields) | {"normalized_name"})
        super().save(*args, **kwargs)

    @property
    def first_name(self) -> str:
        return self.full_name.split(" ")[0] if self.full_name else ""

    @property
    def code(self) -> str:
        return f"{self.number:04d}"

    @property
    def is_minor(self) -> bool:
        return self.age < self.event.adult_age


class ConsentRecord(models.Model):
    class Kind(models.TextChoices):
        GUARDIAN = "autorizacion_tutor_legal", "Autorización de padre, madre o tutor legal"
        SELF = "consentimiento", "Consentimiento del participante"
        ASSENT = "asentimiento", "Asentimiento del menor"

    class Status(models.TextChoices):
        GRANTED = "otorgado", "Otorgado"
        DENIED = "negado", "Negado"
        REVOKED = "revocado", "Revocado"

    class Method(models.TextChoices):
        PAPER = "papel", "Formato firmado en papel"
        VERBAL = "verbal", "De palabra, en la entrevista"
        DIGITAL = "digital", "Formulario digital"

    participant = models.ForeignKey(EventParticipant, on_delete=models.CASCADE, related_name="consents")
    interview = models.ForeignKey(
        "Interview", on_delete=models.SET_NULL, null=True, blank=True, related_name="consents"
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.GRANTED)
    method = models.CharField(max_length=16, choices=Method.choices, default=Method.PAPER)
    notes = models.CharField("Notas", max_length=200, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        indexes = [models.Index(fields=["participant", "kind", "-recorded_at"], name="consent_lookup_idx")]
        verbose_name = "registro de autorización"
        verbose_name_plural = "registros de autorización"


# --------------------------------------------------------------------------------------
# Questionnaires (versioned)
# --------------------------------------------------------------------------------------


class Questionnaire(models.Model):
    church = models.ForeignKey("core.Church", on_delete=models.CASCADE, related_name="questionnaires")
    name = models.CharField("Nombre", max_length=160)
    description = models.TextField("Descripción", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "cuestionario"
        verbose_name_plural = "cuestionarios"

    def __str__(self) -> str:
        return self.name


class QuestionnaireVersion(models.Model):
    class Status(models.TextChoices):
        DRAFT = "borrador", "Borrador"
        PUBLISHED = "publicada", "Publicada"
        RETIRED = "retirada", "Retirada"

    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField("Versión")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    title = models.CharField("Título para la entrevista", max_length=160)
    intro_text = models.TextField("Texto de bienvenida", blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["questionnaire", "-number"]
        constraints = [models.UniqueConstraint(fields=["questionnaire", "number"], name="questionnaire_version_number")]
        verbose_name = "versión de cuestionario"
        verbose_name_plural = "versiones de cuestionario"

    def __str__(self) -> str:
        return f"{self.title} (versión {self.number})"

    @property
    def is_editable(self) -> bool:
        return self.status == self.Status.DRAFT


class _DraftOnlyMixin:
    """Sections, questions and options can change only while their version is a draft."""

    def _version(self) -> QuestionnaireVersion:  # pragma: no cover - overridden
        raise NotImplementedError

    def save(self, *args, **kwargs):
        if not kwargs.pop("allow_published", False) and not self._version().is_editable:
            raise ImmutableRecordError("Una versión publicada no se puede modificar. Crea una nueva versión.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self._version().is_editable:
            raise ImmutableRecordError("Una versión publicada no se puede modificar. Crea una nueva versión.")
        return super().delete(*args, **kwargs)


class Section(_DraftOnlyMixin, models.Model):
    version = models.ForeignKey(QuestionnaireVersion, on_delete=models.CASCADE, related_name="sections")
    order = models.PositiveSmallIntegerField("Orden")
    title = models.CharField("Título", max_length=160)
    description = models.TextField("Descripción", blank=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "sección"
        verbose_name_plural = "secciones"

    def __str__(self) -> str:
        return self.title

    def _version(self):
        return self.version


class Question(_DraftOnlyMixin, models.Model):
    class Kind(models.TextChoices):
        SINGLE = "unica", "Una sola respuesta"
        MULTIPLE = "multiple", "Varias respuestas"
        SHORT_TEXT = "texto_corto", "Texto corto"
        LONG_TEXT = "texto_largo", "Texto libre"

    class Sensitivity(models.TextChoices):
        GENERAL = "general", "General"
        SENSITIVE = "sensible", "Sensible"
        CONFIDENTIAL = "confidencial", "Confidencial"

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="questions")
    order = models.PositiveSmallIntegerField("Orden")
    code = models.SlugField("Clave interna", max_length=60)
    text = models.CharField("Pregunta", max_length=300)
    help_text = models.CharField("Ayuda", max_length=300, blank=True)
    kind = models.CharField("Tipo de respuesta", max_length=16, choices=Kind.choices)
    required = models.BooleanField("Obligatoria", default=False)
    sensitivity = models.CharField(
        "Nivel de privacidad", max_length=16, choices=Sensitivity.choices, default=Sensitivity.SENSITIVE
    )
    condition_question = models.ForeignKey(
        "self",
        verbose_name="Mostrar solo si se respondió",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dependent_questions",
    )
    condition_option_codes = models.JSONField(
        "Con alguna de estas respuestas", default=list, blank=True
    )
    max_length = models.PositiveSmallIntegerField(default=1000)

    class Meta:
        ordering = ["section__order", "order", "id"]
        verbose_name = "pregunta"
        verbose_name_plural = "preguntas"

    def __str__(self) -> str:
        return self.text

    def _version(self):
        return self.section.version

    @property
    def is_choice(self) -> bool:
        return self.kind in {self.Kind.SINGLE, self.Kind.MULTIPLE}

    @property
    def is_text(self) -> bool:
        return not self.is_choice


class QuestionOption(_DraftOnlyMixin, models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="options")
    order = models.PositiveSmallIntegerField("Orden")
    code = models.SlugField("Clave interna", max_length=60)
    label = models.CharField("Texto", max_length=160)
    is_exclusive = models.BooleanField(
        "Excluye las demás", default=False, help_text="Por ejemplo, “Prefiero no responder”."
    )
    is_decline = models.BooleanField("Equivale a no responder", default=False)
    suggests_follow_up = models.BooleanField("Sugiere seguimiento", default=False)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "opción"
        verbose_name_plural = "opciones"

    def __str__(self) -> str:
        return self.label

    def _version(self):
        return self.question.section.version


# --------------------------------------------------------------------------------------
# Interviews, answers and supervised sessions
# --------------------------------------------------------------------------------------


class Interview(models.Model):
    class Mode(models.TextChoices):
        ASSISTED = "asistida", "Entrevista asistida"
        SUPERVISED = "supervisada", "Captura supervisada"

    class Status(models.TextChoices):
        IN_PROGRESS = "en_curso", "En curso"
        COMPLETED = "completada", "Completada"
        DISCARDED = "descartada", "Descartada"

    class FollowUpDecision(models.TextChoices):
        REQUIRED = "requerido", "Se requiere seguimiento"
        NOT_REQUIRED = "no_requerido", "No se requiere seguimiento"
        TO_EVALUATE = "por_evaluar", "Seguimiento pendiente de evaluación"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="interviews")
    participant = models.ForeignKey(EventParticipant, on_delete=models.CASCADE, related_name="interviews")
    version = models.ForeignKey(QuestionnaireVersion, on_delete=models.PROTECT, related_name="interviews")
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.ASSISTED)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IN_PROGRESS)
    conducted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="conducted_interviews"
    )
    station = models.ForeignKey(
        InterviewStation, on_delete=models.SET_NULL, null=True, blank=True, related_name="interviews"
    )
    started_at = models.DateTimeField(default=timezone.now)
    last_activity_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=0)

    # Claim: which browser session may edit right now.
    lock_token_hash = models.CharField(max_length=64, blank=True)
    lock_holder = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    lock_expires_at = models.DateTimeField(null=True, blank=True)

    follow_up_decision = models.CharField(max_length=16, choices=FollowUpDecision.choices, blank=True)
    correction_count = models.PositiveSmallIntegerField(default=0)
    last_corrected_at = models.DateTimeField(null=True, blank=True)
    discarded_at = models.DateTimeField(null=True, blank=True)
    discarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    discarded_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["participant"],
                condition=~Q(status="descartada"),
                name="one_live_interview_per_participant",
            ),
        ]
        indexes = [
            models.Index(fields=["event", "status"], name="interview_event_status_idx"),
            models.Index(fields=["event", "completed_at"], name="interview_completed_idx"),
            models.Index(fields=["conducted_by", "status"], name="interview_conductor_idx"),
        ]
        verbose_name = "entrevista"
        verbose_name_plural = "entrevistas"

    def __str__(self) -> str:
        return f"Entrevista de {self.participant}"

    def lock_is_active(self, now=None) -> bool:
        now = now or timezone.now()
        return bool(self.lock_token_hash and self.lock_expires_at and self.lock_expires_at > now)

    def lock_matches(self, token: str | None, now=None) -> bool:
        return bool(token) and self.lock_is_active(now) and secrets.compare_digest(self.lock_token_hash, hash_token(token))


class Answer(models.Model):
    interview = models.ForeignKey(Interview, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="answers")
    options = models.ManyToManyField(QuestionOption, blank=True, related_name="answers")
    text_value = models.TextField(blank=True)
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    answered_by_participant = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["interview", "question"], name="one_answer_per_question")]
        verbose_name = "respuesta"
        verbose_name_plural = "respuestas"


class AnswerRevision(models.Model):
    """Previous value of an answer that was corrected after the interview was completed."""

    interview = models.ForeignKey(Interview, on_delete=models.CASCADE, related_name="answer_revisions")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="+")
    previous_option_codes = models.JSONField(default=list)
    previous_text = models.TextField(blank=True)
    reason = models.CharField("Motivo de la corrección", max_length=200)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-changed_at"]


class ParticipantSession(models.Model):
    """Temporary, account-less capture session bound to one interview and one station."""

    class State(models.TextChoices):
        ACTIVE = "activa", "El campero está respondiendo"
        AWAITING_UNLOCK = "por_desbloquear", "Esperando al coordinador"
        CLOSED = "cerrada", "Cerrada"

    class EndReason(models.TextChoices):
        UNLOCKED = "desbloqueada", "El coordinador recuperó la computadora"
        EXPIRED = "expirada", "Terminó por tiempo"
        REVOKED = "revocada", "Cerrada por la administración"
        TAKEN_OVER = "retomada", "La entrevista se abrió en otra computadora"
        DISCARDED = "descartada", "La entrevista se descartó"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    interview = models.ForeignKey(Interview, on_delete=models.CASCADE, related_name="participant_sessions")
    station = models.ForeignKey(InterviewStation, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    token_hash = models.CharField(max_length=64)
    lock_token_hash = models.CharField(max_length=64)
    state = models.CharField(max_length=20, choices=State.choices, default=State.ACTIVE)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=20, choices=EndReason.choices, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["state", "expires_at"], name="psession_state_idx")]

    def token_matches(self, token: str | None) -> bool:
        return bool(token) and secrets.compare_digest(self.token_hash, hash_token(token))


# --------------------------------------------------------------------------------------
# Follow-up and safeguarding
# --------------------------------------------------------------------------------------


class FollowUp(models.Model):
    class Kind(models.TextChoices):
        CARE = "seguimiento", "Seguimiento"
        SAFETY = "seguridad", "Situación de riesgo"

    class Status(models.TextChoices):
        PENDING = "pendiente", "Pendiente"
        IN_PROGRESS = "en_proceso", "En proceso"
        DONE = "atendido", "Atendido"
        CANCELLED = "cancelado", "Cancelado"

    class Reason(models.TextChoices):
        TALK_TO_LEADER = "hablar_con_lider", "Pidió hablar con un líder"
        SPIRITUAL_DECISION = "decision_espiritual", "Tomó una decisión espiritual"
        SUPPORT_REQUESTED = "solicito_apoyo", "Pidió apoyo en un área de su vida"
        COORDINATOR = "criterio_coordinador", "A criterio del coordinador"
        SAFETY = "riesgo", "Posible situación de riesgo"
        OTHER = "otro", "Otro motivo"

    OPEN_STATUSES = (Status.PENDING, Status.IN_PROGRESS)

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="follow_ups")
    participant = models.ForeignKey(EventParticipant, on_delete=models.CASCADE, related_name="follow_ups")
    interview = models.ForeignKey(Interview, on_delete=models.SET_NULL, null=True, blank=True, related_name="follow_ups")
    kind = models.CharField("Tipo", max_length=16, choices=Kind.choices, default=Kind.CARE)
    status = models.CharField("Estado", max_length=16, choices=Status.choices, default=Status.PENDING)
    reason = models.CharField("Motivo", max_length=32, choices=Reason.choices, default=Reason.COORDINATOR)
    details = models.TextField("Detalles confidenciales", blank=True)
    confidential = models.BooleanField(
        "Solo para el equipo de cuidado",
        default=False,
        db_default=False,  # older code (rolling deploys) can still insert follow-ups
        help_text="Se creó a partir de respuestas confidenciales: solo lo ve el equipo con acceso confidencial.",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Líder responsable", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    resolution_note = models.TextField("Nota de cierre (confidencial)", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["interview", "confidential"],
                condition=Q(kind="seguimiento") & ~Q(status="cancelado"),
                name="one_open_care_follow_up_per_interview",
            ),
        ]
        indexes = [models.Index(fields=["event", "kind", "status"], name="followup_event_status_idx")]
        verbose_name = "seguimiento"
        verbose_name_plural = "seguimientos"

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES


# --------------------------------------------------------------------------------------
# Reporting and audit
# --------------------------------------------------------------------------------------


class ReportSnapshot(models.Model):
    class Trigger(models.TextChoices):
        AUTOMATIC = "automatico", "Automático"
        MANUAL = "manual", "Manual"

    class Status(models.TextChoices):
        READY = "listo", "Listo"
        FAILED = "fallido", "Falló"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="snapshots")
    scope_key = models.CharField(max_length=40)
    scope_label = models.CharField(max_length=120)
    tutor = models.ForeignKey(
        EventMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="team_snapshots"
    )
    trigger = models.CharField(max_length=16, choices=Trigger.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.READY)
    cutoff_at = models.DateTimeField("Datos al")
    interval_minutes = models.PositiveSmallIntegerField()
    metrics_version = models.CharField(max_length=16)
    data = models.JSONField(default=dict)
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cutoff_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "scope_key", "cutoff_at", "trigger"], name="one_snapshot_per_cutoff"
            ),
        ]
        indexes = [models.Index(fields=["event", "scope_key", "-cutoff_at"], name="snapshot_history_idx")]
        verbose_name = "corte de seguimiento"
        verbose_name_plural = "cortes de seguimiento"

    def save(self, *args, **kwargs):
        if self.pk:
            stored = ReportSnapshot.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored == self.Status.READY:
                raise ImmutableRecordError("Un corte generado no se puede modificar.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == self.Status.READY and not kwargs.pop("purge", False):
            raise ImmutableRecordError("Un corte generado no se puede borrar.")
        return super().delete(*args, **kwargs)


class AuditEvent(models.Model):
    """Who did what. Never stores answer contents."""

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    actor_label = models.CharField(max_length=160, blank=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, null=True, blank=True, related_name="audit_events")
    action = models.CharField(max_length=48)
    summary = models.CharField(max_length=300)
    object_type = models.CharField(max_length=40, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["event", "-created_at"], name="audit_event_time_idx"),
            models.Index(fields=["action", "-created_at"], name="audit_action_idx"),
        ]
        verbose_name = "registro de actividad"
        verbose_name_plural = "registro de actividad"


class AuthThrottle(models.Model):
    """Attempt counter for logins and kiosk unlocks, shared by every worker process.

    Attempts are counted *before* the password is checked and under a row lock, so
    parallel requests cannot test more passwords than the limit allows.
    """

    key = models.CharField(max_length=200, unique=True)
    attempts = models.PositiveIntegerField(default=0)
    strikes = models.PositiveSmallIntegerField(default=0, db_default=0)
    window_started_at = models.DateTimeField(default=timezone.now)
    blocked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "límite de intentos"
        verbose_name_plural = "límites de intentos"
