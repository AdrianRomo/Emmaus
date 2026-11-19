"""Every write of this app: the download's count and audit row, and the request to be removed.

Nothing here deletes or changes a row of another app. An audit row carries ids and fixed words only."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from events.services import throttle
from people.audit import record_church
from people.models import ChurchAuditEvent, Person

from . import registry
from .models import RemovalRequest

DOWNLOADS_PER_DAY = 2
DOWNLOAD_ACTION = "mis_datos_descargados"
# Wrong passwords at the door of the download: 5, then a block that doubles (as the sign-in's own limit).
PASSWORD_LIMIT = {"limit": 5, "window_seconds": 15 * 60, "block_seconds": 5 * 60, "max_block_seconds": 60 * 60}


class TooManyDownloads(Exception):
    """The account already took its downloads of the day in this church."""

    def __init__(self, again_at):
        super().__init__("Too many downloads.")
        self.again_at = again_at


class RequestError(Exception):
    pass


def may_download(person) -> bool:
    """A known minor asks through the church, with a guardian (the rule of ``oracion.access.known_minor``)."""
    from oracion.access import known_minor

    return person is not None and not person.merged_into_id and not known_minor(person)


def password_wait(user) -> int:
    return throttle.blocked_for(f"mis-datos-clave:{user.pk}")


def check_password(user, password: str) -> tuple[bool, int]:
    """(right, seconds to wait). An account without a password cannot pass: it creates one first."""
    key = f"mis-datos-clave:{user.pk}"
    wait = throttle.blocked_for(key)
    if wait:
        return False, wait
    if user.has_usable_password() and password and user.check_password(password):
        throttle.reset(key)
        return True, 0
    return False, throttle.consume(key, **PASSWORD_LIMIT)


def _recent_downloads(church, user, now):
    return ChurchAuditEvent.objects.filter(
        church=church, actor=user, action=DOWNLOAD_ACTION, created_at__gt=now - timedelta(days=1)
    ).order_by("created_at")


def downloads_left(church, user, now=None) -> int:
    return max(DOWNLOADS_PER_DAY - _recent_downloads(church, user, now or timezone.now()).count(), 0)


def build_download(church, person, user, texts, *, kind: str) -> dict:
    """The content of one download, counted and audited in the same transaction that builds it. The account
    row is locked first, so two requests at once cannot both be "the second of the day"."""
    now = timezone.now()
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        recent = list(_recent_downloads(church, user, now)[:DOWNLOADS_PER_DAY])
        if len(recent) >= DOWNLOADS_PER_DAY:
            raise TooManyDownloads(recent[0].created_at + timedelta(days=1))
        ctx = registry.Ctx(church, person, user, frozenset(texts) & set(registry.TEXT_KINDS))
        content = registry.export(ctx, now=now)
        record_church(
            church, user, DOWNLOAD_ACTION, "Descargó sus propios datos", person,
            person_id=person.pk, method=kind, value=",".join(sorted(ctx.texts)) or "sin_textos",
        )
    return content


# ---- asking to be removed ------------------------------------------------------------------------
def open_request(church, user):
    return RemovalRequest.objects.filter(church=church, user=user, status=RemovalRequest.Status.OPEN).first()


def ask_removal(church, person, user, ask: str) -> tuple[RemovalRequest, bool]:
    """One open request per account and church. Asking again while one waits changes nothing."""
    if ask not in RemovalRequest.Ask.values:
        raise RequestError("Elige qué quieres pedir.")
    try:
        with transaction.atomic():
            existing = RemovalRequest.objects.select_for_update().filter(
                church=church, user=user, status=RemovalRequest.Status.OPEN
            ).first()
            if existing is not None:
                return existing, False
            row = RemovalRequest.objects.create(church=church, user=user, ask=ask)
            record_church(church, user, "mis_datos_borrado_pedido", "Pidió que borren sus datos", row, person_id=person.pk, value=ask)
            return row, True
    except IntegrityError:  # two at once: the constraint kept one
        return RemovalRequest.objects.get(church=church, user=user, status=RemovalRequest.Status.OPEN), False


def _close(church, request_id: int, actor, status: str, *, own_only: bool) -> RemovalRequest | None:
    """Lock, read again, then decide: a request somebody already closed is left as it is (None)."""
    with transaction.atomic():
        rows = RemovalRequest.objects.select_for_update().filter(pk=request_id, church=church)
        if own_only:
            rows = rows.filter(user=actor)
        row = rows.first()
        if row is None or not row.is_open:
            return None
        row.status, row.closed_at, row.closed_by = status, timezone.now(), None if own_only else actor
        row.save(update_fields=["status", "closed_at", "closed_by"])
        person = person_of(church, row.user)
        record_church(
            church, actor,
            "mis_datos_borrado_retirado" if own_only else "mis_datos_borrado_atendido",
            "Retiró su solicitud de borrar sus datos" if own_only else "Marcó como atendida una solicitud de borrar datos",
            row, **({"person_id": person.pk} if person else {}),
        )
        return row


def withdraw(church, user, request_id: int) -> RemovalRequest | None:
    return _close(church, request_id, user, RemovalRequest.Status.WITHDRAWN, own_only=True)


def mark_done(church, request_id: int, actor) -> RemovalRequest | None:
    return _close(church, request_id, actor, RemovalRequest.Status.DONE, own_only=False)


def person_of(church, user) -> Person | None:
    """The record ``user`` is in ``church`` today (never one that was joined into another)."""
    return Person.objects.filter(church=church, user=user, merged_into__isnull=True).first()


def open_requests(church):
    return RemovalRequest.objects.filter(church=church, status=RemovalRequest.Status.OPEN).select_related("user")


def open_count(church) -> int:
    return open_requests(church).count()
