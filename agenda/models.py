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
    # Issue #96, second part: whoever reads the public page may leave an e-mail address to be told that there
    # is a new public notice of THIS event (``agenda.subscriptions``). Off unless the administration turns it on.
    notice_mail = models.BooleanField("Avisos por correo para quien no tiene cuenta", default=False, db_default=False)
    # Issue #93 (wave 12): an adult without an account signs up from the public page (``agenda.visitors``). Off
    # unless the administration turns it on, and never on while the event protects minors (``services.save_page``).
    visitor_signup = models.BooleanField("Inscripción para quien no tiene cuenta", default=False, db_default=False)
    visitor_phone = models.BooleanField("Pedir también un teléfono a quien se inscribe sin cuenta", default=False, db_default=False)
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
    # A visitor who signed up by themselves from the public page (``agenda.visitors``, issue #93): the address they
    # confirmed, the random key their private link is signed over, and when they confirmed. ``visitor_key`` is
    # empty (NULL) on every other row. The age of such a row is self-declared: ``guest_age_checked`` stays False.
    guest_email = models.EmailField("Correo", max_length=254, blank=True, default="", db_default="")
    visitor_key = models.CharField(max_length=64, null=True, blank=True, unique=True, editable=False)
    visitor_confirmed_at = models.DateTimeField(null=True, blank=True)
    # Such a visitor's answer to the optional box «La iglesia puede guardar mi nombre y mi teléfono para invitarme a
    # otras actividades» (unticked by default) and when they gave it (when they sent the form; NULL: never asked).
    # Only with a yes may the administration add them to the church's people (``services.promote_guest``).
    visitor_contact_consent = models.BooleanField(default=False, db_default=False)
    visitor_contact_consent_at = models.DateTimeField(null=True, blank=True)
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
            # The waiting list is for who can answer an offer: a person of the church, or a visitor who signed up
            # with a confirmed address (their private link). A visitor the team wrote at the door never waits.
            models.CheckConstraint(
                condition=(
                    ~models.Q(status__in=["en_espera", "ofrecida"]) | models.Q(person__isnull=False)
                    | models.Q(visitor_key__isnull=False)
                ),
                name="agenda_reg_list_person_or_visitor",
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
        return self.guest_email if self.person_id is None else (self.person.email or self.guest_email)

    @property
    def is_visitor_signup(self) -> bool:
        """Somebody without an account signed up from the public page and confirmed their address."""
        return self.visitor_key is not None

    @property
    def may_join_people(self) -> bool:
        """«Agregar a personas de la iglesia» applies to this row: a visitor the team wrote at the door, or one who
        signed up alone and said yes to the church keeping their name and phone."""
        return self.person_id is None and (self.visitor_key is None or self.visitor_contact_consent)

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


class EventNotice(models.Model):
    """One public notice of an event (issue #96): news for anybody with the event's public address, without an
    account ("el camión sale a las 7"). Writing one publishes it; ``retired_at`` takes it off every page for good.
    It is never a ``notices.Announcement``: a notice for the members cannot become public by changing a column.
    Nothing about people belongs in it, and the page never shows who wrote it."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="public_notices")
    title = models.CharField("Título", max_length=120)
    body = models.TextField("Aviso")
    published_at = models.DateTimeField(default=timezone.now)
    # When the title or the text last changed after it was published: readers are told that it changed.
    edited_at = models.DateTimeField(null=True, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    # Decided once, when it was published: the event took e-mail subscriptions and e-mail could go out, and the
    # screen said so to who published. A notice written while it could not is never mailed later.
    mail_subscribers = models.BooleanField(default=False, db_default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "aviso público de un evento"
        verbose_name_plural = "avisos públicos de eventos"
        ordering = ["-published_at", "-id"]
        indexes = [models.Index(fields=["event", "-published_at"], name="agenda_notice_event_recent")]

    def __str__(self) -> str:
        return f"Aviso público {self.pk} de {self.event}"

    @property
    def retired(self) -> bool:
        return self.retired_at is not None


class MailOutcome(models.TextChoices):
    PENDING = "pending", "Por enviar"
    SENT = "sent", "Enviado"
    FAILED = "failed", "No se pudo enviar"


class EventSubscription(models.Model):
    """An e-mail address that asked to be told about new public notices of ONE event (issue #96, second part;
    ``agenda.subscriptions``). It is an address and nothing else: never a ``Person``, never linked to one or to a
    child, never an account, never a visitor, and it opens nothing. Nobody is shown it: no screen, no admin page,
    no audit row, no log. ``email_hash`` (keyed) is what finds a row by its address. ``key`` is what the signed
    links of its e-mails carry, so no link holds the address. Only a row with ``confirmed_at`` is ever told about
    a notice. The row is deleted when the person unsubscribes, 48 hours after an unconfirmed request, and some
    days after the event ends."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="notice_subscriptions")
    email = models.EmailField(max_length=254)
    email_hash = models.CharField(max_length=64)
    key = models.CharField(max_length=64, unique=True)
    # The keyed hash of the address as the LIMITS see it (no capitals, no ``+tag``): one row per inbox and event.
    limit_hash = models.CharField(max_length=64, default="", db_default="")
    created_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # The one e-mail that asks to confirm: sent by the scheduled pass, with attempts and waits like a notice's.
    confirm_outcome = models.CharField(max_length=10, choices=MailOutcome.choices, default=MailOutcome.PENDING, db_default=MailOutcome.PENDING)
    confirm_attempts = models.PositiveSmallIntegerField(default=0, db_default=0)
    confirm_last_attempt_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "suscripción por correo a los avisos de un evento"
        verbose_name_plural = "suscripciones por correo a los avisos de eventos"
        constraints = [models.UniqueConstraint(fields=["event", "email_hash"], name="agenda_subscription_once_per_event")]
        indexes = [models.Index(fields=["email_hash", "created_at"], name="agenda_subscription_hash_idx")]

    def __str__(self) -> str:  # never the address
        return f"Suscripción {self.pk} a los avisos de {self.event}"


class SubscriptionSuppression(models.Model):
    """How many e-mails that ask to confirm were requested for one address on one event's page and never
    answered (``agenda.subscriptions``). It outlives the unconfirmed request, which is deleted after 48 hours,
    so that an address that never confirms is not asked again and again. It holds a keyed hash and no address:
    the hash is of the address as the LIMITS see it (no capitals, no ``+tag``), so spellings of one inbox share
    a row. It hangs from the event's page, not from the event, and is deleted with it or when the event's
    subscriptions end."""

    page = models.ForeignKey(EventPage, on_delete=models.CASCADE, related_name="subscription_limits")
    address_hash = models.CharField(max_length=64)
    unanswered = models.PositiveSmallIntegerField(default=0, db_default=0)
    last_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "límite de confirmaciones por correo"
        verbose_name_plural = "límites de confirmaciones por correo"
        constraints = [models.UniqueConstraint(fields=["page", "address_hash"], name="agenda_subscription_limit_once")]
        indexes = [models.Index(fields=["address_hash", "last_at"], name="agenda_sub_limit_hash_idx")]

    def __str__(self) -> str:
        return f"Límite de confirmaciones {self.pk}"


class EventNoticeEmail(models.Model):
    """One e-mail about one public notice to one subscription. The row is written before the e-mail is handed
    over and the constraint lets one writer win: nobody is mailed twice for a notice, whoever sends. It keeps no
    address and no text. ``sent``: the mail server accepted it, never that it arrived or was read."""

    notice = models.ForeignKey(EventNotice, on_delete=models.CASCADE, related_name="emails")
    subscription = models.ForeignKey(EventSubscription, on_delete=models.CASCADE, related_name="emails")
    outcome = models.CharField(max_length=10, choices=MailOutcome.choices, default=MailOutcome.PENDING, db_default=MailOutcome.PENDING)
    created_at = models.DateTimeField(default=timezone.now)
    attempts = models.PositiveSmallIntegerField(default=0, db_default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "correo de un aviso público"
        verbose_name_plural = "correos de avisos públicos"
        constraints = [models.UniqueConstraint(fields=["notice", "subscription"], name="agenda_notice_email_once")]


class EventSeries(models.Model):
    """Something of the church that repeats by the week ("Servicio dominical", "Reunión de jóvenes"): a template.

    Each date is an ordinary simple ``events.Event`` with its own page, capacity and list, written ahead for a
    rolling window by ``agenda.series.generate`` and tied to the series by a ``SeriesOccurrence``. Nothing here
    is a recurrence worked out on the fly, and nothing here is about a person."""

    WEEKDAYS = [(0, "lunes"), (1, "martes"), (2, "miércoles"), (3, "jueves"), (4, "viernes"), (5, "sábado"), (6, "domingo")]

    church = models.ForeignKey("core.Church", on_delete=models.CASCADE, related_name="event_series")
    name = models.CharField("Nombre", max_length=160)
    location = models.CharField("Lugar", max_length=160, blank=True)
    time_zone = models.CharField("Zona horaria", max_length=64, default="America/Mexico_City")
    # The days of the week it happens on, Monday 0 to Sunday 6, as "6" or "2,6".
    weekdays = models.CharField("Días de la semana", max_length=13)
    # 1: every week. N: every N weeks, counted from the week of ``starts_on``.
    interval_weeks = models.PositiveSmallIntegerField("Cada cuántas semanas", default=1)
    starts_at = models.TimeField("Hora")
    duration_minutes = models.PositiveSmallIntegerField("Cuánto dura, en minutos", default=90)
    starts_on = models.DateField("Desde qué día")
    ends_on = models.DateField("Hasta qué día", null=True, blank=True)
    description = models.TextField("De qué se trata", blank=True)
    registration = models.CharField(
        "Inscripción", max_length=12, choices=EventPage.Registration.choices, default=EventPage.Registration.CLOSED
    )
    capacity = models.PositiveIntegerField("Lugares de cada fecha", null=True, blank=True)
    published = models.BooleanField("Publicar cada fecha para la iglesia", default=False)
    public = models.BooleanField(
        "Cualquiera con el enlace puede ver cada fecha, sin entrar a Emmaus", default=False
    )
    # False: the series ended. No new dates are written; the ones that exist stay as they are.
    active = models.BooleanField(default=True)
    # The last day ``generate`` looked at: a pass of the scheduler skips a series that is written this far.
    generated_through = models.DateField(null=True, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "serie de eventos"
        verbose_name_plural = "series de eventos"
        ordering = ["name", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(interval_weeks__gte=1), name="agenda_series_interval_positive"),
        ]

    def __str__(self) -> str:
        return f"Serie {self.pk}"

    @property
    def weekday_numbers(self) -> list[int]:
        return sorted({int(part) for part in self.weekdays.split(",") if part.strip().isdigit() and int(part) < 7})

    @property
    def takes_registration(self) -> bool:
        return self.registration == EventPage.Registration.OPEN


class SeriesOccurrence(models.Model):
    """One date of a series: the day the rule gave and the ordinary event written for it.

    The row is the series' memory of that day, so two passes never write it twice (the constraint) and a day
    the administration called off ("Este día no hay") is not written again. ``day`` never moves, even when
    somebody moves the event itself to another date.

    * ``activa``: the event follows the series, unless ``edited_at`` says somebody changed it by hand.
    * ``cancelada``: "Este día no hay". The event stays, with everything it has; its page is not published.
    * ``fuera``: the series changed and no longer falls on this day; nobody had touched or signed up for it,
      so its page was unpublished. If the series falls on the day again, it comes back."""

    class Status(models.TextChoices):
        ACTIVE = "activa", "Sigue la serie"
        CANCELLED = "cancelada", "Este día no hay"
        OFF_RULE = "fuera", "La serie ya no cae en este día"

    series = models.ForeignKey(EventSeries, on_delete=models.CASCADE, related_name="occurrences")
    day = models.DateField()
    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name="series_occurrence")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    # When somebody changed this date's event or page by hand: from then on, changes to the series skip it.
    edited_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # Whether the page was published when the day was called off: a fact kept for the record. "Sí hay" does not
    # put the page back as it was; it follows the series as it is then (``agenda.series.restore``).
    published_before = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "fecha de una serie"
        verbose_name_plural = "fechas de series"
        ordering = ["day", "id"]
        constraints = [
            models.UniqueConstraint(fields=["series", "day"], name="agenda_one_occurrence_per_series_day"),
        ]

    def __str__(self) -> str:
        return f"Fecha {self.day} de la serie {self.series_id}"

    @property
    def cancelled(self) -> bool:
        return self.status == self.Status.CANCELLED


class VisitorRequest(models.Model):
    """Somebody without an account filled the sign-up form of ONE event's public page and has not proved the
    address yet (issue #93; ``agenda.visitors``). It holds no place. Only pressing the button behind the signed
    link of its one e-mail turns it into an ``EventRegistration``; then ``registration`` says which, so the same
    link shows the same result. Never a ``Person``, never an account, shown to nobody: the administration reads
    how many wait, as a number. Deleted 48 hours after its e-mail was sent, confirmed or not."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="visitor_requests")
    first_name = models.CharField(max_length=120)
    last_name = models.CharField(max_length=120, blank=True, default="")
    phone = models.CharField(max_length=40, blank=True, default="")
    email = models.EmailField(max_length=254)
    email_hash = models.CharField(max_length=64)
    limit_hash = models.CharField(max_length=64)
    key = models.CharField(max_length=64, unique=True)
    # {question id: the answer as text}, as the form validated it; written as rows when the request is confirmed.
    answers = models.JSONField(default=dict, blank=True)
    # The optional box of the form, carried to the registration when the request is confirmed (its time: ``created_at``).
    contact_consent = models.BooleanField(default=False, db_default=False)
    created_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    registration = models.ForeignKey(EventRegistration, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    confirm_outcome = models.CharField(max_length=10, choices=MailOutcome.choices, default=MailOutcome.PENDING, db_default=MailOutcome.PENDING)
    confirm_attempts = models.PositiveSmallIntegerField(default=0, db_default=0)
    confirm_last_attempt_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "solicitud de inscripción sin cuenta"
        verbose_name_plural = "solicitudes de inscripción sin cuenta"
        constraints = [models.UniqueConstraint(fields=["event", "limit_hash"], name="agenda_visitor_request_once_per_inbox")]
        indexes = [models.Index(fields=["event", "confirmed_at"], name="agenda_visitor_req_event_idx")]

    def __str__(self) -> str:  # never the name or the address
        return f"Solicitud de inscripción {self.pk} a {self.event_id}"


class EventQuestion(models.Model):
    """One question the administration asks at sign-up (issue #93): up to five per event, one line of text, of
    three kinds. Not a camp questionnaire and not a ``formularios`` form: the answers are logistics for the
    event's administration ("¿Qué talla de playera?"), never about health, family or minors. A free-text question
    is not asked while the event protects minors (``agenda.questions.asked``)."""

    class Kind(models.TextChoices):
        TEXT = "texto", "Texto corto"
        CHOICE = "opcion", "Una opción de una lista"
        YES_NO = "si_no", "Sí o no"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="signup_questions")
    text = models.CharField("Pregunta", max_length=120)
    kind = models.CharField("Tipo de respuesta", max_length=8, choices=Kind.choices, default=Kind.YES_NO)
    # One option per line, for ``opcion``.
    options = models.TextField("Opciones", blank=True, default="")
    required = models.BooleanField("Hay que responderla para inscribirse", default=False)
    position = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "pregunta de la inscripción a un evento"
        verbose_name_plural = "preguntas de la inscripción a eventos"
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return f"Pregunta {self.pk} de la inscripción a {self.event_id}"

    @property
    def option_list(self) -> list[str]:
        return [line for line in self.options.splitlines() if line]


class RegistrationAnswer(models.Model):
    """What one registration answered to one question of its event's sign-up: the words, as they were then. Read
    only by the event's administration on "Inscritos"; exported nowhere; deleted with the question, with the
    registration, when the registration is cancelled, and some days after the event ends."""

    registration = models.ForeignKey(EventRegistration, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(EventQuestion, on_delete=models.CASCADE, related_name="answers")
    value = models.CharField(max_length=120)

    class Meta:
        verbose_name = "respuesta de una inscripción"
        verbose_name_plural = "respuestas de inscripciones"
        constraints = [models.UniqueConstraint(fields=["registration", "question"], name="agenda_one_answer_per_question")]

    def __str__(self) -> str:
        return f"Respuesta {self.pk}"


class EventHeadcount(models.Model):
    """"¿Cuántos vinimos?": how many people were at one gathering, as somebody counted them at the door or from the
    back (issue #93). Numbers only: no name, no list, nothing per person. It is never worked out from the named
    attendance of the registrations and never changes it. ``adults`` and ``children`` are optional."""

    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name="headcount")
    total = models.PositiveIntegerField("Total")
    adults = models.PositiveIntegerField("Personas adultas", null=True, blank=True)
    children = models.PositiveIntegerField("Niñas y niños", null=True, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "conteo de asistencia de un evento"
        verbose_name_plural = "conteos de asistencia de eventos"
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(adults__isnull=True) | models.Q(adults__lte=models.F("total")))
                    & (models.Q(children__isnull=True) | models.Q(children__lte=models.F("total")))
                ),
                name="agenda_headcount_parts_within_total",
            ),
        ]

    def __str__(self) -> str:
        return f"Conteo de {self.event_id}"
