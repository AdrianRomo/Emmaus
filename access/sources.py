"""The sources of ``access.RoleAssignment`` and the three things that keep a row honest about its
source (decisions 0003 and 0016):

* ``backed_by_source``: a filter for the projection that keeps only rows whose source membership
  still exists, is active, and says the same account, scope and role. ``core.authz.has_scope`` and
  the "which ones do I manage" lists read through it, so a row that outlived or drifted from its
  source grants nothing. It is one correlated ``EXISTS`` inside the same query, not a second query.
* ``forget_source_row`` / ``forget_scope``: ``post_delete`` receivers. The projection has no foreign
  key to its source, so a deleted membership (deleted directly, or by the cascade from its person,
  team, group or church) must take its row with it. Django sends ``post_delete`` for cascaded rows.
* ``remember_account`` / ``account_changed``: a ``Person`` save re-derives that person's rows only
  when the account really changed, judged against the row in the database before the save.

Each source app registers itself in its ``ready()``; ``core`` and ``access`` import none of them.
"""

from __future__ import annotations

from collections.abc import Callable

from django.db.models import Exists, Q

from .models import RoleAssignment

_LIVE: dict[str, tuple[str, Callable]] = {}
_UNKNOWN = object()


def register(scope_type: str, source_table: str, live: Callable) -> None:
    """``live()`` returns the source's queryset of memberships that grant right now, correlated to
    the role row with ``OuterRef`` (``source_id``, ``scope_id``, ``church_id``, ``role``, ``subject_user_id``)."""
    _LIVE[scope_type] = (source_table, live)


def backed_by_source(scope_type: str) -> Q:
    entry = _LIVE.get(scope_type)
    if entry is None:
        return Q(pk__in=[])  # a projected scope nobody answers for: deny
    source_table, live = entry
    return Q(source_table=source_table) & Q(Exists(live()))


def forget_source_row(source_table: str) -> Callable:
    def receiver(sender, instance, **kwargs) -> None:
        RoleAssignment.objects.filter(source_table=source_table, source_id=instance.pk).delete()

    return receiver


def forget_scope(scope_type: str) -> Callable:
    def receiver(sender, instance, **kwargs) -> None:
        RoleAssignment.objects.filter(scope_type=scope_type, scope_id=instance.pk).delete()

    return receiver


def remember_account(sender, instance, raw=False, update_fields=None, **kwargs) -> None:
    """``pre_save`` of ``Person``: the account the row holds in the database before this save."""
    instance._account_before = _UNKNOWN
    if raw or instance.pk is None or (update_fields is not None and "user" not in update_fields):
        return
    before = sender._base_manager.filter(pk=instance.pk).values_list("user_id", flat=True).first()
    instance._account_before = (before,)


def account_changed(instance, created: bool = False, raw: bool = False) -> bool:
    """After a ``Person`` save: was an account linked, unlinked or replaced by it? A new person has
    no membership yet, and a save that did not write ``user`` changed nothing."""
    before = getattr(instance, "_account_before", _UNKNOWN)
    if raw or created or before is _UNKNOWN:
        return False
    return before[0] != instance.user_id
