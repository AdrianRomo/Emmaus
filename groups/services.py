"""Every write to a group. Each function is one transaction that writes the source row, the
``access.RoleAssignment`` row derived from it (decision 0003, rule 5) and one church audit row.

Callers have already answered the authorization question (``core.authz.has_scope``); these
functions enforce the rules that hold whoever asks: same church, an open group, one pending
request, a group never left without the row that says who guides it being audited.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, F
from django.db.models.functions import Coalesce
from django.utils import timezone

from access.models import RoleAssignment, ScopeType
from people.audit import record_church
from people.models import Person

from .models import Group, GroupAttendance, GroupJoinRequest, GroupMeeting, GroupMembership

SOURCE = "groups.GroupMembership"


class GroupError(ValidationError):
    """A rule of the group refused the action; the message is ready for the screen."""


# ---- the projection -----------------------------------------------------------------------------
def sync_assignment(membership: GroupMembership) -> RoleAssignment | None:
    """Write (or delete) the role row derived from ``membership``. A person without an account
    holds nothing: there is nobody to sign in as them."""
    user_id = membership.person.user_id
    if user_id is None:
        RoleAssignment.objects.filter(source_table=SOURCE, source_id=membership.pk).delete()
        return None
    row, _ = RoleAssignment.objects.update_or_create(
        source_table=SOURCE,
        source_id=membership.pk,
        defaults={
            "subject_user_id": user_id,
            "church_id": membership.group.church_id,
            "scope_type": ScopeType.GROUP,
            "scope_id": membership.group_id,
            "role": membership.role,
            "status": RoleAssignment.Status.ACTIVE if membership.is_active else RoleAssignment.Status.INACTIVE,
            "start_at": membership.start_at,
            "end_at": membership.end_at,
        },
    )
    return row


def sync_person_assignments(sender, instance: Person, **kwargs) -> None:
    """``post_save`` of ``Person``: the account may have been linked or unlinked."""
    for membership in GroupMembership.objects.filter(person=instance).select_related("group", "person"):
        sync_assignment(membership)


def expected_assignments() -> dict[int, dict]:
    """What the projection must hold, keyed by membership id (``rebuild_role_assignments``)."""
    expected = {}
    memberships = GroupMembership.objects.filter(person__user__isnull=False).select_related("group", "person")
    for membership in memberships:
        expected[membership.pk] = {
            "subject_user_id": membership.person.user_id,
            "church_id": membership.group.church_id,
            "scope_type": ScopeType.GROUP,
            "scope_id": membership.group_id,
            "role": membership.role,
            "status": RoleAssignment.Status.ACTIVE if membership.is_active else RoleAssignment.Status.INACTIVE,
            "start_at": membership.start_at,
            "end_at": membership.end_at,
        }
    return expected


# ---- groups -------------------------------------------------------------------------------------
def _same_church(group: Group, person: Person) -> None:
    if person.church_id != group.church_id or person.merged_into_id is not None:
        raise GroupError("Esta persona no es de la iglesia del grupo.", code="other_church")


def save_group(group: Group, actor, *, created: bool) -> Group:
    with transaction.atomic():
        if group.sede_id is not None and group.sede.church_id != group.church_id:
            raise GroupError("Esta sede es de otra iglesia.", code="sede_other_church")
        if created:
            group.created_by = actor
        group.save()
        action = "grupo_creado" if created else "grupo_modificado"
        verb = "Creó" if created else "Modificó"
        record_church(group.church, actor, action, f"{verb} el grupo {group.name}", group, group_id=group.pk)
    return group


def archive_group(group: Group, actor) -> None:
    """The group stops meeting: it leaves the lists, pending requests are closed, and every role in
    it ends (nobody keeps managing a group that no longer exists)."""
    with transaction.atomic():
        now = timezone.now()
        group.status = Group.Status.ARCHIVED
        group.save(update_fields=["status", "updated_at"])
        for request in group.join_requests.filter(status=GroupJoinRequest.Status.PENDING):
            request.status = GroupJoinRequest.Status.WITHDRAWN
            request.decided_at = now
            request.save(update_fields=["status", "decided_at"])
        for membership in group.memberships.filter(status=GroupMembership.Status.ACTIVE).select_related("group", "person"):
            membership.status = GroupMembership.Status.INACTIVE
            membership.end_at = now
            membership.save(update_fields=["status", "end_at", "updated_at"])
            sync_assignment(membership)
        record_church(group.church, actor, "grupo_archivado", f"Archivó el grupo {group.name}", group, group_id=group.pk)


def set_membership(group: Group, person: Person, actor, *, role: str = GroupMembership.Role.MEMBER) -> GroupMembership:
    """Put ``person`` in ``group`` with ``role`` (creating, reactivating or changing the row)."""
    if role not in GroupMembership.Role.values:
        raise ValueError(f"Unknown group role {role!r}.")
    with transaction.atomic():
        _same_church(group, person)
        if not group.is_active:
            raise GroupError("Este grupo está archivado.", code="archived")
        membership = GroupMembership.objects.select_for_update().filter(group=group, person=person).first()
        was_leader = bool(membership and membership.is_active and membership.is_leader)
        if membership is None:
            membership = GroupMembership(group=group, person=person, added_by=actor)
        elif not membership.is_active:
            membership.start_at = timezone.now()
        membership.role = role
        membership.status = GroupMembership.Status.ACTIVE
        membership.end_at = None
        membership.save()
        membership.group, membership.person = group, person
        sync_assignment(membership)
        # Whoever is put in the group no longer waits for an answer.
        GroupJoinRequest.objects.filter(group=group, person=person, status=GroupJoinRequest.Status.PENDING).update(
            status=GroupJoinRequest.Status.ACCEPTED, decided_at=timezone.now(), decided_by=actor
        )
        if role == GroupMembership.Role.LEADER and not was_leader:
            record_church(group.church, actor, "grupo_lider", f"{person.full_name} ahora guía el grupo {group.name}",
                          group, group_id=group.pk, person_id=person.pk, role=role)
        elif was_leader and role != GroupMembership.Role.LEADER:
            record_church(group.church, actor, "grupo_lider", f"{person.full_name} ya no guía el grupo {group.name}",
                          group, group_id=group.pk, person_id=person.pk, role=role)
        else:
            record_church(group.church, actor, "grupo_integrante_agregado", f"Agregó a {person.full_name} al grupo {group.name}",
                          group, group_id=group.pk, person_id=person.pk, role=role)
    return membership


def end_membership(membership: GroupMembership, actor) -> GroupMembership:
    """The person leaves the group (by themselves or taken out). Their role ends now."""
    with transaction.atomic():
        membership = GroupMembership.objects.select_for_update().select_related("group", "person").get(pk=membership.pk)
        if not membership.is_active:
            return membership
        membership.status = GroupMembership.Status.INACTIVE
        membership.end_at = timezone.now()
        membership.save(update_fields=["status", "end_at", "updated_at"])
        sync_assignment(membership)
        group, person = membership.group, membership.person
        record_church(group.church, actor, "grupo_integrante_salio", f"{person.full_name} salió del grupo {group.name}",
                      group, group_id=group.pk, person_id=person.pk)
    return membership


# ---- join requests ------------------------------------------------------------------------------
def request_to_join(group: Group, person: Person, actor) -> tuple[GroupJoinRequest, bool]:
    """Ask to join. Idempotent: asking twice returns the pending request, ``False``."""
    with transaction.atomic():
        _same_church(group, person)
        if not group.is_open:
            raise GroupError("Este grupo no está recibiendo solicitudes por ahora.", code="closed")
        if GroupMembership.objects.filter(group=group, person=person, status=GroupMembership.Status.ACTIVE).exists():
            raise GroupError("Ya eres parte de este grupo.", code="already_member")
        pending = GroupJoinRequest.objects.filter(group=group, person=person, status=GroupJoinRequest.Status.PENDING).first()
        if pending:
            return pending, False
        try:
            with transaction.atomic():
                request = GroupJoinRequest.objects.create(group=group, person=person)
        except IntegrityError:  # two taps at once: the other one created it
            return GroupJoinRequest.objects.get(group=group, person=person, status=GroupJoinRequest.Status.PENDING), False
        record_church(group.church, actor, "grupo_solicitud", f"{person.full_name} pidió unirse al grupo {group.name}",
                      request, group_id=group.pk, person_id=person.pk)
    return request, True


def withdraw_request(request: GroupJoinRequest, actor) -> None:
    with transaction.atomic():
        request = GroupJoinRequest.objects.select_for_update().select_related("group", "person").get(pk=request.pk)
        if not request.is_pending:
            return
        request.status = GroupJoinRequest.Status.WITHDRAWN
        request.decided_at = timezone.now()
        request.save(update_fields=["status", "decided_at"])
        record_church(request.group.church, actor, "grupo_solicitud_retirada",
                      f"{request.person.full_name} retiró su solicitud al grupo {request.group.name}",
                      request, group_id=request.group_id, person_id=request.person_id)


def decide_request(request: GroupJoinRequest, actor, *, accept: bool) -> GroupJoinRequest:
    """Accept (the person becomes a member) or decline. A request already decided is refused, so
    two leaders deciding at once cannot contradict each other silently."""
    with transaction.atomic():
        request = GroupJoinRequest.objects.select_for_update().select_related("group", "person").get(pk=request.pk)
        if not request.is_pending:
            raise GroupError("Esta solicitud ya tenía respuesta.", code="already_decided")
        group, person = request.group, request.person
        if accept:
            if not group.is_active:
                raise GroupError("Este grupo está archivado.", code="archived")
            membership = GroupMembership.objects.select_for_update().filter(group=group, person=person).first()
            if membership is None:
                membership = GroupMembership(group=group, person=person, added_by=actor)
            if not membership.is_active or membership.pk is None:
                membership.start_at = timezone.now()
                membership.role = GroupMembership.Role.MEMBER
            membership.status = GroupMembership.Status.ACTIVE
            membership.end_at = None
            membership.save()
            membership.group, membership.person = group, person
            sync_assignment(membership)
        request.status = GroupJoinRequest.Status.ACCEPTED if accept else GroupJoinRequest.Status.DECLINED
        request.decided_at = timezone.now()
        request.decided_by = actor
        request.save(update_fields=["status", "decided_at", "decided_by"])
        action = "grupo_solicitud_aceptada" if accept else "grupo_solicitud_declinada"
        verb = "Aceptó" if accept else "Declinó"
        record_church(group.church, actor, action, f"{verb} la solicitud de {person.full_name} al grupo {group.name}",
                      request, group_id=group.pk, person_id=person.pk)
    return request


# ---- meetings -----------------------------------------------------------------------------------
NAMES_DELETED = "De esta reunión ya solo se conservan los totales: la lista de quién vino se borró por antigüedad."
# How many meetings a person sees of their own attendance in a group.
OWN_ATTENDANCE_LIMIT = 8


def with_present(meetings):
    """``meetings`` with ``present``: how many came, from the rows of who came or, once those were
    deleted (``prune_attendance``), from the total that was kept."""
    return meetings.annotate(present=Coalesce(F("present_kept"), Count("attendances")))


def own_attendance(membership: GroupMembership, limit: int = OWN_ATTENDANCE_LIMIT) -> list[GroupMeeting]:
    """The last meetings of the group with a list since ``membership`` started, each with ``came``
    for that one person. Meetings whose names were deleted say nothing about anybody: they are left out."""
    since = timezone.localtime(membership.start_at).date()
    meetings = list(
        GroupMeeting.objects.filter(group_id=membership.group_id, date__gte=since, present_kept__isnull=True)
        .order_by("-date", "-id")[:limit]
    )
    came = set(
        GroupAttendance.objects.filter(meeting__in=meetings, person_id=membership.person_id).values_list("meeting_id", flat=True)
    )
    for meeting in meetings:
        meeting.came = meeting.pk in came
    return meetings


def record_meeting(group: Group, day, present_ids, actor) -> GroupMeeting:
    """Who came to the group on ``day``. Saving the same day again replaces the list. Only people
    who are in the group now can be marked; the audit row carries the count, never the names."""
    if not group.is_active:
        raise GroupError("Este grupo está archivado: ya no se pasa lista.")
    if day > timezone.localdate():
        raise GroupError("Ese día todavía no llega. Elige el día en que se reunieron.")
    members = set(
        GroupMembership.objects.filter(group=group, status=GroupMembership.Status.ACTIVE).values_list("person_id", flat=True)
    )
    present = members & {int(value) for value in present_ids if str(value).isdigit()}
    with transaction.atomic():
        # The first list of a day fixes how many were in the group that day. A correction changes only the people
        # the guide can see now (the current members): who came and has left the group since keeps their row.
        meeting, created = GroupMeeting.objects.get_or_create(
            group=group, date=day, defaults={"expected": len(members), "recorded_by": actor}
        )
        if meeting.names_deleted:
            raise GroupError(NAMES_DELETED)
        if not created:
            meeting.recorded_by = actor
            meeting.save(update_fields=["recorded_by", "updated_at"])
        meeting.attendances.filter(person_id__in=members).exclude(person_id__in=present).delete()
        already = set(meeting.attendances.values_list("person_id", flat=True))
        GroupAttendance.objects.bulk_create(GroupAttendance(meeting=meeting, person_id=pk) for pk in present - already)
        meeting.present = meeting.attendances.count()
        meeting.expected = max(meeting.expected, meeting.present)
        if not created:
            meeting.save(update_fields=["expected", "updated_at"])
        record_church(
            group.church, actor, "grupo_reunion",
            f"Pasó lista de {group.name} del {day:%d/%m/%Y}: {meeting.present} de {meeting.expected}", meeting, group_id=group.pk,
        )
    return meeting


def meeting_due(group: Group, today=None):
    """The day of the group's last meeting when nobody has said who came: within the last six
    days, not before the group existed, and not today before the meeting's hour. Else None."""
    today = today or timezone.localdate()
    day = group.last_meeting_day(today)
    if day is None or not group.is_active or day < timezone.localtime(group.created_at).date():
        return None
    if day == today and group.meeting_time and timezone.localtime().time() < group.meeting_time:
        return None
    if GroupMeeting.objects.filter(group=group, date=day).exists():
        return None
    return day
