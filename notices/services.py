"""What a person may read of the church's notices, and every write to one. Each write is one
transaction that saves the row and one church audit row. The audit row says who the notice is for
and carries the group's id; it never carries the title or the body.

Callers have already answered who may write (``notices.views``); these functions enforce what
holds whoever asks: the group is of the same church and active, the dates make sense, the body fits,
and only who holds a church role the church's policy names (``people.policy.urgent_notice_roles``;
today the administration) makes a notice urgent. That one is answered here, from the actor's own
role in that church, not taken from the caller.

A notice has a level (``Announcement.Priority``). The louder ones come first in every list; an
urgent one also waits at the top of "Mi iglesia" until the person answers "Enterado" or it expires.
That answer is a mark on ``AnnouncementRead``: it is counted for the author and never listed by name.

An urgent notice has versions. An answer is to one version and counts only while that is the
notice's version. Who changes the words of an urgent notice that people may have read says whether
the information changed: if it did, the version goes up, everybody is asked again and the count
starts over; if it was a detail, the answers stay (decision 0015).
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, Exists, F, IntegerField, OuterRef, Q, QuerySet, When
from django.utils import timezone

from core.models import Role, UserChurchRole
from groups.models import Group, GroupMembership
from people import policy
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
    shown above (``urgent_pending``), so the home does not say one thing twice. Three on purpose:
    the home is a glance and "Ver todos los avisos" under them leads to the whole list, paged."""
    notices = _unread(person, is_admin=is_admin)
    if skip:
        notices = notices.exclude(pk__in=[notice.pk for notice in skip])
    return list(notices[:limit])


def readable(person: Person, *, is_admin: bool) -> QuerySet[Announcement]:
    """Every notice the person may read, each with ``is_unread``, in the order of "Avisos": for a paged list."""
    return _unread(person, is_admin=is_admin)


def urgent_pending(person: Person, *, is_admin: bool, limit: int = 3) -> list[Announcement]:
    """The urgent notices the person may read and has not answered "Enterado" to, in the version
    they have now. Not their own: who published it does not need to be told. Three at most on
    purpose: each is a red panel above everything else; all of them are in "Avisos", urgent first."""
    answered = _answers().filter(announcement=OuterRef("pk"), person=person)
    notices = visible_to(person, is_admin=is_admin).filter(priority=Priority.URGENT).exclude(Exists(answered))
    if person.user_id:
        notices = notices.exclude(created_by_id=person.user_id)
    return list(notices[:limit])


def _answers() -> QuerySet[AnnouncementRead]:
    """The "Enterado" that count: those given to the version the notice has now."""
    return AnnouncementRead.objects.filter(acknowledged_at__isnull=False, acknowledged_version=F("announcement__version"))


def acknowledged_at(notice: Announcement, person: Person):
    """When the person answered "Enterado" to this notice as it is now, or None."""
    return _answers().filter(announcement=notice, person=person).values_list("acknowledged_at", flat=True).first()


def read_before_it_changed(notice: Announcement, person: Person) -> bool:
    """The person had opened the notice before its last change that asked for "Enterado" again."""
    if notice.changed_at is None:
        return False
    return AnnouncementRead.objects.filter(announcement=notice, person=person, read_at__lt=notice.changed_at).exists()


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
    answered = _answers().filter(announcement=notice, person__in=people)
    return answered.count(), people.count()


def published_by(user, church) -> QuerySet[Announcement]:
    """Everything ``user`` published in ``church``, whatever its state: scheduled, expired and retired too.
    Newest first (the model's order); the screen pages it."""
    return Announcement.objects.filter(church=church, created_by=user).select_related("group")


def mark_read(notice: Announcement, person: Person) -> bool:
    """The person opened the notice. Idempotent; True the first time."""
    try:
        with transaction.atomic():
            _, created = AnnouncementRead.objects.get_or_create(announcement=notice, person=person)
    except IntegrityError:  # two tabs at once: the other one wrote it
        return False
    return created


def acknowledge(notice: Announcement, person: Person, *, version: int | None = None) -> bool:
    """The person answered "Enterado" to an urgent notice, in the version it has now. Idempotent;
    True the first time for that version. An answer to an earlier version is replaced: only the
    latest is kept. ``version``: the one the person had in front of them; if the notice changed
    since, the answer is refused, because it would be to words they have not read. Like reading, it
    is the person's own mark and leaves no audit row."""
    current = Announcement.objects.filter(pk=notice.pk).values_list("priority", "version").first()
    if current is None or current[0] != Priority.URGENT:
        raise NoticeError("Este aviso no pide respuesta.", code="priority")
    if version is not None and version != current[1]:
        raise NoticeError("Este aviso cambió mientras lo leías. Léelo otra vez y toca «Enterado».", code="changed")
    mark_read(notice, person)
    rows = AnnouncementRead.objects.filter(announcement=notice, person=person).filter(
        Q(acknowledged_at__isnull=True) | Q(acknowledged_version__isnull=True) | ~Q(acknowledged_version=current[1])
    )
    return bool(rows.update(acknowledged_at=timezone.now(), acknowledged_version=current[1]))


# ---- writing ------------------------------------------------------------------------------------
def _clean(title: str, body: str, publish_on, expires_on) -> tuple[str, str]:
    title, body = clean_title(title), clean_body(body)
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


def may_publish_urgent(user, church) -> bool:
    """Whether ``user`` holds, in ``church``, a church role its policy lets publish urgent notices."""
    if user is None or church is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    role = UserChurchRole.objects.filter(church=church, user=user).values_list("role", flat=True).first()
    return role is not None and role in policy.urgent_notice_roles(church)


def _level(priority, actor, church) -> str:
    priority = priority or Priority.NORMAL
    if priority not in Priority.values:
        raise NoticeError("Elige el nivel del aviso.", code="priority")
    if priority == Priority.URGENT and not may_publish_urgent(actor, church):
        if policy.urgent_notice_roles(church) == {Role.CHURCH_ADMIN}:
            raise NoticeError("Solo la administración de la iglesia publica avisos urgentes.", code="priority")
        raise NoticeError("En esta iglesia no tienes permiso para publicar avisos urgentes.", code="priority")
    return priority


def asks_on_change(notice: Announcement) -> bool:
    """Whether changing the words of this notice needs the answer to "¿Cambió la información?":
    it is urgent and somebody may have read it (its day has come) or has answered "Enterado"."""
    if not notice.is_urgent:
        return False
    if notice.publish_on <= timezone.localdate():
        return True
    return _answers().filter(announcement=notice).exists()


def clean_title(title: str) -> str:
    return " ".join((title or "").split())


def clean_body(body: str) -> str:
    """A browser sends each line break as two characters; a person counts one."""
    return (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def words_changed(notice: Announcement, title: str, body: str) -> bool:
    return (clean_title(title), clean_body(body)) != (notice.title, notice.body)


def _whom(notice: Announcement) -> str:
    return notice.group.name if notice.group_id else "toda la iglesia"


def _audit(notice: Announcement, actor, action: str, verb: str) -> None:
    metadata = {"group_id": notice.group_id} if notice.group_id else {}
    level = {Priority.URGENT: " urgente", Priority.IMPORTANT: " importante"}.get(notice.priority, "")
    record_church(notice.church, actor, action, f"{verb} un aviso{level} para {_whom(notice)}", notice, **metadata)


def _email(notice: Announcement, base_url: str | None) -> None:
    from . import email  # imported here: ``notices.email`` reads who a notice is for from this module

    email.schedule(notice, base_url)


def publish(
    church, actor, *, title: str, body: str, group: Group | None = None, publish_on=None, expires_on=None,
    priority: str = Priority.NORMAL, base_url: str | None = None,
) -> Announcement:
    """A new notice, for the whole church or for ``group``. With a ``publish_on`` that has not come,
    nobody reads it until that day. Who publishes has read it already. An urgent one is refused
    unless ``actor`` holds in ``church`` a role its policy allows (``may_publish_urgent``).

    ``base_url``: the address of this Emmaus (the view takes it from the request). With it, who chose
    to hear about notices by e-mail is mailed after the transaction commits (``notices.email``);
    without it, or for a later day, that is left to ``manage.py send_notice_emails``."""
    publish_on = publish_on or timezone.localdate()
    title, body = _clean(title, body, publish_on, expires_on)
    priority = _level(priority, actor, church)
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
        _email(notice, base_url)
    return notice


def update(
    notice: Announcement, actor, *, title: str, body: str, publish_on, expires_on=None,
    priority: str | None = None, material: bool | None = None, base_url: str | None = None,
) -> Announcement:
    """Correct a notice. Who it is for does not change; a retired notice is not edited. ``priority``
    None keeps its level (a guide correcting a notice the administration made urgent).

    ``material`` answers "¿Cambió la información?" and is needed when the title or the body of an
    urgent notice changes, it stays urgent and ``asks_on_change``: True makes a new version (every
    "Enterado" given stops counting and everybody is asked again), False keeps the answers, None is
    refused. In any other edit it is not heard. A notice that becomes urgent starts with no answers.

    E-mail (``notices.email``, with ``base_url``): a new version is mailed again; a notice that
    becomes urgent is mailed to who had not been mailed; a notice that waited for a later day and now
    can be read is mailed. Any other edit sends nothing."""
    title, body = _clean(title, body, publish_on, expires_on)
    with transaction.atomic():
        notice = Announcement.objects.select_for_update(of=("self",)).select_related("church", "group").get(pk=notice.pk)
        if priority is not None:
            priority = _level(priority, actor, notice.church)
        if notice.is_retired:
            raise NoticeError("Este aviso ya está retirado. Publica uno nuevo.", code="retired")
        was_urgent = notice.is_urgent
        was_waiting = notice.publish_on > timezone.localdate()
        stays_urgent = was_urgent and priority in (None, Priority.URGENT)
        asks_again = False
        if stays_urgent and words_changed(notice, title, body) and asks_on_change(notice):
            if material is None:
                raise NoticeError(
                    "Cambiaste lo que dice un aviso urgente. Elige si cambió la información, para saber si pedimos «Enterado» otra vez.",
                    code="material",
                )
            asks_again = bool(material)
        notice.title, notice.body, notice.publish_on, notice.expires_on = title, body, publish_on, expires_on
        if priority is not None:
            notice.priority = priority
        if asks_again:
            notice.version += 1
            notice.changed_at = timezone.now()
        elif notice.is_urgent and not was_urgent:
            # Urgent again after not being it: what was answered then was to another moment of the notice.
            notice.changed_at = None
            AnnouncementRead.objects.filter(announcement=notice).update(acknowledged_version=None)
        became_urgent = notice.is_urgent and not was_urgent
        if became_urgent:
            notice.emailed_version = None  # who asked only for the urgent ones has not been mailed
            notice.email_reopened_at = timezone.now()
        notice.save(
            update_fields=[
                "title", "body", "priority", "publish_on", "expires_on", "version", "changed_at", "emailed_version", "email_reopened_at",
                "updated_at",
            ]
        )
        _audit(notice, actor, "aviso_modificado", "Modificó")
        if asks_again:
            metadata = {"group_id": notice.group_id} if notice.group_id else {}
            record_church(
                notice.church, actor, "aviso_enterado_pedido",
                f"Cambió la información de un aviso urgente para {_whom(notice)} y pidió «Enterado» otra vez", notice, **metadata,
            )
        if asks_again or became_urgent or was_waiting:
            _email(notice, base_url)
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
