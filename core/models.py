from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from .logos import validate_church_logo


hex_color_validator = RegexValidator(
    regex=r"^#[0-9A-Fa-f]{6}$",
    message="Usa un color hexadecimal con el formato #RRGGBB.",
)


class Role(models.TextChoices):
    CHURCH_ADMIN = "church_admin", "Administración de la iglesia"
    PASTOR = "pastor", "Pastor"
    MENTOR = "mentor", "Mentor"
    PARTICIPANT = "participant", "Participante"


class Church(models.Model):
    name = models.CharField("Nombre de la iglesia", max_length=180)
    slug = models.SlugField("Identificador", unique=True)
    public_welcome_text = models.TextField("Mensaje de bienvenida", blank=True)
    contact_email = models.EmailField("Correo de contacto", blank=True)
    time_zone = models.CharField("Zona horaria", max_length=80, default="America/Mexico_City")
    active = models.BooleanField("Activa", default=True)
    basic_disclaimer_text = models.TextField(
        "Aviso para participantes",
        blank=True,
        default=(
            "Emmaus apoya el acompañamiento de discipulado de tu iglesia. No es un servicio de "
            "emergencias, terapia, consejo médico o legal, ni sustituye el cuidado pastoral."
        ),
    )
    self_registration_enabled = models.BooleanField("Permitir registro propio", default=False)
    leaders_can_invite_participants = models.BooleanField("Los líderes pueden invitar participantes", default=True)
    discipleship_enabled = models.BooleanField(
        "Usar el módulo de discipulado",
        default=False,
        help_text="Rutas, lecciones y preguntas de participantes con cuenta propia.",
    )
    custom_theme_enabled = models.BooleanField(
        "Usar colores propios de la iglesia",
        default=False,
        help_text="Si está apagado se usa la paleta de Emmaus, revisada para contraste y legibilidad.",
    )
    theme_primary_color = models.CharField(
        max_length=7,
        default="#111111",
        validators=[hex_color_validator],
        help_text="Color principal para botones y acciones.",
    )
    theme_accent_color = models.CharField(
        max_length=7,
        default="#B85C38",
        validators=[hex_color_validator],
        help_text="Color de acento para resaltar elementos activos.",
    )
    theme_secondary_color = models.CharField(
        max_length=7,
        default="#596B54",
        validators=[hex_color_validator],
        help_text="Color secundario de apoyo.",
    )
    theme_background_color = models.CharField(
        max_length=7,
        default="#F0F0EE",
        validators=[hex_color_validator],
        help_text="Color de fondo de la página.",
    )
    theme_surface_color = models.CharField(
        max_length=7,
        default="#FFFFFF",
        validators=[hex_color_validator],
        help_text="Color de superficies y formularios.",
    )
    theme_text_color = models.CharField(
        max_length=7,
        default="#111111",
        validators=[hex_color_validator],
        help_text="Color del texto principal.",
    )
    theme_muted_text_color = models.CharField(
        max_length=7,
        default="#6D6A64",
        validators=[hex_color_validator],
        help_text="Color del texto secundario.",
    )
    theme_border_color = models.CharField(
        max_length=7,
        default="#D9D9D6",
        validators=[hex_color_validator],
        help_text="Color de bordes.",
    )
    # The church's identity in the wordmark's slot (docs/design-system.md, "Marca de la iglesia"). Stored under
    # MEDIA_ROOT, served only by core.views.church_logo; never in the repository. db_default: older code
    # (a rollback) can still create a church.
    logo = models.FileField(
        "Logotipo", upload_to="church-logos/", blank=True, default="", db_default="", validators=[validate_church_logo]
    )
    logo_dark = models.FileField(
        "Logotipo para fondo oscuro",
        upload_to="church-logos/",
        blank=True,
        default="",
        db_default="",
        validators=[validate_church_logo],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "iglesia"
        verbose_name_plural = "iglesias"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class UserChurchRole(models.Model):
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="user_roles")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="church_roles")
    role = models.CharField(max_length=32, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "rol en la iglesia"
        verbose_name_plural = "roles en la iglesia"
        unique_together = ("church", "user")
        ordering = ["church__name", "role", "user__last_name", "user__first_name"]

    def __str__(self) -> str:
        return f"{self.user.get_full_name() or self.user.email} - {self.get_role_display()}"


def invitation_token() -> str:
    return secrets.token_urlsafe(32)


def invitation_default_expiration():
    return timezone.now() + timedelta(days=14)


class Invitation(models.Model):
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=32, choices=Role.choices)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_invitations",
    )
    assigned_mentor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_invitations",
    )
    path = models.ForeignKey(
        "DiscipleshipPath",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitations",
    )
    token = models.CharField(max_length=128, unique=True, default=invitation_token)
    expires_at = models.DateTimeField(default=invitation_default_expiration)
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "invitación"
        verbose_name_plural = "invitaciones"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.email} invitado como {self.get_role_display()}"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_accepted(self) -> bool:
        return self.accepted_at is not None

    @property
    def can_accept(self) -> bool:
        return not self.is_accepted and not self.is_expired


class ParticipantProfile(models.Model):
    class ContactMethod(models.TextChoices):
        EMAIL = "email", "Correo electrónico"
        PHONE = "phone", "Teléfono"
        WHATSAPP = "whatsapp", "WhatsApp"
        IN_PERSON = "in_person", "En persona"
        UNSPECIFIED = "unspecified", "Sin preferencia"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="participant_profile")
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="participants")
    assigned_mentor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mentees",
    )
    date_joined = models.DateTimeField(auto_now_add=True)
    preferred_contact_method = models.CharField(
        max_length=24,
        choices=ContactMethod.choices,
        default=ContactMethod.UNSPECIFIED,
    )
    phone_number = models.CharField(max_length=40, blank=True)
    consent_accepted_at = models.DateTimeField(null=True, blank=True)
    emergency_disclaimer_accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "perfil de participante"
        verbose_name_plural = "perfiles de participante"
        ordering = ["user__last_name", "user__first_name", "user__email"]

    def __str__(self) -> str:
        return self.user.get_full_name() or self.user.email


class DiscipleshipPath(models.Model):
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="paths")
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    target_audience = models.CharField(max_length=180, blank=True)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_paths",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ruta de discipulado"
        verbose_name_plural = "rutas de discipulado"
        ordering = ["church__name", "-is_default", "title"]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            DiscipleshipPath.objects.filter(church=self.church, is_default=True).exclude(pk=self.pk).update(
                is_default=False
            )


class Lesson(models.Model):
    path = models.ForeignKey(DiscipleshipPath, on_delete=models.CASCADE, related_name="lessons")
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=180)
    scripture_references = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    practical_step = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "lección"
        verbose_name_plural = "lecciones"
        unique_together = ("path", "order")
        ordering = ["path", "order"]

    def __str__(self) -> str:
        return f"{self.order}. {self.title}"


class ReflectionQuestion(models.Model):
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="reflection_questions")
    question_text = models.TextField()
    order = models.PositiveIntegerField()

    class Meta:
        verbose_name = "pregunta de reflexión"
        verbose_name_plural = "preguntas de reflexión"
        unique_together = ("lesson", "order")
        ordering = ["lesson", "order"]

    def __str__(self) -> str:
        return self.question_text[:80]


class Enrollment(models.Model):
    participant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enrollments")
    path = models.ForeignKey(DiscipleshipPath, on_delete=models.CASCADE, related_name="enrollments")
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "inscripción"
        verbose_name_plural = "inscripciones"
        unique_together = ("participant", "path")
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.participant.email} en {self.path.title}"


class LessonProgress(models.Model):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="lesson_progress")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="progress_records")
    completed_at = models.DateTimeField(null=True, blank=True)
    last_viewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "avance de lección"
        verbose_name_plural = "avances de lección"
        unique_together = ("enrollment", "lesson")
        ordering = ["lesson__order"]

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None


class ReflectionAnswer(models.Model):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="reflection_answers")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="reflection_answers")
    question = models.ForeignKey(ReflectionQuestion, on_delete=models.CASCADE, related_name="answers")
    answer_text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "respuesta de reflexión"
        verbose_name_plural = "respuestas de reflexión"
        unique_together = ("enrollment", "question")
        ordering = ["lesson__order", "question__order"]

    def __str__(self) -> str:
        return self.answer_text[:80]


class ParticipantQuestion(models.Model):
    class Sensitivity(models.TextChoices):
        NORMAL = "normal", "Normal"
        SENSITIVE = "sensitive", "Delicada"
        PASTORAL_CARE_REQUIRED = "pastoral_care_required", "Requiere cuidado pastoral"

    class Status(models.TextChoices):
        OPEN = "open", "Abierta"
        ANSWERED = "answered", "Respondida"
        ESCALATED = "escalated", "Escalada"
        CLOSED = "closed", "Cerrada"

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="participant_questions")
    participant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="participant_questions",
    )
    related_enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="questions",
    )
    question_text = models.TextField()
    sensitivity_level = models.CharField(
        max_length=32,
        choices=Sensitivity.choices,
        default=Sensitivity.NORMAL,
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "pregunta de participante"
        verbose_name_plural = "preguntas de participantes"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.question_text[:80]


class QuestionResponse(models.Model):
    question = models.ForeignKey(ParticipantQuestion, on_delete=models.CASCADE, related_name="responses")
    responder = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="question_responses")
    response_body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "respuesta de líder"
        verbose_name_plural = "respuestas de líderes"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return self.response_body[:80]


class FollowUpRequest(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Abierto"
        IN_PROGRESS = "in_progress", "En proceso"
        COMPLETED = "completed", "Completado"
        CANCELLED = "cancelled", "Cancelado"

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        PARTICIPANT_REQUEST = "participant_request", "Solicitud del participante"
        QUESTION_ESCALATION = "question_escalation", "Pregunta escalada"
        INACTIVITY = "inactivity", "Inactividad"

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="follow_ups")
    participant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="follow_ups")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_follow_ups",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_follow_ups",
    )
    reason = models.TextField()
    source = models.CharField(max_length=32, choices=Source.choices, default=Source.MANUAL)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.OPEN)
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "solicitud de seguimiento"
        verbose_name_plural = "solicitudes de seguimiento"
        ordering = ["status", "due_date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.participant.email}: {self.reason[:60]}"


class PastoralNote(models.Model):
    class Visibility(models.TextChoices):
        MENTOR_ONLY = "mentor_only", "Solo el mentor"
        PASTOR_ONLY = "pastor_only", "Solo pastores"
        PASTOR_AND_MENTOR = "pastor_and_mentor", "Pastores y mentor"

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="pastoral_notes")
    participant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pastoral_notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="authored_pastoral_notes")
    body = models.TextField()
    visibility = models.CharField(
        max_length=24,
        choices=Visibility.choices,
        default=Visibility.PASTOR_AND_MENTOR,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "nota pastoral"
        verbose_name_plural = "notas pastorales"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.body[:80]


class AIDraft(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Borrador"
        APPROVED = "approved", "Aprobado"
        REJECTED = "rejected", "Rechazado"
        EDITED = "edited", "Editado"

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="ai_drafts")
    related_question = models.ForeignKey(ParticipantQuestion, on_delete=models.CASCADE, related_name="ai_drafts")
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="generated_ai_drafts")
    generated_text = models.TextField()
    edited_final_text = models.TextField(blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "borrador con IA"
        verbose_name_plural = "borradores con IA"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Borrador para la pregunta {self.related_question_id}"
