from django.conf import settings
from django.db import models

from core.models import Church
from people.models import Person


class Team(models.Model):
    """A team of volunteers of one church: "Bienvenida", "Alabanza", "Niños"."""

    class Status(models.TextChoices):
        ACTIVE = "activo", "Activo"
        ARCHIVED = "archivado", "Archivado"

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="serving_teams")
    name = models.CharField("Nombre del equipo", max_length=120)
    description = models.TextField("Qué hace el equipo", blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "equipo"
        verbose_name_plural = "equipos"
        ordering = ["name", "id"]
        indexes = [models.Index(fields=["church", "status"], name="serving_team_church_status_idx")]

    def __str__(self) -> str:
        return self.name

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE


class TeamMembership(models.Model):
    """One person in one team. It gives nothing outside the team: no church role, no group role,
    no event role. Taking somebody out keeps the row as "inactiva"."""

    class Role(models.TextChoices):
        COORDINATOR = "coordina", "Coordina el equipo"
        SERVES = "sirve", "Sirve en el equipo"

    class Status(models.TextChoices):
        ACTIVE = "activa", "Activa"
        INACTIVE = "inactiva", "Inactiva"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="team_memberships")
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.SERVES)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "integrante de un equipo"
        verbose_name_plural = "integrantes de equipos"
        constraints = [models.UniqueConstraint(fields=["team", "person"], name="serving_one_membership_per_person")]

    def __str__(self) -> str:
        return f"{self.person} en {self.team}"

    @property
    def is_coordinator(self) -> bool:
        return self.role == self.Role.COORDINATOR


class ServiceDate(models.Model):
    """One day a team serves, and what is needed that day."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="dates")
    date = models.DateField("Día")
    starts_at = models.TimeField("Hora", null=True, blank=True)
    what = models.CharField("Qué se necesita", max_length=120)
    place = models.CharField("Lugar", max_length=120, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "fecha de servicio"
        verbose_name_plural = "fechas de servicio"
        ordering = ["date", "starts_at", "id"]
        indexes = [models.Index(fields=["team", "date"], name="serving_date_team_date_idx")]

    def __str__(self) -> str:
        return f"{self.what} ({self.date})"


class Assignment(models.Model):
    """One person asked to serve on one date, and what they answered."""

    class Status(models.TextChoices):
        PENDING = "pendiente", "Por confirmar"
        ACCEPTED = "aceptada", "Confirmó"
        DECLINED = "declinada", "No puede"

    service_date = models.ForeignKey(ServiceDate, on_delete=models.CASCADE, related_name="assignments")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="serving_assignments")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "asignación"
        verbose_name_plural = "asignaciones"
        constraints = [models.UniqueConstraint(fields=["service_date", "person"], name="serving_one_assignment_per_person")]
        indexes = [models.Index(fields=["person", "status"], name="serving_assign_person_idx")]

    def __str__(self) -> str:
        return f"{self.person} el {self.service_date.date}"


class Unavailable(models.Model):
    """"Ese día no puedo": one person, one day, for every team at once."""

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="unavailable_days")
    date = models.DateField("Día")

    class Meta:
        verbose_name = "día que no puede"
        verbose_name_plural = "días que no puede"
        ordering = ["date", "id"]
        constraints = [models.UniqueConstraint(fields=["person", "date"], name="serving_one_unavailable_per_day")]

    def __str__(self) -> str:
        return f"{self.person} no puede el {self.date}"
