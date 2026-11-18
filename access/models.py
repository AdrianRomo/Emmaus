from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import Church


class ScopeType(models.TextChoices):
    CHURCH = "church", "Iglesia"
    EVENT = "event", "Evento"
    GROUP = "group", "Grupo"
    MINISTRY = "ministry", "Equipo de servicio"


class RoleAssignment(models.Model):
    """A role an account holds in one scope, for a time (decision 0003).

    A projection: every row derives from exactly one source row (``source_table``, ``source_id``)
    and is written by that source's service in the same transaction. The sources are
    ``groups.GroupMembership`` (scope ``group``) and ``serving.TeamMembership`` (scope ``ministry``,
    decision 0016), and for those scopes this table *is* the answer of ``core.authz.has_scope``.
    Church and event roles stay in ``UserChurchRole`` and ``EventMembership``; ``has_scope``
    delegates to them and no row is stored here for them.

    A role here never implies anything outside its scope: a group leader or a team's
    coordinator holds nothing in the church or in an event, and an event role holds nothing in a group
    or a team (AGENTS.md invariant 2).
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Vigente"
        INACTIVE = "inactive", "Terminado"

    subject_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Cuenta", on_delete=models.CASCADE, related_name="role_assignments"
    )
    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="role_assignments")
    scope_type = models.CharField("Ámbito", max_length=16, choices=ScopeType.choices)
    scope_id = models.PositiveBigIntegerField("Identificador del ámbito")
    role = models.CharField("Rol", max_length=32)
    status = models.CharField("Estado", max_length=10, choices=Status.choices, default=Status.ACTIVE)
    start_at = models.DateTimeField("Desde", default=timezone.now)
    end_at = models.DateTimeField("Hasta", null=True, blank=True)
    source_table = models.CharField(max_length=64)
    source_id = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "rol por ámbito"
        verbose_name_plural = "roles por ámbito"
        ordering = ["church_id", "scope_type", "scope_id", "id"]
        constraints = [
            models.UniqueConstraint(fields=["source_table", "source_id"], name="role_assignment_one_per_source"),
        ]
        indexes = [
            models.Index(fields=["subject_user", "scope_type", "status"], name="role_assignment_subject_idx"),
            models.Index(fields=["church", "scope_type", "scope_id"], name="role_assignment_scope_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.subject_user_id} {self.role} @ {self.scope_type}:{self.scope_id}"


def in_force(now=None) -> Q:
    """Rows that grant something right now: active, started, not ended. Expiry needs no job."""
    now = now or timezone.now()
    return Q(status=RoleAssignment.Status.ACTIVE, start_at__lte=now) & (Q(end_at__isnull=True) | Q(end_at__gt=now))
