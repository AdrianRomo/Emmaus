"""Server-side authorization for events.

Every view resolves an ``EventAccess`` for (user, event) and asks it for querysets.
Nothing in a URL or form can widen these querysets: a tutor's participant list is
always derived from the tutor's own assignments, a coordinator's from their own
membership.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

from django.contrib.auth.models import AnonymousUser, User
from django.db.models import Q, QuerySet

from core.models import Role, UserChurchRole

from .models import (
    Event,
    EventMembership,
    EventParticipant,
    FollowUp,
    Interview,
    MembershipRole,
    Question,
    ReportSnapshot,
)

ROLE_LABELS = dict(MembershipRole.choices)


@dataclass
class EventAccess:
    event: Event
    user: User
    role: str
    membership: EventMembership | None = None
    via_church_role: bool = False
    _cache: dict = field(default_factory=dict, repr=False)

    # ---- role predicates -------------------------------------------------------------
    @property
    def is_admin(self) -> bool:
        return self.role == MembershipRole.ADMIN

    @property
    def is_pastor(self) -> bool:
        return self.role == MembershipRole.PASTOR

    @property
    def is_tutor(self) -> bool:
        return self.role == MembershipRole.TUTOR

    @property
    def is_coordinator(self) -> bool:
        return self.role == MembershipRole.COORDINATOR

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def sees_whole_event(self) -> bool:
        return self.is_admin or self.is_pastor

    @property
    def can_view_sensitive(self) -> bool:
        """Explicit, per-event authorization. Pastoral or admin status alone is not enough."""
        return bool(self.membership and self.membership.is_active and self.membership.can_view_sensitive)

    @property
    def can_manage_event(self) -> bool:
        return self.is_admin

    @property
    def can_conduct_interviews(self) -> bool:
        return self.is_coordinator or self.is_admin

    @property
    def can_register_participants(self) -> bool:
        return self.is_coordinator or self.is_admin

    @property
    def can_view_reports(self) -> bool:
        return self.is_admin or self.is_pastor or self.is_tutor

    @property
    def can_generate_reports(self) -> bool:
        return self.can_view_reports

    @property
    def can_see_aggregate_outcomes(self) -> bool:
        """Privacy-suppressed, event-level aggregates of spiritual/support answers."""
        return self.is_admin or self.is_pastor

    # ---- scope -----------------------------------------------------------------------
    @cached_property
    def supervised_coordinator_ids(self) -> list[int]:
        if not self.is_tutor or not self.membership:
            return []
        return list(
            EventMembership.objects.filter(
                event=self.event,
                role=MembershipRole.COORDINATOR,
                tutor_assignment__tutor=self.membership,
            ).values_list("id", flat=True)
        )

    def coordinators(self) -> QuerySet[EventMembership]:
        qs = EventMembership.objects.filter(event=self.event, role=MembershipRole.COORDINATOR).select_related(
            "user", "tutor_assignment__tutor__user"
        )
        if self.sees_whole_event:
            return qs
        if self.is_tutor:
            return qs.filter(id__in=self.supervised_coordinator_ids)
        if self.is_coordinator and self.membership:
            return qs.filter(id=self.membership.id)
        return qs.none()

    def tutors(self) -> QuerySet[EventMembership]:
        qs = EventMembership.objects.filter(event=self.event, role=MembershipRole.TUTOR).select_related("user")
        if self.sees_whole_event:
            return qs
        if self.is_tutor and self.membership:
            return qs.filter(id=self.membership.id)
        return qs.none()

    def participants(self, include_inactive: bool = False) -> QuerySet[EventParticipant]:
        qs = EventParticipant.objects.filter(event=self.event).select_related("coordinator__user")
        if not include_inactive:
            qs = qs.filter(is_active=True)
        if self.sees_whole_event:
            return qs
        if self.is_tutor:
            return qs.filter(coordinator_id__in=self.supervised_coordinator_ids)
        if self.is_coordinator and self.membership:
            return qs.filter(coordinator=self.membership)
        return qs.none()

    def interviews(self) -> QuerySet[Interview]:
        return Interview.objects.filter(
            event=self.event, participant__in=self.participants(include_inactive=True)
        ).select_related("participant", "conducted_by", "station")

    def follow_ups(self) -> QuerySet[FollowUp]:
        qs = FollowUp.objects.filter(
            event=self.event, participant__in=self.participants(include_inactive=True)
        ).select_related("participant", "assigned_to", "created_by")
        if not self.can_view_sensitive:
            # Referrals created from confidential answers belong to the care team only.
            qs = qs.filter(Q(confidential=False) | Q(created_by=self.user))
        if not (self.can_view_sensitive or self.is_admin):
            # Safety reports are visible to the authorized care team and administrators only.
            # Their reporter still sees that a report exists (see can_view_follow_up).
            qs = qs.filter(Q(kind=FollowUp.Kind.CARE) | Q(created_by=self.user))
        return qs

    # ---- object checks ---------------------------------------------------------------
    def can_view_participant(self, participant: EventParticipant) -> bool:
        return participant.event_id == self.event.id and self.participants(include_inactive=True).filter(
            pk=participant.pk
        ).exists()

    def can_edit_participant(self, participant: EventParticipant) -> bool:
        if not self.can_view_participant(participant):
            return False
        return self.is_admin or (self.is_coordinator and participant.coordinator_id == getattr(self.membership, "id", None))

    def can_interview(self, participant: EventParticipant) -> bool:
        return self.can_conduct_interviews and self.can_edit_participant(participant) and participant.is_active

    def can_view_answer(self, interview: Interview, question: Question, *, during_capture: bool = False) -> bool:
        """Field-level visibility of an individual answer.

        general      -> anyone who can see the participant
        sensible     -> the assigned coordinator, or members with explicit sensitive access
        confidencial -> members with explicit sensitive access; the coordinator only while
                        they are recording it in an assisted interview
        """
        if not self.can_view_participant(interview.participant):
            return False
        sensitivity = question.sensitivity
        if sensitivity == Question.Sensitivity.GENERAL:
            return True
        if self.can_view_sensitive:
            return True
        is_assigned_coordinator = (
            self.is_coordinator and interview.participant.coordinator_id == getattr(self.membership, "id", None)
        ) or (self.is_admin and interview.conducted_by_id == self.user.id)
        if sensitivity == Question.Sensitivity.SENSITIVE:
            return is_assigned_coordinator
        return (
            during_capture
            and is_assigned_coordinator
            and interview.mode == Interview.Mode.ASSISTED
            and interview.status == Interview.Status.IN_PROGRESS
        )

    def can_correct(self, interview: Interview) -> bool:
        if interview.status != Interview.Status.COMPLETED:
            return False
        if self.is_admin:
            return self.can_view_participant(interview.participant)
        return self.is_coordinator and self.can_edit_participant(interview.participant)

    def can_view_follow_up(self, follow_up: FollowUp) -> bool:
        return self.follow_ups().filter(pk=follow_up.pk).exists()

    def can_manage_follow_up(self, follow_up: FollowUp) -> bool:
        if not self.can_view_follow_up(follow_up):
            return False
        if follow_up.kind == FollowUp.Kind.SAFETY:
            return self.can_view_sensitive or self.is_admin
        return self.is_admin or self.is_pastor or self.is_tutor or self.can_view_sensitive or (
            self.is_coordinator and follow_up.participant.coordinator_id == getattr(self.membership, "id", None)
        )

    def can_read_follow_up_details(self, follow_up: FollowUp) -> bool:
        return self.can_view_follow_up(follow_up) and self.can_view_sensitive

    # ---- reports ---------------------------------------------------------------------
    def report_scopes(self) -> list[dict]:
        """Scopes this user may read *now*. A saved report never widens access."""
        if self.sees_whole_event:
            scopes = [{"key": "evento", "label": self.event.all_of_kind, "tutor": None}]
            for tutor in self.tutors():
                scopes.append({"key": f"equipo:{tutor.id}", "label": tutor.team_label, "tutor": tutor})
            return scopes
        if self.is_tutor and self.membership:
            return [{"key": f"equipo:{self.membership.id}", "label": self.membership.team_label, "tutor": self.membership}]
        return []

    def can_view_snapshot(self, snapshot: ReportSnapshot) -> bool:
        if snapshot.event_id != self.event.id:
            return False
        return snapshot.scope_key in {scope["key"] for scope in self.report_scopes()}

    def snapshots(self) -> QuerySet[ReportSnapshot]:
        keys = [scope["key"] for scope in self.report_scopes()]
        return ReportSnapshot.objects.filter(event=self.event, scope_key__in=keys)


def resolve_access(user: User | AnonymousUser, event: Event) -> EventAccess | None:
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    any_membership = EventMembership.objects.filter(event=event, user=user).select_related("user").first()
    membership = any_membership if any_membership and any_membership.is_active else None
    church_role = UserChurchRole.objects.filter(church_id=event.church_id, user=user).values_list("role", flat=True).first()
    if user.is_superuser or church_role == Role.CHURCH_ADMIN:
        return EventAccess(event, user, MembershipRole.ADMIN, membership, via_church_role=membership is None)
    if any_membership is not None and not any_membership.is_active:
        return None  # access removed for this event: a church-wide pastor role does not bring it back
    if membership:
        return EventAccess(event, user, membership.role, membership)
    if church_role == Role.PASTOR:
        return EventAccess(event, user, MembershipRole.PASTOR, None, via_church_role=True)
    return None


def accessible_events(user: User | AnonymousUser) -> QuerySet[Event]:
    if not getattr(user, "is_authenticated", False):
        return Event.objects.none()
    if user.is_superuser:
        return Event.objects.all().select_related("church")
    church_ids = UserChurchRole.objects.filter(user=user, role__in=[Role.CHURCH_ADMIN, Role.PASTOR]).values_list(
        "church_id", flat=True
    )
    admin_church_ids = UserChurchRole.objects.filter(user=user, role=Role.CHURCH_ADMIN).values_list("church_id", flat=True)
    removed = EventMembership.objects.filter(user=user, is_active=False).values_list("event_id", flat=True)
    return (
        Event.objects.filter(
            Q(church_id__in=church_ids) | Q(memberships__user=user, memberships__is_active=True)
        )
        .exclude(Q(id__in=removed) & ~Q(church_id__in=admin_church_ids))  # same rule as resolve_access
        .exclude(status=Event.Status.ARCHIVED)
        .distinct()
        .select_related("church")
    )


def administered_churches(user: User):
    from core.models import Church

    if user.is_superuser:
        return Church.objects.all()
    return Church.objects.filter(user_roles__user=user, user_roles__role=Role.CHURCH_ADMIN)
