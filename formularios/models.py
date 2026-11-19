"""General forms of a church (issue #92).

* ``Form``: what is asked, of whom, why, between which days and for how long the answers are kept.
* ``Question``: the questions as the administration is writing them (the draft).
* ``FormVersion``: the questions and the purpose as they were published. Never edited: publishing a
  change writes a new one, and an answer names the version it answered.
* ``Submission``: one answer. ``FormTask``: the one thing to do an answer may leave for the staff.
* ``FormReader``: a group or a team whose guide or coordinator may read the answers of one form.

No file, no HTML, no score. Nothing here links to the camp's questionnaires or to their answers.
"""

import secrets

from django.conf import settings
from django.db import models

from core.models import Church
from people.models import Person

MAX_QUESTIONS = 30
MAX_OPTIONS = 12
SHORT_MAX = 200
LONG_MAX = 1000
OPTION_MAX = 80


def new_token() -> str:
    return secrets.token_urlsafe(24)


class Form(models.Model):
    class Audience(models.TextChoices):
        MEMBERS = "iglesia", "Personas de la iglesia, con su cuenta"
        PUBLIC = "publico", "Cualquiera que tenga el enlace, sin cuenta"

    class Status(models.TextChoices):
        DRAFT = "borrador", "Borrador"
        PUBLISHED = "publicado", "Publicado"
        CLOSED = "cerrado", "Cerrado"

    RETENTION_DAYS = (30, 90, 365)
    RETENTION_CHOICES = [(30, "30 días"), (90, "90 días"), (365, "Un año")]
    TITLE_MAX = 120
    PURPOSE_MAX = 300

    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="forms")
    title = models.CharField("Título", max_length=TITLE_MAX)
    # "Para qué lo pedimos": one line, shown to whoever answers, and kept with each answer as it was shown.
    purpose = models.CharField("Para qué lo pedimos", max_length=PURPOSE_MAX)
    audience = models.CharField("Quién responde", max_length=10, choices=Audience.choices, default=Audience.MEMBERS)
    status = models.CharField("Estado", max_length=10, choices=Status.choices, default=Status.DRAFT)
    retention_days = models.PositiveSmallIntegerField("Cuánto se guardan las respuestas", choices=RETENTION_CHOICES, default=90)
    # Days of the church's own calendar (``core.Church.time_zone``), both included.
    opens_on = models.DateField("Abre el")
    closes_on = models.DateField("Cierra el")
    # The day the administration closed it before ``closes_on``. Closing is final.
    closed_on = models.DateField(null=True, blank=True)
    # The address of a public form: random, so it cannot be guessed from another one.
    public_token = models.CharField(max_length=43, unique=True, default=new_token, editable=False)
    # A public form takes long answers only after an administrator said, on the screen, that they
    # know that is where sensitive data arrives. Who and when; emptied never.
    long_text_ack_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    long_text_ack_at = models.DateTimeField(null=True, blank=True)
    creates_task = models.BooleanField("Cada respuesta deja un pendiente", default=False)
    # An account, not a person record: what may read the form is the account (``formularios.access``).
    task_owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    task_due_days = models.PositiveSmallIntegerField("Días para dar seguimiento", default=7)
    current_version = models.ForeignKey("FormVersion", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "formulario"
        verbose_name_plural = "formularios"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["church", "status"], name="form_church_status_idx")]

    def __str__(self) -> str:
        return f"Formulario {self.pk}"

    @property
    def is_public(self) -> bool:
        return self.audience == self.Audience.PUBLIC

    @property
    def last_day(self):
        """The last day it took answers, or will: the day it was closed by hand, else ``closes_on``."""
        return self.closed_on or self.closes_on


class Question(models.Model):
    """One question of the draft. ``FormVersion.schema`` holds what was published."""

    class Kind(models.TextChoices):
        SHORT = "corto", "Texto corto"
        LONG = "largo", "Texto largo"
        CHOICE = "opcion", "Una opción"
        MULTI = "varias", "Varias opciones"
        YES_NO = "si_no", "Sí o no"
        DATE = "fecha", "Fecha"
        CONTACT = "contacto", "Datos de contacto"

    LABEL_MAX = 200
    CONSENT_MAX = 300

    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name="questions")
    position = models.PositiveSmallIntegerField(default=0)
    kind = models.CharField("Tipo", max_length=10, choices=Kind.choices)
    label = models.CharField("Pregunta", max_length=LABEL_MAX)
    required = models.BooleanField("Es obligatoria", default=False)
    options = models.JSONField(default=list, blank=True)
    # One level: shown only when that earlier question (one option, or yes/no) was answered with this value.
    show_if_question = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="dependents")
    show_if_value = models.CharField(max_length=OPTION_MAX, blank=True, default="")
    # The contact block of a public form: the name always, and the e-mail, the phone or both.
    ask_email = models.BooleanField(default=False)
    ask_phone = models.BooleanField(default=False)
    consent = models.CharField(max_length=CONSENT_MAX, blank=True, default="")

    class Meta:
        verbose_name = "pregunta de un formulario"
        verbose_name_plural = "preguntas de un formulario"
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return f"Pregunta {self.pk}"

    @property
    def key(self) -> str:
        return f"q{self.pk}"


class FormVersion(models.Model):
    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=Form.TITLE_MAX)
    purpose = models.CharField(max_length=Form.PURPOSE_MAX)
    schema = models.JSONField(default=list)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "versión de un formulario"
        verbose_name_plural = "versiones de un formulario"
        ordering = ["form_id", "number"]
        constraints = [models.UniqueConstraint(fields=["form", "number"], name="form_version_number")]

    def __str__(self) -> str:
        return f"Versión {self.number} del formulario {self.form_id}"


class FormReader(models.Model):
    """Who else reads the answers of one form: whoever guides this group or coordinates this team
    right now (``core.authz.has_scope``, ``manage``). No list of names: when the role ends, so does the reading."""

    class Scope(models.TextChoices):
        GROUP = "group", "Quien guía un grupo"
        MINISTRY = "ministry", "Quien coordina un equipo"

    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name="readers")
    scope_type = models.CharField(max_length=10, choices=Scope.choices)
    scope_id = models.PositiveBigIntegerField()
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "lector de un formulario"
        verbose_name_plural = "lectores de un formulario"
        constraints = [models.UniqueConstraint(fields=["form", "scope_type", "scope_id"], name="form_reader_once")]


class Submission(models.Model):
    """One answer to one version of a form. Deleted when the form's retention ends
    (``formularios.services.prune``); its task goes with it.

    ``person``: in a form of the church, who answered; in a public form, empty until the
    administration says "Agregar a personas" and the record it made or recognized is kept here.
    ``email_key`` and ``phone_key``: keyed hashes of the version and the normalised contact
    (``services.contact_key``), to notice the same contact answering twice in a day."""

    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name="submissions")
    version = models.ForeignKey(FormVersion, on_delete=models.CASCADE, related_name="submissions")
    person = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True, related_name="form_submissions")
    answers = models.JSONField(default=dict)
    purpose_shown = models.CharField(max_length=Form.PURPOSE_MAX)
    contact_first_name = models.CharField(max_length=120, blank=True, default="")
    contact_last_name = models.CharField(max_length=120, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    contact_phone = models.CharField(max_length=40, blank=True, default="")
    consent_shown = models.CharField(max_length=Question.CONSENT_MAX, blank=True, default="")
    email_key = models.CharField(max_length=64, blank=True, default="")
    phone_key = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    # The last day this answer may exist: what the person was told when they answered (the form's last day plus
    # its retention, as they were then). A later change of the form only ever brings it earlier
    # (``services.shorten_kept_until``); extending or reopening the form counts for new answers only.
    delete_after = models.DateField(null=True, blank=True)
    # A public answer whose e-mail or phone another answer to this form gave within a day. Both are kept:
    # anybody can type somebody else's contact, so a repeated contact never discards an answer.
    same_contact = models.BooleanField(default=False)
    # "Por revisar": a public answer that arrived while the form was receiving more than people send, or after the
    # form was full. Kept, with no task and no line in the activity log, for the administration to accept or delete.
    held = models.BooleanField(default=False)

    class Meta:
        verbose_name = "respuesta a un formulario"
        verbose_name_plural = "respuestas a formularios"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["form", "-created_at"], name="form_submission_recent_idx"),
            models.Index(fields=["version", "email_key"], name="form_submission_email_idx"),
            models.Index(fields=["version", "phone_key"], name="form_submission_phone_idx"),
            models.Index(fields=["form", "held", "-created_at"], name="form_submission_held_idx"),
            models.Index(fields=["delete_after"], name="form_submission_delete_idx"),
        ]

    def __str__(self) -> str:
        return f"Respuesta {self.pk}"

    @property
    def contact_name(self) -> str:
        return f"{self.contact_first_name} {self.contact_last_name}".strip()


class SeenSeal(models.Model):
    """That the seal of one drawn public form page was already used to send an answer
    (``formularios.public``). ``key`` is a keyed hash of the seal's random part: it names no person, no
    device and no network. Deleted when the seal could no longer be accepted anyway (``services.prune``)."""

    key = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = "sello usado de un formulario público"
        verbose_name_plural = "sellos usados de formularios públicos"


class FormTask(models.Model):
    """"Dar seguimiento" to one answer: who, by when, done or not, and a short note of the staff's
    own. The note is never the answers; the screens say so and nothing copies them here."""

    class Status(models.TextChoices):
        OPEN = "abierta", "Pendiente"
        DONE = "hecha", "Hecho"

    NOTE_MAX = 300
    STATUS_MARKS = {"abierta": "pendiente", "hecha": "completada"}

    submission = models.OneToOneField(Submission, on_delete=models.CASCADE, related_name="task")
    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name="tasks")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="form_tasks")
    due_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    note = models.CharField(max_length=NOTE_MAX, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "pendiente de un formulario"
        verbose_name_plural = "pendientes de formularios"
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["owner", "status"], name="form_task_owner_status_idx")]

    def __str__(self) -> str:
        return f"Pendiente {self.pk}"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN

    @property
    def status_mark(self) -> str:
        return self.STATUS_MARKS[self.status]
