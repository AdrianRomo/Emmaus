"""Which church a request is about (issues #32 and #37, decision 0003 rule 6).

One answer for the landing page, the logo (``brand_church``), the colours (``theme_church``) and
the shell, so a page never wears one church and shows another:

- **Signed in:** the church the request acts in (``core.authz.church_choices``: the choice kept in
  the session when the account still belongs there, else its first church). The legacy fallback
  follows: the church of the account's first church role (``core.access.get_user_church``).
- **Signed out, or an account with no church** (somebody who only works in an event): the first
  active church, the rule the public pages always had.

Pages *about* a church or an event do not ask here: the public event page, the church's public
page and every camp screen pass their own church (``event.church``), whoever reads them.

Nothing here authorizes: ``has_scope`` decides, with the church of the request.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .access import get_user_church
from .authz import church_choices
from .models import Church


def first_active_church() -> Church | None:
    """The church the public pages speak for."""
    return Church.objects.filter(active=True).order_by("created_at").first()


@dataclass(frozen=True)
class Resolution:
    church: Church | None  # the church this request acts in (core.authz); None when the account has no active church
    choices: list[Church] = field(default_factory=list)  # every church the account may choose, in order
    role_church: Church | None = None  # where the legacy discipleship screens act (the first church role)

    @property
    def elsewhere(self) -> bool:
        """The request acts in another church than the one the legacy discipleship screens follow."""
        return self.church is not None and self.role_church is not None and self.church.pk != self.role_church.pk

    @property
    def others(self) -> list[Church]:
        """The churches the switcher offers: none for an account of one church."""
        if len(self.choices) < 2:
            return []
        return [church for church in self.choices if church.pk != self.church.pk]


def resolve(request, role_church=...) -> Resolution:
    """The church of a signed-in request. ``role_church`` when the caller already read the first role."""
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return Resolution(None)
    if role_church is ...:
        role_church = get_user_church(user)
    church, choices = church_choices(request)
    return Resolution(church, choices, role_church)


def current_church(request) -> Church | None:
    """The church this request wears and speaks for. See the module's docstring."""
    where = resolve(request)
    return where.church or where.role_church or first_active_church()
