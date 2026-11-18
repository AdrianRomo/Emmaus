from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Church
from groups.models import Group
from people.models import Person


def today():
    return timezone.localdate()


class Announcement(models.Model):
    """One notice of a church. ``group`` empty: for the whole church; else for that group's people.
    ``body`` is plain text: it is escaped when shown and never copied to a log or an audit row."""

    BODY_MAX = 1200

    class Status(models.TextChoices):
        PUBLISHED = "publicado", "Publicado"
        RETIRED = "retirado", "Retirado"

    class Priority(models.TextChoices):
        """How loud a notice is: a level of the same thing, not another kind of content."""

        NORMAL = "normal", "Normal"
        IMPORTANT = "important", "Importante"
        URGENT = "urgent", "Urgente"

    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="announcements")
    group = models.ForeignKey(
        Group, verbose_name="Grupo", on_delete=models.CASCADE, null=True, blank=True, related_name="announcements"
    )
    title = models.CharField("Título", max_length=120)
    body = models.TextField("Aviso")
    priority = models.CharField("Nivel", max_length=10, choices=Priority.choices, default=Priority.NORMAL, db_default=Priority.NORMAL)
    publish_on = models.DateField("Se publica el", default=today)
    expires_on = models.DateField("Deja de verse después del", null=True, blank=True)
    status = models.CharField("Estado", max_length=12, choices=Status.choices, default=Status.PUBLISHED)
    # An urgent notice asks for "Enterado" once per version. A change of what it says that matters
    # makes a new version (``services.update``); ``changed_at`` is when the last one was made.
    version = models.PositiveIntegerField("Versión", default=1, db_default=1)
    changed_at = models.DateTimeField(null=True, blank=True)
    # The version whose round of e-mails is finished (``notices.email``): nobody else is mailed for it,
    # so who joins the church or a group later is not mailed old notices. Empty: the round is open.
    emailed_version = models.PositiveIntegerField(null=True, blank=True)
    # When a round was opened again without a new version (the notice became urgent), so that the
    # command still finds it inside its window of days.
    email_reopened_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "aviso"
        verbose_name_plural = "avisos"
        ordering = ["-publish_on", "-id"]
        indexes = [models.Index(fields=["church", "status", "publish_on"], name="notice_church_status_day_idx")]

    def __str__(self) -> str:
        return self.title

    @property
    def is_retired(self) -> bool:
        return self.status == self.Status.RETIRED

    @property
    def state(self) -> str:
        """What its author reads: "retirado", "programado", "vencido" or "publicado"."""
        day = today()
        if self.is_retired:
            return "retirado"
        if self.publish_on > day:
            return "programado"
        if self.expires_on and self.expires_on < day:
            return "vencido"
        return "publicado"

    @property
    def is_urgent(self) -> bool:
        return self.priority == self.Priority.URGENT

    @property
    def level(self) -> str:
        """The word that goes beside the title: "Importante", "Urgente", or nothing for a normal notice."""
        return "" if self.priority == self.Priority.NORMAL else self.get_priority_display()

    @property
    def audience(self) -> str:
        return f"Para {self.group.name}" if self.group_id else "Para toda la iglesia"


class AnnouncementRead(models.Model):
    """A person opened a notice. One row per person and notice. ``acknowledged_at``: the day they
    answered "Enterado" to an urgent one, and ``acknowledged_version`` the version of the notice they
    answered to: the answer counts only while that is the notice's version. Only the latest answer is
    kept. It says a person tapped a button, never that a message arrived."""

    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="reads")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="announcement_reads")
    read_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_version = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "aviso leído"
        verbose_name_plural = "avisos leídos"
        constraints = [models.UniqueConstraint(fields=["announcement", "person"], name="notice_read_once")]


class EmailPreference(models.Model):
    """Which notices of their church a person wants to hear about by e-mail. One row per person; no
    row is the default, "Solo los urgentes". The e-mail never carries the notice: it says there is
    one and where (``core.notify``). Kept here and not on ``Person``: it is a choice about notices."""

    class Choice(models.TextChoices):
        URGENT = "urgent", "Solo los urgentes"
        IMPORTANT = "important", "Importantes y urgentes"
        ALL = "all", "Todos"
        NONE = "none", "Ninguno"

    person = models.OneToOneField(Person, on_delete=models.CASCADE, related_name="notice_email_preference")
    choice = models.CharField("Avisos por correo", max_length=10, choices=Choice.choices, default=Choice.URGENT, db_default=Choice.URGENT)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "preferencia de avisos por correo"
        verbose_name_plural = "preferencias de avisos por correo"


class NoticeEmail(models.Model):
    """One e-mail about one version of a notice to one person. The row is written before the e-mail
    is handed over, and the constraint lets one writer win: nobody is mailed twice for a version,
    whoever sends (the publish, the command, two commands at once). It keeps no address and no text.
    ``sent``: the mail server accepted it, never that it arrived or was read."""

    class Outcome(models.TextChoices):
        PENDING = "pending", "Por enviar"
        SENT = "sent", "Enviado"
        FAILED = "failed", "No se pudo enviar"

    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="emails")
    version = models.PositiveIntegerField()
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="notice_emails")
    outcome = models.CharField(max_length=10, choices=Outcome.choices, default=Outcome.PENDING, db_default=Outcome.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    # How many times the e-mail was handed to the mail server and when it last was: a mail that fails is tried
    # again later and later, and no more after ``notices.email.MAX_ATTEMPTS``. 0: a row from before the counter.
    attempts = models.PositiveSmallIntegerField(default=0, db_default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "correo de un aviso"
        verbose_name_plural = "correos de avisos"
        constraints = [models.UniqueConstraint(fields=["announcement", "version", "person"], name="notice_email_once_per_version")]
