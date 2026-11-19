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

# ``role_record`` of every function below: the church role the caller acts with. A view passes the role the account
# holds in the church of its request (``core.tenancy.resolve(request).role``, put on ``request.church_role`` by
# ``core.views.role_required``); None there means "no role in that church" and answers nothing. Left out, it is the
# account's first role by id, which is what these functions always answered: for code with no request (commands,
# onboarding of an account of one church), never for a screen.
FIRST_ROLE = object()


def get_user_role(user: User | AnonymousUser) -> UserChurchRole | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return user.church_roles.select_related("church").order_by("id").first()


def role_in(user: User | AnonymousUser, church: Church | None) -> UserChurchRole | None:
    """The role the account holds in ``church`` (at most one: ``unique_together``), or None. Never another church's."""
    if church is None or not getattr(user, "is_authenticated", False):
        return None
    return user.church_roles.select_related("church").filter(church=church).first()


def _acting(user, role_record) -> UserChurchRole | None:
    return get_user_role(user) if role_record is FIRST_ROLE else role_record


# How a church role is named to the person who holds it (the rail, "Mi cuenta"): the work, not a gendered noun.
# "Pastor" stays: it is the title churches themselves use, and the model's own word everywhere else.
ROLE_WORDS = {Role.MENTOR: "Mentoría"}


def role_words(role_record: UserChurchRole | None) -> str:
    if role_record is None:
        return ""
    return ROLE_WORDS.get(role_record.role, role_record.get_role_display())


def get_user_church(user: User | AnonymousUser) -> Church | None:
    role = get_user_role(user)
    return role.church if role else None


def get_role_context(user: User, role_record=FIRST_ROLE) -> dict:
    role_record = _acting(user, role_record)
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


def has_role(user: User, *roles: str, role_record=FIRST_ROLE) -> bool:
    role_record = _acting(user, role_record)
    return bool(role_record and role_record.role in roles)


def has_pastoral_access(user: User, role_record=FIRST_ROLE) -> bool:
    return has_role(user, Role.CHURCH_ADMIN, Role.PASTOR, role_record=role_record)


def has_leader_access(user: User, role_record=FIRST_ROLE) -> bool:
    return has_role(user, Role.CHURCH_ADMIN, Role.PASTOR, Role.MENTOR, role_record=role_record)


def visible_participant_profiles(user: User, role_record=FIRST_ROLE) -> QuerySet[ParticipantProfile]:
    role_record = _acting(user, role_record)
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


def can_view_participant(user: User, participant: User | ParticipantProfile, role_record=FIRST_ROLE) -> bool:
    profile = participant if isinstance(participant, ParticipantProfile) else getattr(participant, "participant_profile", None)
    if profile is None:
        return False
    return visible_participant_profiles(user, role_record).filter(pk=profile.pk).exists()


def visible_questions(user: User, role_record=FIRST_ROLE) -> QuerySet[ParticipantQuestion]:
    role_record = _acting(user, role_record)
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


def can_answer_question(user: User, question: ParticipantQuestion, role_record=FIRST_ROLE) -> bool:
    role_record = _acting(user, role_record)
    if not role_record or role_record.church_id != question.church_id:
        return False
    if role_record.role in {Role.CHURCH_ADMIN, Role.PASTOR}:
        return True
    return role_record.role == Role.MENTOR and question.participant.participant_profile.assigned_mentor_id == user.id


def visible_notes(user: User, role_record=FIRST_ROLE) -> QuerySet[PastoralNote]:
    role_record = _acting(user, role_record)
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


def visible_ai_drafts(user: User, role_record=FIRST_ROLE) -> QuerySet[AIDraft]:
    role_record = _acting(user, role_record)
    queryset = AIDraft.objects.select_related("church", "related_question", "generated_by")
    if not role_record or role_record.role == Role.PARTICIPANT:
        return queryset.none()
    queryset = queryset.filter(church=role_record.church)
    if role_record.role == Role.MENTOR:
        return queryset.filter(related_question__participant__participant_profile__assigned_mentor=user)
    if role_record.role in {Role.CHURCH_ADMIN, Role.PASTOR}:
        return queryset
    return queryset.none()
