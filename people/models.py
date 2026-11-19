from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.dispatch import Signal
from django.utils import timezone

from core.models import Church, ParticipantProfile
from events.models import Sede, normalize_name


# Sent after a save that changed what the church knows about a person's age: the date of birth or
# its verification. ``agenda`` listens and re-evaluates the person's registrations (decision 0015).
age_facts_changed = Signal()
_NOT_LOADED = object()


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
        IMPORT = "import", "Archivo importado"

    class BirthDateMethod(models.TextChoices):
        """How the team verified a date of birth (decision 0015). The method is all that is kept: no
        image of a document and no document number, ever."""

        DOCUMENT = "documento", "Acta o documento oficial visto por el equipo"
        CHURCH_RECORD = "registro", "Registro de la iglesia"
        GUARDIAN = "tutor", "Lo confirmó su madre, padre o tutor en persona"

    class Stage(models.TextChoices):
        """Where the church's administration says a person stands with the church: a short closed list
        the church does not edit. A label a person of the administration sets by hand, never a score:
        nothing moves it by itself and nothing infers it from attendance (decision 0009). Empty: nobody
        has said. Written only by ``people.services.set_stage`` (and at creation)."""

        VISITOR = "visita", "Visita"
        ATTENDER = "asistente", "Asistente"
        MEMBER = "miembro", "Miembro"
        GONE = "ya_no_asiste", "Ya no asiste"

    ContactMethod = ParticipantProfile.ContactMethod

    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.PROTECT, related_name="people")
    first_name = models.CharField("Nombre", max_length=120)
    last_name = models.CharField("Apellidos", max_length=120, blank=True)
    normalized_name = models.CharField(max_length=250, editable=False, db_default="")
    birth_date = models.DateField("Fecha de nacimiento", null=True, blank=True)
    # Set only by ``people.services.verify_birth_date``. A date somebody typed (the person, or the
    # administration in "Editar los datos") is a claim; any change of the date clears these three.
    birth_date_verified_at = models.DateTimeField("Edad confirmada el", null=True, blank=True, editable=False)
    birth_date_verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Edad confirmada por", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", editable=False,
    )
    birth_date_verified_method = models.CharField(
        "Cómo se confirmó la edad", max_length=12, choices=BirthDateMethod.choices, blank=True, default="", db_default="",
        editable=False,
    )
    email = models.EmailField("Correo electrónico", blank=True)
    phone = models.CharField("Teléfono", max_length=40, blank=True)
    preferred_contact_method = models.CharField(
        "Medio de contacto preferido", max_length=20, choices=ContactMethod.choices, default=ContactMethod.EMAIL
    )
    created_from = models.CharField("Origen", max_length=20, choices=CreatedFrom.choices, default=CreatedFrom.MANUAL)
    stage = models.CharField("Etapa", max_length=12, choices=Stage.choices, blank=True, default="", db_default="")
    # The day somebody of the administration last set ``stage``. Never the day a person "became" anything.
    stage_changed_on = models.DateField("Etapa anotada el", null=True, blank=True, editable=False)
    # Key dates, all optional. The administration writes the three; the person writes the first two in
    # "Mis datos" (they are theirs to say), never the third. No other member ever reads them.
    baptized_on = models.DateField("Se bautizó el", null=True, blank=True)
    arrived_on = models.DateField("Llegó a la iglesia el", null=True, blank=True)
    member_since = models.DateField("Es miembro desde", null=True, blank=True)
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
    # The file that created this row, if one did (``people.imports``): what its undo may delete, and nothing else.
    imported_in = models.ForeignKey(
        "people.PeopleImport", verbose_name="Importación", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="people", editable=False,
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

    VERIFICATION_FIELDS = ("birth_date_verified_at", "birth_date_verified_by", "birth_date_verified_method")

    @property
    def birth_date_verified(self) -> bool:
        """The team verified this date of birth by an approved method."""
        return self.birth_date is not None and self.birth_date_verified_at is not None

    @classmethod
    def from_db(cls, db, field_names, values):
        person = super().from_db(db, field_names, values)
        person._remember_loaded_birth_date()
        return person

    def refresh_from_db(self, *args, **kwargs):
        super().refresh_from_db(*args, **kwargs)
        self._remember_loaded_birth_date()

    def _remember_loaded_birth_date(self) -> None:
        """The date of birth this instance READ from the database. Only a read sets it (never a
        save), and it decides one thing: whether an instance that differs from the database is
        changing the date or is merely stale."""
        self._loaded_birth_date = self.__dict__.get("birth_date", _NOT_LOADED)

    def save(self, *args, keep_birth_date_verification: bool = False, **kwargs):
        """``keep_birth_date_verification`` is for ``people.services.verify_birth_date`` alone.

        What is about to be written is compared with the row in the database, never with what
        this instance remembers (decision 0015): any other save that changes the date of birth
        clears its verification, and no other save writes a verification at all, so an instance
        read before a correction cannot bring an old date or an old verification back."""
        # Names are stored single-spaced and trimmed; the normalized form is only for duplicate hints.
        self.first_name = " ".join(self.first_name.split())
        self.last_name = " ".join(self.last_name.split())
        self.normalized_name = normalize_name(self.full_name)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and {"first_name", "last_name"} & set(update_fields):
            update_fields = kwargs["update_fields"] = {*update_fields, "normalized_name"}
        adding = self._state.adding
        changed = False
        if adding:
            if not keep_birth_date_verification:  # a new person may not arrive verified
                self.birth_date_verified_at, self.birth_date_verified_by, self.birth_date_verified_method = None, None, ""
        else:
            deferred = self.get_deferred_fields()
            names = {"birth_date", "birth_date_verified_at", "birth_date_verified_by_id", "birth_date_verified_method"}
            if update_fields is None:
                written = names - deferred
            else:
                written = {name for name in names if name in update_fields or name.removesuffix("_id") in update_fields}
            stored = None
            if written:
                stored = Person.objects.filter(pk=self.pk).values(*names).first()
            if stored is not None:
                writes_date = "birth_date" in written
                if keep_birth_date_verification:
                    changed = (stored["birth_date"], stored["birth_date_verified_at"]) != (self.birth_date, self.birth_date_verified_at)
                else:
                    loaded = getattr(self, "_loaded_birth_date", _NOT_LOADED)
                    if writes_date and stored["birth_date"] != self.birth_date and loaded == self.birth_date:
                        # This instance did not change the date: it read it before somebody else did. Stale.
                        self.birth_date = stored["birth_date"]
                    if writes_date and stored["birth_date"] != self.birth_date:
                        changed = True
                        self.birth_date_verified_at, self.birth_date_verified_by, self.birth_date_verified_method = None, None, ""
                        if update_fields is not None:
                            kwargs["update_fields"] = {*update_fields, *self.VERIFICATION_FIELDS}
                    else:
                        # The verification is not this save's to write: what the database has stays.
                        self.birth_date_verified_at = stored["birth_date_verified_at"]
                        self.birth_date_verified_by_id = stored["birth_date_verified_by_id"]
                        self.birth_date_verified_method = stored["birth_date_verified_method"]
        super().save(*args, **kwargs)
        if changed:
            self._loaded_birth_date = self.birth_date
            # ``_age_facts_actor``: who is making the change, when a service said so (for the audit).
            age_facts_changed.send(sender=Person, person=self, actor=getattr(self, "_age_facts_actor", None))


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


class PersonMerge(models.Model):
    """Two records of one church were said to be the same person (``people.merge.merge``, the one
    write path): ``absorbed`` was joined into ``survivor`` by an administrator, one pair at a time.

    ``manifest`` is what the undo needs and nothing else: the ids of the rows that were re-pointed,
    by table; the ids of the camperos whose link moved; the account that moved; and, for 30 days,
    the contact values the survivor had before (``survivor_before``). It never holds an answer, a
    note or any text somebody else wrote. ``people.merge.clear_expired`` empties the contact values
    when the undo is no longer possible. Both people are protected: a record that took part in a
    merge is not deleted while this row exists (``Person.merged_into`` is ``SET_NULL``, so deleting
    a survivor would bring the absorbed record back as if nothing had happened).
    """

    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="person_merges")
    survivor = models.ForeignKey(Person, verbose_name="Se queda", on_delete=models.PROTECT, related_name="merges_received")
    absorbed = models.ForeignKey(Person, verbose_name="Se une", on_delete=models.PROTECT, related_name="merges_given")
    merged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    merged_at = models.DateTimeField("Fecha de la unión", default=timezone.now)
    manifest = models.JSONField(default=dict, blank=True)
    contact_cleared_at = models.DateTimeField(null=True, blank=True)
    undone_at = models.DateTimeField("Fecha en que se deshizo", null=True, blank=True)
    undone_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # Counts by kind of what the undo could not put back; never an id of a private row, never a name.
    undo_report = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "unión de dos registros de persona"
        verbose_name_plural = "uniones de registros de persona"
        ordering = ["-merged_at", "-id"]
        constraints = [
            # One record is joined into one other at a time: two administrators at once make one merge.
            models.UniqueConstraint(fields=["absorbed"], condition=models.Q(undone_at__isnull=True), name="person_merge_one_in_force"),
            models.CheckConstraint(condition=~models.Q(survivor=models.F("absorbed")), name="person_merge_two_people"),
        ]
        indexes = [models.Index(fields=["church", "-merged_at"], name="person_merge_recent_idx")]

    def __str__(self) -> str:
        return f"Unión {self.pk}"

    @property
    def is_undone(self) -> bool:
        return self.undone_at is not None


class DuplicateDismissal(models.Model):
    """"No son la misma persona": an administrator looked at a suggested pair and said no. The pair
    is not suggested again and cannot be merged while this row exists. ``person_a`` is always the
    lower id, so a pair has one row whichever way it was shown."""

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="duplicate_dismissals")
    person_a = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="+")
    person_b = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="+")
    dismissed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    dismissed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "par que no es la misma persona"
        verbose_name_plural = "pares que no son la misma persona"
        ordering = ["-dismissed_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["person_a", "person_b"], name="duplicate_dismissal_once"),
            models.CheckConstraint(condition=models.Q(person_a__lt=models.F("person_b")), name="duplicate_dismissal_ordered"),
        ]

    def __str__(self) -> str:
        return f"Descartado {self.person_a_id}/{self.person_b_id}"


class Tag(models.Model):
    """A label the church's administration defines for its own filtering ("Coro", "Llaves del templo").

    Staff-only: the person and other members never see a tag. A tag is not a permission and nothing
    in ``core.authz`` or ``access`` reads it. Short and single-line on purpose, and the screen that
    creates one says not to use tags for sensitive matters. Written only through ``people.tags``;
    at most ``people.tags.MAX_TAGS`` per church.
    """

    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="person_tags")
    name = models.CharField("Nombre", max_length=40)
    normalized_name = models.CharField(max_length=40, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "etiqueta"
        verbose_name_plural = "etiquetas"
        ordering = ["normalized_name", "id"]
        constraints = [models.UniqueConstraint(fields=["church", "normalized_name"], name="person_tag_one_name_per_church")]

    def __str__(self) -> str:
        return self.name


class PersonTag(models.Model):
    """This person carries this tag. Both of the same church (``people.tags.set_tags``, the one write path)."""

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="tag_links")
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="person_links")
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "etiqueta de una persona"
        verbose_name_plural = "etiquetas de personas"
        ordering = ["tag__normalized_name", "id"]
        constraints = [models.UniqueConstraint(fields=["person", "tag"], name="person_tag_once")]

    def __str__(self) -> str:
        return f"{self.person_id}/{self.tag_id}"


class PeopleImport(models.Model):
    """One confirmed import of people from a file (``people.imports.apply``, the one write path).

    The file is never stored, and neither is its name or any of its cells: this row says who
    imported, when and how many people it created; ``Person.imported_in`` says which. For
    ``people.imports.UNDO_HOURS`` the import can be undone: exactly the rows it created are deleted,
    except the ones that gained something since (``undo_report`` counts both, never a name).
    """

    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="people_imports")
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    imported_at = models.DateTimeField("Fecha de la importación", default=timezone.now)
    created_count = models.PositiveIntegerField("Personas creadas", default=0)
    undone_at = models.DateTimeField("Fecha en que se deshizo", null=True, blank=True)
    undone_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    undo_report = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "importación de personas"
        verbose_name_plural = "importaciones de personas"
        ordering = ["-imported_at", "-id"]
        indexes = [models.Index(fields=["church", "-imported_at"], name="people_import_recent_idx")]

    def __str__(self) -> str:
        return f"Importación {self.pk}"

    @property
    def is_undone(self) -> bool:
        return self.undone_at is not None
