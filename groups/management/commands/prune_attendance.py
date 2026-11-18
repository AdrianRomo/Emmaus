"""Retention of group attendance: who came is kept for a while, the totals a while longer.

* Meetings older than ``EMMAUS_ATTENDANCE_IDENTIFIABLE_MONTHS`` (12): the meeting keeps its totals
  (``present_kept`` and ``expected``) and the rows that say who came are deleted.
* Meetings older than ``EMMAUS_ATTENDANCE_AGGREGATE_MONTHS`` (24): the meeting is deleted.

Both periods are product defaults to be checked against each church's privacy notice, not legal
deadlines. Without ``--apply`` nothing is written: the command only counts. Running it again finds
nothing left to do. It prints counts and never a name. Nothing schedules it: an operator runs it.
"""

from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from groups.models import GroupAttendance, GroupMeeting


def months_before(day: date, months: int) -> date:
    """The same day ``months`` earlier; the last day of that month when it is shorter."""
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    for last in (day.day, 30, 29, 28):
        try:
            return date(year, month, last)
        except ValueError:
            continue
    raise ValueError("No such month.")  # pragma: no cover


def prune(today: date, *, apply: bool) -> dict[str, int]:
    """Counts of what is (or would be) done. One transaction when ``apply``."""
    identifiable = settings.EMMAUS_ATTENDANCE_IDENTIFIABLE_MONTHS
    aggregate = settings.EMMAUS_ATTENDANCE_AGGREGATE_MONTHS
    if identifiable < 1 or aggregate < identifiable:
        raise CommandError(
            "Revisa EMMAUS_ATTENDANCE_IDENTIFIABLE_MONTHS y EMMAUS_ATTENDANCE_AGGREGATE_MONTHS: "
            "el primero debe ser al menos 1 y el segundo no puede ser menor que el primero."
        )
    names_before = months_before(today, identifiable)
    meetings_before = months_before(today, aggregate)
    with transaction.atomic():
        expired = GroupMeeting.objects.filter(date__lt=meetings_before)
        aged = GroupMeeting.objects.filter(date__lt=names_before, date__gte=meetings_before, present_kept__isnull=True)
        to_totals = list(aged.select_for_update().order_by("id"))
        names = GroupAttendance.objects.filter(
            meeting__date__lt=names_before, meeting__date__gte=meetings_before, meeting__present_kept__isnull=True
        )
        came = dict(names.values_list("meeting_id").annotate(total=Count("id")).order_by())
        result = {
            "meetings_to_totals": len(to_totals),
            "names_deleted": sum(came.values()),
            "meetings_deleted": expired.count(),
            "names_deleted_with_meetings": GroupAttendance.objects.filter(meeting__date__lt=meetings_before).count(),
        }
        if apply:
            # The names go and the totals are written in the same transaction: a meeting never loses both.
            names.delete()
            for meeting in to_totals:
                meeting.present_kept = came.get(meeting.pk, 0)
                meeting.expected = max(meeting.expected, meeting.present_kept)
            GroupMeeting.objects.bulk_update(to_totals, ["present_kept", "expected"], batch_size=500)
            expired.delete()
    return result


class Command(BaseCommand):
    help = (
        "Borra la asistencia de grupos que ya cumplió su tiempo: después de 12 meses conserva solo los totales de la "
        "reunión; después de 24 borra la reunión. Sin --apply solo cuenta."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Escribe los cambios. Sin esta opción no se borra nada.")

    def handle(self, *args, **opts):
        apply = opts["apply"]
        result = prune(timezone.localdate(), apply=apply)
        verb = "Se hizo" if apply else "Se haría (ensayo: no se escribió nada; usa --apply para aplicarlo)"
        self.stdout.write(f"{verb}:")
        self.stdout.write(
            f"  Reuniones de más de {settings.EMMAUS_ATTENDANCE_IDENTIFIABLE_MONTHS} meses que conservan solo sus totales: "
            f"{result['meetings_to_totals']} (registros de quién vino borrados: {result['names_deleted']})"
        )
        self.stdout.write(
            f"  Reuniones de más de {settings.EMMAUS_ATTENDANCE_AGGREGATE_MONTHS} meses borradas: "
            f"{result['meetings_deleted']} (registros de quién vino borrados con ellas: {result['names_deleted_with_meetings']})"
        )
