from django.conf import settings
from django.db import models

from events.models import Event
from people.models import Person


class EventPage(models.Model):
    """What the people of the church read about one event, and whether they can sign up from
    Emmaus. The event itself (name, dates, place) stays on ``events.Event``; an event without a
    page, or with one that is not published, is invisible to them."""

    class Registration(models.TextChoices):
        CLOSED = "cerrada", "No hay inscripción desde Emmaus"
        OPEN = "abierta", "Las personas se inscriben desde Emmaus"

    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name="page")
    published = models.BooleanField("Publicar para la iglesia", default=False)
    description = models.TextField("De qué se trata", blank=True)
    starts_at = models.TimeField("Hora", null=True, blank=True)
    registration = models.CharField(
        "Inscripción", max_length=12, choices=Registration.choices, default=Registration.CLOSED
    )
    capacity = models.PositiveIntegerField("Lugares", null=True, blank=True)
    registration_closes_on = models.DateField("Último día para inscribirse", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "página de evento"
        verbose_name_plural = "páginas de evento"

    def __str__(self) -> str:
        return f"Página de {self.event}"

    @property
    def registration_open(self) -> bool:
        return self.registration == self.Registration.OPEN


class EventRegistration(models.Model):
    """One person said they want to come to one event. It gives no role and no access, and it is
    never an ``EventParticipant``. Cancelling keeps the row; signing up again reactivates it."""

    class Status(models.TextChoices):
        ACTIVE = "inscrita", "Inscrita"
        CANCELLED = "cancelada", "Cancelada"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="registrations")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="event_registrations")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # None: nobody has said yet whether this person came.
    attended = models.BooleanField(null=True, blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "inscripción a un evento"
        verbose_name_plural = "inscripciones a eventos"
        constraints = [models.UniqueConstraint(fields=["event", "person"], name="agenda_one_registration_per_person")]
        indexes = [models.Index(fields=["event", "status"], name="agenda_reg_event_status_idx")]

    def __str__(self) -> str:
        return f"{self.person} en {self.event}"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE
