"""What the navigation shows about prayer requests (``core.shell``). It decides what is shown; the
views decide again what is allowed."""

from . import access, services
from .models import PrayerRequest


def shows_writer_link(church, person) -> bool:
    """A door for who may write one today, or who still has one of their own to read or delete."""
    if services.is_on(church):
        if access.may_write(person):
            return True
    return PrayerRequest.objects.filter(church=church, person=person).exists()


def reader_badge(user, church) -> int | None:
    """None for anybody who is not a pastor of ``church`` (no link at all); else how many requests
    addressed to them nobody marked as read. A number of things to open, shown to nobody else."""
    if not access.is_reader(user, church):
        return None
    return services.unread_count(user, church)
