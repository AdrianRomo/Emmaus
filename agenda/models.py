from django.conf import settings
from django.db import models
from django.utils import timezone

from events.models import Event
from people import policy
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
    requires_guardian_consent = models.BooleanField(
        "Los menores de edad necesitan autorización de su madre, padre o tutor", default=False, db_default=False
    )
    # Decision 0011: when the places run out, who signs up waits in order and is OFFERED a freed place.
    waiting_list = models.BooleanField(
        "Cuando se llenen los lugares, anotar en lista de espera", default=False, db_default=False
    )
    # Guardian access, phase A (decision 0010): what the event is, for anybody with the address and
    # without an account. Only while the page is published; never anything about people.
    public = models.BooleanField(
        "Cualquiera con el enlace puede ver la información del evento, sin entrar a Emmaus", default=False, db_default=False
    )
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
    """Somebody wants to come to one event, or came. It gives no role and no access, and it is
    never an ``EventParticipant``. Cancelling keeps the row; signing up again reactivates it.

    The row is either a person of the church (``person``) or a visitor the team wrote down at the
    door (``guest_first_name`` and the other ``guest_`` fields): exactly one of the two, by a
    database constraint. A visitor never needs an account. ``pendiente`` holds the place, but the
    registration is not confirmed and cannot be marked as attended; ``pending_reason`` says what is
    missing: the team has to verify the person's age, or a mother, father or guardian has to
    authorize a minor. ``agenda.services.standing`` is the one function that decides it (decision
    0015), and nothing else takes a row out of ``pendiente``.

    ``annulled_reason`` set: the team wrote this row at the door by mistake and annulled it. It is
    ``cancelada``, in no list and no count, and stays for the audit.

    The waiting list (decision 0011): ``en_espera`` is somebody who signed up when the event was
    full; the row holds no place and its order is ``waiting_since``. ``ofrecida`` is a freed place
    offered to the first of the list until ``offer_expires_at``: it holds the place, and only the
    person turns it into a registration, by accepting (``agenda.services.accept_offer``, which asks
    ``standing`` like any sign-up). Neither is ever marked as attended. ``waiting_outcome`` says how
    a row left the list without a place, as a fixed word."""

    class Status(models.TextChoices):
        ACTIVE = "inscrita", "Inscrita"
        PENDING = "pendiente", "Pendiente"
        CANCELLED = "cancelada", "Cancelada"
        WAITING = "en_espera", "En lista de espera"
        OFFERED = "ofrecida", "Te ofrecimos un lugar"

    class PendingReason(models.TextChoices):
        AGE = "age", "Edad por confirmar"
        CONSENT = "consent", "Pendiente de autorización"

    class AnnulReason(models.TextChoices):
        MISTAKE = "error", "Se registró por error"
        DUPLICATE = "duplicado", "Registro duplicado"
        LEFT = "no_se_quedo", "La persona no se quedó"

    class WaitingOutcome(models.TextChoices):
        EXPIRED = "expiro", "No respondió a tiempo"
        DECLINED = "declino", "No aceptó el lugar"
        LEFT = "salio", "Salió de la lista de espera"
        REMOVED = "retirada", "La administración la quitó de la lista de espera"

    # Who is in: confirmed, or waiting for the team to verify an age or for a guardian's authorization.
    HOLDING = (Status.ACTIVE, Status.PENDING)
    # The statuses that use a place of the event's capacity: who is in, and a place that was offered.
    TAKING = (Status.ACTIVE, Status.PENDING, Status.OFFERED)
    # On the waiting list: in line, or with an offer to answer.
    LISTED = (Status.WAITING, Status.OFFERED)

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="registrations")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, null=True, blank=True, related_name="event_registrations")
    guest_first_name = models.CharField("Nombre", max_length=120, blank=True, default="", db_default="")
    guest_last_name = models.CharField("Apellidos", max_length=120, blank=True, default="", db_default="")
    guest_phone = models.CharField("Teléfono", max_length=40, blank=True, default="", db_default="")
    # What the team said at the door: "es menor de edad". For a visitor it is all the church knows;
    # for a person of the church it counts only while the date of birth is unknown.
    guest_is_minor = models.BooleanField("Es menor de edad", default=False, db_default=False)
    # The team saw this visitor at the door and said "es mayor de edad". Without it, in an event that
    # asks, a visitor nobody marked as a minor waits for that look (decision 0015: no age is assumed).
    guest_age_checked = models.BooleanField("Es mayor de edad, visto por el equipo", default=False, db_default=False)
    # Who wrote this row for somebody else (a walk-up). None: the person signed up by themselves.
    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    # Why the row waits; empty when it does not (a row from before this column waits for a guardian).
    pending_reason = models.CharField(max_length=8, choices=PendingReason.choices, blank=True, default="", db_default="")
    annulled_reason = models.CharField(max_length=12, choices=AnnulReason.choices, blank=True, default="", db_default="")
    # The waiting list: since when the row waits (its order), until when an offered place is held,
    # and how the row left the list when it left without a place.
    waiting_since = models.DateTimeField(null=True, blank=True)
    offer_expires_at = models.DateTimeField(null=True, blank=True)
    waiting_outcome = models.CharField(max_length=12, choices=WaitingOutcome.choices, blank=True, default="", db_default="")
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # None: nobody has said yet whether this person came.
    attended = models.BooleanField(null=True, blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "inscripción a un evento"
        verbose_name_plural = "inscripciones a eventos"
        constraints = [
            models.UniqueConstraint(fields=["event", "person"], name="agenda_one_registration_per_person"),
            models.CheckConstraint(
                condition=(
                    models.Q(person__isnull=False, guest_first_name="")
                    | (models.Q(person__isnull=True) & ~models.Q(guest_first_name=""))
                ),
                name="agenda_reg_person_or_guest",
            ),
            # An offer always has its deadline, and nothing else carries one: a row cannot stop
            # being an offer and keep holding a place by a forgotten date.
            models.CheckConstraint(
                condition=(
                    models.Q(status="ofrecida", offer_expires_at__isnull=False)
                    | (~models.Q(status="ofrecida") & models.Q(offer_expires_at__isnull=True))
                ),
                name="agenda_reg_offer_has_deadline",
            ),
            # The waiting list is for people of the church: a visitor is written down at the door.
            models.CheckConstraint(
                condition=~models.Q(status__in=["en_espera", "ofrecida"]) | models.Q(person__isnull=False),
                name="agenda_reg_list_is_person",
            ),
        ]
        indexes = [models.Index(fields=["event", "status"], name="agenda_reg_event_status_idx")]

    def __str__(self) -> str:
        return f"{self.display_name} en {self.event}"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def is_pending(self) -> bool:
        return self.status == self.Status.PENDING

    @property
    def is_waiting(self) -> bool:
        return self.status == self.Status.WAITING

    @property
    def is_offered(self) -> bool:
        return self.status == self.Status.OFFERED

    @property
    def is_guest(self) -> bool:
        return self.person_id is None

    @property
    def display_name(self) -> str:
        """The name to show, whoever this is: the person's, or the one written at the door."""
        if self.person_id is None:
            return f"{self.guest_first_name} {self.guest_last_name}".strip()
        return self.person.full_name

    @property
    def contact_phone(self) -> str:
        return self.guest_phone if self.person_id is None else self.person.phone

    @property
    def contact_email(self) -> str:
        return "" if self.person_id is None else self.person.email

    @property
    def waits_for(self) -> str:
        """``age``, ``consent`` or empty: what a pending row is missing."""
        if self.status != self.Status.PENDING:
            return ""
        return self.pending_reason or self.PendingReason.CONSENT

    @property
    def is_walkup(self) -> bool:
        """The team wrote this row for somebody who arrived."""
        return self.registered_by_id is not None

    @property
    def recorded_consent(self):
        """What the team recorded and nobody withdrew, whether or not it confirms, or None."""
        consent = getattr(self, "consent", None)
        return consent if consent is not None and consent.revoked_at is None else None

    @property
    def valid_consent(self):
        """The guardian's authorization in force, or None: not withdrawn, for taking part, by a
        method that counts and under the current version of the words. Read it only for the
        administration."""
        consent = self.recorded_consent
        return consent if consent is not None and consent.confirms else None

    @property
    def consent_withdrawn(self) -> bool:
        consent = getattr(self, "consent", None)
        return consent is not None and consent.revoked_at is not None


class GuardianConsent(models.Model):
    """A mother, father or guardian authorized one minor to come to one event: the scope is the
    registration, never the person in general. It records who said yes and how the team knows it;
    there is no free text. Only the church's administration reads it."""

    class Relationship(models.TextChoices):
        MOTHER = "madre", "Madre"
        FATHER = "padre", "Padre"
        GUARDIAN = "tutor", "Tutor legal"

    class Method(models.TextChoices):
        PAPER = "papel", "Formato firmado en papel"
        IN_PERSON = "en_persona", "En persona, con identidad y parentesco verificados por el equipo"
        # The value is the one rows already have. A call or a message is kept as a note of contact
        # and never confirms a registration (decision 0015, point 5).
        VERIFIED = "verificado", "Por llamada o mensaje"

    # The evidence that counts.
    CONFIRMING_METHODS = (Method.PAPER, Method.IN_PERSON)

    registration = models.OneToOneField(EventRegistration, on_delete=models.CASCADE, related_name="consent")
    guardian_name = models.CharField("Nombre de su madre, padre o tutor", max_length=160)
    relationship = models.CharField("¿Quién autoriza?", max_length=12, choices=Relationship.choices)
    guardian_phone = models.CharField("Teléfono de su madre, padre o tutor", max_length=40, blank=True)
    method = models.CharField("¿Cómo autorizó?", max_length=12, choices=Method.choices)
    # One choice per purpose (decision 0015, point 4). The words on the screen are provisional, NOT
    # approved legal text: the church's leadership approves them (issue #75), and ``policy_version``
    # says under which words each authorization was recorded. Nothing medical lives here.
    # ``db_default=True``: a row from before these columns was an authorization to take part.
    allows_participation = models.BooleanField("Autoriza que participe en este evento", default=False, db_default=True)
    allows_transport = models.BooleanField(
        "Autoriza el transporte organizado por la iglesia", default=False, db_default=False
    )
    allows_photos = models.BooleanField("Autoriza que aparezca en fotografías del evento", default=False, db_default=False)
    policy_version = models.CharField(
        max_length=24, default=policy.CONSENT_POLICY_VERSION, db_default=policy.CONSENT_POLICY_VERSION, editable=False
    )
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "autorización de un tutor"
        verbose_name_plural = "autorizaciones de tutores"

    def __str__(self) -> str:
        return f"Autorización de {self.registration}"

    @property
    def confirms(self) -> bool:
        """Enough to confirm the registration it belongs to: in force, for taking part, by a signed
        form or in person, and under the current version of the words."""
        return (
            self.revoked_at is None
            and self.allows_participation
            and self.method in self.CONFIRMING_METHODS
            and self.policy_version == policy.consent_policy_version()
        )


class EventFaq(models.Model):
    """One frequent question about an event and its answer, as the people of the church read them."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="faqs")
    question = models.CharField("Pregunta", max_length=200)
    answer = models.TextField("Respuesta")
    position = models.PositiveSmallIntegerField("Orden", default=1)

    class Meta:
        verbose_name = "pregunta frecuente de un evento"
        verbose_name_plural = "preguntas frecuentes de eventos"
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return self.question


class EventScheduleItem(models.Model):
    """One moment of an event's programme: the day, the hour, what happens and where."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="schedule_items")
    day = models.DateField("Día")
    starts_at = models.TimeField("Empieza")
    ends_at = models.TimeField("Termina", null=True, blank=True)
    title = models.CharField("Qué es", max_length=120)
    place = models.CharField("Dónde", max_length=120, blank=True)

    class Meta:
        verbose_name = "momento del programa de un evento"
        verbose_name_plural = "momentos del programa de eventos"
        ordering = ["day", "starts_at", "id"]

    def __str__(self) -> str:
        return f"{self.title} ({self.day})"
