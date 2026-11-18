from datetime import date, timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import Church
from events.models import Sede
from people.models import Person

WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábados", "domingos"]
WEEKDAY = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
ORDINALS = {1: "primer", 2: "segundo", 3: "tercer", 4: "cuarto", 5: "último"}


def nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    """The first, second, third or fourth ``weekday`` of a month; 5 is the last one (the fourth or the fifth)."""
    first = date(year, month, 1)
    day = first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (min(nth, 5) - 1))
    return day if day.month == month else day - timedelta(days=7)


class Group(models.Model):
    """A group of one church. What anybody of the church may read about it is on this row except
    ``meeting_place``, which only its members and the administration see (it is often a home)."""

    class Kind(models.TextChoices):
        SMALL = "pequeno", "Grupo pequeño"
        STUDY = "estudio", "Estudio bíblico"
        OTHER = "otro", "Otro grupo"

    class Status(models.TextChoices):
        ACTIVE = "activo", "Activo"
        ARCHIVED = "archivado", "Archivado"

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Lunes"
        TUESDAY = 1, "Martes"
        WEDNESDAY = 2, "Miércoles"
        THURSDAY = 3, "Jueves"
        FRIDAY = 4, "Viernes"
        SATURDAY = 5, "Sábado"
        SUNDAY = 6, "Domingo"

    class Frequency(models.TextChoices):
        WEEKLY = "semanal", "Cada semana"
        BIWEEKLY = "quincenal", "Cada dos semanas"
        MONTHLY = "mensual", "Una vez al mes"
        VARIABLE = "variable", "Sin día fijo"

    class Week(models.IntegerChoices):
        FIRST = 1, "El primero del mes"
        SECOND = 2, "El segundo del mes"
        THIRD = 3, "El tercero del mes"
        FOURTH = 4, "El cuarto del mes"
        LAST = 5, "El último del mes"

    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="groups")
    sede = models.ForeignKey(
        Sede, verbose_name="Sede", on_delete=models.SET_NULL, null=True, blank=True, related_name="groups"
    )
    name = models.CharField("Nombre del grupo", max_length=120)
    kind = models.CharField("Tipo de grupo", max_length=16, choices=Kind.choices, default=Kind.SMALL)
    description = models.TextField("De qué se trata", blank=True)
    audience = models.CharField("Para quién es", max_length=120, blank=True)
    meeting_day = models.PositiveSmallIntegerField("Día en que se reúnen", choices=Weekday.choices, null=True, blank=True)
    meeting_frequency = models.CharField(
        "Cada cuándo se reúnen", max_length=12, choices=Frequency.choices, default=Frequency.WEEKLY, db_default=Frequency.WEEKLY
    )
    # Which of the month's weekdays, for a group that meets once a month ("el segundo jueves").
    meeting_week = models.PositiveSmallIntegerField("Cuál del mes", choices=Week.choices, null=True, blank=True)
    # A date the group does meet, for one that meets every two weeks: it says which weeks are theirs.
    meeting_anchor = models.DateField("Una fecha en que sí se reúnen", null=True, blank=True)
    meeting_time = models.TimeField("Hora", null=True, blank=True)
    meeting_place = models.CharField("Lugar", max_length=160, blank=True)
    accepts_requests = models.BooleanField("Recibe solicitudes para unirse", default=True, db_default=True)
    status = models.CharField("Estado", max_length=12, choices=Status.choices, default=Status.ACTIVE, db_default=Status.ACTIVE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "grupo"
        verbose_name_plural = "grupos"
        ordering = ["name", "id"]
        indexes = [models.Index(fields=["church", "status"], name="group_church_status_idx")]

    def __str__(self) -> str:
        return self.name

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def is_open(self) -> bool:
        """People of the church can ask to join."""
        return self.is_active and self.accepts_requests

    @property
    def has_fixed_day(self) -> bool:
        """The app can tell which days the group meets: a day, and what its rhythm needs to place it."""
        if self.meeting_day is None or self.meeting_frequency == self.Frequency.VARIABLE:
            return False
        if self.meeting_frequency == self.Frequency.BIWEEKLY:
            return self.meeting_anchor is not None
        if self.meeting_frequency == self.Frequency.MONTHLY:
            return self.meeting_week is not None
        return True

    @property
    def schedule(self) -> str:
        """"Los jueves a las 19:30", "Los jueves, cada dos semanas, a las 19:30", "El segundo jueves de cada mes",
        "Sin día fijo", or "" when the day is not set."""
        frequency = self.meeting_frequency
        if frequency == self.Frequency.VARIABLE:
            return "Sin día fijo"
        if self.meeting_day is None:
            return ""
        hour = f" a las {self.meeting_time:%H:%M}" if self.meeting_time else ""
        if frequency == self.Frequency.MONTHLY and self.meeting_week:
            return f"El {ORDINALS[self.meeting_week]} {WEEKDAY[self.meeting_day]} de cada mes{hour}"
        text = f"Los {WEEKDAYS[self.meeting_day]}"
        if frequency == self.Frequency.BIWEEKLY:
            return f"{text}, cada dos semanas{',' if hour else ''}{hour}"
        if frequency == self.Frequency.MONTHLY:
            return f"{text}, una vez al mes{',' if hour else ''}{hour}"
        return text + hour

    def _meeting_near(self, day: date, forward: bool) -> date | None:
        """The date the group meets that is nearest to ``day`` on one side of it, ``day`` included: on or after it
        (``forward``) or on or before it. None when the app cannot know (``has_fixed_day``)."""
        if not self.has_fixed_day:
            return None
        weekday = self.meeting_day
        if self.meeting_frequency == self.Frequency.MONTHLY:
            found = nth_weekday(day.year, day.month, weekday, self.meeting_week)
            if (found < day) if forward else (found > day):
                month = day.month + (1 if forward else -1)
                year = day.year + (month == 13) - (month == 0)
                found = nth_weekday(year, (month - 1) % 12 + 1, weekday, self.meeting_week)
            return found
        if self.meeting_frequency == self.Frequency.BIWEEKLY:
            # The meeting of the anchor's own week (Monday to Sunday), then every fourteen days either way.
            base = self.meeting_anchor + timedelta(days=weekday - self.meeting_anchor.weekday())
            periods, rest = divmod((day - base).days, 14)
            return base + timedelta(days=14 * (periods + (1 if forward and rest else 0)))
        if forward:
            return day + timedelta(days=(weekday - day.weekday()) % 7)
        return day - timedelta(days=(day.weekday() - weekday) % 7)

    def next_meeting(self, today: date | None = None) -> date | None:
        """The next date the group meets by its rhythm, today included; None when the day is not set, the group
        has no fixed day or it is archived."""
        if not self.is_active:
            return None
        return self._meeting_near(today or timezone.localdate(), forward=True)

    def last_meeting_day(self, today: date | None = None) -> date | None:
        """The most recent date the group was due to meet by its rhythm, today included; None when the day is not
        set or the group has no fixed day."""
        return self._meeting_near(today or timezone.localdate(), forward=False)


class GroupMembership(models.Model):
    """A person in a group, as leader or member. Written only by ``groups.services``, which also
    writes the ``access.RoleAssignment`` row that ``has_scope`` reads when the person has an account."""

    class Role(models.TextChoices):
        LEADER = "lider", "Guía del grupo"
        MEMBER = "integrante", "Integrante"

    class Status(models.TextChoices):
        ACTIVE = "activa", "Activa"
        INACTIVE = "inactiva", "Inactiva"

    group = models.ForeignKey(Group, verbose_name="Grupo", on_delete=models.CASCADE, related_name="memberships")
    person = models.ForeignKey(Person, verbose_name="Persona", on_delete=models.CASCADE, related_name="group_memberships")
    role = models.CharField("Rol", max_length=12, choices=Role.choices, default=Role.MEMBER)
    status = models.CharField("Estado", max_length=10, choices=Status.choices, default=Status.ACTIVE)
    start_at = models.DateTimeField("Desde", default=timezone.now)
    end_at = models.DateTimeField("Hasta", null=True, blank=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "integrante de grupo"
        verbose_name_plural = "integrantes de grupos"
        ordering = ["role", "person__first_name", "person__last_name", "id"]
        constraints = [models.UniqueConstraint(fields=["group", "person"], name="group_membership_one_per_person")]

    def __str__(self) -> str:
        return f"{self.person} en {self.group}"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def is_leader(self) -> bool:
        return self.role == self.Role.LEADER


class GroupJoinRequest(models.Model):
    """"Quiero unirme": a person asks, a leader (or the administration) decides. No free text on
    purpose: the request carries who and which group, nothing a person typed."""

    class Status(models.TextChoices):
        PENDING = "pendiente", "Por decidir"
        ACCEPTED = "aceptada", "Aceptada"
        DECLINED = "declinada", "No aceptada"
        WITHDRAWN = "retirada", "Retirada"

    group = models.ForeignKey(Group, verbose_name="Grupo", on_delete=models.CASCADE, related_name="join_requests")
    person = models.ForeignKey(Person, verbose_name="Persona", on_delete=models.CASCADE, related_name="group_join_requests")
    status = models.CharField("Estado", max_length=12, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField("Fecha de la solicitud", auto_now_add=True)
    decided_at = models.DateTimeField("Fecha de la decisión", null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "solicitud para unirse a un grupo"
        verbose_name_plural = "solicitudes para unirse a grupos"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["group", "person"], condition=Q(status="pendiente"), name="group_join_request_one_pending"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.person} → {self.group} ({self.get_status_display()})"

    @property
    def is_pending(self) -> bool:
        return self.status == self.Status.PENDING


class GroupMeeting(models.Model):
    """One day a group met, as who guides it recorded it: the day, how many were in the group that
    day, and who came (``GroupAttendance``). Never what was said there. Written only by
    ``groups.services.record_meeting``; who came is read only by the group's guides, the
    administration and each person about themselves; the totals also by "Panorama" (``overview``).

    Retention (``prune_attendance``): after ``EMMAUS_ATTENDANCE_IDENTIFIABLE_MONTHS`` the rows that
    say who came are deleted and ``present_kept`` keeps how many they were; after
    ``EMMAUS_ATTENDANCE_AGGREGATE_MONTHS`` the meeting itself is deleted."""

    group = models.ForeignKey(Group, verbose_name="Grupo", on_delete=models.CASCADE, related_name="meetings")
    date = models.DateField("Día de la reunión")
    expected = models.PositiveSmallIntegerField("Personas en el grupo ese día", default=0)
    # None while the meeting still has its rows of who came; a number once they were deleted.
    present_kept = models.PositiveSmallIntegerField("Personas que vinieron (total que se conserva)", null=True, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "reunión de grupo"
        verbose_name_plural = "reuniones de grupos"
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["group", "date"], name="group_meeting_one_per_day")]

    def __str__(self) -> str:
        return f"{self.group} · {self.date:%Y-%m-%d}"

    @property
    def names_deleted(self) -> bool:
        """Who came is no longer stored: only the totals are (``prune_attendance``)."""
        return self.present_kept is not None


class GroupAttendance(models.Model):
    """A person came to a meeting. Only who came has a row; who did not is the rest of the group."""

    meeting = models.ForeignKey(GroupMeeting, verbose_name="Reunión", on_delete=models.CASCADE, related_name="attendances")
    person = models.ForeignKey(Person, verbose_name="Persona", on_delete=models.CASCADE, related_name="group_attendances")

    class Meta:
        verbose_name = "asistencia a una reunión"
        verbose_name_plural = "asistencias a reuniones"
        constraints = [models.UniqueConstraint(fields=["meeting", "person"], name="group_attendance_one_per_person")]

    def __str__(self) -> str:
        return f"{self.person} en {self.meeting}"
