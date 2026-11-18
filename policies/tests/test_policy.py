"""``people.policy`` reads a church's own choices and holds the platform's floors whatever is stored."""

from unittest import mock

from django.test import TestCase, override_settings

from core.models import Church
from people import policy
from policies.models import ChurchPolicy


class PolicyReadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.church = Church.objects.create(name="Iglesia A", slug="politica-a")
        cls.other = Church.objects.create(name="Iglesia B", slug="politica-b")

    def write(self, **values):
        """Straight to the table, as a hand edit or a bug would: no form, no ``save``."""
        ChurchPolicy.objects.get_or_create(church=self.church)
        ChurchPolicy.objects.filter(church=self.church).update(**values)
        policy.forget()

    def test_without_a_row_and_without_a_church_everything_is_the_product_default(self):
        for church in (None, self.church, Church(name="Sin guardar"), mock.Mock()):  # a test double is no church
            self.assertEqual(policy.min_reporting_size(church), 5)
            self.assertEqual(policy.feedback_open_days(church), 14)
            self.assertEqual(policy.urgent_notice_roles(church), frozenset({"church_admin"}))
            self.assertIs(policy.minors_consent_default(church), False)
            self.assertEqual(policy.attendance_retention(church), (12, 24))
            self.assertEqual(policy.consent_policy_version(church), "borrador-2026-11")
        self.assertFalse(ChurchPolicy.objects.exists())  # reading never creates the row

    def test_a_row_with_every_column_empty_is_the_product_default(self):
        ChurchPolicy.objects.create(church=self.church)
        self.assertEqual(policy.min_reporting_size(self.church), 5)
        self.assertEqual(policy.feedback_open_days(self.church), 14)
        self.assertEqual(policy.urgent_notice_roles(self.church), frozenset({"church_admin"}))
        self.assertIs(policy.minors_consent_default(self.church), False)
        self.assertEqual(policy.attendance_retention(self.church), (12, 24))

    def test_a_church_reads_its_own_choices_and_no_other_church_does(self):
        ChurchPolicy.objects.create(
            church=self.church, min_reporting_size=8, feedback_open_days=21, urgent_notice_pastors=True,
            minors_need_consent_by_default=True, attendance_identifiable_months=9, attendance_aggregate_months=36,
        )
        self.assertEqual(policy.min_reporting_size(self.church), 8)
        self.assertEqual(policy.feedback_open_days(self.church), 21)
        self.assertEqual(policy.urgent_notice_roles(self.church), frozenset({"church_admin", "pastor"}))
        self.assertIs(policy.minors_consent_default(self.church), True)
        retention = policy.attendance_retention(self.church)
        self.assertEqual((retention.identifiable_months, retention.aggregate_months), (9, 36))
        self.assertEqual(policy.min_reporting_size(self.other), 5)
        self.assertEqual(policy.urgent_notice_roles(self.other), frozenset({"church_admin"}))
        self.assertIs(policy.minors_consent_default(self.other), False)
        self.assertEqual(policy.attendance_retention(self.other), (12, 24))

    def test_a_value_below_the_floor_in_the_table_still_reads_as_the_floor(self):
        self.write(min_reporting_size=1, feedback_open_days=0, attendance_identifiable_months=1, attendance_aggregate_months=2)
        self.assertEqual(policy.min_reporting_size(self.church), 5)
        self.assertEqual(policy.feedback_open_days(self.church), 7)
        self.assertEqual(policy.attendance_retention(self.church), (6, 12))
        self.write(min_reporting_size=0)
        self.assertEqual(policy.min_reporting_size(self.church), 5)

    def test_a_value_above_the_ceiling_in_the_table_still_reads_as_the_ceiling(self):
        self.write(min_reporting_size=500, feedback_open_days=365, attendance_identifiable_months=240, attendance_aggregate_months=999)
        self.assertEqual(policy.min_reporting_size(self.church), 20)
        self.assertEqual(policy.feedback_open_days(self.church), 30)
        self.assertEqual(policy.attendance_retention(self.church), (24, 60))

    def test_the_totals_are_never_kept_for_less_time_than_the_names(self):
        self.write(attendance_identifiable_months=24, attendance_aggregate_months=12)
        self.assertEqual(policy.attendance_retention(self.church), (24, 24))
        self.write(attendance_identifiable_months=None, attendance_aggregate_months=13)
        self.assertEqual(policy.attendance_retention(self.church), (12, 13))
        with override_settings(EMMAUS_ATTENDANCE_AGGREGATE_MONTHS=12):  # only the names chosen, an operator's shorter totals
            self.write(attendance_identifiable_months=18, attendance_aggregate_months=None)
            self.assertEqual(policy.attendance_retention(self.church), (18, 18))

    def test_without_a_choice_the_retention_is_the_operators_settings_as_they_are(self):
        with override_settings(EMMAUS_ATTENDANCE_IDENTIFIABLE_MONTHS=3, EMMAUS_ATTENDANCE_AGGREGATE_MONTHS=6):
            self.assertEqual(policy.attendance_retention(None), (3, 6))
            self.assertEqual(policy.attendance_retention(self.church), (3, 6))
            self.write(attendance_identifiable_months=7)  # a church's choice brings the bounds with it
            self.assertEqual(policy.attendance_retention(self.church), (7, 12))


class PolicyCacheTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.church = Church.objects.create(name="Iglesia A", slug="politica-a")

    def test_one_query_per_church_object_until_a_row_is_saved(self):
        with self.assertNumQueries(1):
            policy.min_reporting_size(self.church)
            policy.feedback_open_days(self.church)
            policy.urgent_notice_roles(self.church)
            policy.attendance_retention(self.church)
        row = ChurchPolicy.objects.create(church=self.church, min_reporting_size=9)
        with self.assertNumQueries(1):  # the save was noticed by the object that had the old answer
            self.assertEqual(policy.min_reporting_size(self.church), 9)
            self.assertEqual(policy.min_reporting_size(self.church), 9)
        row.min_reporting_size = 11
        row.save()
        self.assertEqual(policy.min_reporting_size(self.church), 11)
        row.delete()
        self.assertEqual(policy.min_reporting_size(self.church), 5)

    def test_another_object_of_the_same_church_reads_for_itself(self):
        policy.min_reporting_size(self.church)
        again = Church.objects.get(pk=self.church.pk)
        with self.assertNumQueries(1):
            policy.min_reporting_size(again)

    def test_a_queryset_update_is_seen_after_forget(self):
        ChurchPolicy.objects.create(church=self.church, min_reporting_size=9)
        self.assertEqual(policy.min_reporting_size(self.church), 9)
        ChurchPolicy.objects.filter(church=self.church).update(min_reporting_size=12)
        policy.forget(self.church)
        self.assertEqual(policy.min_reporting_size(self.church), 12)

    def test_a_first_test_that_saves_a_row(self):
        ChurchPolicy.objects.create(church=self.church, min_reporting_size=15)
        self.assertEqual(policy.min_reporting_size(self.church), 15)

    def test_nothing_leaks_from_a_test_that_saved_a_row(self):
        """Runs after the one above. Its row was rolled back, and what it read stayed on its own copy
        of the church (each test gets one): there is no table of answers outside the objects."""
        self.assertFalse(ChurchPolicy.objects.exists())
        self.assertEqual(policy.min_reporting_size(self.church), 5)
