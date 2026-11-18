"""E-mail about a notice: that there is one and where, to who chose to hear about it (decision 0012).

The e-mail goes through ``core.notify`` and nowhere else: a fixed text per kind (one for a notice,
one for an urgent notice), the church's name and the address of the notice's page, which asks for the
account and answers by ``notices.services.visible_to``. Never the title, the body, the group or who
wrote it. An important notice uses the same words as a normal one.

Who is mailed (``recipients``): the people the notice is for who have an account
(``services._addressed_to``: same church, the group's active members, never the author), an e-mail
in "Mis datos", and a preference that includes the notice's level (``EmailPreference``; no row is
"Solo los urgentes").

When: once per version of a notice, from the day it can be read. Publishing sends after its
transaction commits (``schedule``), so a failure to send never undoes or breaks a publish; a notice
kept for a later day, what is left above ``SEND_CAP`` or the time budget, and what failed are sent
by the scheduler's pass every few minutes (``send_due``; ``core.scheduler``) and by
``manage.py send_notice_emails``. A version's round ends when everybody it was for has a row
(``Announcement.emailed_version``): who arrives later is not mailed old notices. A change that asks
for "Enterado" again is a new version and a new round; an urgent level given later reopens the round
for who had not been mailed.

The command only looks at rounds that opened in the last ``WINDOW_DAYS`` days (``in_window``): the
notice's day, a change of its information, or becoming urgent. Correcting a detail of an old notice
opens nothing. So a notice published while the server had no e-mail is mailed if e-mail is
configured within those days, and never after them: its page says that no e-mail will go out.

Nobody is mailed twice for a version: the ``NoticeEmail`` row is written before the e-mail is handed
over and its unique constraint lets one writer win (``_claim``). A row left "pending" by a process
that died (a worker killed while the mail server hung) may be taken again after
``PENDING_STALE_MINUTES``, by one writer, like a row that failed. One call uses one connection to
the mail server, which gives up after ``settings.EMAIL_TIMEOUT`` seconds.

A mail that failed is not tried again at once: each row counts its attempts, the next one waits
``BACKOFF_MINUTES`` (5 minutes, 30 minutes, 3 hours, 12 hours) and after ``MAX_ATTEMPTS`` the row is
final: it stays "failed", is never taken again and no longer keeps the round open (the notice's page
says that some e-mails could not be sent). That holds for every sender, the command by hand included.

What is not known: a send that failed after the message was handed over (a timeout while the server
was answering) looks the same as one the server refused, and so does a sender that died. Trying
those again can deliver a second copy. That was so before the counter; the counter bounds it to
``MAX_ATTEMPTS`` tries per person and version instead of one per pass.

Whether sending works at all (``health``): a pass whose first attempts all failed says so, and so do
the following passes until an e-mail goes out, so that nobody is promised an e-mail meanwhile.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from dataclasses import dataclass

from django.core import mail
from django.db import IntegrityError, transaction
from django.db.models import F, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from core.notify import NOT_CONFIGURED, SENT, notify
from events.services.team import email_is_configured
from people import flags
from people.models import Person

from . import services
from .models import Announcement, EmailPreference, NoticeEmail

logger = logging.getLogger(__name__)

Choice = EmailPreference.Choice
Outcome = NoticeEmail.Outcome
Priority = Announcement.Priority

KIND_NOTICE = "aviso_publicado"
KIND_URGENT = "aviso_urgente"
# Most e-mails one call hands over for one notice; the rest waits for the command.
SEND_CAP = 2000
# Seconds a publish may spend sending before it leaves the rest to the command: the person who
# publishes is waiting for the page, and the web server gives a request 60 seconds.
PUBLISH_BUDGET_SECONDS = 20
# Days after a round opens during which the command still sends it.
WINDOW_DAYS = 3
# A "pending" row this old belongs to a sender that died: no live one waits longer than the mail
# server's timeout for a single mail.
PENDING_STALE_MINUTES = 10
# After the n-th failed attempt, the minutes before the next one. One more attempt than entries.
BACKOFF_MINUTES = (5, 30, 180, 720)
MAX_ATTEMPTS = len(BACKOFF_MINUTES) + 1
# What ``health`` answers when nothing is getting through.
ALL_SENDS_FAILED = "AllSendsFailed"
# The preferences that include each level. No row counts as "Solo los urgentes".
_WANTED = {
    Priority.URGENT: {Choice.URGENT, Choice.IMPORTANT, Choice.ALL},
    Priority.IMPORTANT: {Choice.IMPORTANT, Choice.ALL},
    Priority.NORMAL: {Choice.ALL},
}


@dataclass
class Round:
    """What one call did. ``left``: people still to mail (above the cap or the budget, or failed)."""

    sent: int = 0
    failed: int = 0
    left: int = 0
    # Of ``failed``, the mails that failed on their first attempt (the rest were retries).
    first_failed: int = 0
    not_configured: bool = False


def preference(person: Person) -> str:
    choice = EmailPreference.objects.filter(person=person).values_list("choice", flat=True).first()
    return choice or Choice.URGENT


def set_preference(person: Person, choice: str) -> None:
    """The person's own choice; like reading a notice, it leaves no audit row."""
    if choice not in Choice.values:
        raise ValueError("Unknown e-mail preference.")
    EmailPreference.objects.update_or_create(person=person, defaults={"choice": choice})


def is_mailable(notice: Announcement) -> bool:
    """The notice can be read today: published, its day has come, not expired, its group still
    active and its church still using the module."""
    if notice.state != "publicado":
        return False
    if notice.group_id and not notice.group.is_active:
        return False
    return flags.enabled(notice.church, "community")


def recipients(notice: Announcement) -> QuerySet[Person]:
    """Who gets an e-mail about ``notice`` as it is now, whether or not they already got it."""
    people = services._addressed_to(notice).exclude(email="")
    wanted = _WANTED[notice.priority]
    chosen = Q(notice_email_preference__choice__in=wanted)
    if Choice.URGENT in wanted:
        chosen |= Q(notice_email_preference__isnull=True)
    return people.filter(chosen)


def _stale():
    return timezone.now() - timedelta(minutes=PENDING_STALE_MINUTES)


def _retriable() -> Q:
    """The rows that may be taken again now: the mail failed and its wait is over, or its sender died
    before saying how it went. Never a row that used its ``MAX_ATTEMPTS``."""
    now = timezone.now()
    # A failed row from before the counter (0 attempts, no time) is taken as before: at once.
    waited = Q(attempts=0)
    for attempts, minutes in enumerate(BACKOFF_MINUTES, start=1):
        waited |= Q(attempts=attempts, last_attempt_at__lte=now - timedelta(minutes=minutes))
    return Q(outcome=Outcome.FAILED) & waited | Q(outcome=Outcome.PENDING, created_at__lt=_stale(), attempts__lt=MAX_ATTEMPTS)


def _open_rows() -> Q:
    """The rows that still keep a round open: a mail being sent, one whose sender died and one that
    failed, while attempts are left. Not a mail that was sent, nor one that was given up."""
    given_up = Q(outcome=Outcome.FAILED) | Q(outcome=Outcome.PENDING, created_at__lt=_stale())
    return ~Q(outcome=Outcome.SENT) & ~(given_up & Q(attempts__gte=MAX_ATTEMPTS))


def _waiting(notice: Announcement) -> QuerySet[Person]:
    """The recipients with no row for this version, or with one that may be taken again."""
    done = NoticeEmail.objects.filter(announcement=notice, version=notice.version).exclude(_retriable())
    return recipients(notice).exclude(pk__in=done.values("person_id")).order_by("pk")


def waiting_count(notice: Announcement) -> int:
    """How many e-mails a round would send now. Zero for a notice that cannot be read or whose round ended."""
    if notice.emailed_version == notice.version or not is_mailable(notice):
        return 0
    return _waiting(notice).count()


def _window(days: int) -> Q:
    """The round opened in the last ``days`` days: the notice's day came, its information changed
    (a new version) or it became urgent. Not ``updated_at``: correcting a detail opens nothing."""
    since = timezone.now() - timedelta(days=days)
    return (
        Q(publish_on__gte=timezone.localdate() - timedelta(days=days))
        | Q(changed_at__gte=since)
        | Q(email_reopened_at__gte=since)
    )


def in_window(notice: Announcement, days: int = WINDOW_DAYS) -> bool:
    return Announcement.objects.filter(_window(days), pk=notice.pk).exists()


def open_rounds(days: int = WINDOW_DAYS) -> QuerySet[Announcement]:
    """The notices that can be read today whose round of e-mails has not ended and opened in the
    last ``days`` days. The window keeps an old notice from being mailed the day e-mail is
    configured, or the day somebody corrects a word in it."""
    today = timezone.localdate()
    return (
        Announcement.objects.filter(status=Announcement.Status.PUBLISHED, publish_on__lte=today)
        .filter(Q(expires_on__isnull=True) | Q(expires_on__gte=today))
        .filter(_window(days))
        .exclude(emailed_version=F("version"))
        .select_related("church", "group")
        .order_by("pk")
    )


def will_not_be_mailed(notice: Announcement) -> bool:
    """The round never ended and the command no longer looks at it: no e-mail will go out for this
    notice as it is (published without e-mail on the server, or before e-mails existed)."""
    return notice.emailed_version != notice.version and notice.state == "publicado" and not in_window(notice)


FIRST_ATTEMPT, RETRY = 1, 2


def _claim(notice: Announcement, version: int, person_id: int) -> int:
    """Take the one right to mail this person about this version, and count the attempt. 0: somebody
    else has it, or it is not time to try again. ``FIRST_ATTEMPT`` or ``RETRY`` otherwise."""
    now = timezone.now()
    try:
        with transaction.atomic():
            NoticeEmail.objects.create(announcement=notice, version=version, person_id=person_id, attempts=1, last_attempt_at=now)
        return FIRST_ATTEMPT
    except IntegrityError:
        # A row exists. Only one that failed, or whose sender died, may be tried again, and only by
        # one writer: the update is conditional on the row's state and age, and restarts its age.
        retry = NoticeEmail.objects.filter(_retriable(), announcement=notice, version=version, person_id=person_id)
        taken = retry.update(outcome=Outcome.PENDING, created_at=now, attempts=F("attempts") + 1, last_attempt_at=now)
        return RETRY if taken else 0


def _release(notice: Announcement, version: int, person_id: int) -> None:
    """Nothing was handed over (no e-mail is configured): the person can be mailed another day."""
    NoticeEmail.objects.filter(announcement=notice, version=version, person_id=person_id, outcome=Outcome.PENDING).delete()


def link_to(notice_id: int, base_url: str) -> str:
    return f"{base_url.rstrip('/')}{reverse('notices:detail', args=[notice_id])}"


def _open(connection) -> None:
    """Open the connection every e-mail of this call travels on. If the server does not answer,
    each e-mail tries by itself and is recorded as failed."""
    try:
        connection.open()
    except Exception:
        logger.exception("The mail server could not be reached.")


def _close(connection) -> None:
    try:
        connection.close()
    except Exception:
        logger.exception("The connection to the mail server did not close cleanly.")


def deliver(notice_id: int, base_url: str, *, limit: int = SEND_CAP, budget_seconds: float | None = None) -> Round:
    """Send this version's e-mails to who is still waiting, at most ``limit`` of them and for at
    most ``budget_seconds``. Safe to call again and from two places at once."""
    done = Round()
    notice = Announcement.objects.select_related("church", "group").filter(pk=notice_id).first()
    if notice is None or notice.emailed_version == notice.version or not is_mailable(notice):
        return done
    version, priority = notice.version, notice.priority
    kind = KIND_URGENT if notice.is_urgent else KIND_NOTICE
    context = {"church_name": notice.church.name, "link": link_to(notice.pk, base_url)}
    waiting = list(_waiting(notice).values_list("pk", "email")[: limit + 1])
    if not email_is_configured():
        done.not_configured, done.left = True, len(waiting)
        return done
    started = time.monotonic()
    handled = 0
    connection = mail.get_connection()
    _open(connection)
    try:
        for person_id, address in waiting[:limit]:
            if budget_seconds is not None and time.monotonic() - started > budget_seconds:
                break
            handled += 1
            attempt = _claim(notice, version, person_id)
            if not attempt:
                continue
            try:
                outcome = notify(kind, address, context, connection=connection)
            except ValueError:
                # A church name or an address ``core.notify`` refuses. Its message names no value.
                logger.exception("A notice e-mail could not be written.")
                outcome = "failed"
            if outcome == NOT_CONFIGURED:  # the setting changed under us: nothing was handed over
                _release(notice, version, person_id)
                done.not_configured, done.left = True, len(waiting)
                return done
            sent = outcome == SENT
            NoticeEmail.objects.filter(announcement=notice, version=version, person_id=person_id).update(
                outcome=Outcome.SENT if sent else Outcome.FAILED
            )
            done.sent += sent
            done.failed += not sent
            done.first_failed += not sent and attempt == FIRST_ATTEMPT
            if not sent:  # the connection may be the thing that broke: the next e-mail starts on a new one
                _close(connection)
                _open(connection)
    finally:
        _close(connection)
    # A row still "pending" is another sender's, alive or dead, and a row that failed waits for its
    # next attempt: either way that person has no e-mail yet, and the round must stay open so that
    # the row can be taken again. A row that used all its attempts no longer counts.
    unfinished = NoticeEmail.objects.filter(_open_rows(), announcement=notice, version=version).count()
    done.left = len(waiting) - handled + unfinished
    if not done.left:
        # Only if the notice is still what this round was computed for. One that became urgent
        # meanwhile has people this call never looked at: its round stays open for the next pass.
        Announcement.objects.filter(pk=notice.pk, version=version, priority=priority).update(emailed_version=version)
    return done


def send_due(base_url: str, *, days: int = WINDOW_DAYS, budget_seconds: float | None = None) -> dict:
    """Every open round (``open_rounds``): the notices whose day came, what a publish left above its
    cap or its budget, and what failed. What ``send_notice_emails --apply`` and the scheduler's pass
    run. Counts only: ``notices`` looked at, ``sent``, ``failed`` (``first_failed`` of them on their
    first attempt), ``left``, and ``not_configured`` when the server has no e-mail (then nothing was
    sent and the rest was not looked at).
    ``budget_seconds`` bounds the whole call, so that a mail server that hangs cannot hold the
    scheduler: what is left waits for the next call."""
    started = time.monotonic()
    notices = [notice for notice in open_rounds(days) if is_mailable(notice)]
    totals = {"notices": len(notices), "sent": 0, "failed": 0, "first_failed": 0, "left": 0, "not_configured": False}
    for notice in notices:
        while True:
            remaining = None if budget_seconds is None else budget_seconds - (time.monotonic() - started)
            if remaining is not None and remaining <= 0:
                totals["left"] += waiting_count(notice)
                break
            done = deliver(notice.pk, base_url, budget_seconds=remaining)
            if done.not_configured:
                totals["not_configured"] = True
                return totals
            totals["sent"] += done.sent
            totals["failed"] += done.failed
            totals["first_failed"] += done.first_failed
            # Again only while a full batch went out: what is left then is the part above the cap.
            if not done.left or done.failed or not done.sent:
                totals["left"] += done.left
                break
    return totals


def health(done: dict, was_failing: bool) -> str:
    """Whether a pass of ``send_due`` shows that e-mail is getting through: empty, or
    ``ALL_SENDS_FAILED``. A pass with nothing to send is fine. One that sent something is fine. One
    where nothing went out and a first attempt failed is not (wrong credentials, a server that
    refuses everything); retries that fail alone are not news, they are one address the server will
    not take. Once failing, it stays so until an e-mail of a notice goes out, from a pass or from a
    publish: a pass that only waits for its next attempt proves nothing."""
    if done["sent"]:
        return ""
    if done["first_failed"]:
        return ALL_SENDS_FAILED
    if not was_failing:
        return ""
    attempted = NoticeEmail.objects.filter(last_attempt_at__isnull=False)
    last_failure = attempted.filter(outcome=Outcome.FAILED).order_by("-last_attempt_at").values_list("last_attempt_at", flat=True).first()
    if last_failure is None or attempted.filter(outcome=Outcome.SENT, last_attempt_at__gt=last_failure).exists():
        return ""
    return ALL_SENDS_FAILED


def gave_up(notice: Announcement) -> bool:
    """Some e-mail of the notice's current version failed ``MAX_ATTEMPTS`` times and will not be tried again."""
    rows = NoticeEmail.objects.filter(announcement=notice, version=notice.version)
    return rows.filter(outcome=Outcome.FAILED, attempts__gte=MAX_ATTEMPTS).exists()


def schedule(notice: Announcement, base_url: str | None) -> None:
    """After the current transaction commits, send the notice's e-mails. Without a ``base_url``
    there is no address to put in the e-mail: it is left for the command. A failure here is logged
    and never reaches who published (``robust``)."""
    if not base_url:
        return
    notice_id = notice.pk

    def send_after_commit() -> None:
        deliver(notice_id, base_url, budget_seconds=PUBLISH_BUDGET_SECONDS)

    # A named function: when a robust callback fails, Django logs its ``__qualname__``, which a partial lacks.
    transaction.on_commit(send_after_commit, robust=True)


def sent_count(notice: Announcement) -> tuple[int, int, bool]:
    """(e-mails the server accepted for the notice's current version, people with an account it
    is for, whether any failed). Numbers only."""
    rows = NoticeEmail.objects.filter(announcement=notice, version=notice.version)
    return (
        rows.filter(outcome=Outcome.SENT).count(),
        services._addressed_to(notice).count(),
        rows.filter(outcome=Outcome.FAILED).exists(),
    )
