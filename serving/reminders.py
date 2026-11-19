"""A reminder by e-mail that a date to serve is near (docs/v2/wave10/rotas.md).

Off until who coordinates a team turns it on (``Team.reminder_days``: 2 or 5 days before). The
e-mail goes through ``core.notify`` and says only that a date is near in the person's church, with
the address of "Servir", which asks for the account: never the team, the day, the role, who else
serves or anything somebody wrote.

Who is mailed: a person asked for a date that has not started and is at most ``reminder_days`` days
away, who has not said no, whose record is not joined to another, with an account they can sign in
with and an e-mail in "Mis datos", and who has not turned reminders off ("Servir"). Only in a
church that is active and uses the ``community`` module.

Once per assignment: the ``ServiceReminder`` row is written before the e-mail is handed over and
its one-to-one key lets one writer win (as ``notices.email`` does for a notice). A mail that failed
is tried again on a later pass, ``MAX_ATTEMPTS`` times at most, each after a longer wait; a row left
"pending" by a sender that died may be taken again after ``PENDING_STALE_MINUTES``. As there, a send
that failed after the message was handed over cannot be told from one the server refused, so a
retry can deliver a second copy.
"""

from __future__ import annotations

import logging
import time as clock
from datetime import timedelta

from django.core import mail
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from core.notify import NOT_CONFIGURED, SENT, notify
from events.services.team import email_is_configured
from people import flags

from . import rotas
from .models import Assignment, ServiceReminder, ServingPreference, Team

logger = logging.getLogger(__name__)

KIND = "recordatorio_servir"
Outcome = ServiceReminder.Outcome
PENDING_STALE_MINUTES = 10
BACKOFF_MINUTES = (30, 180)
MAX_ATTEMPTS = len(BACKOFF_MINUTES) + 1
SEND_CAP = 500
ALL_SENDS_FAILED = "AllSendsFailed"


def wants_reminders(person) -> bool:
    choice = ServingPreference.objects.filter(person=person).values_list("reminders", flat=True).first()
    return True if choice is None else choice


def set_reminders(person, wanted: bool) -> None:
    """The person's own choice; like the choice about notices, it leaves no audit row."""
    ServingPreference.objects.update_or_create(person=person, defaults={"reminders": bool(wanted)})


def team_sends(person) -> bool:
    """Some team the person is in sends reminders: only then is the choice worth showing."""
    return Team.objects.filter(
        memberships__person=person, memberships__status="activa", status=Team.Status.ACTIVE, reminder_days__gt=0
    ).exists()


def _retriable(now) -> Q:
    waited = Q()
    for attempts, minutes in enumerate(BACKOFF_MINUTES, start=1):
        waited |= Q(reminder__attempts=attempts, reminder__last_attempt_at__lte=now - timedelta(minutes=minutes))
    stale = Q(reminder__outcome=Outcome.PENDING, reminder__created_at__lt=now - timedelta(minutes=PENDING_STALE_MINUTES),
              reminder__attempts__lt=MAX_ATTEMPTS)
    return Q(reminder__outcome=Outcome.FAILED) & waited | stale


def due(now=None) -> list[Assignment]:
    """The assignments a reminder should go out for now (see the module docstring)."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    rows = (
        Assignment.objects.filter(
            service_date__team__status=Team.Status.ACTIVE, service_date__team__reminder_days__gt=0,
            service_date__team__church__active=True, service_date__date__gte=today,
            service_date__date__lte=today + timedelta(days=5),
            person__merged_into__isnull=True, person__user__isnull=False, person__user__is_active=True,
        )
        .exclude(status=Assignment.Status.DECLINED).exclude(person__email="")
        .exclude(person__serving_preference__reminders=False)
        .filter(Q(reminder__isnull=True) | _retriable(now))
        .select_related("service_date__team__church", "person")
        .order_by("service_date__date", "pk")
    )
    churches: dict[int, bool] = {}
    out = []
    for assignment in rows:
        service_date = assignment.service_date
        team = service_date.team
        if (service_date.date - today).days > team.reminder_days or not rotas.is_coming(service_date, now):
            continue
        if team.church_id not in churches:
            churches[team.church_id] = flags.enabled(team.church, "community")
        if churches[team.church_id]:
            out.append(assignment)
    return out


def _claim(assignment_id: int, now) -> int:
    """Take the one right to remind about this assignment. 0: somebody else has it. 1: first attempt. 2: a retry."""
    try:
        with transaction.atomic():
            ServiceReminder.objects.create(assignment_id=assignment_id, attempts=1, last_attempt_at=now, created_at=now)
        return 1
    except IntegrityError:
        waited = Q()
        for attempts, minutes in enumerate(BACKOFF_MINUTES, start=1):
            waited |= Q(attempts=attempts, last_attempt_at__lte=now - timedelta(minutes=minutes))
        stale = Q(outcome=Outcome.PENDING, created_at__lt=now - timedelta(minutes=PENDING_STALE_MINUTES), attempts__lt=MAX_ATTEMPTS)
        taken = ServiceReminder.objects.filter(Q(outcome=Outcome.FAILED) & waited | stale, assignment_id=assignment_id).update(
            outcome=Outcome.PENDING, created_at=now, attempts=F("attempts") + 1, last_attempt_at=now
        )
        return 2 if taken else 0


def link(base_url: str) -> str:
    return f"{base_url.rstrip('/')}{reverse('serving:home')}"


def send_due(base_url: str, *, now=None, budget_seconds: float | None = None) -> dict:
    """Send every reminder that is due. Safe to run again and from two places at once. Counts only."""
    now = now or timezone.now()
    done = {"sent": 0, "failed": 0, "first_failed": 0, "left": 0, "not_configured": False}
    waiting = due(now)
    if not waiting:
        return done
    if not base_url or not email_is_configured():
        done["not_configured"], done["left"] = True, len(waiting)
        return done
    started = clock.monotonic()
    connection = mail.get_connection()
    try:
        for number, assignment in enumerate(waiting):
            if number >= SEND_CAP or (budget_seconds is not None and clock.monotonic() - started > budget_seconds):
                done["left"] = len(waiting) - number
                break
            attempt = _claim(assignment.pk, now)
            if not attempt:
                continue
            context = {"church_name": assignment.service_date.team.church.name, "link": link(base_url)}
            try:
                outcome = notify(KIND, assignment.person.email, context, connection=connection)
            except ValueError:
                logger.exception("A serving reminder could not be written.")  # the message names no value
                outcome = "failed"
            if outcome == NOT_CONFIGURED:  # the setting changed under us: nothing was handed over
                ServiceReminder.objects.filter(assignment_id=assignment.pk, outcome=Outcome.PENDING).delete()
                done["not_configured"], done["left"] = True, len(waiting) - number
                break
            sent = outcome == SENT
            ServiceReminder.objects.filter(assignment_id=assignment.pk).update(outcome=Outcome.SENT if sent else Outcome.FAILED)
            done["sent"] += sent
            done["failed"] += not sent
            done["first_failed"] += not sent and attempt == 1
    finally:
        try:
            connection.close()
        except Exception:
            logger.exception("The connection to the mail server did not close cleanly.")
    return done


def verdict(done: dict) -> str:
    """A pass in which nothing went out and a first attempt failed is a failed pass."""
    return ALL_SENDS_FAILED if not done["sent"] and done["first_failed"] else ""
