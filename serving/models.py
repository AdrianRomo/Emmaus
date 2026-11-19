from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Church
from people.models import Person


REMINDER_CHOICES = ((0, "No enviar"), (2, "2 días antes"), (5, "5 días antes"))


class Team(models.Model):
    """A team of volunteers of one church: "Bienvenida", "Alabanza", "Niños"."""

    class Status(models.TextChoices):
        ACTIVE = "activo", "Activo"
        ARCHIVED = "archivado", "Archivado"

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="serving_teams")
    name = models.CharField("Nombre del equipo", max_length=120)
    description = models.TextField("Qué hace el equipo", blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    # Wave 10 (docs/v2/wave10/rotas.md). All three start "off": nothing changes for a team until who
    # coordinates it says so.
    swap_needs_approval = models.BooleanField("Los cambios necesitan mi visto bueno", default=False)
    reminder_days = models.PositiveSmallIntegerField("Recordatorio por correo", choices=REMINDER_CHOICES, default=0)
    rotation_every_weeks = models.PositiveSmallIntegerField("Cada cuántas semanas le toca a una persona", default=1)
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
    # What the person does that day. Empty for every assignment made before roles existed, and
    # whenever the coordinator names nobody's role: those keep working as they did.
    role = models.ForeignKey("serving.TeamRole", on_delete=models.SET_NULL, null=True, blank=True, related_name="assignments")

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


class TeamRole(models.Model):
    """What somebody does in a team on a date: "Bienvenida", "Sonido". A role that is no longer used
    is kept as not active: the assignments that named it keep their word."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="roles")
    name = models.CharField("Nombre del rol", max_length=60)
    adults_only = models.BooleanField("Solo personas adultas", default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "rol de un equipo"
        verbose_name_plural = "roles de equipos"
        ordering = ["name", "id"]
        constraints = [models.UniqueConstraint(fields=["team", "name"], name="serving_one_role_per_name")]

    def __str__(self) -> str:
        return self.name


class RoleNeed(models.Model):
    """How many people of one role a date needs."""

    service_date = models.ForeignKey(ServiceDate, on_delete=models.CASCADE, related_name="needs")
    role = models.ForeignKey(TeamRole, on_delete=models.CASCADE, related_name="needs")
    count = models.PositiveSmallIntegerField()

    class Meta:
        verbose_name = "personas que necesita una fecha"
        verbose_name_plural = "personas que necesitan las fechas"
        constraints = [models.UniqueConstraint(fields=["service_date", "role"], name="serving_one_need_per_role")]

    def __str__(self) -> str:
        return f"{self.count} de {self.role} el {self.service_date.date}"


class RotationEntry(models.Model):
    """One person's place in the order a role rotates in. Being here assigns nothing: the rotation
    only proposes, and who coordinates confirms (``serving.rotation``)."""

    role = models.ForeignKey(TeamRole, on_delete=models.CASCADE, related_name="rotation")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="rotation_entries")
    position = models.PositiveSmallIntegerField()

    class Meta:
        verbose_name = "lugar en una rotación"
        verbose_name_plural = "lugares en rotaciones"
        ordering = ["role_id", "position", "id"]
        constraints = [models.UniqueConstraint(fields=["role", "person"], name="serving_one_rotation_place")]

    def __str__(self) -> str:
        return f"{self.person} en la rotación de {self.role}"


class SwapRequest(models.Model):
    """Somebody asked a teammate to take their date. The assignment moves only when the teammate
    accepts (and, if the team says so, when who coordinates agrees). No reason is ever stored."""

    class Status(models.TextChoices):
        PENDING = "pendiente", "Espera respuesta"
        TO_APPROVE = "por_aprobar", "Espera el visto bueno"
        DONE = "hecha", "Hecho"
        DECLINED = "declinada", "No pudo"
        REJECTED = "rechazada", "Sin visto bueno"
        CANCELLED = "cancelada", "Retirada"
        EXPIRED = "vencida", "Venció"

    OPEN = (Status.PENDING, Status.TO_APPROVE)

    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="swaps")
    from_person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="swaps_asked")
    to_person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="swaps_received")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "cambio de fecha entre dos personas"
        verbose_name_plural = "cambios de fecha entre personas"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignment"], condition=models.Q(status__in=("pendiente", "por_aprobar")),
                name="serving_one_open_swap_per_assignment",
            )
        ]

    def __str__(self) -> str:
        return f"Cambio de {self.from_person} a {self.to_person}"


class PlanItem(models.Model):
    """One moment of the order of a date: what, for how many minutes, and who is in charge. No
    songs, lyrics, chords, files or links (their rights are another decision)."""

    service_date = models.ForeignKey(ServiceDate, on_delete=models.CASCADE, related_name="plan_items")
    position = models.PositiveSmallIntegerField()
    title = models.CharField("Qué", max_length=80)
    minutes = models.PositiveSmallIntegerField("Minutos")
    responsible = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True, related_name="plan_items")

    class Meta:
        verbose_name = "momento del orden de una fecha"
        verbose_name_plural = "momentos del orden de las fechas"
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return self.title


class ServingPreference(models.Model):
    """The person's own choice about reminders of their dates. No row is "yes"."""

    person = models.OneToOneField(Person, on_delete=models.CASCADE, related_name="serving_preference")
    reminders = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "preferencia de recordatorios para servir"
        verbose_name_plural = "preferencias de recordatorios para servir"

    def __str__(self) -> str:
        return f"Recordatorios de {self.person}"


class ServiceReminder(models.Model):
    """The one reminder of an assignment: written before the e-mail is handed over, so two senders
    never mail the same person about the same date (``serving.reminders``)."""

    class Outcome(models.TextChoices):
        PENDING = "pending", "Enviando"
        SENT = "sent", "Enviado"
        FAILED = "failed", "Falló"

    assignment = models.OneToOneField(Assignment, on_delete=models.CASCADE, related_name="reminder")
    outcome = models.CharField(max_length=8, choices=Outcome.choices, default=Outcome.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "recordatorio de una fecha"
        verbose_name_plural = "recordatorios de fechas"

    def __str__(self) -> str:
        return f"Recordatorio de {self.assignment_id}"
