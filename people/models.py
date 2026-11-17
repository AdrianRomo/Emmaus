from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.models import Church, ParticipantProfile
from events.models import Sede, normalize_name


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
        EVENT = "event", "Un evento"

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
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and {"first_name", "last_name"} & set(update_fields):
            kwargs["update_fields"] = {*update_fields, "normalized_name"}
        super().save(*args, **kwargs)


class CampusAffiliation(models.Model):
    """A person's belonging to one sede of their church: several per person, at most one primary.

    Additive and unread (decision 0002, roadmap P1-4): the camp keeps ``EventParticipant.sede``.
    The sede must be of the person's church (``clean`` and ``people.services.affiliate``, the
    one write path); a sede with affiliations cannot be deleted (PROTECT), it is deactivated.
    """

    class Status(models.TextChoices):
        ACTIVE = "activa", "Activa"
        INACTIVE = "inactiva", "Inactiva"

    person = models.ForeignKey(
        Person, verbose_name="Persona", on_delete=models.CASCADE, related_name="campus_affiliations"
    )
    sede = models.ForeignKey(Sede, verbose_name="Sede", on_delete=models.PROTECT, related_name="person_affiliations")
    is_primary = models.BooleanField("Sede principal", default=False)
    status = models.CharField("Estado", max_length=10, choices=Status.choices, default=Status.ACTIVE)
    start_date = models.DateField("Desde", null=True, blank=True)
    end_date = models.DateField("Hasta", null=True, blank=True)
    created_at = models.DateTimeField("Fecha de registro", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "afiliación a sede"
        verbose_name_plural = "afiliaciones a sedes"
        ordering = ["-is_primary", "id"]
        constraints = [
            models.UniqueConstraint(fields=["person", "sede"], name="campus_affiliation_one_per_person_and_sede"),
            models.UniqueConstraint(
                fields=["person"], condition=models.Q(is_primary=True), name="campus_affiliation_one_primary_per_person"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.person} · {self.sede}"

    def clean(self):
        super().clean()
        if self.person_id is not None and self.sede_id is not None and self.sede.church_id != self.person.church_id:
            message = "Esta sede es de otra iglesia. Elige una sede de la iglesia de la persona."
            raise ValidationError({"sede": ValidationError(message, code="sede_other_church")})


class ChurchSettings(models.Model):
    """Per-church module flags (decision 0004). Read only through ``people.flags.enabled``.

    One row per church, created by the first write: a church with no row has every module off.
    They move to ``Church`` when the camp's schema may change (decision 0004, rule 8).
    """

    church = models.OneToOneField(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="module_settings")
    people_enabled = models.BooleanField("Usar el módulo de personas", default=False, db_default=False)
    community_enabled = models.BooleanField("Usar el módulo de comunidad", default=False, db_default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "módulos de la iglesia"
        verbose_name_plural = "módulos de las iglesias"

    def __str__(self) -> str:
        return f"Módulos de {self.church}"


class ChurchAuditEvent(models.Model):
    """What happened in a church outside an event (issue #35): a person created or linked, a module
    switched, a group decision. Written only by ``people.audit.record_church``.

    ``summary`` and ``metadata`` never hold an e-mail, a phone or free text typed by a person.
    Interim home: ``events.AuditEvent`` gains a church column when the camp's schema may change.
    """

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="audit_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    actor_label = models.CharField(max_length=160)
    action = models.SlugField(max_length=48)
    summary = models.CharField(max_length=300)
    object_type = models.CharField(max_length=48, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "movimiento de la iglesia"
        verbose_name_plural = "movimientos de la iglesia"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["church", "-created_at"], name="church_audit_recent_idx")]

    def __str__(self) -> str:
        return f"{self.actor_label}: {self.summary}"


class EventParticipantLink(models.Model):
    """"This campero is this person": confirmed by an administrator, one at a time, after the event.

    A separate table on purpose (decision 0002): the camp's ``EventParticipant`` gains no column and
    no camp screen reads this. One campero is one person; one person is one campero per event. The
    person and the event are always of the same church (``people.services.link_participant``, the one
    write path). The retention purge deletes the campero and, with it, this row; never the person.
    Nothing here opens a camp answer: group and church screens read only the event's name and dates.
    """

    participant = models.OneToOneField(
        "events.EventParticipant", verbose_name="Campero", on_delete=models.CASCADE, related_name="person_link"
    )
    person = models.ForeignKey(Person, verbose_name="Persona", on_delete=models.CASCADE, related_name="event_links")
    # Denormalised from participant.event so "one campero per event per person" is a database rule.
    event = models.ForeignKey("events.Event", verbose_name="Evento", on_delete=models.CASCADE, related_name="person_links")
    linked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Vinculó", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    linked_at = models.DateTimeField("Fecha del vínculo", auto_now_add=True)

    class Meta:
        verbose_name = "vínculo de campero y persona"
        verbose_name_plural = "vínculos de camperos y personas"
        ordering = ["-linked_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["person", "event"], name="participant_link_one_per_person_and_event"),
        ]

    def __str__(self) -> str:
        return f"{self.person} · {self.event}"

    def clean(self):
        super().clean()
        if self.participant_id is None or self.person_id is None:
            return
        if self.participant.event.church_id != self.person.church_id:
            raise ValidationError(
                {"person": ValidationError("Esta persona es de otra iglesia.", code="person_other_church")}
            )
