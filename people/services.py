"""Writes to the ``people`` app. Nothing in the product calls them yet (decision 0002)."""

from django.db import transaction
from django.utils import timezone

from events.models import Sede

from .models import CampusAffiliation, Person


def affiliate(person: Person, sede: Sede, primary: bool = False) -> CampusAffiliation:
    """Affiliate ``person`` to ``sede`` of the same church, creating or reactivating the row.

    Refuses (``ValidationError`` on ``sede``, nothing written) a sede of another church. An
    inactive affiliation becomes ``activa`` again and loses its ``end_date``; ``start_date`` is
    never invented. ``primary=True`` makes this the person's primary sede and demotes the previous
    one; ``primary=False`` does not demote an affiliation that is already primary. The person row
    is locked (``SELECT … FOR UPDATE`` on PostgreSQL) so concurrent calls for one person run one
    after the other instead of racing into the unique constraints.
    """
    with transaction.atomic():
        Person.objects.select_for_update().only("pk").get(pk=person.pk)
        affiliation = CampusAffiliation.objects.select_related("person", "sede").filter(person=person, sede=sede).first()
        if affiliation is None:
            affiliation = CampusAffiliation(person=person, sede=sede)
        affiliation.clean()  # the same-church rule, before any write
        if primary:
            others = CampusAffiliation.objects.filter(person=person, is_primary=True)
            if affiliation.pk is not None:
                others = others.exclude(pk=affiliation.pk)
            others.update(is_primary=False, updated_at=timezone.now())
            affiliation.is_primary = True
        affiliation.status = CampusAffiliation.Status.ACTIVE
        affiliation.end_date = None
        affiliation.save()
    return affiliation
