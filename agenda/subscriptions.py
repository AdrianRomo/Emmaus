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

Limits on ``request``, so the form cannot be used to fill somebody's inbox:

* one row per address and event (``agenda_subscription_once_per_event``) and one confirmation e-mail per row;
  an unconfirmed row lives ``CONFIRM_HOURS``, so an address gets at most one confirmation e-mail per event
  every 48 hours (stricter than one a day);
* ``ADDRESS_REQUESTS_PER_DAY`` unconfirmed rows per address across all events;
* ``EVENT_REQUESTS_PER_HOUR`` requests per event (``events.services.throttle``, keyed by the event: it counts
  under a row lock, needs no session, and its window is the hour its purge already assumes). Over it, requests
  are dropped for an hour: somebody can so keep others from subscribing to one event for a while, which costs
  less than letting the form mail without bound.

Telling about a notice (``run_pass``, the job ``suscripciones_evento``): a notice is mailed only if, when it
was published, the event took subscriptions and the server could send (``EventNotice.mail_subscribers``);
only to rows confirmed by then; once per notice and row (``EventNoticeEmail`` is written first and its
constraint lets one writer win); only while the notice is in force, at most ``WINDOW_DAYS`` old, and the page
is public, the option on, the church active and using the module. An edit sends nothing (the row exists) and
neither does retiring. A mail that fails waits ``BACKOFF_MINUTES`` and is given up after ``MAX_ATTEMPTS``.
The e-mail says that there is a notice, of which event, and where; never its title or its text
(``agenda.subscription_mail``).

Endings, all automatic (``purge``; it only deletes, so it runs for every church whatever its flags):
an unconfirmed row after ``CONFIRM_HOURS``; every row ``RETENTION_DAYS`` after the event's last day.
Unsubscribing deletes the row at once. Turning the option off or making the page private deletes nothing and
mails nothing: the rows wait for their expiry.

What nobody sees: the addresses. The administration reads a count of confirmed rows, and only from the
church's minimum reporting size up (``people.policy``). No audit row is written by anything here: who
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
from django.db.models import Count, F, Q
from django.utils import timezone
from django.utils.crypto import salted_hmac

from core import scheduler
from core.notify import SENT
from events.models import Event
from events.services import throttle
from events.services.team import email_is_configured
from people import flags, policy

from . import public, services, subscription_mail
from .models import EventNotice, EventNoticeEmail, EventPage, EventSubscription, MailOutcome

logger = logging.getLogger(__name__)

PURPOSE = "Te avisamos por correo cuando haya un aviso nuevo de este evento. Nada más."
# Hours an unconfirmed request and its link last.
CONFIRM_HOURS = 48
# Days after the event's last day that a subscription is kept. Nothing is mailed in them (the public page
# stops with the event); they only cover an event whose dates are corrected afterwards.
RETENTION_DAYS = 14
EVENT_REQUESTS_PER_HOUR = 30
ADDRESS_REQUESTS_PER_DAY = 3
# Days after a notice is published during which the pass still mails it.
WINDOW_DAYS = 3
# A claim this old that never got its outcome belongs to a sender that died.
PENDING_STALE_MINUTES = 10
# After the n-th failed attempt, the minutes before the next one. One more attempt than entries.
BACKOFF_MINUTES = (5, 30, 180, 720)
MAX_ATTEMPTS = len(BACKOFF_MINUTES) + 1
# Most e-mails of one kind a pass hands over for one notice (or of confirmations); the rest waits for the next.
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
def _event_key(event: Event) -> str:
    return f"suscripcion-evento:{event.pk}"


def request(event: Event, address: str) -> None:
    """Somebody typed ``address`` in the event's form. Returns nothing, whatever happened: see the module docstring."""
    digest = address_hash(address)
    now = timezone.now()
    if throttle.consume(_event_key(event), limit=EVENT_REQUESTS_PER_HOUR, window_seconds=3600, block_seconds=3600,
                        max_block_seconds=3600):
        return
    rows = EventSubscription.objects.filter(email_hash=digest)
    # An unconfirmed request whose hours ended is gone, whether or not the pass has deleted it yet.
    rows.filter(event=event, confirmed_at__isnull=True, created_at__lt=now - timedelta(hours=CONFIRM_HOURS)).delete()
    if rows.filter(event=event).exists():
        return
    if rows.filter(confirmed_at__isnull=True, created_at__gte=now - timedelta(days=1)).count() >= ADDRESS_REQUESTS_PER_DAY:
        return
    try:
        with transaction.atomic():
            EventSubscription.objects.create(event=event, email=address.strip(), email_hash=digest,
                                             key=secrets.token_urlsafe(32), created_at=now)
    except IntegrityError:  # the same address, twice at once: one row
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
    and not older than ``CONFIRM_HOURS``, neither is an unconfirmed row, and the event still takes subscriptions."""
    subscription = _by_token(token, CONFIRM_SALT, CONFIRM_HOURS * 3600)
    if subscription is None:
        return None
    expired = subscription.created_at < timezone.now() - timedelta(hours=CONFIRM_HOURS)
    if subscription.confirmed_at is None and expired:
        return None
    event = subscription.event
    if not takes_subscriptions(event, EventPage.objects.filter(event=event).first()):
        return None
    return subscription


def confirm(subscription: EventSubscription) -> None:
    """Confirm once: a second press, or a copy read before the first, changes nothing."""
    fresh = timezone.now() - timedelta(hours=CONFIRM_HOURS)
    EventSubscription.objects.filter(pk=subscription.pk, confirmed_at__isnull=True, created_at__gte=fresh).update(
        confirmed_at=timezone.now()
    )


def for_unsubscribe(token: str) -> EventSubscription | None:
    """The row an unsubscribe link is for. The link never expires: it works as long as the row exists,
    whatever became of the event's page."""
    return _by_token(token, UNSUBSCRIBE_SALT)


def unsubscribe(subscription: EventSubscription) -> None:
    """Delete the address now, with the rows that say which notices it was told about."""
    EventSubscription.objects.filter(pk=subscription.pk).delete()


# ---- what the administration reads ----------------------------------------------------------------
def audience_words(event: Event) -> str:
    """How many confirmed addresses, as the staff screen may say it: a number only from the church's
    minimum reporting size up. Below it, the words do not tell none from a few."""
    count = EventSubscription.objects.filter(event=event, confirmed_at__isnull=False).count()
    smallest = policy.min_reporting_size(event.church)
    if count >= smallest:
        return f"{count} direcciones suscritas"
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
    reporting size are not said, and none is told from a few."""
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
        line = f"Salieron {sent} correos por este aviso." if sent >= smallest else (
            f"Por este aviso salieron menos de {smallest} correos (puede que ninguno).")
        if counts.get(pk, {}).get("failed", 0):
            line += " Algunos correos no se pudieron enviar."
        lines[pk] = line
    return lines


# ---- the endings ----------------------------------------------------------------------------------
def _deleted(result) -> int:
    return result[1].get(EventSubscription._meta.label, 0)


def purge(now=None) -> dict:
    """Delete what is over: unconfirmed requests after ``CONFIRM_HOURS`` and every row ``RETENTION_DAYS`` after
    its event's last day (an event without dates: after the row's own age). Counts only."""
    now = now or timezone.now()
    limit = timezone.localdate(now) - timedelta(days=RETENTION_DAYS)
    unconfirmed = EventSubscription.objects.filter(confirmed_at__isnull=True, created_at__lt=now - timedelta(hours=CONFIRM_HOURS))
    over = (
        Q(event__ends_on__lt=limit)
        | Q(event__ends_on__isnull=True, event__starts_on__lt=limit)
        | Q(event__ends_on__isnull=True, event__starts_on__isnull=True, created_at__lt=now - timedelta(days=RETENTION_DAYS))
    )
    return {
        "unconfirmed": _deleted(unconfirmed.delete()),
        "expired": _deleted(EventSubscription.objects.filter(over).delete()),
    }


# ---- sending --------------------------------------------------------------------------------------
def _retriable(outcome: str = "outcome", attempts: str = "attempts", last: str = "last_attempt_at") -> Q:
    """The claims that may be taken again now: the mail failed and its wait is over, or its sender died before
    saying how it went. Never one that used its ``MAX_ATTEMPTS``."""
    now = timezone.now()
    waited = Q(pk__in=[])
    for done, minutes in enumerate(BACKOFF_MINUTES, start=1):
        waited |= Q(**{attempts: done, f"{last}__lte": now - timedelta(minutes=minutes)})
    died = Q(**{outcome: MailOutcome.PENDING, f"{attempts}__gte": 1, f"{attempts}__lt": MAX_ATTEMPTS,
                f"{last}__lt": now - timedelta(minutes=PENDING_STALE_MINUTES)})
    return Q(**{outcome: MailOutcome.FAILED}) & waited | died


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


def _send_confirmations(run: _Pass, events: dict[int, Event]) -> None:
    fresh = timezone.now() - timedelta(hours=CONFIRM_HOURS)
    waiting = list(
        EventSubscription.objects.filter(event_id__in=events, confirmed_at__isnull=True, created_at__gte=fresh)
        .filter(Q(confirm_attempts=0) | _retriable(*_CONFIRM_FIELDS)).order_by("pk")[: SEND_CAP + 1]
    )
    handled = 0
    for subscription in waiting[:SEND_CAP]:
        if run.out_of_time():
            break
        handled += 1
        attempt = _claim_confirmation(subscription)
        if not attempt:
            continue
        event = events[subscription.event_id]
        context = {
            "church_name": event.church.name, "event_name": event.name,
            "link": _link(run.base_url, public.confirm_path(confirm_token(subscription))),
        }
        went = _hand_over(subscription_mail.CONFIRM, subscription.email, context, run.connection)
        EventSubscription.objects.filter(pk=subscription.pk).update(
            confirm_outcome=MailOutcome.SENT if went else MailOutcome.FAILED)
        run.record(went, attempt, "confirmations")
    run.counts["left"] += len(waiting) - handled


def _claim_notice(notice: EventNotice, subscription_id: int) -> int:
    """Take the one right to mail this row about this notice, and count the attempt. 0: somebody else has it,
    it is not time to try again, or the row was deleted meanwhile (the person unsubscribed)."""
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
    """What the scheduler runs: the deletions always, then the e-mails if this server can send them."""
    base_url = settings.EMMAUS_PUBLIC_BASE_URL if base_url is None else base_url
    counts = {"confirmations": 0, "sent": 0, "failed": 0, "first_failed": 0, "left": 0, **purge(), "not_configured": False}
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
