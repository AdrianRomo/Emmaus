"""The one way to read people: scoped to the churches the user holds a role in."""

from django.contrib.auth.models import AnonymousUser, User
from django.db.models import QuerySet

from core.models import UserChurchRole

from .models import Person


def persons_for(user: User | AnonymousUser) -> QuerySet[Person]:
    """People of every church where ``user`` holds a church role (all of them, not only the first).

    Anonymous or inactive users get nothing; a superuser sees every church, as in
    ``events.access.administered_churches``. Event roles alone give no access to people.
    """
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return Person.objects.none()
    if user.is_superuser:
        return Person.objects.all()
    churches = UserChurchRole.objects.filter(user=user).values("church_id")
    return Person.objects.filter(church_id__in=churches)


def own_person(user: User | AnonymousUser, church) -> Person | None:
    """The person ``user`` is in ``church`` (``Person.user``), or None. This is what makes somebody
    "of the church" for the community screens: no church role is needed, and an event role is not enough."""
    if not getattr(user, "is_authenticated", False) or not user.is_active or church is None:
        return None
    return Person.objects.filter(church=church, user=user, merged_into__isnull=True).first()
