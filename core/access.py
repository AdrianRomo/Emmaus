from __future__ import annotations

from django.contrib.auth.models import AnonymousUser, User
from django.db.models import QuerySet

from .models import (
    AIDraft,
    Church,
    ParticipantProfile,
    ParticipantQuestion,
    PastoralNote,
    Role,
    UserChurchRole,
)

LEADER_ROLES = {Role.CHURCH_ADMIN, Role.PASTOR, Role.MENTOR}
PASTORAL_ROLES = {Role.CHURCH_ADMIN, Role.PASTOR}


def get_user_role(user: User | AnonymousUser) -> UserChurchRole | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return user.church_roles.select_related("church").order_by("id").first()


def get_user_church(user: User | AnonymousUser) -> Church | None:
    role = get_user_role(user)
    return role.church if role else None


def get_role_context(user: User) -> dict:
    role_record = get_user_role(user)
    role = role_record.role if role_record else None
    return {
        "current_role_record": role_record,
        "current_church": role_record.church if role_record else None,
        "current_role": role,
        "is_church_admin": role == Role.CHURCH_ADMIN,
        "is_pastor": role == Role.PASTOR,
        "is_mentor": role == Role.MENTOR,
        "is_participant": role == Role.PARTICIPANT,
        "is_leader": role in LEADER_ROLES if role else False,
        "is_pastoral_leader": role in PASTORAL_ROLES if role else False,
    }


def has_role(user: User, *roles: str) -> bool:
    role_record = get_user_role(user)
    return bool(role_record and role_record.role in roles)


def has_pastoral_access(user: User) -> bool:
    return has_role(user, Role.CHURCH_ADMIN, Role.PASTOR)


def has_leader_access(user: User) -> bool:
    return has_role(user, Role.CHURCH_ADMIN, Role.PASTOR, Role.MENTOR)


def visible_participant_profiles(user: User) -> QuerySet[ParticipantProfile]:
    role_record = get_user_role(user)
    queryset = ParticipantProfile.objects.select_related("user", "church", "assigned_mentor")
    if not role_record:
        return queryset.none()
    queryset = queryset.filter(church=role_record.church)
    if role_record.role == Role.PARTICIPANT:
        return queryset.filter(user=user)
    if role_record.role == Role.MENTOR:
        return queryset.filter(assigned_mentor=user)
    if role_record.role in {Role.CHURCH_ADMIN, Role.PASTOR}:
        return queryset
    return queryset.none()


def can_view_participant(user: User, participant: User | ParticipantProfile) -> bool:
    profile = participant if isinstance(participant, ParticipantProfile) else getattr(participant, "participant_profile", None)
    if profile is None:
        return False
    return visible_participant_profiles(user).filter(pk=profile.pk).exists()


def visible_questions(user: User) -> QuerySet[ParticipantQuestion]:
    role_record = get_user_role(user)
    queryset = ParticipantQuestion.objects.select_related("participant", "church", "related_enrollment")
    if not role_record:
        return queryset.none()
    queryset = queryset.filter(church=role_record.church)
    if role_record.role == Role.PARTICIPANT:
        return queryset.filter(participant=user)
    if role_record.role == Role.MENTOR:
        return queryset.filter(participant__participant_profile__assigned_mentor=user)
    if role_record.role in {Role.CHURCH_ADMIN, Role.PASTOR}:
        return queryset
    return queryset.none()


def can_answer_question(user: User, question: ParticipantQuestion) -> bool:
    role_record = get_user_role(user)
    if not role_record or role_record.church_id != question.church_id:
        return False
    if role_record.role in {Role.CHURCH_ADMIN, Role.PASTOR}:
        return True
    return role_record.role == Role.MENTOR and question.participant.participant_profile.assigned_mentor_id == user.id


def visible_notes(user: User) -> QuerySet[PastoralNote]:
    role_record = get_user_role(user)
    queryset = PastoralNote.objects.select_related("participant", "author", "church")
    if not role_record or role_record.role == Role.PARTICIPANT:
        return queryset.none()
    queryset = queryset.filter(church=role_record.church)
    if role_record.role in {Role.CHURCH_ADMIN, Role.PASTOR}:
        return queryset
    if role_record.role == Role.MENTOR:
        return queryset.filter(
            participant__participant_profile__assigned_mentor=user,
            visibility__in=[PastoralNote.Visibility.MENTOR_ONLY, PastoralNote.Visibility.PASTOR_AND_MENTOR],
        )
    return queryset.none()


def visible_ai_drafts(user: User) -> QuerySet[AIDraft]:
    role_record = get_user_role(user)
    queryset = AIDraft.objects.select_related("church", "related_question", "generated_by")
    if not role_record or role_record.role == Role.PARTICIPANT:
        return queryset.none()
    queryset = queryset.filter(church=role_record.church)
    if role_record.role == Role.MENTOR:
        return queryset.filter(related_question__participant__participant_profile__assigned_mentor=user)
    if role_record.role in {Role.CHURCH_ADMIN, Role.PASTOR}:
        return queryset
    return queryset.none()
