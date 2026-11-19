"""Which church a request is about (issues #32 and #37, decision 0003 rule 6).

One answer for the landing page, the logo (``brand_church``), the colours (``theme_church``) and
the shell, so a page never wears one church and shows another:

- **Signed in:** the church the request acts in (``core.authz.church_choices``: the choice kept in
  the session when the account still belongs there, else its first church). Only an account with no
  active church falls back to the church of its first church role (``core.access.get_user_church``:
  a role in a church that was closed).
- **Signed out, or an account with no church** (somebody who only works in an event): the first
  active church, the rule the public pages always had.

Pages *about* a church or an event do not ask here: the public event page, the church's public
page and every camp screen pass their own church (``event.church``), whoever reads them.

The discipleship-era screens (``core.views.role_required``, docs/v2/wave10/legado.md) act in that same church,
with the role the account holds *there* (``Resolution.role``): an account can administer one church
and be a mentor in another, and each request sees only the church it acts in.

Nothing here authorizes: ``has_scope`` and ``role_required`` decide, with the church of the request.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .access import get_user_role, role_in
from .authz import church_choices
from .models import Church, Role, UserChurchRole


def first_active_church() -> Church | None:
    """The church the public pages speak for."""
    return Church.objects.filter(active=True).order_by("created_at").first()


@dataclass(frozen=True)
class Resolution:
    church: Church | None  # the church this request acts in (core.authz); None when the account has no active church
    choices: list[Church] = field(default_factory=list)  # every church the account may choose, in order
    # The church role this request acts with: the one held in ``church`` (None for a person of it with no role).
    # With no active church at all, the account's first role, as before (a role in a church that was closed).
    role: UserChurchRole | None = None

    @property
    def role_church(self) -> Church | None:
        """Where the discipleship-era screens act: the church of ``role``."""
        return self.role.church if self.role else None

    @property
    def others(self) -> list[Church]:
        """The churches the switcher offers: none for an account of one church."""
        if len(self.choices) < 2:
            return []
        return [church for church in self.choices if church.pk != self.church.pk]


def resolve(request) -> Resolution:
    """The church of a signed-in request and the role held in it. The role is read from the table on
    every call and only for that church: a role of another church is never the answer."""
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return Resolution(None)
    church, choices = church_choices(request)
    role = role_in(user, church) if church is not None else get_user_role(user)
    return Resolution(church, choices, role)


def listed_events(request, where: Resolution | None = None):
    """The camp events a church screen lists for this request (the event list, "Mi cuenta", the "Eventos donde
    sirves" link): ``events.access.accessible_events`` without the events the account reaches only through a church
    role in *another* church than the one it is working in. Kept, whatever their church: the events where the
    account holds an event role of its own (an active ``EventMembership``): an event role is not a church role, and
    the camp is entered from this list. Nothing is authorized here: each event still asks ``resolve_access``, and
    the events left out open again by switching church. A superuser, and an account whose request has no church,
    keep the full list."""
    from django.db.models import Q

    from events.access import accessible_events

    events = accessible_events(request.user)
    where = where or resolve(request)
    if where.church is None or request.user.is_superuser:
        return events
    return events.filter(
        Q(church=where.church) | Q(memberships__user=request.user, memberships__is_active=True)
    ).distinct()


def church_for_new_event(request) -> Church | None:
    """The church where "Crear evento" creates: the church of the request, when the account administers it.
    An account whose request has no church (a superuser with no church of its own, or a role in a church that was
    closed) keeps the rule the camp always had: the first church it administers. None: it may not create one here."""
    from events.access import administered_churches

    where = resolve(request)
    if where.church is None:
        return next(iter(administered_churches(request.user)[:1]), None)  # the same one as always: no new ordering
    if request.user.is_superuser:
        return where.church  # administers every church (events.access.administered_churches)
    if where.role is not None and where.role.role == Role.CHURCH_ADMIN:
        return where.church
    return None


def current_church(request) -> Church | None:
    """The church this request wears and speaks for. See the module's docstring."""
    where = resolve(request)
    return where.church or where.role_church or first_active_church()
