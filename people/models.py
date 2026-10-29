from django.conf import settings
from django.db import models

from core.models import Church, ParticipantProfile
from events.models import normalize_name


class Person(models.Model):
    """A person known to one church: with or without an account, with or without an event.

    Church-scoped by construction: every query goes through ``people.persons.persons_for``. One
    account is at most one person per church (``person_one_per_church_and_user``); the same
    account in another church is another row. Nothing links a person to a campero here; that
    link is a separate, human-confirmed table in a later step (decision 0002, rule 1).
    """

    class CreatedFrom(models.TextChoices):
        SETUP = "setup", "Configuración inicial"
        INVITATION = "invitation", "Invitación"
        TEAM = "team", "Equipo de un evento"
        BACKFILL = "backfill", "Cuentas existentes"
        MANUAL = "manual", "Registro manual"

    ContactMethod = ParticipantProfile.ContactMethod

    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.PROTECT, related_name="people")
    first_name = models.CharField("Nombre", max_length=120)
    last_name = models.CharField("Apellidos", max_length=120, blank=True)
    normalized_name = models.CharField(max_length=250, editable=False, db_default="")
    birth_date = models.DateField("Fecha de nacimiento", null=True, blank=True)
    email = models.EmailField("Correo electrónico", blank=True)
    phone = models.CharField("Teléfono", max_length=40, blank=True)
    preferred_contact_method = models.CharField(
        "Medio de contacto preferido", max_length=20, choices=ContactMethod.choices, default=ContactMethod.EMAIL
    )
    created_from = models.CharField("Origen", max_length=20, choices=CreatedFrom.choices, default=CreatedFrom.MANUAL)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Cuenta",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="persons",
    )
    merged_into = models.ForeignKey(
        "self", verbose_name="Unida a", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "persona"
        verbose_name_plural = "personas"
        ordering = ["last_name", "first_name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["church", "user"], condition=models.Q(user__isnull=False), name="person_one_per_church_and_user"
            ),
        ]
        indexes = [models.Index(fields=["church", "normalized_name"], name="person_church_name_idx")]

    def __str__(self) -> str:
        return self.full_name

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def save(self, *args, **kwargs):
        # Names are stored single-spaced and trimmed; the normalized form is only for duplicate hints.
        self.first_name = " ".join(self.first_name.split())
        self.last_name = " ".join(self.last_name.split())
        self.normalized_name = normalize_name(self.full_name)
        super().save(*args, **kwargs)
