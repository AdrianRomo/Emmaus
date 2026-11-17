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
    priority = models.CharField("Nivel", max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    publish_on = models.DateField("Se publica el", default=today)
    expires_on = models.DateField("Deja de verse después del", null=True, blank=True)
    status = models.CharField("Estado", max_length=12, choices=Status.choices, default=Status.PUBLISHED)
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
    answered "Enterado" to an urgent one. It says a person tapped a button, never that a message arrived."""

    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="reads")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="announcement_reads")
    read_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "aviso leído"
        verbose_name_plural = "avisos leídos"
        constraints = [models.UniqueConstraint(fields=["announcement", "person"], name="notice_read_once")]
