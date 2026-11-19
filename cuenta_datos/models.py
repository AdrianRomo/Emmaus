from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import Church


class RemovalRequest(models.Model):
    """«Pedir que borren mis datos»: one task for the church's administration. It deletes nothing.

    It names the ACCOUNT that asked and the church it asked in; the person record is looked up when
    the task is opened, so joining two records of the same person (``people.merge``) needs no rule
    here. The only thing the person says is one of three fixed choices: there is no free text.
    At most one open request per account and church."""

    class Ask(models.TextChoices):
        ACCOUNT = "cuenta", "Mi cuenta"
        CONTACT = "contacto", "Mis datos de contacto"
        EVERYTHING = "todo", "Todo lo que se pueda"

    class Status(models.TextChoices):
        OPEN = "abierta", "Por atender"
        DONE = "atendida", "Atendida"
        WITHDRAWN = "retirada", "Retirada"

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="removal_requests")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="removal_requests")
    ask = models.CharField("Qué pide", max_length=10, choices=Ask.choices)
    status = models.CharField("Estado", max_length=10, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="removal_requests_closed"
    )

    class Meta:
        verbose_name = "solicitud de borrado de datos"
        verbose_name_plural = "solicitudes de borrado de datos"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["church", "user"], condition=Q(status="abierta"), name="removal_request_one_open"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} @ {self.church_id}: {self.ask} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN
