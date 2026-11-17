"""``people.CampusAffiliation``: a person's sedes, at most one primary (issue #30, decision 0002)."""

from datetime import date

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase

from core.models import Church, Role
from core.services import assign_role
from events.models import Sede
from people.models import CampusAffiliation, Person
from people.persons import persons_for
from people.services import affiliate

ACTIVE = CampusAffiliation.Status.ACTIVE
INACTIVE = CampusAffiliation.Status.INACTIVE


def make_user(email: str, **extra) -> User:
    return User.objects.create_user(email, email, "x", **extra)


def primary_sedes(person: Person) -> list[Sede]:
    return [a.sede for a in person.campus_affiliations.filter(is_primary=True)]


class CampusAffiliationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.church_a = Church.objects.create(name="Iglesia A", slug="iglesia-a")
        cls.church_b = Church.objects.create(name="Iglesia B", slug="iglesia-b")
        cls.satelite = Sede.objects.create(church=cls.church_a, name="Satélite", slug="satelite")
        cls.merida = Sede.objects.create(church=cls.church_a, name="Mérida", slug="merida")
        cls.centro_b = Sede.objects.create(church=cls.church_b, name="Centro", slug="centro")
        cls.ana = Person.objects.create(church=cls.church_a, first_name="Ana", last_name="López")
        cls.beto = Person.objects.create(church=cls.church_b, first_name="Beto", last_name="Ruiz")

    # The same-church rule.

    def test_clean_refuses_a_sede_of_another_church(self):
        affiliation = CampusAffiliation(person=self.ana, sede=self.centro_b)
        with self.assertRaises(ValidationError) as caught:
            affiliation.clean()
        self.assertEqual([e.code for e in caught.exception.error_dict["sede"]], ["sede_other_church"])
        with self.assertRaises(ValidationError):
            affiliation.full_clean()
        CampusAffiliation(person=self.ana, sede=self.satelite).full_clean()  # same church: valid

    def test_service_refuses_a_sede_of_another_church_and_writes_nothing(self):
        primary = affiliate(self.ana, self.satelite, primary=True)
        for as_primary in (False, True):
            with self.assertRaises(ValidationError) as caught:
                affiliate(self.ana, self.centro_b, primary=as_primary)
            self.assertEqual([e.code for e in caught.exception.error_dict["sede"]], ["sede_other_church"])
        self.assertFalse(CampusAffiliation.objects.filter(sede=self.centro_b).exists())
        # The refused primary call demoted nothing: the check runs before any write.
        primary.refresh_from_db()
        self.assertTrue(primary.is_primary)
        self.assertEqual(CampusAffiliation.objects.count(), 1)

    # Several sedes, one row each, one primary at most.

    def test_a_person_can_have_several_sedes_but_one_row_per_sede(self):
        CampusAffiliation.objects.create(person=self.ana, sede=self.satelite)
        CampusAffiliation.objects.create(person=self.ana, sede=self.merida)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            CampusAffiliation.objects.create(person=self.ana, sede=self.satelite, status=INACTIVE)
        self.assertEqual(self.ana.campus_affiliations.count(), 2)

    def test_database_allows_one_primary_per_person(self):
        CampusAffiliation.objects.create(person=self.ana, sede=self.satelite, is_primary=True)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            CampusAffiliation.objects.create(person=self.ana, sede=self.merida, is_primary=True)
        # Non-primary rows are not limited, and another person's primary on the same sede is fine.
        CampusAffiliation.objects.create(person=self.ana, sede=self.merida)
        carla = Person.objects.create(church=self.church_a, first_name="Carla")
        CampusAffiliation.objects.create(person=carla, sede=self.satelite, is_primary=True)
        self.assertEqual(primary_sedes(self.ana), [self.satelite])

    def test_service_swaps_the_primary(self):
        carla = Person.objects.create(church=self.church_a, first_name="Carla")
        carla_primary = affiliate(carla, self.satelite, primary=True)
        first = affiliate(self.ana, self.satelite, primary=True)
        first_saved_at = first.updated_at
        affiliate(self.ana, self.merida, primary=True)
        first.refresh_from_db()
        self.assertFalse(first.is_primary)
        self.assertEqual(first.status, ACTIVE)  # demoted, still affiliated
        self.assertGreater(first.updated_at, first_saved_at)  # the bulk demotion still stamps the row
        self.assertEqual(primary_sedes(self.ana), [self.merida])
        # Back to the first sede: its row is promoted, no new row.
        again = affiliate(self.ana, self.satelite, primary=True)
        self.assertEqual(again.pk, first.pk)
        self.assertEqual(self.ana.campus_affiliations.count(), 2)
        self.assertEqual(primary_sedes(self.ana), [self.satelite])
        # Another person's primary on the same sede is untouched.
        carla_primary.refresh_from_db()
        self.assertTrue(carla_primary.is_primary)

    def test_service_creates_an_active_affiliation_without_inventing_dates(self):
        affiliation = affiliate(self.ana, self.satelite)
        affiliation.refresh_from_db()
        self.assertEqual(affiliation.status, ACTIVE)
        self.assertFalse(affiliation.is_primary)
        self.assertIsNone(affiliation.start_date)
        self.assertIsNone(affiliation.end_date)

    def test_service_reactivates_instead_of_duplicating(self):
        old = CampusAffiliation.objects.create(
            person=self.ana, sede=self.satelite, status=INACTIVE, start_date=date(2024, 1, 7), end_date=date(2025, 6, 1)
        )
        again = affiliate(self.ana, self.satelite)
        self.assertEqual(again.pk, old.pk)
        old.refresh_from_db()
        self.assertEqual(old.status, ACTIVE)
        self.assertIsNone(old.end_date)
        self.assertEqual(old.start_date, date(2024, 1, 7))
        self.assertEqual(CampusAffiliation.objects.count(), 1)

    def test_service_without_primary_keeps_an_existing_primary(self):
        affiliate(self.ana, self.satelite, primary=True)
        self.assertTrue(affiliate(self.ana, self.satelite).is_primary)
        affiliate(self.ana, self.merida)  # a second sede does not take the primary
        self.assertEqual(primary_sedes(self.ana), [self.satelite])

    # Deletion follows the foreign keys.

    def test_a_sede_with_affiliations_is_protected_and_a_person_takes_theirs_along(self):
        affiliate(self.ana, self.satelite)
        with transaction.atomic(), self.assertRaises(ProtectedError):
            self.satelite.delete()
        self.ana.delete()
        self.assertFalse(CampusAffiliation.objects.exists())
        self.satelite.delete()
        self.assertFalse(Sede.objects.filter(slug="satelite").exists())

    # Reading people does not change.

    def test_persons_for_is_unaffected(self):
        admin_a = make_user("admin-a@example.org")
        assign_role(admin_a, self.church_a, Role.CHURCH_ADMIN)
        mentor_b = make_user("mentor-b@example.org")
        assign_role(mentor_b, self.church_b, Role.MENTOR)
        root = User.objects.create_superuser("root@example.org", "root@example.org", "x")
        # An account linked to an affiliated person, with no church role: an affiliation grants nothing.
        linked = make_user("linked@example.org")
        linked_person = Person.objects.create(church=self.church_a, first_name="Dani", user=linked)
        users = (admin_a, mentor_b, root, linked)
        before = {user.pk: list(persons_for(user)) for user in users}

        affiliate(self.ana, self.satelite, primary=True)
        affiliate(self.ana, self.merida)
        affiliate(linked_person, self.satelite, primary=True)
        affiliate(self.beto, self.centro_b, primary=True)

        for user in users:
            self.assertEqual(list(persons_for(user)), before[user.pk], user.email)
        # Ana has two sedes and is still one row (ordered by last name: Dani has none).
        self.assertEqual(list(persons_for(admin_a)), [linked_person, self.ana])
        self.assertEqual(list(persons_for(mentor_b)), [self.beto])
        self.assertFalse(persons_for(linked).exists())

    def test_campus_affiliation_has_no_admin(self):
        self.assertNotIn(CampusAffiliation, admin.site._registry)


class CampusAffiliationMigrationTests(TransactionTestCase):
    def test_campus_affiliation_reverses_to_0001_and_back(self):
        church = Church.objects.create(name="Iglesia", slug="iglesia")
        satelite = Sede.objects.create(church=church, name="Satélite", slug="satelite")
        person = Person.objects.create(church=church, first_name="Ana")
        affiliate(person, satelite, primary=True)

        call_command("migrate", "people", "0001", verbosity=0)
        tables = connection.introspection.table_names()
        self.assertNotIn("people_campusaffiliation", tables)
        self.assertIn("people_person", tables)

        call_command("migrate", "people", verbosity=0)
        self.assertIn("people_campusaffiliation", connection.introspection.table_names())
        self.assertEqual(CampusAffiliation.objects.count(), 0)
        # People and sedes survive the rollback; only the affiliation table went.
        self.assertEqual(list(Person.objects.values_list("pk", flat=True)), [person.pk])
        self.assertTrue(Sede.objects.filter(pk=satelite.pk).exists())
        # The constraints come back with the table.
        merida = Sede.objects.create(church=church, name="Mérida", slug="merida")
        CampusAffiliation.objects.create(person=person, sede=satelite, is_primary=True)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            CampusAffiliation.objects.create(person=person, sede=merida, is_primary=True)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            CampusAffiliation.objects.create(person=person, sede=satelite)
