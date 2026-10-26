"""Adding a person to an event team. Shared by "Agregar persona" and by approving an access request."""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction

from core.models import UserChurchRole
from core.services import full_name_to_parts

from ..access import resolve_access
from ..models import EventMembership, EventParticipant, MembershipRole, TutorAssignment
from . import audit


@dataclass
class AddedMember:
    membership: EventMembership
    user: User
    is_new: bool  # the account was created here and has no password yet: it needs the link to create one


class AlreadyMember(Exception):
    """The account already has active access to the event: nothing to add, and its role is left alone."""

    def __init__(self, membership: EventMembership):
        super().__init__(membership.display_name)
        self.membership = membership


def account_for_email(email: str) -> User | None:
    return User.objects.filter(email__iexact=email).first()


def describe_account(event, email: str) -> str:
    """For the administration only: whose account an e-mail is, and what it can already do here. "" if none."""
    user = account_for_email(email)
    if user is None:
        return ""
    access = resolve_access(user, event)
    roles = UserChurchRole.objects.filter(user=user)
    memberships = EventMembership.objects.filter(user=user)
    if roles.filter(church_id=event.church_id).exists() or memberships.filter(event__church_id=event.church_id).exists():
        church = event.church.name
    elif roles.exists() or memberships.exists():
        church = "otra iglesia"
    else:
        church = "ninguna"
    name = user.get_full_name() or user.email
    role = access.role_label.lower() if access else "ninguno"
    return f"Ya tiene cuenta: {name}; acceso actual en este evento: {role}; iglesia: {church}"


def add_member(event, actor, *, full_name: str, email: str, role: str, team_name: str = "",
               can_view_sensitive: bool = False, tutor: EventMembership | None = None) -> AddedMember:
    """Reuse the account with this e-mail or create one without a password, then give it ``role`` here.

    An earlier membership whose access was removed is brought back (an event has one membership per
    person), with the new role; a request never brings back confidential access by itself.
    """
    with transaction.atomic():
        user = account_for_email(email)
        is_new = user is None
        if is_new:
            first, last = full_name_to_parts(full_name)
            try:
                with transaction.atomic():
                    user = User.objects.create_user(username=email, email=email, first_name=first, last_name=last)
                    user.set_unusable_password()
                    user.save()
            except IntegrityError:
                # Another request created this account a moment ago (auth_user.username is unique): use that one.
                user = account_for_email(email)
                if user is None:
                    raise
                is_new = False
        membership = None if is_new else EventMembership.objects.filter(event=event, user=user).first()
        if membership is not None and membership.is_active:
            raise AlreadyMember(membership)
        reactivated = membership is not None
        if membership is None:
            membership = EventMembership.objects.create(
                event=event,
                user=user,
                role=role,
                team_name=team_name if role == MembershipRole.TUTOR else "",
                can_view_sensitive=can_view_sensitive,
                created_by=actor,
            )
        else:
            had_sensitive = membership.can_view_sensitive
            membership.role = role
            membership.team_name = (team_name or membership.team_name) if role == MembershipRole.TUTOR else ""
            membership.can_view_sensitive = can_view_sensitive
            membership.is_active = True
            membership.save(update_fields=["role", "team_name", "can_view_sensitive", "is_active", "updated_at"])
            # The same clean-up as changing a role on "Editar acceso"
            if role != MembershipRole.TUTOR:
                TutorAssignment.objects.filter(tutor=membership).delete()
            if role != MembershipRole.COORDINATOR:
                TutorAssignment.objects.filter(coordinator=membership).delete()
                EventParticipant.objects.filter(coordinator=membership).update(coordinator=None)
            elif tutor:
                TutorAssignment.objects.filter(coordinator=membership).delete()
            if had_sensitive and not can_view_sensitive:
                audit.record(actor, event, "acceso_confidencial", f"Retiró acceso confidencial a {membership.display_name}", membership)
        if role == MembershipRole.COORDINATOR and tutor:
            TutorAssignment.objects.create(event=event, tutor=tutor, coordinator=membership, created_by=actor)
        audit.record(actor, event, "integrante_agregado", f"Agregó a {membership.display_name} como {membership.get_role_display().lower()}",
                     membership, **({"reactivada": True} if reactivated else {}))
        if can_view_sensitive:
            audit.record(actor, event, "acceso_confidencial", f"Otorgó acceso confidencial a {membership.display_name}", membership)
    return AddedMember(membership=membership, user=user, is_new=is_new)
