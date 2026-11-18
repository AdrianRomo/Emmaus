"""What "Preparación" on the administration home reads about the team before the camp (issue #49).

Every figure is counted from the database on each request: nothing here is a box somebody ticks. No figure counts the
practice campero (``real()``) and none reads an answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count, Q

from ..models import EventMembership, EventParticipant, MembershipRole, QuestionnaireVersion
from . import event_questionnaires as eq
from . import follow_ups as follow_up_svc
from . import practice as practice_svc


@dataclass
class Readiness:
    members: int  # people with access to the event
    never_signed_in: int  # of them, accounts that have never signed in
    coordinators: int
    coordinators_without_campers: int
    tutors: int
    tutors_without_coordinators: int
    confidential_readers: int  # people who can attend a confidential follow-up or a safety report
    follow_ups_without_owner: int  # open ones this viewer may see that nobody attends
    practised: int  # coordinadores who completed a practice interview
    practice_participant: EventParticipant | None
    drafts: list[str]  # titles of the event's questionnaires that have a draft version being edited
    newer_versions: int  # questionnaires whose newest published version the event does not use


def for_event(event, access) -> Readiness:
    active = EventMembership.objects.filter(event=event, is_active=True, user__is_active=True)
    coordinators = list(
        active.filter(role=MembershipRole.COORDINATOR).annotate(
            campers=Count("assigned_participants", filter=Q(assigned_participants__is_active=True, assigned_participants__is_practice=False))
        )
    )
    tutors = list(active.filter(role=MembershipRole.TUTOR).annotate(team=Count("supervised_assignments")))
    practised = practice_svc.practised_user_ids(event)
    linked = [link.questionnaire_id for link in eq.links_for(event)]
    drafts = list(
        QuestionnaireVersion.objects.filter(questionnaire_id__in=linked, status=QuestionnaireVersion.Status.DRAFT)
        .order_by("questionnaire_id", "number")
        .values_list("title", flat=True)
    )
    return Readiness(
        members=active.count(),
        never_signed_in=active.filter(user__last_login__isnull=True).count(),
        coordinators=len(coordinators),
        coordinators_without_campers=sum(1 for c in coordinators if not c.campers),
        tutors=len(tutors),
        tutors_without_coordinators=sum(1 for t in tutors if not t.team),
        confidential_readers=active.filter(can_view_sensitive=True).count(),
        follow_ups_without_owner=follow_up_svc.unassigned_open(access).count(),
        practised=sum(1 for c in coordinators if c.user_id in practised),
        practice_participant=practice_svc.get(event),
        drafts=drafts,
        newer_versions=len(eq.newer_unused_versions(event)),
    )
