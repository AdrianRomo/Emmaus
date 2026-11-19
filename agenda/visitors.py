"""Signing up for ONE event without an account (issue #93, wave 12).

Off unless the administration turns "Inscripción para quien no tiene cuenta" on for an event
(``EventPage.visitor_signup``), and offered only while ``offered_page`` answers: the event's public page
answers, it takes sign-ups, it does NOT protect minors (``services.visitor_block``: an inbox proves no age, so
that rule is never a preference) and this server can send (``subscriptions.not_sending``).

Adults only, one person per form. The form asks a name, an address, a phone only when the event asks, the
event's own questions and the statement "Soy mayor de edad". That statement is all the church has: the row it
leads to is a guest registration whose age nobody checked (``guest_age_checked`` False), exactly like a visitor
the team writes down without saying anything about the age.

Proof of contact before a place is held. ``request`` stores a ``VisitorRequest`` and answers nothing about the
address: the caller shows the same page whatever happened to it (new, already asked, already signed up, over a
limit), so the form is no oracle, and no e-mail leaves inside the request. The one other answer is about the
EVENT and is the same for every address: it takes no more requests now (``taking_requests``), so nothing is
stored and the page says so. The scheduled pass sends ONE e-mail with a signed link
that lasts ``CONFIRM_HOURS``; opening it shows a button and only pressing it (``confirm``) signs the visitor up,
through ``services.register_visitor``: the capacity, the waiting list and ``standing`` are the ones of every
other sign-up. Full with a waiting list: the visitor waits in line and the page says so. Full without one, or
the option switched off meanwhile: nothing is created and the page says why; the link can be tried again
while it lasts.

LOCK ORDER, extending the one of ``agenda.services``: the request row, then the event's page, then registration
rows. Nothing locks a request after a page.

The private link (``manage_token``): signed over the registration's random ``visitor_key``, without a deadline.
It shows where the registration stands and lets the visitor cancel until the event starts, accept a place the
waiting list offered, or leave the list. It is shown once, on the page that confirms; it is in no e-mail
except the one that says a place was freed. Whoever holds it can do those three things and read the event's
name: it shows no other person and no count.

Both links look at the church and the event first (``closed``): with the church inactive, the ``community``
module off, the event no longer active, public and published, or the option off (``menores`` included: the
rule switches it off), the confirmation link is the dead link and signs nobody up, and the private link shows
nothing of the event and only lets the visitor cancel or leave the list: that only takes their own place.

The optional box «La iglesia puede guardar mi nombre y mi teléfono para invitarme a otras actividades» travels
from the request to the registration (``contact_consent``). Without it the administration cannot add the visitor
to the church's people (``services.promote_guest``), so at the retention below nothing of them stays.

Limits, so the form cannot fill somebody's inbox. They are SHARED with the e-mail subscriptions of the same
event (``SubscriptionSuppression``, a keyed hash, no address), counted on the address as the limits see it
(``subscriptions.limit_key``):

* HARD, at the form: one request per inbox and event; ``subscriptions.UNANSWERED_PER_EVENT`` (2) unanswered
  confirmation e-mails per address and event, of either kind; ``UNANSWERED_PER_CHURCH`` (4) per address across a
  church's events in 30 days; ``ADDRESS_REQUESTS_PER_DAY`` (3) events per address per day, anywhere. Silent: the
  page is the same.
* HARD, per EVENT, whatever the session, the address or the network: ``PENDING_PER_EVENT`` (200) unconfirmed
  requests and ``NEW_PER_EVENT_PER_HOUR`` (60) new requests in the last hour. Over either, nothing is stored and
  the page says that the event takes no more requests for now (the same page for every address). Two requests
  at the same instant may each read the count before the other's row: a bound, not an exact number.
* PACING, when sending: ``CONFIRMATIONS_PER_PASS`` (50), ``DOMAIN_CONFIRMATIONS_PER_HOUR`` (60) per mail domain
  and event, ``CONFIRMATIONS_PER_CHURCH_PER_DAY`` (300) per church. What does not fit waits; nothing is dropped.
  The pass takes turns: one e-mail per church in each round, and inside a church one per event, oldest first; a
  church at its daily cap takes no turn. So a backlog in one church (or one event) never delays another.

Endings (``delete_due``; it only deletes and reads no flag and no setting): a request ``CONFIRM_HOURS`` after its
e-mail was sent, confirmed or not (one never sent, after ``QUEUE_DAYS``); and, ``Event.retention_days`` after the
event's last day, every answer given at its sign-up, the registrations of visitors who signed up alone, and the
address and key of the ones the administration added to the church's people.

What nobody sees: the requests. The administration reads how many wait, as a number. A confirmed visitor is a
row of "Inscritos" like any other, marked "Se inscribió sin cuenta, correo confirmado". The church's log gets
one row per sign-up with ids and no name: the name is something the visitor typed.
"""

from __future__ import annotations

import logging
import secrets
from collections import deque
from collections.abc import Iterator
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Min, Q
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from events.services.team import email_is_configured
from people import flags

from . import public, services, subscription_mail, subscriptions
from .models import (
    EventPage,
    EventRegistration,
    MailOutcome,
    RegistrationAnswer,
    SubscriptionSuppression,
    VisitorRequest,
)

logger = logging.getLogger(__name__)

CONFIRM_HOURS = 48
QUEUE_DAYS = 2
PENDING_PER_EVENT = 200
NEW_PER_EVENT_PER_HOUR = 60
# What ``request`` answers when the event takes no more requests now (the only answer it gives).
NOT_NOW = "lleno"
CONFIRMATIONS_PER_PASS = 50
DOMAIN_CONFIRMATIONS_PER_HOUR = 60
CONFIRMATIONS_PER_CHURCH_PER_DAY = 300
CONFIRM_SALT = "agenda.visitors.confirm"
MANAGE_SALT = "agenda.visitors.manage"
HELD = (*EventRegistration.HOLDING, *EventRegistration.LISTED)


# ---- whether it is offered ------------------------------------------------------------------------
def offered_page(slug: str) -> EventPage | None:
    """The public page of the event when somebody without an account may sign up from it now, else None."""
    page = public.public_page(slug)
    if page is None or services.visitor_block(page.event, page) or subscriptions.not_sending():
        return None
    return page


def state_for_public(event: Event, page: EventPage) -> str:
    """What the event's public page says about signing up without an account: ``si`` (the link to the form),
    ``menores`` (the event protects minors: at the church or with an account) or empty (nothing about it).
    Settings and the page row only: no query, no table of people."""
    block = services.visitor_block(event, page)
    if block == "menores" and page.registration_open:
        return "menores"
    return "" if block or subscriptions.not_sending() else "si"


# ---- asking ---------------------------------------------------------------------------------------
def _lapsed(now) -> Q:
    """A request that is over: its e-mail was sent ``CONFIRM_HOURS`` ago, or was never sent in ``QUEUE_DAYS``."""
    sent = Q(confirm_attempts__gte=1, confirm_last_attempt_at__lt=now - timedelta(hours=CONFIRM_HOURS))
    never = Q(confirm_attempts=0, created_at__lt=now - timedelta(days=QUEUE_DAYS))
    return sent | never


def same_inbox(event: Event, address: str) -> bool:
    """A visitor already holds a place or waits in line for ``event`` with this inbox, however it was spelled."""
    wanted = subscriptions.limit_key(address)
    held = EventRegistration.objects.filter(event=event, visitor_key__isnull=False, status__in=HELD).exclude(guest_email="")
    return any(subscriptions.limit_key(other) == wanted for other in held.values_list("guest_email", flat=True))


def taking_requests(event: Event, now=None) -> bool:
    """Whether ``event`` takes one more request now: fewer than ``PENDING_PER_EVENT`` wait for their confirmation
    and fewer than ``NEW_PER_EVENT_PER_HOUR`` were stored in the last hour. About the event only: it reads no
    address, no session and no network, so it answers the same to whoever asks."""
    now = now or timezone.now()
    rows = VisitorRequest.objects.filter(event=event)
    if rows.filter(created_at__gte=now - timedelta(hours=1)).count() >= NEW_PER_EVENT_PER_HOUR:
        return False
    return rows.filter(confirmed_at__isnull=True).exclude(_lapsed(now)).count() < PENDING_PER_EVENT


def request(event: Event, *, first_name: str, last_name: str = "", phone: str = "", email: str, answers: dict | None = None,
            contact_consent: bool = False) -> str | None:
    """Somebody sent the form. ``NOT_NOW`` when the event takes no more requests now (``taking_requests``): nothing
    is stored and nothing about the address is read. Otherwise None, whatever happened to the address (the module
    docstring says why). What the caller shows is the same for every address; the work in the database is not (a
    new address is written, a known one is not), and nothing here waits to hide that."""
    now = timezone.now()
    if not taking_requests(event, now):
        return NOT_NOW
    exact, digest = subscriptions.address_hash(email), subscriptions.limit_hash(email)
    page = EventPage.objects.select_related("event").filter(event=event).first()
    rows = VisitorRequest.objects.filter(event=event, limit_hash=digest)
    rows.filter(_lapsed(now)).delete()
    # A request that was confirmed and whose registration no longer stands (the visitor cancelled) is over too:
    # the same inbox may ask again.
    rows.filter(confirmed_at__isnull=False).exclude(registration__status__in=HELD).delete()
    known = rows.exists()
    registered = same_inbox(event, email)
    here, church, today = subscriptions._unanswered(page, digest, now)
    refused = (here >= subscriptions.UNANSWERED_PER_EVENT or church >= subscriptions.UNANSWERED_PER_CHURCH
               or today >= subscriptions.ADDRESS_REQUESTS_PER_DAY)
    if known or registered or refused or page is None:
        rows.update(email_hash=F("email_hash"))
        return None
    try:
        with transaction.atomic():
            VisitorRequest.objects.create(
                event=event, first_name=" ".join(first_name.split())[:120], last_name=" ".join(last_name.split())[:120],
                phone=phone.strip()[:40], email=email.strip(), email_hash=exact, limit_hash=digest,
                key=secrets.token_urlsafe(32), answers=answers or {}, contact_consent=bool(contact_consent), created_at=now,
            )
            mine = SubscriptionSuppression.objects.filter(page=page, address_hash=digest)
            if not mine.update(unanswered=F("unanswered") + 1, last_at=now):
                SubscriptionSuppression.objects.create(page=page, address_hash=digest, unanswered=1, last_at=now)
    except IntegrityError:  # the same inbox, twice at once: one request, one count
        return None
    return None


def pending_count(event: Event) -> int:
    """How many requests wait for their confirmation: all the administration ever reads about them."""
    return VisitorRequest.objects.filter(event=event, confirmed_at__isnull=True).exclude(_lapsed(timezone.now())).count()


# ---- the links ------------------------------------------------------------------------------------
def confirm_token(row: VisitorRequest) -> str:
    return signing.dumps(row.key, salt=CONFIRM_SALT)


def confirm_path(token: str) -> str:
    return reverse("evento:visitor_confirm", args=[token])


def manage_token(registration: EventRegistration) -> str:
    return signing.dumps(registration.visitor_key, salt=MANAGE_SALT)


def manage_path(registration: EventRegistration) -> str:
    return reverse("evento:visitor_manage", args=[manage_token(registration)])


def _key(token: str, salt: str, max_age: int | None = None) -> str | None:
    try:
        key = signing.loads(token, salt=salt, max_age=max_age)
    except signing.BadSignature:  # tampered, of another kind, or expired
        return None
    return key if isinstance(key, str) and key else None


def for_confirmation(token: str) -> VisitorRequest | None:
    """The request a confirmation link is for, while the link is whole, not older than ``CONFIRM_HOURS`` and the
    request still exists."""
    key = _key(token, CONFIRM_SALT, CONFIRM_HOURS * 3600)
    if key is None:
        return None
    return VisitorRequest.objects.select_related("event__church").filter(key=key).exclude(_lapsed(timezone.now())).first()


def for_manage(token: str) -> EventRegistration | None:
    """The registration a private link is for. The link has no deadline: it works while the row keeps its key."""
    key = _key(token, MANAGE_SALT)
    if key is None:
        return None
    return EventRegistration.objects.select_related("event__church", "person").filter(visitor_key=key).first()


def closed(event: Event) -> bool:
    """The links of a visitor show nothing of ``event`` now: the church is not active or does not use the
    ``community`` module, the event is no longer active, public and published, or the option is off (an event that
    protects minors switches it off). The confirmation link is then the dead link; the private link only lets the
    visitor cancel or leave the list. A sign-up that closed by its date, or an event that ended, is not "closed":
    the pages say those themselves."""
    church = event.church
    if not church.active or not flags.enabled(church, "community"):
        return True
    page = EventPage.objects.filter(event=event).first()
    if page is None or not page.published or not page.public or event.status != Event.Status.ACTIVE:
        return True
    return page.requires_guardian_consent or not page.visitor_signup


# ---- confirming -----------------------------------------------------------------------------------
def confirm(row: VisitorRequest, *, base_url: str | None = None) -> tuple[str, EventRegistration | None]:
    """The button behind the link was pressed. ``("ok", registration)`` when the visitor is in (confirmed,
    pending or on the waiting list: read the row), and again for every later press of the same link. Otherwise
    one word and None: ``full`` (no place and no list), ``registration_over``, ``ended``, another word of
    ``services.visitor_block`` when the option is gone, or ``invalid`` when the request no longer exists."""
    from . import questions

    with transaction.atomic():
        row = VisitorRequest.objects.select_for_update(of=("self",)).select_related("event__church").filter(pk=row.pk).first()
        if row is None:
            return "invalid", None
        if row.confirmed_at is not None:
            registration = EventRegistration.objects.select_related("event__church").filter(pk=row.registration_id).first()
            return ("ok", registration) if registration is not None else ("invalid", None)
        try:
            registration = services.register_visitor(
                row.event, key=row.key, first_name=row.first_name, last_name=row.last_name, phone=row.phone,
                email=row.email, contact_consent=row.contact_consent, consent_at=row.created_at, base_url=base_url,
            )
        except services.AgendaError as error:
            return error.code, None
        if registration.visitor_key == row.key:  # this request made the row: its answers go with it
            questions.save_answers(registration, row.answers)
        row.registration, row.confirmed_at = registration, timezone.now()
        row.save(update_fields=["registration", "confirmed_at"])
        # This confirmation was answered: it no longer counts against the address.
        SubscriptionSuppression.objects.filter(page__event_id=row.event_id, address_hash=row.limit_hash, unanswered__gt=0).update(
            unanswered=F("unanswered") - 1)
    return "ok", registration


# ---- e-mail ---------------------------------------------------------------------------------------
def announce_offer(registration: EventRegistration, base_url: str) -> None:
    """After the transaction commits, one e-mail to a visitor on the waiting list: a place was freed and where to
    answer (their private link). The church's and the event's names and the link; nothing about anybody."""
    event, address = registration.event, registration.guest_email
    if not address or not base_url:
        return
    context = {"church_name": event.church.name, "event_name": event.name,
               "link": f"{base_url.rstrip('/')}{manage_path(registration)}"}

    def send_after_commit() -> None:
        try:
            subscription_mail.send(subscription_mail.VISITOR_OFFER, address, context)
        except ValueError:  # a name the e-mail refuses; the refusal names no value
            logger.error("The e-mail about a place offered to a visitor could not be written.")

    transaction.on_commit(send_after_commit, robust=True)


def mailable_events() -> dict[int, Event]:
    """The events whose requests may be mailed now: the ones whose form is offered."""
    today = timezone.localdate()
    pages = public.public_pages().filter(visitor_signup=True, requires_guardian_consent=False).select_related("event", "event__church")
    return {
        page.event_id: page.event for page in pages
        if not services.visitor_block(page.event, page, today) and flags.enabled(page.event.church, "community")
    }


_CONFIRM_FIELDS = ("confirm_outcome", "confirm_attempts", "confirm_last_attempt_at")


def _claim(row: VisitorRequest) -> int:
    """Take the one right to send this request's e-mail now, and count the attempt. 0: somebody else has it."""
    taken = (
        VisitorRequest.objects.filter(pk=row.pk, confirmed_at__isnull=True)
        .filter(Q(confirm_attempts=0) | subscriptions._retriable(*_CONFIRM_FIELDS))
        .update(confirm_outcome=MailOutcome.PENDING, confirm_attempts=F("confirm_attempts") + 1, confirm_last_attempt_at=timezone.now())
    )
    if not taken:
        return 0
    return subscriptions.FIRST_ATTEMPT if row.confirm_attempts == 0 else subscriptions.RETRY


def send_due(base_url: str, *, budget_seconds: float | None = None) -> dict:
    """Send the confirmation e-mails that wait, as far as the three paces allow, taking turns: each round gives
    one e-mail to every church that has one to send and is under its daily cap (the church with the oldest
    request first), and inside a church the turn goes round its events, each sending its oldest request whose mail
    domain is under its hourly pace. A church at its cap takes no turn and holds no place, so one church's backlog
    (or one event's) never delays another. Counts only; ``left`` is what waits for a later pass."""
    run = subscriptions._Pass(base_url, budget_seconds)
    counts = {"confirmations": 0, "failed": 0, "first_failed": 0, "left": 0}
    events = mailable_events()
    if not events:
        return counts
    now = timezone.now()
    try:
        sendable = (
            VisitorRequest.objects.filter(event_id__in=events, confirmed_at__isnull=True).exclude(_lapsed(now))
            .filter(Q(confirm_attempts=0) | subscriptions._retriable(*_CONFIRM_FIELDS))
        )
        by_church: dict[int, int] = {}
        by_domain: dict[tuple[int, str], int] = {}
        recent = VisitorRequest.objects.filter(event_id__in=events, confirm_attempts__gte=1, confirm_last_attempt_at__gte=now - timedelta(days=1))
        for event_id, address, last in recent.values_list("event_id", "email", "confirm_last_attempt_at"):
            church_id = events[event_id].church_id
            by_church[church_id] = by_church.get(church_id, 0) + 1
            if last >= now - timedelta(hours=1):
                where = event_id, subscriptions._domain(address)
                by_domain[where] = by_domain.get(where, 0) + 1
        # The work, per church and per event (one aggregate query); each event's rows are read when its turn comes.
        work: dict[int, list[tuple]] = {}
        waiting = 0
        for event_id, oldest, number in (
            sendable.order_by().values("event_id").annotate(oldest=Min("created_at"), number=Count("id"))
            .values_list("event_id", "oldest", "number")
        ):
            waiting += number
            work.setdefault(events[event_id].church_id, []).append((oldest, event_id))
        turns = deque(
            (church_id, deque(event_id for _, event_id in sorted(rows)))
            for church_id, rows in sorted(work.items(), key=lambda item: (min(item[1]), item[0]))
        )
        queues: dict[int, Iterator[VisitorRequest]] = {}

        def oldest_of(event_id: int) -> VisitorRequest | None:
            """The event's next request whose mail domain is under its pace; what is passed over waits for a later pass."""
            if event_id not in queues:
                queues[event_id] = iter(sendable.filter(event_id=event_id).order_by("created_at", "pk")[:PENDING_PER_EVENT])
            for row in queues[event_id]:
                if by_domain.get((event_id, subscriptions._domain(row.email)), 0) < DOMAIN_CONFIRMATIONS_PER_HOUR:
                    return row
            return None

        handed = lost = 0
        while turns and handed < CONFIRMATIONS_PER_PASS and not run.out_of_time():
            church_id, its_events = turns.popleft()
            if by_church.get(church_id, 0) >= CONFIRMATIONS_PER_CHURCH_PER_DAY:
                continue  # at its cap: out of the turns, holding no place
            row = None
            while its_events and row is None:
                event_id = its_events.popleft()
                row = oldest_of(event_id)
                if row is not None:
                    its_events.append(event_id)  # its next turn comes after the church's other events
            if row is None:
                continue  # nothing this church can send in this pass
            turns.append((church_id, its_events))
            attempt = _claim(row)
            if not attempt:
                lost += 1  # another pass took it
                continue
            handed += 1
            event = events[row.event_id]
            where = row.event_id, subscriptions._domain(row.email)
            by_church[church_id] = by_church.get(church_id, 0) + 1
            by_domain[where] = by_domain.get(where, 0) + 1
            context = {"church_name": event.church.name, "event_name": event.name,
                       "link": f"{base_url.rstrip('/')}{confirm_path(confirm_token(row))}"}
            went = subscriptions._hand_over(subscription_mail.VISITOR_CONFIRM, row.email, context, run.connection)
            VisitorRequest.objects.filter(pk=row.pk).update(confirm_outcome=MailOutcome.SENT if went else MailOutcome.FAILED)
            run.record(went, attempt, "confirmations")
        run.counts["left"] = max(waiting - handed - lost, 0)
    finally:
        run.close()
    return {key: run.counts[key] for key in counts}


# ---- the endings ----------------------------------------------------------------------------------
def delete_due(now=None) -> dict:
    """Delete what is over (the module docstring lists it). It only deletes, takes no flag and reads no setting:
    it runs for every church whatever is switched off. Counts only."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    requests = VisitorRequest.objects.filter(_lapsed(now)).delete()[1].get(VisitorRequest._meta.label, 0)
    # The events whose own retention is over (``Event.retention_days`` after the last day), found through what is
    # left to delete so that an old event is looked at once.
    with_answers = set(RegistrationAnswer.objects.values_list("registration__event_id", flat=True).distinct())
    with_visitors = set(EventRegistration.objects.filter(visitor_key__isnull=False).values_list("event_id", flat=True).distinct())
    over = [
        event.pk for event in Event.objects.filter(pk__in=with_answers | with_visitors)
        if (last := services.last_day(event)) is not None and (today - last).days > event.retention_days
    ]
    answers = RegistrationAnswer.objects.filter(registration__event_id__in=over).delete()[0] if over else 0
    visitors = EventRegistration.objects.filter(event_id__in=over, visitor_key__isnull=False)
    # Who the administration added to the church's people keeps the row (it is a person's now) and loses the link.
    contacts = visitors.filter(person__isnull=False).update(guest_email="", visitor_key=None) if over else 0
    registrations = visitors.filter(person__isnull=True).delete()[1].get(EventRegistration._meta.label, 0) if over else 0
    return {"requests": requests, "answers": answers, "registrations": registrations, "contacts": contacts}


def run_pass(base_url: str | None = None, *, budget_seconds: float | None = None, acting: bool = True) -> dict:
    """The deletions, always; then the e-mails when ``acting`` and this server can send them."""
    base_url = settings.EMMAUS_PUBLIC_BASE_URL if base_url is None else base_url
    counts = {"confirmations": 0, "failed": 0, "first_failed": 0, "left": 0, **delete_due(), "not_configured": False}
    if not acting or not base_url or not email_is_configured():
        counts["not_configured"] = True
        return counts
    counts.update(send_due(base_url, budget_seconds=budget_seconds))
    return counts


def verdict(done: dict) -> str:
    """A pass in which first attempts failed and nothing went out is a failed pass."""
    return subscriptions.ALL_SENDS_FAILED if done["first_failed"] and not done["confirmations"] else ""
