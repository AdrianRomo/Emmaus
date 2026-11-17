"""``manage.py link_people``: one Person per account the church already knows, none for camperos (issue #23)."""

from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from core.models import Church, ParticipantProfile, Role, UserChurchRole
from core.services import assign_role
from events.models import Event, EventMembership, EventParticipant, normalize_name
from people.management.commands import link_people as link_people_module
from people.management.commands.link_people import Command as LinkPeople
from people.models import Person

ROLLBACK = 'Person.objects.filter(created_from="backfill").delete()'


def link_people(*args) -> str:
    out = StringIO()
    call_command("link_people", *args, stdout=out, stderr=out)
    return out.getvalue()


def account_pairs() -> set[tuple[int, int]]:
    """Every (church, account) the church already knows: a participant profile or a church role."""
    return set(ParticipantProfile.objects.values_list("church_id", "user_id")) | set(
        UserChurchRole.objects.values_list("church_id", "user_id")
    )


def snapshot() -> list[tuple]:
    return [tuple(row) for row in Person.objects.order_by("pk").values_list()]


class LinkPeopleOnTheSeedsTests(TestCase):
    """Both synthetic seeds as a demo server has them, plus a third church with the cases the seeds lack."""

    @classmethod
    def setUpTestData(cls):
        with override_settings(DEBUG=True):
            call_command(
                "seed_camp_demo", "--reset", "--participants", "40", "--hours", "1", password="x", stdout=StringIO()
            )
            call_command("seed_dev_data", password="x", stdout=StringIO())
        cls.dev_church = Church.objects.get(slug="emmaus-demo")
        cls.camp_church = Church.objects.get(slug="iglesia-demo")
        cls.north = Church.objects.create(name="Iglesia Norte", slug="iglesia-norte")
        # A profile with a phone and a preference, a church role in the same church (covered by the
        # profile) and another in a second church, where nothing of the profile may be copied.
        cls.eva = User.objects.create_user(
            "eva@example.org", "eva@example.org", "x", first_name="Eva", last_name="Campos Ruiz"
        )
        ParticipantProfile.objects.create(
            user=cls.eva, church=cls.north, phone_number="55 1234 5678",
            preferred_contact_method=ParticipantProfile.ContactMethod.WHATSAPP,
        )
        assign_role(cls.eva, cls.north, Role.PARTICIPANT)
        assign_role(cls.eva, cls.dev_church, Role.MENTOR)
        # A profile without any church role.
        cls.only_profile = User.objects.create_user(
            "solo.perfil@example.org", "solo.perfil@example.org", "x", first_name="Inés"
        )
        ParticipantProfile.objects.create(
            user=cls.only_profile, church=cls.north, phone_number="5587654321",
            preferred_contact_method=ParticipantProfile.ContactMethod.PHONE,
        )
        # A camper whose name is a church-role holder's name: still only the account's person.
        event = Event.objects.get(slug="jovenes-2026")
        EventParticipant.objects.create(event=event, number=999, full_name="Laura Méndez Ortiz", age=30)

    def test_backfill_creates_one_person_per_profile_and_role_holder(self):
        pairs = account_pairs()
        link_people("--apply")

        persons = list(Person.objects.all())
        self.assertEqual(len(persons), len(pairs))
        self.assertEqual({(p.church_id, p.user_id) for p in persons}, pairs)
        self.assertEqual({p.created_from for p in persons}, {Person.CreatedFrom.BACKFILL})
        # seed_dev_data: four role holders (the participant's profile is in the same church) plus Eva's
        # mentor role; seed_camp_demo: only the administrator holds a church role.
        self.assertEqual(Person.objects.filter(church=self.dev_church).count(), 5)
        self.assertEqual(Person.objects.filter(church=self.camp_church).count(), 1)
        self.assertEqual(Person.objects.filter(church=self.north).count(), 2)

        for profile in ParticipantProfile.objects.select_related("user"):
            person = Person.objects.get(church=profile.church, user=profile.user)
            self.assertEqual(person.phone, profile.phone_number)
            self.assertEqual(person.preferred_contact_method, profile.preferred_contact_method)
            self.assertEqual(person.email, profile.user.email)
            self.assertEqual((person.first_name, person.last_name), (profile.user.first_name, profile.user.last_name))
        eva_north = Person.objects.get(church=self.north, user=self.eva)
        self.assertEqual((eva_north.phone, eva_north.preferred_contact_method), ("55 1234 5678", "whatsapp"))
        self.assertEqual(eva_north.normalized_name, "eva campos ruiz")
        # The profile belongs to one church; the same account's person in another church gets none of it.
        eva_dev = Person.objects.get(church=self.dev_church, user=self.eva)
        self.assertEqual(eva_dev.phone, "")
        self.assertEqual(eva_dev.preferred_contact_method, Person._meta.get_field("preferred_contact_method").default)
        admin = Person.objects.get(church=self.camp_church)
        self.assertEqual(admin.full_name, "Laura Méndez Ortiz")
        self.assertEqual(admin.user.email, "laura.mendez@demo.invalid")

    def test_no_person_is_created_for_event_participants(self):
        campers = {normalize_name(name) for name in EventParticipant.objects.values_list("full_name", flat=True)}
        self.assertGreaterEqual(len(campers), 41)
        link_people("--apply")

        self.assertEqual(Person.objects.count(), len(account_pairs()))
        self.assertFalse(Person.objects.filter(user__isnull=True).exists())
        staff = set(EventMembership.objects.values_list("user_id", flat=True)) | set(
            User.objects.filter(is_staff=True).values_list("pk", flat=True)
        )
        clashes = [p for p in Person.objects.all() if p.normalized_name in campers]
        self.assertEqual([p.user_id for p in clashes if p.user_id not in staff], [])
        self.assertEqual(Person.objects.filter(normalized_name="laura mendez ortiz").count(), 1)

    def test_backfill_is_idempotent(self):
        first = link_people("--apply")
        before = snapshot()
        second = link_people("--apply")

        self.assertEqual(snapshot(), before)
        self.assertIn(f"Se crearon {len(before)} persona(s)", first)
        self.assertIn("Se crearon 0 persona(s)", second)
        self.assertIn("Se crearían 0 persona(s)", link_people())
        self.assertEqual(snapshot(), before)

    def test_an_account_that_already_has_a_person_is_left_alone(self):
        existing = Person.objects.create(
            church=self.north, user=self.eva, first_name="Eva", last_name="C.", created_from=Person.CreatedFrom.SETUP
        )
        link_people("--apply")

        existing.refresh_from_db()
        self.assertEqual((existing.last_name, existing.phone, existing.created_from), ("C.", "", "setup"))
        self.assertEqual(Person.objects.filter(church=self.north, user=self.eva).count(), 1)
        self.assertEqual(Person.objects.count(), len(account_pairs()))

    def test_dry_run_writes_nothing(self):
        output = link_people()

        self.assertFalse(Person.objects.exists())
        pairs = len(account_pairs())
        self.assertIn(f"Se crearían {pairs} persona(s)", output)
        self.assertIn("Simulación", output)
        self.assertIn("link_people --apply", output)
        link_people("--apply")
        self.assertEqual(Person.objects.count(), pairs)

    def test_the_command_never_reads_events_tables(self):
        for args in ((), ("--apply",)):
            with CaptureQueriesContext(connection) as queries:
                link_people(*args)
            self.assertTrue(queries.captured_queries)
            reading_events = [q["sql"] for q in queries.captured_queries if "events_" in q["sql"]]
            self.assertEqual(reading_events, [], args)

    def test_a_concurrent_link_rolls_everything_back(self):
        # Someone else links one of the accounts between the plan and the save: nothing is kept.
        real_plan = link_people_module.build_plan

        def plan_with_a_duplicate():
            plan = real_plan()
            first = plan.new[0]
            plan.new.append(Person(church_id=first.church_id, user=first.user, first_name="X"))
            return plan

        with mock.patch.object(link_people_module, "build_plan", plan_with_a_duplicate):
            with self.assertRaisesMessage(CommandError, "No se guardó nada"):
                link_people("--apply")
        self.assertFalse(Person.objects.exists())

    def test_output_has_counts_and_no_names(self):
        output = link_people() + link_people("--apply") + link_people("--apply")

        self.assertTrue(Person.objects.exists())
        for person in Person.objects.select_related("user"):
            for value in (person.full_name, person.first_name, person.last_name, person.user.email, person.phone):
                if value:
                    self.assertNotIn(value, output)
        self.assertIn("Perfiles de participante", output)
        self.assertIn("Roles en la iglesia", output)


class LinkPeopleNamesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.church = Church.objects.create(name="Iglesia", slug="iglesia")

    def test_names_fall_back_to_the_email_local_part_only_when_both_are_empty(self):
        nameless = User.objects.create_user("soporte.local@example.org", "soporte.local@example.org", "x")
        assign_role(nameless, self.church, Role.CHURCH_ADMIN)
        surname_only = User.objects.create_user("ruiz@example.org", "ruiz@example.org", "x", last_name="Ruiz")
        assign_role(surname_only, self.church, Role.MENTOR)
        link_people("--apply")

        person = Person.objects.get(user=nameless)
        self.assertEqual(
            (person.first_name, person.last_name, person.email), ("soporte.local", "", "soporte.local@example.org")
        )
        self.assertEqual(person.normalized_name, "soporte.local")
        person = Person.objects.get(user=surname_only)
        self.assertEqual((person.first_name, person.last_name), ("", "Ruiz"))

    def test_an_account_without_an_email_falls_back_to_its_username(self):
        account = User.objects.create_user("cuenta-sin-correo", "", "x")
        assign_role(account, self.church, Role.PASTOR)
        link_people("--apply")

        self.assertEqual(Person.objects.get(user=account).first_name, "cuenta-sin-correo")

    def test_names_longer_than_the_person_field_are_cut_and_counted(self):
        limit = Person._meta.get_field("first_name").max_length
        account = User.objects.create_user("largo@example.org", "largo@example.org", "x", first_name="A" * 150)
        assign_role(account, self.church, Role.MENTOR)
        output = link_people("--apply")

        self.assertEqual(Person.objects.get(user=account).first_name, "A" * limit)
        self.assertIn(f"Nombres recortados a {limit} caracteres: 1", output)

    def test_no_accounts_no_persons(self):
        output = link_people("--apply")

        self.assertFalse(Person.objects.exists())
        self.assertIn("Se crearon 0 persona(s)", output)

    def test_help_documents_the_rollback(self):
        self.assertIn(ROLLBACK, LinkPeople.help)
        self.assertIn("--apply", LinkPeople.help)
