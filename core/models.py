from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


hex_color_validator = RegexValidator(
    regex=r"^#[0-9A-Fa-f]{6}$",
    message="Use a hex color in the format #RRGGBB.",
)


class Role(models.TextChoices):
    CHURCH_ADMIN = "church_admin", "Church admin"
    PASTOR = "pastor", "Pastor"
    MENTOR = "mentor", "Mentor"
    PARTICIPANT = "participant", "Participant"


class Church(models.Model):
    name = models.CharField(max_length=180)
    slug = models.SlugField(unique=True)
    public_welcome_text = models.TextField(blank=True)
    contact_email = models.EmailField(blank=True)
    time_zone = models.CharField(max_length=80, default="UTC")
    active = models.BooleanField(default=True)
    basic_disclaimer_text = models.TextField(
        blank=True,
        default=(
            "Emmaus supports discipleship follow-up through your church. It is not "
            "emergency support, therapy, medical advice, legal advice, or a replacement "
            "for pastoral care."
        ),
    )
    self_registration_enabled = models.BooleanField(default=False)
    leaders_can_invite_participants = models.BooleanField(default=True)
    theme_primary_color = models.CharField(
        max_length=7,
        default="#111111",
        validators=[hex_color_validator],
        help_text="Main brand color used for primary actions and strong contrast.",
    )
    theme_accent_color = models.CharField(
        max_length=7,
        default="#B85C38",
        validators=[hex_color_validator],
        help_text="Warm accent color used for highlights and important calls to action.",
    )
    theme_secondary_color = models.CharField(
        max_length=7,
        default="#596B54",
        validators=[hex_color_validator],
        help_text="Secondary support color for calm labels and subtle emphasis.",
    )
    theme_background_color = models.CharField(
        max_length=7,
        default="#F0F0EE",
        validators=[hex_color_validator],
        help_text="Page background color.",
    )
    theme_surface_color = models.CharField(
        max_length=7,
        default="#FFFFFF",
        validators=[hex_color_validator],
        help_text="Card and form surface color.",
    )
    theme_text_color = models.CharField(
        max_length=7,
        default="#111111",
        validators=[hex_color_validator],
        help_text="Primary text color.",
    )
    theme_muted_text_color = models.CharField(
        max_length=7,
        default="#6D6A64",
        validators=[hex_color_validator],
        help_text="Secondary text color.",
    )
    theme_border_color = models.CharField(
        max_length=7,
        default="#D9D9D6",
        validators=[hex_color_validator],
        help_text="Subtle border color.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class UserChurchRole(models.Model):
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="user_roles")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="church_roles")
    role = models.CharField(max_length=32, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
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
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.email} invited as {self.role}"

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
        EMAIL = "email", "Email"
        PHONE = "phone", "Phone"
        WHATSAPP = "whatsapp", "WhatsApp"
        IN_PERSON = "in_person", "In person"
        UNSPECIFIED = "unspecified", "Unspecified"

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
        unique_together = ("path", "order")
        ordering = ["path", "order"]

    def __str__(self) -> str:
        return f"{self.order}. {self.title}"


class ReflectionQuestion(models.Model):
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="reflection_questions")
    question_text = models.TextField()
    order = models.PositiveIntegerField()

    class Meta:
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
        unique_together = ("participant", "path")
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.participant.email} in {self.path.title}"


class LessonProgress(models.Model):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="lesson_progress")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="progress_records")
    completed_at = models.DateTimeField(null=True, blank=True)
    last_viewed_at = models.DateTimeField(auto_now=True)

    class Meta:
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
        unique_together = ("enrollment", "question")
        ordering = ["lesson__order", "question__order"]

    def __str__(self) -> str:
        return self.answer_text[:80]


class ParticipantQuestion(models.Model):
    class Sensitivity(models.TextChoices):
        NORMAL = "normal", "Normal"
        SENSITIVE = "sensitive", "Sensitive"
        PASTORAL_CARE_REQUIRED = "pastoral_care_required", "Pastoral care required"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ANSWERED = "answered", "Answered"
        ESCALATED = "escalated", "Escalated"
        CLOSED = "closed", "Closed"

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
        ordering = ["created_at"]

    def __str__(self) -> str:
        return self.response_body[:80]


class FollowUpRequest(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        PARTICIPANT_REQUEST = "participant_request", "Participant request"
        QUESTION_ESCALATION = "question_escalation", "Question escalation"
        INACTIVITY = "inactivity", "Inactivity"

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
        ordering = ["status", "due_date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.participant.email}: {self.reason[:60]}"


class PastoralNote(models.Model):
    class Visibility(models.TextChoices):
        MENTOR_ONLY = "mentor_only", "Mentor only"
        PASTOR_ONLY = "pastor_only", "Pastor only"
        PASTOR_AND_MENTOR = "pastor_and_mentor", "Pastor and mentor"

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
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.body[:80]


class AIDraft(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EDITED = "edited", "Edited"

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="ai_drafts")
    related_question = models.ForeignKey(ParticipantQuestion, on_delete=models.CASCADE, related_name="ai_drafts")
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="generated_ai_drafts")
    generated_text = models.TextField()
    edited_final_text = models.TextField(blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Draft for question {self.related_question_id}"
