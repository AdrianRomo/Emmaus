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
by ``manage.py send_notice_emails``. A version's round ends when everybody it was for has a row
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


def _retriable() -> Q:
    """The rows that may be taken again: the mail failed, or its sender died before saying how it went."""
    stale = timezone.now() - timedelta(minutes=PENDING_STALE_MINUTES)
    return Q(outcome=Outcome.FAILED) | Q(outcome=Outcome.PENDING, created_at__lt=stale)


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


def _claim(notice: Announcement, version: int, person_id: int) -> bool:
    """Take the one right to mail this person about this version. False: somebody else has it."""
    try:
        with transaction.atomic():
            NoticeEmail.objects.create(announcement=notice, version=version, person_id=person_id)
        return True
    except IntegrityError:
        # A row exists. Only one that failed, or whose sender died, may be tried again, and only by
        # one writer: the update is conditional on the row's state and age, and restarts its age.
        retry = NoticeEmail.objects.filter(_retriable(), announcement=notice, version=version, person_id=person_id)
        return bool(retry.update(outcome=Outcome.PENDING, created_at=timezone.now()))


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
            if not _claim(notice, version, person_id):
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
            if not sent:  # the connection may be the thing that broke: the next e-mail starts on a new one
                _close(connection)
                _open(connection)
    finally:
        _close(connection)
    # A row still "pending" is another sender's, alive or dead: either way that person has no e-mail
    # yet, and the round must stay open so that the row can be taken again if its sender died.
    others = NoticeEmail.objects.filter(announcement=notice, version=version, outcome=Outcome.PENDING).count()
    done.left = len(waiting) - handled + done.failed + others
    if not done.left:
        # Only if the notice is still what this round was computed for. One that became urgent
        # meanwhile has people this call never looked at: its round stays open for the next pass.
        Announcement.objects.filter(pk=notice.pk, version=version, priority=priority).update(emailed_version=version)
    return done


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
