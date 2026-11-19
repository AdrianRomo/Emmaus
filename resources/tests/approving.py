"""For tests: a church pastor, and the real approval that publishes what the administration prepared.
Nothing here shortens the rule: it calls ``resources.approvals.approve`` as the screens do."""

from core.models import Role, UserChurchRole
from core.services import assign_role
from events.tests.base import make_user
from people.services import ensure_own_person
from resources import approvals


def church_pastor(church):
    """The account that holds the church role of pastor in ``church`` (made the first time)."""
    role = UserChurchRole.objects.filter(church=church, role=Role.PASTOR).select_related("user").order_by("id").first()
    if role is not None:
        return role.user
    user = make_user(f"pastor-{church.slug}@prueba.mx", "Paula", "Pastora")
    assign_role(user, church, Role.PASTOR)
    ensure_own_person(user, church)
    return user


def approve(church, kind_key: str, obj, *, use: str = "propia", note: str = ""):
    """A pastor of ``church`` approves ``obj`` as it is now. The object is read again afterwards."""
    kind = approvals.kind_of(kind_key)
    obj.refresh_from_db()
    approvals.approve(church, kind_key, obj.pk, church_pastor(church), revision=kind.revision(obj), permitted_use=use, source_note=note)
    obj.refresh_from_db()
    return obj
