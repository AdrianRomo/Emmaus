"""What a person may read of the church's notices, and every write to one. Each write is one
transaction that saves the row and one church audit row. The audit row says who the notice is for
and carries the group's id; it never carries the title or the body.

Callers have already answered who may write (``notices.views``); these functions enforce what
holds whoever asks: the group is of the same church and active, the dates make sense, the body fits,
and only the administration (``by_admin``) makes a notice urgent.

A notice has a level (``Announcement.Priority``). The louder ones come first in every list; an
urgent one also waits at the top of "Mi iglesia" until the person answers "Enterado" or it expires.
That answer is a mark on ``AnnouncementRead``: it is counted for the author and never listed by name.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, QuerySet, When
from django.utils import timezone

from groups.models import Group, GroupMembership
from people.audit import record_church
from people.models import Person

from .models import Announcement, AnnouncementRead


class NoticeError(ValidationError):
    """A rule of the notices refused the action; the message is ready for the screen."""


Priority = Announcement.Priority
_LOUDEST_FIRST = Case(
    When(priority=Priority.URGENT, then=0), When(priority=Priority.IMPORTANT, then=1), default=2, output_field=IntegerField()
)


# ---- reading ------------------------------------------------------------------------------------
def visible_to(person: Person, *, is_admin: bool) -> QuerySet[Announcement]:
    """The current notices ``person`` may read: the whole church's, and those of the active groups
    they are in (every group, for the administration). Urgent first, then important, then the rest;
    inside a level, newest ``publish_on`` first, then newest id."""
    today = timezone.localdate()
    for_groups = Q(group__status=Group.Status.ACTIVE)
    if not is_admin:
        mine = GroupMembership.objects.filter(person=person, status=GroupMembership.Status.ACTIVE).values("group_id")
        for_groups &= Q(group_id__in=mine)
    return (
        Announcement.objects.filter(church_id=person.church_id, status=Announcement.Status.PUBLISHED, publish_on__lte=today)
        .filter(Q(expires_on__isnull=True) | Q(expires_on__gte=today))
        .filter(Q(group__isnull=True) | for_groups)
        .select_related("group")
        .order_by(_LOUDEST_FIRST, "-publish_on", "-id")
    )


def _unread(person: Person, *, is_admin: bool) -> QuerySet[Announcement]:
    read = AnnouncementRead.objects.filter(announcement=OuterRef("pk"), person=person)
    return visible_to(person, is_admin=is_admin).annotate(is_unread=~Exists(read))


def unread_count(person: Person, *, is_admin: bool) -> int:
    return _unread(person, is_admin=is_admin).filter(is_unread=True).count()


def for_home(person: Person, *, is_admin: bool, limit: int = 3, skip=()) -> list[Announcement]:
    """The first notices the person may read, each with ``is_unread``. ``skip``: notices already
    shown above (``urgent_pending``), so the home does not say one thing twice."""
    notices = _unread(person, is_admin=is_admin)
    if skip:
        notices = notices.exclude(pk__in=[notice.pk for notice in skip])
    return list(notices[:limit])


def urgent_pending(person: Person, *, is_admin: bool, limit: int = 3) -> list[Announcement]:
    """The urgent notices the person may read and has not answered "Enterado" to. Not their own:
    who published it does not need to be told."""
    answered = AnnouncementRead.objects.filter(announcement=OuterRef("pk"), person=person, acknowledged_at__isnull=False)
    notices = visible_to(person, is_admin=is_admin).filter(priority=Priority.URGENT).exclude(Exists(answered))
    if person.user_id:
        notices = notices.exclude(created_by_id=person.user_id)
    return list(notices[:limit])


def acknowledged_at(notice: Announcement, person: Person):
    """When the person answered "Enterado" to this notice, or None."""
    return (
        AnnouncementRead.objects.filter(announcement=notice, person=person)
        .values_list("acknowledged_at", flat=True)
        .first()
    )


def _addressed_to(notice: Announcement) -> QuerySet[Person]:
    """The people the notice is for who have an account they can sign in with: the whole church, or
    the group's active members. Without its author."""
    people = Person.objects.filter(
        church_id=notice.church_id, merged_into__isnull=True, user__isnull=False, user__is_active=True
    )
    if notice.group_id:
        members = GroupMembership.objects.filter(group_id=notice.group_id, status=GroupMembership.Status.ACTIVE)
        people = people.filter(pk__in=members.values("person_id"))
    if notice.created_by_id:
        people = people.exclude(user_id=notice.created_by_id)
    return people


def acknowledgement_count(notice: Announcement) -> tuple[int, int]:
    """(how many answered "Enterado", how many the notice is for and have an account). Numbers
    only: nobody reads who has not answered. It is not a proof of delivery and no screen says so."""
    people = _addressed_to(notice)
    answered = AnnouncementRead.objects.filter(announcement=notice, acknowledged_at__isnull=False, person__in=people)
    return answered.count(), people.count()


def published_by(user, church, limit: int = 50) -> list[Announcement]:
    """Everything ``user`` published in ``church``, whatever its state: scheduled, expired and retired too."""
    return list(Announcement.objects.filter(church=church, created_by=user).select_related("group")[:limit])


def mark_read(notice: Announcement, person: Person) -> bool:
    """The person opened the notice. Idempotent; True the first time."""
    try:
        with transaction.atomic():
            _, created = AnnouncementRead.objects.get_or_create(announcement=notice, person=person)
    except IntegrityError:  # two tabs at once: the other one wrote it
        return False
    return created


def acknowledge(notice: Announcement, person: Person) -> bool:
    """The person answered "Enterado" to an urgent notice. Idempotent; True the first time. Like
    reading, it is the person's own mark and leaves no audit row."""
    if not notice.is_urgent:
        raise NoticeError("Este aviso no pide respuesta.", code="priority")
    mark_read(notice, person)
    rows = AnnouncementRead.objects.filter(announcement=notice, person=person, acknowledged_at__isnull=True)
    return bool(rows.update(acknowledged_at=timezone.now()))


# ---- writing ------------------------------------------------------------------------------------
def _clean(title: str, body: str, publish_on, expires_on) -> tuple[str, str]:
    title = " ".join((title or "").split())
    body = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not title:
        raise NoticeError("Escribe un título para el aviso.", code="title")
    if len(title) > 120:
        raise NoticeError("El título puede tener hasta 120 caracteres. Acórtalo.", code="title")
    if not body:
        raise NoticeError("Escribe el aviso.", code="body")
    if len(body) > Announcement.BODY_MAX:
        raise NoticeError(
            f"El aviso puede tener hasta {Announcement.BODY_MAX} caracteres y este tiene {len(body)}. Acórtalo.", code="body"
        )
    if expires_on and expires_on < publish_on:
        raise NoticeError("El último día del aviso no puede ser antes del día en que se publica.", code="expires_on")
    return title, body


def _level(priority, by_admin: bool) -> str:
    priority = priority or Priority.NORMAL
    if priority not in Priority.values:
        raise NoticeError("Elige el nivel del aviso.", code="priority")
    if priority == Priority.URGENT and not by_admin:
        raise NoticeError("Solo la administración de la iglesia publica avisos urgentes.", code="priority")
    return priority


def _whom(notice: Announcement) -> str:
    return notice.group.name if notice.group_id else "toda la iglesia"


def _audit(notice: Announcement, actor, action: str, verb: str) -> None:
    metadata = {"group_id": notice.group_id} if notice.group_id else {}
    level = {Priority.URGENT: " urgente", Priority.IMPORTANT: " importante"}.get(notice.priority, "")
    record_church(notice.church, actor, action, f"{verb} un aviso{level} para {_whom(notice)}", notice, **metadata)


def publish(
    church, actor, *, title: str, body: str, group: Group | None = None, publish_on=None, expires_on=None,
    priority: str = Priority.NORMAL, by_admin: bool = False,
) -> Announcement:
    """A new notice, for the whole church or for ``group``. With a ``publish_on`` that has not come,
    nobody reads it until that day. Who publishes has read it already. ``by_admin``: the caller
    answered that ``actor`` is the church's administration; without it an urgent notice is refused."""
    publish_on = publish_on or timezone.localdate()
    title, body = _clean(title, body, publish_on, expires_on)
    priority = _level(priority, by_admin)
    if group is not None and (group.church_id != church.pk or not group.is_active):
        raise NoticeError("Ese grupo ya no recibe avisos. Elige otro.", code="group")
    with transaction.atomic():
        notice = Announcement.objects.create(
            church=church, group=group, title=title, body=body, priority=priority,
            publish_on=publish_on, expires_on=expires_on, created_by=actor,
        )
        author = Person.objects.filter(church=church, user=actor, merged_into__isnull=True).first() if actor else None
        if author is not None:
            AnnouncementRead.objects.create(announcement=notice, person=author)
        _audit(notice, actor, "aviso_publicado", "Publicó")
    return notice


def update(
    notice: Announcement, actor, *, title: str, body: str, publish_on, expires_on=None,
    priority: str | None = None, by_admin: bool = False,
) -> Announcement:
    """Correct a notice. Who it is for does not change; a retired notice is not edited. ``priority``
    None keeps its level (a guide correcting a notice the administration made urgent)."""
    title, body = _clean(title, body, publish_on, expires_on)
    if priority is not None:
        priority = _level(priority, by_admin)
    with transaction.atomic():
        notice = Announcement.objects.select_for_update(of=("self",)).select_related("church", "group").get(pk=notice.pk)
        if notice.is_retired:
            raise NoticeError("Este aviso ya está retirado. Publica uno nuevo.", code="retired")
        notice.title, notice.body, notice.publish_on, notice.expires_on = title, body, publish_on, expires_on
        if priority is not None:
            notice.priority = priority
        notice.save(update_fields=["title", "body", "priority", "publish_on", "expires_on", "updated_at"])
        _audit(notice, actor, "aviso_modificado", "Modificó")
    return notice


def retire(notice: Announcement, actor) -> Announcement:
    """Nobody reads it any more. Nothing is deleted. Idempotent."""
    with transaction.atomic():
        notice = Announcement.objects.select_for_update(of=("self",)).select_related("church", "group").get(pk=notice.pk)
        if notice.is_retired:
            return notice
        notice.status = Announcement.Status.RETIRED
        notice.save(update_fields=["status", "updated_at"])
        _audit(notice, actor, "aviso_retirado", "Retiró")
    return notice
