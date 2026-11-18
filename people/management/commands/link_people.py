"""Create the ``Person`` rows that the existing accounts imply (issue #23, roadmap P1-3, decision 0002).

One person per ``ParticipantProfile`` (in the profile's church, with its phone and contact preference)
and one per distinct ``(church, user)`` in ``UserChurchRole`` that the profiles did not already cover,
all with ``created_from=backfill`` and ``user`` set, so ``person_one_per_church_and_user`` is the link.
An account that already has a person in that church is left as it is, whatever the person's origin:
running the command again creates nothing.

What it never does: read an ``events`` table, create a person for a campero (``EventParticipant``),
link anything by name, e-mail or phone, or copy a profile's data into another church. It prints
counts only, never a name, an e-mail or a phone.

Names come from ``User.first_name`` / ``last_name`` as they are; only when both are empty, from the
local part of the e-mail (or of the username when there is no e-mail). A name longer than the
person's field is cut to fit and counted. Without ``--apply`` nothing is written.

Undoing it deletes people, and with them (by cascade) their memberships of groups and teams. The
role rows derived from those memberships go too (``access.sources``); ``rebuild_role_assignments
--apply`` afterwards is the proof that nothing was left behind.
"""

from collections import Counter
from dataclasses import dataclass, field

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from core.models import Church, ParticipantProfile, UserChurchRole
from people.models import Person

ROLLBACK = 'Person.objects.filter(created_from="backfill").delete()'
AFTER_ROLLBACK = "Después corre: python manage.py rebuild_role_assignments --apply"
NAME_LIMIT = Person._meta.get_field("first_name").max_length


@dataclass
class Plan:
    profiles: int = 0
    profiles_linked: int = 0
    roles: int = 0
    roles_linked: int = 0
    roles_covered_by_profile: int = 0
    names_cut: int = 0
    new: list[Person] = field(default_factory=list)


def _fit(value: str) -> tuple[str, bool]:
    value = " ".join(value.split())
    return value[:NAME_LIMIT], len(value) > NAME_LIMIT


def person_for(user, church_id: int, profile: ParticipantProfile | None) -> tuple[Person, bool]:
    """An unsaved backfill person for ``user`` in ``church_id``; True when a name had to be cut."""
    first, last = user.first_name, user.last_name
    if not first.strip() and not last.strip():
        first = (user.email or user.username).split("@", 1)[0]
    first, first_cut = _fit(first)
    last, last_cut = _fit(last)
    person = Person(
        church_id=church_id, user=user, first_name=first, last_name=last, email=user.email,
        created_from=Person.CreatedFrom.BACKFILL,
    )
    if profile is not None:  # only the profile's own church gets its data
        person.phone = profile.phone_number
        person.preferred_contact_method = profile.preferred_contact_method
    return person, first_cut or last_cut


def build_plan() -> Plan:
    plan = Plan()
    linked = set(Person.objects.filter(user__isnull=False).values_list("church_id", "user_id"))
    planned = set()

    def add(user, church_id, profile):
        person, cut = person_for(user, church_id, profile)
        plan.new.append(person)
        plan.names_cut += cut
        planned.add((church_id, user.pk))

    # order_by("pk") also drops the models' default ordering, which joins other tables.
    for profile in ParticipantProfile.objects.select_related("user").order_by("pk"):
        plan.profiles += 1
        if (profile.church_id, profile.user_id) in linked:
            plan.profiles_linked += 1
        else:
            add(profile.user, profile.church_id, profile)
    # UserChurchRole is unique per (church, user): one row is one distinct role holder.
    for role in UserChurchRole.objects.select_related("user").order_by("pk"):
        plan.roles += 1
        key = (role.church_id, role.user_id)
        if key in linked:
            plan.roles_linked += 1
        elif key in planned:
            plan.roles_covered_by_profile += 1
        else:
            add(role.user, role.church_id, None)
    return plan


class Command(BaseCommand):
    help = (
        "Crea una persona (origen «Cuentas existentes», backfill) por cada perfil de participante, en la iglesia "
        "del perfil y con su teléfono y medio de contacto preferido, y por cada cuenta con un rol en una iglesia "
        "que todavía no tenga persona ahí. Nunca crea personas para camperos ni lee tablas de eventos. Volver a "
        "correrlo no cambia nada. Solo muestra conteos, nunca nombres. Sin --apply solo muestra lo que crearía. "
        f"Para deshacerlo, en python manage.py shell: from people.models import Person; {ROLLBACK}. {AFTER_ROLLBACK}"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true", help="Crear las personas de verdad. Sin esta opción no se guarda nada."
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        try:
            with transaction.atomic():
                plan = build_plan()
                if apply:
                    for person in plan.new:
                        person.save()
        except IntegrityError as error:
            raise CommandError(
                "No se guardó nada: alguien creó una persona para una de estas cuentas mientras corría el comando "
                "(por ejemplo, otra ejecución al mismo tiempo). Vuelve a correrlo."
            ) from error
        self._report(plan, apply)

    def _report(self, plan: Plan, apply: bool):
        new = len(plan.new)
        self.stdout.write(f"Perfiles de participante: {plan.profiles}")
        self.stdout.write(f"  ya tenían persona en su iglesia: {plan.profiles_linked}")
        self.stdout.write(f"  persona nueva: {plan.profiles - plan.profiles_linked}")
        self.stdout.write(f"Roles en la iglesia (uno por cuenta y por iglesia): {plan.roles}")
        self.stdout.write(f"  ya tenían persona en su iglesia: {plan.roles_linked}")
        self.stdout.write(f"  cubiertos por su perfil de participante: {plan.roles_covered_by_profile}")
        self.stdout.write(f"  persona nueva: {plan.roles - plan.roles_linked - plan.roles_covered_by_profile}")
        if plan.new:
            per_church = Counter(person.church_id for person in plan.new)
            slugs = dict(Church.objects.filter(pk__in=per_church).values_list("pk", "slug"))
            self.stdout.write("Personas nuevas por iglesia (identificador):")
            for church_id, count in sorted(per_church.items(), key=lambda item: slugs[item[0]]):
                self.stdout.write(f"  {slugs[church_id]:<30} {count:>7}")
        if plan.names_cut:
            self.stdout.write(self.style.WARNING(f"Nombres recortados a {NAME_LIMIT} caracteres: {plan.names_cut}"))
        if not apply:
            self.stdout.write(
                self.style.WARNING(
                    f"Simulación: no se guardó nada. Se crearían {new} persona(s). "
                    "Para crearlas de verdad: python manage.py link_people --apply"
                )
            )
            return
        self.stdout.write(self.style.SUCCESS(f"Listo. Se crearon {new} persona(s) con origen «Cuentas existentes»."))
        if new:
            self.stdout.write(
                f"Para deshacerlo, en python manage.py shell: from people.models import Person; {ROLLBACK}. {AFTER_ROLLBACK}"
            )
