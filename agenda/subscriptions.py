"""Updates of ONE event by e-mail, for somebody without an account (issue #96, second part).

Purpose, and the only one: "Te avisamos por correo cuando haya un aviso nuevo de este evento. Nada más." One
event per subscription. The address is never used for anything else, never becomes a list of the church, is
never linked to a ``Person`` or to a child, never becomes an account or a visitor, and opens nothing: an inbox
proves nothing about who somebody is.

Off unless the administration turns "Avisos por correo para quien no tiene cuenta" on for an event
(``EventPage.notice_mail``), and offered only while the event's public page answers and this server can send
(``not_sending``): the scheduled jobs run, ``EMMAUS_PUBLIC_BASE_URL`` is set, and a mail backend delivers.

Double opt-in. ``request`` stores the address as unconfirmed and answers nothing: the caller shows the same
page whatever happened (already subscribed, already asked, over a limit, new), so the form is no oracle, and
no mail is handed over inside the request, so its duration is none either. The scheduled pass sends ONE
e-mail with a signed link that lasts ``CONFIRM_HOURS``; opening the link shows a button, and only pressing
it confirms (a mail scanner that follows links confirms nothing). Only a confirmed row is ever told about a
notice.

Limits on ``request``, so the form cannot be used to fill somebody's inbox. They count an address as the
LIMITS see it (``limit_key``: no capitals, no ``+tag``, and no dots for the one provider known to ignore them),
so spellings of one inbox are one address; the e-mail still goes to what was typed.

* HARD, at the form: one row per inbox and event; ``UNANSWERED_PER_EVENT`` (2) unanswered confirmations per
  address and event, for as long as the event's subscriptions exist; ``UNANSWERED_PER_CHURCH`` (4) per
  address across a church's events in ``SUPPRESSION_DAYS``; ``ADDRESS_REQUESTS_PER_DAY`` (3) events per
  address per day, anywhere; and ``PENDING_PER_EVENT`` (1,000) unconfirmed rows per event, over which an
  unknown address is not stored. The count of unanswered confirmations (``SubscriptionSuppression``: a keyed
  hash, no address) outlives the unconfirmed row, so an address that never answers is not asked again and
  again. Confirming takes one off the count. The e-mail tells the truth about it: the first says one more
  may come, the second that none will;
* PACING, when sending, never at the form: a request that passes the hard limits is always stored, and its
  confirmation goes out when the budget allows, oldest first: ``CONFIRMATIONS_PER_PASS`` (50) per pass,
  ``DOMAIN_CONFIRMATIONS_PER_HOUR`` (60) per mail domain and event, ``CONFIRMATIONS_PER_CHURCH_PER_DAY`` (500)
  per church. A camp that announces its page to a hundred families the same evening loses none of them:
  their e-mails leave over the following passes. The 48 hours of a request start when its e-mail is SENT;
  one that was never sent is deleted after ``QUEUE_DAYS``.

A request for a known or refused address does the same reads as a new one and one write, so that how long
the form takes says little; the e-mail itself never goes out inside the request.

Telling about a notice (``run_pass``, the job ``suscripciones_evento``): a notice is mailed only if, when it
was published, the event took subscriptions and the server could send (``EventNotice.mail_subscribers``);
only to rows confirmed by then; AT MOST once per notice and row: ``EventNoticeEmail`` is written, with its
attempt counted, before the e-mail is handed over, its constraint lets one writer win, and a claim whose
sender died before saying how it went is never taken again (that one e-mail is lost rather than repeated).
What can still repeat: a mail the server reports as failed after having accepted it; only while the notice is in force, at most ``WINDOW_DAYS`` old, and the page
is public, the option on, the church active and using the module. An edit sends nothing (the row exists) and
neither does retiring. A mail that fails waits ``BACKOFF_MINUTES`` and is given up after ``MAX_ATTEMPTS``.
The e-mail says that there is a notice, of which event, and where; never its title or its text
(``agenda.subscription_mail``).

Endings, all automatic (``delete_due``; it only deletes, reads no flag and no setting, and is safe to run for
every church whatever is switched off): an unconfirmed row ``CONFIRM_HOURS`` after its e-mail was sent; every row, and the counts
of unanswered confirmations, ``RETENTION_DAYS`` after the event's last day.
Unsubscribing deletes the row at once. Turning the option off or making the page private deletes nothing and
mails nothing: the rows wait for their expiry.

What nobody sees: the addresses. The administration reads a count of confirmed rows, and only from the
church's minimum reporting size up (``people.policy``), rounded down to a multiple of five, and delivery problems
only when a number is shown. No audit row is written by anything here: who
subscribes has no account to record, and a row about them could only hold what must not be kept.
"""

from __future__ import annotations

import logging
import secrets
import time
from datetime import timedelta

from django.conf import settings
from django.core import mail, signing
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from django.utils.crypto import salted_hmac

from core import scheduler
from core.notify import SENT
from events.models import Event
from events.services.team import email_is_configured
from people import flags, policy

from . import public, services, subscription_mail
from .models import EventNotice, EventNoticeEmail, EventPage, EventSubscription, MailOutcome, SubscriptionSuppression

logger = logging.getLogger(__name__)

PURPOSE = "Te avisamos por correo cuando haya un aviso nuevo de este evento. Nada más."
# Hours an unconfirmed request and its link last.
CONFIRM_HOURS = 48
# Days after the event's last day that a subscription is kept. Nothing is mailed in them (the public page
# stops with the event); they only cover an event whose dates are corrected afterwards.
RETENTION_DAYS = 14
# Unconfirmed rows one event may hold; over it an unknown address is not stored.
PENDING_PER_EVENT = 1000
# Days a request whose confirmation was never sent waits before it is deleted.
QUEUE_DAYS = 7
# Confirmation e-mails sent per mail domain and event in an hour: a pace for sending, never a limit on asking.
DOMAIN_CONFIRMATIONS_PER_HOUR = 60
# Events one address (as the limits see it) may be asked to confirm for in a day, in any church.
ADDRESS_REQUESTS_PER_DAY = 3
# Confirmation e-mails an address may leave unanswered: for one event, while its subscriptions exist; and
# across a church's events in ``SUPPRESSION_DAYS``.
UNANSWERED_PER_EVENT = 2
UNANSWERED_PER_CHURCH = 4
SUPPRESSION_DAYS = 30
# Confirmation e-mails one pass hands over, and one church's events get in 24 hours. The rest waits.
CONFIRMATIONS_PER_PASS = 50
CONFIRMATIONS_PER_CHURCH_PER_DAY = 500
# The staff's numbers are said in steps of this many.
COUNT_STEP = 5
# Days after a notice is published during which the pass still mails it.
WINDOW_DAYS = 3
# After the n-th failed attempt, the minutes before the next one. One more attempt than entries.
BACKOFF_MINUTES = (5, 30, 180, 720)
MAX_ATTEMPTS = len(BACKOFF_MINUTES) + 1
# Most e-mails a pass hands over for one notice; the rest waits for the next.
SEND_CAP = 500
ALL_SENDS_FAILED = "AllSendsFailed"
CONFIRM_SALT = "agenda.subscriptions.confirm"
UNSUBSCRIBE_SALT = "agenda.subscriptions.unsubscribe"
FIRST_ATTEMPT, RETRY = 1, 2
# Why this server sends nothing, as the staff screen says it.
REASON_WORDS = {
    scheduler.OFF: "los envíos programados están apagados en este servidor",
    scheduler.NO_BASE_URL: "falta configurar la dirección pública de Emmaus en el servidor",
    scheduler.NO_EMAIL: "no hay correo configurado",
}


# ---- the address ----------------------------------------------------------------------------------
def normalize(address: str) -> str:
    """What two spellings of one address share: no spaces around it, no capitals."""
    return address.strip().lower()


def address_hash(address: str) -> str:
    """A keyed hash of the address (the key comes from ``SECRET_KEY``): finds a row without comparing addresses,
    and says nothing to who reads the table without the key."""
    return salted_hmac("agenda.EventSubscription.email", normalize(address), algorithm="sha256").hexdigest()


# Providers known to deliver "a.b@" and "ab@" to one inbox. Only where that is documented by the provider.
DOTS_IGNORED = frozenset({"gmail.com", "googlemail.com"})


def limit_key(address: str) -> str:
    """The address as the LIMITS see it, never as it is mailed: no capitals, no ``+tag`` in the local part, and
    no dots where the provider ignores them. Two spellings of one inbox give one key."""
    local, _, domain = normalize(address).rpartition("@")
    local = local.split("+", 1)[0]
    if domain in DOTS_IGNORED:
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


def limit_hash(address: str) -> str:
    return salted_hmac("agenda.SubscriptionSuppression.address", limit_key(address), algorithm="sha256").hexdigest()


def _domain(address: str) -> str:
    return limit_key(address).rpartition("@")[2]


def _lapsed(now) -> Q:
    """An unconfirmed request that is over: its e-mail was sent ``CONFIRM_HOURS`` ago, or was never sent in
    ``QUEUE_DAYS``. The hours count from the sending, so who waited for the budget gets a link that works."""
    sent = Q(confirm_attempts__gte=1, confirm_last_attempt_at__lt=now - timedelta(hours=CONFIRM_HOURS))
    never = Q(confirm_attempts=0, created_at__lt=now - timedelta(days=QUEUE_DAYS))
    return Q(confirmed_at__isnull=True) & (sent | never)


# ---- whether it is offered ------------------------------------------------------------------------
def not_sending() -> str:
    """Why this server sends no e-mail to subscribers, or empty when it does. Settings only: no query."""
    if not scheduler.community_jobs_enabled():
        return scheduler.OFF
    if not settings.EMMAUS_PUBLIC_BASE_URL:
        return scheduler.NO_BASE_URL
    if not email_is_configured():
        return scheduler.NO_EMAIL
    return ""


def takes_subscriptions(event: Event, page: EventPage | None) -> bool:
    """The option is on and the event's public page answers right now."""
    return page is not None and page.notice_mail and public.is_public(event, page)


def will_mail(event: Event, page: EventPage | None) -> bool:
    """Publishing a notice of ``event`` now would be followed by e-mails to its confirmed subscribers."""
    return takes_subscriptions(event, page) and not not_sending()


def offered_page(slug: str) -> EventPage | None:
    """The public page of the event when somebody may subscribe from it now, else None (one answer for all)."""
    page = public.public_page(slug)
    if page is None or not page.notice_mail or not_sending():
        return None
    return page


# ---- asking, confirming, leaving ------------------------------------------------------------------
def _unanswered(page: EventPage | None, digest: str, now) -> tuple[int, int, int]:
    """For an address as the limits see it: unanswered confirmations for this event, across the church's events
    lately, and the events it was asked for in the last day. Three reads, always the same three."""
    limits = SubscriptionSuppression.objects.filter(address_hash=digest)
    here = limits.filter(page=page).values_list("unanswered", flat=True).first() or 0
    church = limits.filter(
        page__event__church_id=page.event.church_id if page else None, last_at__gte=now - timedelta(days=SUPPRESSION_DAYS)
    ).aggregate(total=Sum("unanswered"))["total"] or 0
    today = limits.filter(last_at__gte=now - timedelta(days=1)).count()
    return here, church, today


def request(event: Event, address: str) -> None:
    """Somebody typed ``address`` in the event's form. Returns nothing, whatever happened: see the module docstring."""
    exact, digest = address_hash(address), limit_hash(address)
    now = timezone.now()
    page = EventPage.objects.select_related("event").filter(event=event).first()
    of_event = EventSubscription.objects.filter(event=event)
    rows = of_event.filter(Q(email_hash=exact) | Q(limit_hash=digest))
    # A request that is over is gone, whether or not the pass has deleted it yet.
    rows.filter(_lapsed(now)).delete()
    known = rows.exists()  # this address, or another spelling of the same inbox
    here, church, today = _unanswered(page, digest, now)
    full = of_event.filter(confirmed_at__isnull=True).count() >= PENDING_PER_EVENT
    refused = here >= UNANSWERED_PER_EVENT or church >= UNANSWERED_PER_CHURCH or today >= ADDRESS_REQUESTS_PER_DAY
    if known or refused or full or page is None:
        rows.update(email_hash=F("email_hash"))  # the same reads were made; one write, as a new address makes
        return
    try:
        with transaction.atomic():
            EventSubscription.objects.create(event=event, email=address.strip(), email_hash=exact, limit_hash=digest,
                                             key=secrets.token_urlsafe(32), created_at=now)
            mine = SubscriptionSuppression.objects.filter(page=page, address_hash=digest)
            if not mine.update(unanswered=F("unanswered") + 1, last_at=now):
                SubscriptionSuppression.objects.create(page=page, address_hash=digest, unanswered=1, last_at=now)
    except IntegrityError:  # the same address, twice at once: one row, one count
        return


def confirm_token(subscription: EventSubscription) -> str:
    return signing.dumps(subscription.key, salt=CONFIRM_SALT)


def unsubscribe_token(subscription: EventSubscription) -> str:
    return signing.dumps(subscription.key, salt=UNSUBSCRIBE_SALT)


def _by_token(token: str, salt: str, max_age: int | None = None) -> EventSubscription | None:
    try:
        key = signing.loads(token, salt=salt, max_age=max_age)
    except signing.BadSignature:  # tampered, of the other kind, or expired
        return None
    if not isinstance(key, str):
        return None
    return EventSubscription.objects.select_related("event__church").filter(key=key).first()


def for_confirmation(token: str) -> EventSubscription | None:
    """The row a confirmation link is for, while it can still be confirmed (or already is): the link is whole
    and not older than ``CONFIRM_HOURS``, the unconfirmed row is not over, and the event still takes subscriptions."""
    subscription = _by_token(token, CONFIRM_SALT, CONFIRM_HOURS * 3600)
    if subscription is None:
        return None
    if EventSubscription.objects.filter(_lapsed(timezone.now()), pk=subscription.pk).exists():
        return None
    event = subscription.event
    if not takes_subscriptions(event, EventPage.objects.filter(event=event).first()):
        return None
    return subscription


def confirm(subscription: EventSubscription) -> None:
    """Confirm once: a second press, or a copy read before the first, changes nothing."""
    now = timezone.now()
    with transaction.atomic():
        won = EventSubscription.objects.filter(pk=subscription.pk, confirmed_at__isnull=True).exclude(_lapsed(now)).update(
            confirmed_at=now
        )
        if won:  # this confirmation was answered: it no longer counts against the address
            SubscriptionSuppression.objects.filter(
                page__event_id=subscription.event_id, address_hash=limit_hash(subscription.email), unanswered__gt=0
            ).update(unanswered=F("unanswered") - 1)


def for_unsubscribe(token: str) -> EventSubscription | None:
    """The row an unsubscribe link is for. The link never expires: it works as long as the row exists,
    whatever became of the event's page."""
    return _by_token(token, UNSUBSCRIBE_SALT)


def unsubscribe(subscription: EventSubscription) -> None:
    """Delete the address now, with the rows that say which notices it was told about."""
    EventSubscription.objects.filter(pk=subscription.pk).delete()


# ---- what the administration reads ----------------------------------------------------------------
def _stepped(count: int, smallest: int) -> int:
    """``count`` as the staff may read it: down to a multiple of ``COUNT_STEP``, never under the minimum."""
    return max(smallest, count - count % COUNT_STEP)


def audience_words(event: Event) -> str:
    """How many confirmed addresses, as the staff screen may say it: only from the church's minimum reporting
    size up, and then in steps ("5 o más", "10 o más"), so that one address more or less is not read off the
    screen. Below the minimum the words do not tell none from a few. What remains readable: that the count
    crossed the minimum itself."""
    count = EventSubscription.objects.filter(event=event, confirmed_at__isnull=False).count()
    smallest = policy.min_reporting_size(event.church)
    if count >= smallest:
        return f"{_stepped(count, smallest)} o más direcciones suscritas"
    return f"menos de {smallest} direcciones suscritas (puede no haber ninguna)"


def job_problem() -> str:
    """Empty when the pass that sends these e-mails ended well lately; else why not (``core.scheduler.problem``)."""
    try:
        return scheduler.problem(scheduler.SUBSCRIPTIONS)
    except DatabaseError:
        return scheduler.NEVER


def state_for_staff(event: Event, page: EventPage | None) -> dict:
    """What the screen of public notices says about e-mail, honestly: whether publishing now sends anything."""
    wanted = bool(page and page.notice_mail)
    state = {"wanted": wanted, "public": False, "will": False, "reason": "", "reason_words": "", "late": False,
             "audience": "", "retention_days": RETENTION_DAYS, "window_days": WINDOW_DAYS}
    if not wanted:
        return state
    state["reason"] = not_sending()
    state["reason_words"] = REASON_WORDS.get(state["reason"], "")
    state["public"] = public.is_public(event, page)
    state["will"] = state["public"] and not state["reason"]
    if state["will"]:
        state["audience"] = audience_words(event)
        state["late"] = bool(job_problem())
    return state


def mail_lines(notices, church) -> dict[int, str]:
    """For each notice that was to be mailed, one sentence about its e-mails. Numbers under the minimum
    reporting size are not said, none is told from a few, and a failure is mentioned only beside a number."""
    wanted = [notice.pk for notice in notices if notice.mail_subscribers]
    if not wanted:
        return {}
    smallest = policy.min_reporting_size(church)
    rows = EventNoticeEmail.objects.filter(notice_id__in=wanted).values("notice_id").annotate(
        sent=Count("pk", filter=Q(outcome=MailOutcome.SENT)), failed=Count("pk", filter=Q(outcome=MailOutcome.FAILED)))
    counts = {row["notice_id"]: row for row in rows}
    lines = {}
    for pk in wanted:
        sent = counts.get(pk, {}).get("sent", 0)
        if sent < smallest:  # and nothing about failures: "some failed" would say that there is somebody
            lines[pk] = f"Por este aviso salieron menos de {smallest} correos (puede que ninguno)."
            continue
        lines[pk] = f"Salieron {_stepped(sent, smallest)} o más correos por este aviso."
        if counts.get(pk, {}).get("failed", 0):
            lines[pk] += " Algunos correos no se pudieron enviar."
    return lines


# ---- the endings ----------------------------------------------------------------------------------
def _deleted(result) -> int:
    return result[1].get(EventSubscription._meta.label, 0)


def delete_due(now=None) -> dict:
    """Delete what is over: unconfirmed requests ``CONFIRM_HOURS`` after their e-mail was sent (``_lapsed``); every row, and the counts of
    unanswered confirmations, ``RETENTION_DAYS`` after the event's last day (an event without dates: after the
    row's own age). It only deletes, takes no flag and reads no setting: whoever schedules it may run it for
    every church whatever is switched off. Counts only."""
    now = now or timezone.now()
    limit = timezone.localdate(now) - timedelta(days=RETENTION_DAYS)
    aged = now - timedelta(days=RETENTION_DAYS)

    def over(event: str, age: str) -> Q:
        return (
            Q(**{f"{event}__ends_on__lt": limit})
            | Q(**{f"{event}__ends_on__isnull": True, f"{event}__starts_on__lt": limit})
            | Q(**{f"{event}__ends_on__isnull": True, f"{event}__starts_on__isnull": True, f"{age}__lt": aged})
        )

    unconfirmed = EventSubscription.objects.filter(_lapsed(now))
    return {
        "unconfirmed": _deleted(unconfirmed.delete()),
        "expired": _deleted(EventSubscription.objects.filter(over("event", "created_at")).delete()),
        "limits": SubscriptionSuppression.objects.filter(over("page__event", "last_at")).delete()[0],
    }


purge = delete_due  # the name the first version had


# ---- sending --------------------------------------------------------------------------------------
def _retriable(outcome: str = "outcome", attempts: str = "attempts", last: str = "last_attempt_at") -> Q:
    """The claims that may be taken again now: the mail server refused the e-mail and its wait is over. Never one
    that used its ``MAX_ATTEMPTS``, and never one left "pending": its sender died between handing the e-mail
    over and writing how it went, and nobody knows which; taking it again could deliver a second copy."""
    now = timezone.now()
    waited = Q(pk__in=[])
    for done, minutes in enumerate(BACKOFF_MINUTES, start=1):
        waited |= Q(**{attempts: done, f"{last}__lte": now - timedelta(minutes=minutes)})
    return Q(**{outcome: MailOutcome.FAILED}) & waited


_CONFIRM_FIELDS = ("confirm_outcome", "confirm_attempts", "confirm_last_attempt_at")


def mailable_events() -> dict[int, Event]:
    """The events whose subscribers may be mailed now: the option on, the public page answering
    (``agenda.public.public_pages`` and its two checks) and the church using the module."""
    today = timezone.localdate()
    pages = public.public_pages().filter(notice_mail=True).select_related("event", "event__church")
    return {
        page.event_id: page.event for page in pages
        if not services.has_ended(page.event, today) and flags.enabled(page.event.church, "community")
    }


def _link(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def _hand_over(kind: str, address: str, context: dict, connection) -> bool:
    try:
        return subscription_mail.send(kind, address, context, connection=connection) == SENT
    except ValueError:  # a name the e-mail refuses (two lines, too long). The refusal names no value.
        logger.error("A subscription e-mail could not be written.")
        return False


class _Pass:
    """One pass: its counts, its one connection to the mail server and its deadline."""

    def __init__(self, base_url: str, budget_seconds: float | None):
        self.base_url = base_url
        self.deadline = None if budget_seconds is None else time.monotonic() + budget_seconds
        self.counts = {"confirmations": 0, "sent": 0, "failed": 0, "first_failed": 0, "left": 0}
        self.connection = mail.get_connection()

    def out_of_time(self) -> bool:
        return self.deadline is not None and time.monotonic() > self.deadline

    def record(self, went: bool, attempt: int, key: str) -> None:
        self.counts[key] += went
        self.counts["failed"] += not went
        self.counts["first_failed"] += not went and attempt == FIRST_ATTEMPT
        if not went:  # the connection may be what broke: the next e-mail starts on a new one
            self.close()

    def close(self) -> None:
        try:
            self.connection.close()
        except Exception as error:  # noqa: BLE001
            logger.error("The connection to the mail server did not close cleanly: %s", type(error).__name__)


def _claim_confirmation(subscription: EventSubscription) -> int:
    """Take the one right to send this row's confirmation now, and count the attempt. 0: somebody else has it."""
    never = Q(confirm_attempts=0)
    taken = (
        EventSubscription.objects.filter(pk=subscription.pk, confirmed_at__isnull=True)
        .filter(never | _retriable(*_CONFIRM_FIELDS))
        .update(confirm_outcome=MailOutcome.PENDING, confirm_attempts=F("confirm_attempts") + 1, confirm_last_attempt_at=timezone.now())
    )
    if not taken:
        return 0
    return FIRST_ATTEMPT if subscription.confirm_attempts == 0 else RETRY


def _confirmation_kind(subscription: EventSubscription) -> str:
    """Which text: the one that says one more may come, or the one that says none will (the last the limits allow)."""
    unanswered = SubscriptionSuppression.objects.filter(
        page__event_id=subscription.event_id, address_hash=limit_hash(subscription.email)
    ).values_list("unanswered", flat=True).first() or 0
    return subscription_mail.CONFIRM_LAST if unanswered >= UNANSWERED_PER_EVENT else subscription_mail.CONFIRM


def _send_confirmations(run: _Pass, events: dict[int, Event]) -> None:
    """The requests whose e-mail is still to go, oldest first, as far as the three paces allow. What does not
    fit waits for a later pass: nothing is dropped."""
    now = timezone.now()
    pending = EventSubscription.objects.filter(event_id__in=events, confirmed_at__isnull=True).exclude(_lapsed(now))
    waiting = list(
        pending.filter(Q(confirm_attempts=0) | _retriable(*_CONFIRM_FIELDS)).order_by("created_at", "pk")[: PENDING_PER_EVENT * 2]
    )
    # What was already sent: per church in the last 24 hours, per event and mail domain in the last hour.
    by_church: dict[int, int] = {}
    by_domain: dict[tuple[int, str], int] = {}
    recent = EventSubscription.objects.filter(event_id__in=events, confirm_attempts__gte=1, confirm_last_attempt_at__gte=now - timedelta(days=1))
    for event_id, address, last in recent.values_list("event_id", "email", "confirm_last_attempt_at"):
        church_id = events[event_id].church_id
        by_church[church_id] = by_church.get(church_id, 0) + 1
        if last >= now - timedelta(hours=1):
            by_domain[event_id, _domain(address)] = by_domain.get((event_id, _domain(address)), 0) + 1
    handed = 0
    for subscription in waiting:
        event = events[subscription.event_id]
        domain = subscription.event_id, _domain(subscription.email)
        if (run.out_of_time() or handed >= CONFIRMATIONS_PER_PASS
                or by_church.get(event.church_id, 0) >= CONFIRMATIONS_PER_CHURCH_PER_DAY
                or by_domain.get(domain, 0) >= DOMAIN_CONFIRMATIONS_PER_HOUR):
            run.counts["left"] += 1
            continue
        attempt = _claim_confirmation(subscription)
        if not attempt:
            continue
        handed += 1
        by_church[event.church_id] = by_church.get(event.church_id, 0) + 1
        by_domain[domain] = by_domain.get(domain, 0) + 1
        context = {
            "church_name": event.church.name, "event_name": event.name,
            "link": _link(run.base_url, public.confirm_path(confirm_token(subscription))),
        }
        went = _hand_over(_confirmation_kind(subscription), subscription.email, context, run.connection)
        EventSubscription.objects.filter(pk=subscription.pk).update(
            confirm_outcome=MailOutcome.SENT if went else MailOutcome.FAILED)
        run.record(went, attempt, "confirmations")


def _claim_notice(notice: EventNotice, subscription_id: int) -> int:
    """Take the one right to mail this row about this notice, and count the attempt, BEFORE the e-mail is handed
    over. 0: somebody else has it or had it and died, it is not time to try again, or the row was deleted
    meanwhile (the person unsubscribed)."""
    now = timezone.now()
    try:
        with transaction.atomic():
            EventNoticeEmail.objects.create(notice=notice, subscription_id=subscription_id, attempts=1, last_attempt_at=now)
        return FIRST_ATTEMPT
    except IntegrityError:
        retry = EventNoticeEmail.objects.filter(_retriable(), notice=notice, subscription_id=subscription_id)
        taken = retry.update(outcome=MailOutcome.PENDING, attempts=F("attempts") + 1, last_attempt_at=now)
        return RETRY if taken else 0


def _waiting(notice: EventNotice):
    """The rows confirmed by the time the notice was published that have no e-mail about it, or one that may
    be tried again. Who confirms later is not mailed old notices."""
    done = EventNoticeEmail.objects.filter(notice=notice).exclude(_retriable())
    return (
        EventSubscription.objects.filter(event_id=notice.event_id, confirmed_at__isnull=False, confirmed_at__lte=notice.published_at)
        .exclude(pk__in=done.values("subscription_id")).order_by("pk")
    )


def _send_notice(run: _Pass, notice: EventNotice, event: Event) -> None:
    waiting = list(_waiting(notice).values_list("pk", "email", "key")[: SEND_CAP + 1])
    handled = 0
    for subscription_id, address, key in waiting[:SEND_CAP]:
        if run.out_of_time():
            break
        handled += 1
        attempt = _claim_notice(notice, subscription_id)
        if not attempt:
            continue
        token = signing.dumps(key, salt=UNSUBSCRIBE_SALT)
        context = {
            "church_name": event.church.name, "event_name": event.name,
            "link": _link(run.base_url, public.notices_path(event)),
            "unsubscribe_link": _link(run.base_url, public.unsubscribe_path(token)),
        }
        went = _hand_over(subscription_mail.NOTICE, address, context, run.connection)
        EventNoticeEmail.objects.filter(notice=notice, subscription_id=subscription_id).update(
            outcome=MailOutcome.SENT if went else MailOutcome.FAILED)
        run.record(went, attempt, "sent")
    run.counts["left"] += len(waiting) - handled


def due_notices(events: dict[int, Event]):
    """The notices the pass still mails: marked for it when published, in force, of a mailable event and
    at most ``WINDOW_DAYS`` old."""
    since = timezone.now() - timedelta(days=WINDOW_DAYS)
    return EventNotice.objects.filter(
        mail_subscribers=True, retired_at__isnull=True, published_at__gte=since, event_id__in=events
    ).order_by("pk")


def send_due(base_url: str, *, budget_seconds: float | None = None) -> dict:
    """Send what is waiting: confirmations first (somebody is looking at their inbox), then the notices.
    Safe to call again and from two places at once. Counts only."""
    run = _Pass(base_url, budget_seconds)
    events = mailable_events()
    if not events:
        return run.counts
    try:
        _send_confirmations(run, events)
        for notice in due_notices(events):
            if run.out_of_time():
                run.counts["left"] += _waiting(notice).count()
                continue
            _send_notice(run, notice, events[notice.event_id])
    finally:
        run.close()
    return run.counts


def run_pass(base_url: str | None = None, *, budget_seconds: float | None = None) -> dict:
    """The deletions (``delete_due``) and then the e-mails (``send_due``) if this server can send them. Whoever
    schedules the two apart calls those two: only ``send_due`` belongs under the community switch."""
    base_url = settings.EMMAUS_PUBLIC_BASE_URL if base_url is None else base_url
    counts = {"confirmations": 0, "sent": 0, "failed": 0, "first_failed": 0, "left": 0, **delete_due(), "not_configured": False}
    if not base_url or not email_is_configured():
        counts["not_configured"] = True
        return counts
    counts.update(send_due(base_url, budget_seconds=budget_seconds))
    return counts


def verdict(done: dict) -> str:
    """A pass in which first attempts failed and nothing went out is a failed pass: nothing is getting through."""
    if done["first_failed"] and not (done["sent"] + done["confirmations"]):
        return ALL_SENDS_FAILED
    return ""
