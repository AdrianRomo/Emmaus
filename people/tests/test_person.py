"""The ``people`` app: church-scoped persons that nothing reads yet (issue #22, decision 0002)."""

from pathlib import Path

from django.contrib.auth.models import AnonymousUser, User
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import NoReverseMatch, get_resolver, reverse

from django.utils import timezone

from core.models import Church, Role
from core.services import assign_role
from events.models import Event, EventMembership, MembershipRole
from people.models import Person
from people.persons import persons_for

REPO = Path(__file__).resolve().parents[2]


def make_user(email: str, **extra) -> User:
    return User.objects.create_user(email, email, "x", **extra)


class PersonScopingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.church_a = Church.objects.create(name="Iglesia A", slug="iglesia-a")
        cls.church_b = Church.objects.create(name="Iglesia B", slug="iglesia-b")
        cls.admin_a = make_user("admin-a@example.org")
        assign_role(cls.admin_a, cls.church_a, Role.CHURCH_ADMIN)
        cls.mentor_b = make_user("mentor-b@example.org")
        assign_role(cls.mentor_b, cls.church_b, Role.MENTOR)
        cls.both = make_user("both@example.org")
        assign_role(cls.both, cls.church_a, Role.PASTOR)
        assign_role(cls.both, cls.church_b, Role.PARTICIPANT)
        cls.person_a = Person.objects.create(church=cls.church_a, first_name="Ana", last_name="López")
        cls.person_b = Person.objects.create(church=cls.church_b, first_name="Beto", last_name="Ruiz")

    def test_person_is_church_scoped(self):
        self.assertEqual(list(persons_for(self.admin_a)), [self.person_a])
        self.assertEqual(list(persons_for(self.mentor_b)), [self.person_b])
        with self.assertRaises(Person.DoesNotExist):
            persons_for(self.admin_a).get(pk=self.person_b.pk)

    def test_every_church_role_counts_not_only_the_first(self):
        self.assertEqual(set(persons_for(self.both)), {self.person_a, self.person_b})

    def test_no_role_no_person(self):
        self.assertFalse(persons_for(AnonymousUser()).exists())
        nobody = make_user("nobody@example.org")
        self.assertFalse(persons_for(nobody).exists())
        inactive = make_user("inactive@example.org", is_active=False)
        assign_role(inactive, self.church_a, Role.CHURCH_ADMIN)
        self.assertFalse(persons_for(inactive).exists())

    def test_event_roles_alone_cannot_read_people(self):
        # A coordinator (or even an event administrator) with no church role sees nobody, not even in
        # the church that runs the event: people are a church matter, events are not (decision 0002).
        event = Event.objects.create(
            church=self.church_a, name="Campamento", slug="campamento",
            starts_on=timezone.localdate(), ends_on=timezone.localdate(),
        )
        for role in (MembershipRole.COORDINATOR, MembershipRole.ADMIN):
            member = make_user(f"{role}@example.org")
            EventMembership.objects.create(event=event, user=member, role=role)
            self.assertFalse(persons_for(member).exists())
            with self.assertRaises(Person.DoesNotExist):
                persons_for(member).get(pk=self.person_a.pk)

    def test_superuser_sees_every_church(self):
        root = User.objects.create_superuser("root@example.org", "root@example.org", "x")
        self.assertEqual(persons_for(root).count(), 2)

    def test_identity_link_is_unique_per_church_and_user(self):
        Person.objects.create(church=self.church_a, first_name="Cuenta", user=self.both)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Person.objects.create(church=self.church_a, first_name="Otra", user=self.both)
        # The same account is another person in another church (decision 0002: never merged across churches).
        Person.objects.create(church=self.church_b, first_name="Cuenta", user=self.both)
        # Two persons without an account in one church are fine.
        Person.objects.create(church=self.church_a, first_name="Sin", last_name="Cuenta")
        Person.objects.create(church=self.church_a, first_name="Sin", last_name="Cuenta")

    def test_normalized_name_is_derived_on_save(self):
        person = Person.objects.create(church=self.church_a, first_name="  José María ", last_name="Núñez")
        self.assertEqual(person.normalized_name, "jose maria nunez")
        self.assertEqual(person.full_name, "José María Núñez")
        self.assertEqual((person.first_name, person.last_name), ("José María", "Núñez"))

    def test_normalized_name_follows_a_partial_save_of_the_name(self):
        person = Person.objects.create(church=self.church_a, first_name="Ana", last_name="Pérez")
        person.first_name = "  María  José "
        person.save(update_fields=["first_name"])
        person.refresh_from_db()
        self.assertEqual(person.first_name, "María José")
        self.assertEqual(person.normalized_name, "maria jose perez")

    def test_church_cannot_be_deleted_while_it_has_people(self):
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            self.church_a.delete()


class PersonHasNoSurfaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.church = Church.objects.create(name="Iglesia", slug="iglesia")
        cls.church_admin = make_user("admin@example.org", is_staff=True)  # church admins are is_staff today (audit F1)
        assign_role(cls.church_admin, cls.church, Role.CHURCH_ADMIN)
        cls.root = User.objects.create_superuser("root@example.org", "root@example.org", "x")
        Person.objects.create(church=cls.church, first_name="Ana")

    def test_person_has_no_url(self):
        names = [
            name
            for name in get_resolver().reverse_dict
            if isinstance(name, str) and ("person" in name.lower() or name.startswith("people"))
        ]
        self.assertEqual(names, [])
        with self.assertRaises(NoReverseMatch):
            reverse("people:person_list")

    def test_admin_is_read_only_and_superuser_only(self):
        self.client.force_login(self.church_admin)
        self.assertEqual(self.client.get("/admin/people/person/").status_code, 403)
        self.client.force_login(self.root)
        response = self.client.get("/admin/people/person/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "/admin/people/person/add/")
        self.assertEqual(self.client.get("/admin/people/person/add/").status_code, 403)
        person = Person.objects.get()
        self.assertEqual(self.client.post(f"/admin/people/person/{person.pk}/delete/", {"post": "yes"}).status_code, 403)


class MigrationScopeTests(TestCase):
    def test_core_and_events_migrations_unchanged(self):
        core_files = sorted(p.name for p in (REPO / "core" / "migrations").glob("0*.py"))
        events_files = sorted(p.name for p in (REPO / "events" / "migrations").glob("0*.py"))
        self.assertEqual(len(core_files), 10, core_files)
        self.assertEqual(len(events_files), 12, events_files)
        self.assertEqual([p.name for p in (REPO / "people" / "migrations").glob("0*.py")], ["0001_initial.py"])
        with self.assertRaises(SystemExit) as caught:  # pragma: no cover - only when something is missing
            call_command("makemigrations", "--check", "--dry-run", verbosity=0)
            raise SystemExit(0)
        self.assertEqual(caught.exception.code, 0)


class PeopleMigrationsReverseTests(TransactionTestCase):
    def test_people_migrations_reverse_cleanly(self):
        church = Church.objects.create(name="Iglesia", slug="iglesia")
        Person.objects.create(church=church, first_name="Ana")
        call_command("migrate", "people", "zero", verbosity=0)
        self.assertNotIn("people_person", connection.introspection.table_names())
        call_command("migrate", "people", verbosity=0)
        self.assertIn("people_person", connection.introspection.table_names())
        self.assertEqual(Person.objects.count(), 0)
