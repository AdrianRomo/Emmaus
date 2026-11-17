"""Writes to the ``people`` app: people, their sedes, their link to a campero and their account.

Callers have already answered the authorization question; these functions hold the rules that
apply whoever asks (decision 0002): nothing crosses a church, nothing is linked by name, e-mail or
phone alone, and every link is one audited action by one person.
"""

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import UserChurchRole
from core.services import full_name_to_parts
from events.models import Event, EventParticipant, Sede

from .audit import record_church
from .models import CampusAffiliation, EventParticipantLink, Person


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


class PeopleError(ValidationError):
    """A rule refused the action; the message is ready for the screen."""


def ensure_own_person(user, church) -> Person | None:
    """The person of ``user`` in ``church``; created the first time for an account that holds a
    church role there (staff accounts predate ``Person``). Nobody else gets a person this way."""
    person = Person.objects.filter(church=church, user=user, merged_into__isnull=True).first()
    if person is not None or not UserChurchRole.objects.filter(church=church, user=user).exists():
        return person
    first = user.first_name or (user.email or user.username).split("@", 1)[0]
    try:
        with transaction.atomic():
            return Person.objects.create(
                church=church, user=user, first_name=first[:120], last_name=user.last_name[:120], email=user.email,
                created_from=Person.CreatedFrom.BACKFILL,
            )
    except IntegrityError:  # two first visits at once
        return Person.objects.get(church=church, user=user)


def save_person(person: Person, actor, *, created: bool) -> Person:
    with transaction.atomic():
        person.save()
        action = "persona_creada" if created else "persona_modificada"
        verb = "Registró a" if created else "Modificó los datos de"
        record_church(person.church, actor, action, f"{verb} {person.full_name}", person, person_id=person.pk)
    return person


# ---- campero <-> person -------------------------------------------------------------------------
def event_is_over(event: Event, today=None) -> bool:
    """Links are made after the event: it is closed or archived, or its last day has passed."""
    if event.status in (Event.Status.CLOSED, Event.Status.ARCHIVED):
        return True
    today = today or timezone.localdate()
    return event.status == Event.Status.ACTIVE and event.ends_on is not None and event.ends_on < today


def link_participant(person: Person, participant: EventParticipant, actor) -> EventParticipantLink:
    """"This campero is this person": same church, after the event, one campero per person per event."""
    with transaction.atomic():
        event = participant.event
        if event.church_id != person.church_id or person.merged_into_id is not None:
            raise PeopleError("Esta persona es de otra iglesia.", code="person_other_church")
        if not event_is_over(event):
            raise PeopleError("Podrás vincular a los camperos cuando el evento termine.", code="event_running")
        if EventParticipantLink.objects.filter(participant=participant).exists():
            raise PeopleError("Este campero ya está vinculado con una persona.", code="participant_linked")
        if EventParticipantLink.objects.filter(person=person, event=event).exists():
            raise PeopleError("Esta persona ya está vinculada con otro campero de este evento.", code="person_linked")
        try:
            with transaction.atomic():
                link = EventParticipantLink.objects.create(participant=participant, person=person, event=event, linked_by=actor)
        except IntegrityError as error:
            raise PeopleError("Alguien más hizo este vínculo hace un momento.", code="race") from error
        record_church(person.church, actor, "persona_vinculada",
                      f"Vinculó a {person.full_name} con su registro en {event.name}", person,
                      person_id=person.pk, event_id=event.pk)
    return link


def create_person_from_participant(participant: EventParticipant, actor) -> Person:
    """A new person for a campero who is not in the church's people yet, linked at once. Only the
    name and the sede cross over: no age, no room, no answer."""
    with transaction.atomic():
        event = participant.event
        if not event_is_over(event):
            raise PeopleError("Podrás vincular a los camperos cuando el evento termine.", code="event_running")
        if EventParticipantLink.objects.filter(participant=participant).exists():
            raise PeopleError("Este campero ya está vinculado con una persona.", code="participant_linked")
        first, last = full_name_to_parts(participant.full_name)
        person = Person(church_id=event.church_id, first_name=first[:120], last_name=last[:120], created_from=Person.CreatedFrom.EVENT)
        person.save()
        record_church(person.church, actor, "persona_creada", f"Registró a {person.full_name}", person,
                      person_id=person.pk, source="event")
        if participant.sede_id is not None:
            affiliate(person, participant.sede, primary=True)
        link_participant(person, participant, actor)
    return person


def unlink_participant(link: EventParticipantLink, actor) -> None:
    """Undo a link made by mistake. The person and the campero both stay."""
    with transaction.atomic():
        person, event = link.person, link.event
        link.delete()
        record_church(person.church, actor, "persona_desvinculada",
                      f"Quitó el vínculo de {person.full_name} con {event.name}", person,
                      person_id=person.pk, event_id=event.pk)


def was_minor_at_an_event(person: Person) -> bool:
    """True when a linked campero was under the event's adult age and no birth date says otherwise."""
    if person.birth_date is not None:
        today = timezone.localdate()
        age = today.year - person.birth_date.year - ((today.month, today.day) < (person.birth_date.month, person.birth_date.day))
        return age < 18
    links = person.event_links.select_related("participant", "event")
    return any(link.participant.age < link.event.adult_age for link in links)


# ---- person -> account --------------------------------------------------------------------------
class ExistingAccount(Exception):
    """An account with the person's e-mail already exists: linking it needs the administrator's explicit yes."""

    def __init__(self, user: User):
        super().__init__("existing account")
        self.user = user


def grant_access(person: Person, actor, *, link_existing: bool = False, minor_confirmed: bool = False) -> tuple[User, bool]:
    """Give ``person`` an account: a new one without a password (the caller shows the one-time
    link), or, only with ``link_existing``, the account that already uses the person's e-mail.

    Returns ``(user, is_new)``. No church role and no event role is created: the account is a
    person of the church, nothing more (``RoleAssignment != identity``).
    """
    with transaction.atomic():
        person = Person.objects.select_for_update().get(pk=person.pk)
        if person.user_id is not None:
            raise PeopleError("Esta persona ya tiene acceso a Emmaus.", code="has_account")
        email = person.email.strip().lower()
        if not email:
            raise PeopleError("Primero escribe el correo de esta persona: con él va a entrar.", code="no_email")
        if was_minor_at_an_event(person) and not minor_confirmed:
            raise PeopleError(
                "Confirma que su mamá, papá o tutor está de acuerdo en que tenga una cuenta.", code="minor_unconfirmed"
            )
        user = User.objects.filter(email__iexact=email).first() or User.objects.filter(username__iexact=email).first()
        is_new = user is None
        if not is_new:
            if not link_existing:
                raise ExistingAccount(user)
            if Person.objects.filter(church_id=person.church_id, user=user).exists():
                raise PeopleError("La cuenta con ese correo ya es de otra persona de esta iglesia.", code="account_taken")
        else:
            user = User.objects.create_user(username=email, email=email, first_name=person.first_name, last_name=person.last_name)
            user.set_unusable_password()
            user.save()
        person.user = user
        person.save(update_fields=["user", "updated_at"])
        action = "cuenta_creada" if is_new else "cuenta_vinculada"
        verb = "Dio acceso a" if is_new else "Vinculó con una cuenta que ya existía a"
        record_church(person.church, actor, action, f"{verb} {person.full_name}", person,
                      person_id=person.pk, minor_confirmed=bool(minor_confirmed))
    return user, is_new
