from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator
from django.db import models
from django.db.models import F, Q

from core.models import Church
from people.models import Person

MAX_BUFFER_MINUTES = 240


class Space(models.Model):
    """A room or a piece of equipment of one church that one activity at a time can use."""

    class Kind(models.TextChoices):
        ROOM = "salon", "Salón"
        EQUIPMENT = "equipo", "Equipo"

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="spaces")
    name = models.CharField("Nombre", max_length=120)
    kind = models.CharField("Qué es", max_length=12, choices=Kind.choices, default=Kind.ROOM)
    capacity = models.PositiveIntegerField("Cuántas personas caben", null=True, blank=True)
    # Read only by the administration: where the key is, what must not be moved.
    notes = models.TextField("Notas para la administración", blank=True)
    active = models.BooleanField("Se puede reservar", default=True)
    needs_approval = models.BooleanField("La administración aprueba cada reserva", default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "espacio"
        verbose_name_plural = "espacios"
        ordering = ["kind", "name", "id"]
        constraints = [models.UniqueConstraint(fields=["church", "name"], name="espacios_one_name_per_church")]

    def __str__(self) -> str:
        return self.name


class Booking(models.Model):
    """One use of one space. ``blocked_from`` and ``blocked_until`` are the hours nobody else can
    have it: the use itself plus the time to set up before and to put away after. They are written
    by ``save()`` from the four fields beside them and are what every overlap question reads."""

    class Status(models.TextChoices):
        REQUESTED = "solicitada", "Solicitada"
        APPROVED = "aprobada", "Aprobada"
        REJECTED = "rechazada", "Rechazada"
        CANCELLED = "cancelada", "Cancelada"

    # The states that keep the space: a request holds its hours while the administration decides.
    HOLDING = (Status.REQUESTED, Status.APPROVED)

    space = models.ForeignKey(Space, on_delete=models.PROTECT, related_name="bookings")
    title = models.CharField("Para qué es", max_length=120)
    starts_at = models.DateTimeField("Empieza")
    ends_at = models.DateTimeField("Termina")
    setup_minutes = models.PositiveSmallIntegerField("Minutos para preparar antes", default=0, validators=[MaxValueValidator(MAX_BUFFER_MINUTES)])
    teardown_minutes = models.PositiveSmallIntegerField("Minutos para recoger después", default=0, validators=[MaxValueValidator(MAX_BUFFER_MINUTES)])
    blocked_from = models.DateTimeField(editable=False)
    blocked_until = models.DateTimeField(editable=False)
    requested_by = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True, related_name="space_bookings")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    # Why the administration said no. Read only by the administration; never copied to the activity log.
    decision_reason = models.CharField("Motivo", max_length=200, blank=True)
    # The administration saw the bookings this one overlaps and kept it anyway (espacios.services).
    overrode_clash = models.BooleanField(default=False)
    event = models.ForeignKey("events.Event", on_delete=models.SET_NULL, null=True, blank=True, related_name="space_bookings")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "reserva"
        verbose_name_plural = "reservas"
        ordering = ["starts_at", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="espacios_booking_ends_after_start"),
            models.CheckConstraint(
                condition=Q(blocked_from__lte=F("starts_at")) & Q(blocked_until__gte=F("ends_at")),
                name="espacios_booking_block_covers_use",
            ),
        ]
        indexes = [
            models.Index(fields=["space", "status", "blocked_from"], name="espacios_booking_window_idx"),
            models.Index(fields=["requested_by", "status"], name="espacios_booking_person_idx"),
        ]

    def __str__(self) -> str:
        return f"Reserva {self.pk} de {self.space}"

    def set_block(self) -> None:
        self.blocked_from = self.starts_at - timedelta(minutes=self.setup_minutes)
        self.blocked_until = self.ends_at + timedelta(minutes=self.teardown_minutes)

    def save(self, *args, **kwargs):
        fields = kwargs.get("update_fields")
        if fields is None or {"starts_at", "ends_at", "setup_minutes", "teardown_minutes"} & set(fields):
            self.set_block()
            if fields is not None:
                kwargs["update_fields"] = [*fields, "blocked_from", "blocked_until"]
        super().save(*args, **kwargs)

    @property
    def holds(self) -> bool:
        return self.status in self.HOLDING
