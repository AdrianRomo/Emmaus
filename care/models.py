from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Church
from people.models import Person


def today():
    return timezone.localdate()


class FollowUp(models.Model):
    """Somebody of the church should be reached by ``owner`` before ``due_on``.

    Privacy rule of the module: no field here or in ``FollowUpStep`` holds free text. The reason is
    one of six fixed sentences and a step is one of four; what was said is never written in Emmaus.
    Not the camp's follow-ups (``events``) nor the discipleship ones (``core``): no link to either.
    """

    class Reason(models.TextChoices):
        VISIT = "visita", "Vino por primera vez"
        ABSENCE = "ausencia", "Hace tiempo que no viene"
        GROUP = "grupo", "Quiere unirse a un grupo"
        SERVE = "servir", "Quiere servir"
        CONTACT = "contacto", "Pidió que la contacten"
        OTHER = "otro", "Otro motivo"

    class Status(models.TextChoices):
        PENDING = "pendiente", "Pendiente"
        IN_PROCESS = "en_proceso", "En proceso"
        DONE = "atendido", "Atendido"
        CANCELLED = "cancelado", "Cancelado"

    OPEN = (Status.PENDING, Status.IN_PROCESS)
    # The key ``{% status %}`` (events.templatetags.emmaus) draws each state with.
    STATUS_MARKS = {"pendiente": "pendiente", "en_proceso": "en_curso", "atendido": "completada", "cancelado": "descartada"}

    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="care_follow_ups")
    person = models.ForeignKey(Person, verbose_name="Persona", on_delete=models.CASCADE, related_name="follow_ups")
    reason = models.CharField("Motivo", max_length=12, choices=Reason.choices)
    owner = models.ForeignKey(
        Person, verbose_name="Responsable", on_delete=models.SET_NULL, null=True, blank=True, related_name="owned_follow_ups"
    )
    due_on = models.DateField("Para cuándo", null=True, blank=True)
    status = models.CharField("Estado", max_length=12, choices=Status.choices, default=Status.PENDING)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "seguimiento de persona"
        verbose_name_plural = "seguimientos de persona"
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["church", "status"], name="care_church_status_idx"),
            models.Index(fields=["owner", "status"], name="care_owner_status_idx"),
        ]

    def __str__(self) -> str:
        return f"Seguimiento de persona {self.pk}"

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN

    @property
    def status_mark(self) -> str:
        return self.STATUS_MARKS[self.status]


class FollowUpStep(models.Model):
    """One thing the owner did to reach the person: which of four, and the day. Never what was said."""

    class Kind(models.TextChoices):
        CALL = "llamada", "Le llamé"
        MESSAGE = "mensaje", "Le escribí"
        VISIT = "visita", "Nos vimos"
        NO_ANSWER = "sin_respuesta", "No respondió"

    follow_up = models.ForeignKey(FollowUp, on_delete=models.CASCADE, related_name="steps")
    kind = models.CharField("Qué hiciste", max_length=16, choices=Kind.choices)
    on = models.DateField("Día", default=today)
    by = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "paso de un seguimiento"
        verbose_name_plural = "pasos de un seguimiento"
        ordering = ["on", "id"]

    def __str__(self) -> str:
        return self.get_kind_display()
