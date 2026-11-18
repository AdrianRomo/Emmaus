"""``prune_attendance``: after 12 months the totals stay and the names go; after 24 the meeting goes."""

from datetime import date, timedelta
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from groups import services
from groups.management.commands.prune_attendance import months_before, prune
from people import policy
from groups.models import GroupAttendance, GroupMeeting

from .base import CommunityFixture


def run(*args) -> str:
    out = StringIO()
    call_command("prune_attendance", *args, stdout=out)
    return out.getvalue()


class PruneAttendanceTests(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.today = timezone.localdate()
        services.set_membership(cls.group, cls.member, cls.admin)
        services.set_membership(cls.group, cls.no_account, cls.admin)
        cls.recent = cls._meeting(months_before(cls.today, 11), 3, [cls.member, cls.leader])
        cls.aged = cls._meeting(months_before(cls.today, 13), 3, [cls.member, cls.leader])
        cls.aged_empty = cls._meeting(months_before(cls.today, 14), 2, [])
        cls.expired = cls._meeting(months_before(cls.today, 25), 3, [cls.leader])
        cls.other_church = cls._meeting(months_before(cls.today, 13), 1, [cls.member_b], group=cls.group_b)

    @classmethod
    def _meeting(cls, day, expected, present, group=None):
        meeting = GroupMeeting.objects.create(group=group or cls.group, date=day, expected=expected)
        GroupAttendance.objects.bulk_create(GroupAttendance(meeting=meeting, person=person) for person in present)
        return meeting

    def state(self):
        return (
            sorted(GroupMeeting.objects.values_list("pk", "expected", "present_kept")),
            sorted(GroupAttendance.objects.values_list("meeting_id", "person_id")),
        )

    def test_without_apply_it_only_counts(self):
        before = self.state()
        output = run()
        self.assertEqual(self.state(), before)
        self.assertIn("ensayo", output)
        # Church by church, each by its own periods (people.policy.attendance_retention)
        self.assertIn("solo sus totales: 2 (registros de quién vino borrados: 2)", output)
        self.assertIn("solo sus totales: 1 (registros de quién vino borrados: 1)", output)
        self.assertIn("meses borradas: 1 (registros de quién vino borrados con ellas: 1)", output)

    def test_apply_keeps_the_totals_and_deletes_the_names(self):
        output = run("--apply")
        self.assertIn("Se hizo", output)
        self.aged.refresh_from_db()
        self.aged_empty.refresh_from_db()
        self.recent.refresh_from_db()
        self.assertEqual((self.aged.present_kept, self.aged.expected, self.aged.names_deleted), (2, 3, True))
        self.assertEqual((self.aged_empty.present_kept, self.aged_empty.expected), (0, 2))
        self.assertFalse(GroupAttendance.objects.filter(meeting__in=[self.aged, self.aged_empty, self.other_church]).exists())
        self.assertFalse(GroupMeeting.objects.filter(pk=self.expired.pk).exists())
        # What is younger than 12 months is untouched.
        self.assertIsNone(self.recent.present_kept)
        self.assertEqual(self.recent.attendances.count(), 2)

    def test_running_it_again_changes_nothing(self):
        run("--apply")
        after = self.state()
        output = run("--apply")
        self.assertEqual(self.state(), after)
        self.assertIn("solo sus totales: 0 (registros de quién vino borrados: 0)", output)
        self.assertIn("meses borradas: 0 ", output)

    def test_it_prints_counts_and_never_a_name(self):
        output = run() + run("--apply")
        for name in ("Diego", "Hernández", "Gala", "Guía", "Jóvenes Roca", "Bruno", "prueba.mx"):
            self.assertNotIn(name, output)

    def test_the_periods_are_settings(self):
        with override_settings(EMMAUS_ATTENDANCE_IDENTIFIABLE_MONTHS=6, EMMAUS_ATTENDANCE_AGGREGATE_MONTHS=12):
            run("--apply")
        self.assertEqual(list(GroupMeeting.objects.values_list("pk", "present_kept")), [(self.recent.pk, 2)])
        with override_settings(EMMAUS_ATTENDANCE_IDENTIFIABLE_MONTHS=24, EMMAUS_ATTENDANCE_AGGREGATE_MONTHS=12):
            with self.assertRaises(CommandError):
                run("--apply")

    def test_months_before(self):
        self.assertEqual(months_before(date(2026, 11, 17), 12), date(2025, 11, 17))
        self.assertEqual(months_before(date(2026, 3, 31), 1), date(2026, 2, 28))
        self.assertEqual(months_before(date(2024, 2, 29), 24), date(2022, 2, 28))
        self.assertEqual(months_before(date(2026, 1, 15), 13), date(2024, 12, 15))

    def test_the_guide_s_pages_keep_working_with_the_totals(self):
        run("--apply")
        self.client.force_login(self.leader_user)
        listing = self.client.get(reverse("community:group_meetings", args=[self.group.pk]))
        self.assertContains(listing, "Vinieron 2 de 3", count=2)  # the recent one from its rows, the aged one from its total
        self.assertContains(listing, "Vinieron 0 de 2")
        self.assertContains(listing, "Solo totales", count=2)
        url = reverse("community:meeting_record", args=[self.group.pk, self.aged.date.isoformat()])
        page = self.client.get(url)
        self.assertContains(page, "Vinieron 2 personas de 3")
        self.assertContains(page, "se borró por antigüedad")
        self.assertNotContains(page, 'name="vino"')
        self.assertNotContains(page, "Guardar la lista")
        # The list of a meeting that kept only its totals cannot be written again: the total would be lost.
        response = self.client.post(url, {"vino": [self.member.pk]}, follow=True)
        self.assertContains(response, "ya solo se conservan los totales")
        with self.assertRaises(services.GroupError):
            services.record_meeting(self.group, self.aged.date, [self.member.pk], self.leader_user)
        self.aged.refresh_from_db()
        self.assertEqual((self.aged.present_kept, self.aged.attendances.count()), (2, 0))

    def test_a_person_s_own_page_forgets_the_aged_meetings(self):
        from groups.models import GroupMembership

        GroupMembership.objects.filter(group=self.group).update(start_at=timezone.now() - timedelta(days=900))
        run("--apply")
        self.client.force_login(self.member_user)
        html = self.client.get(reverse("community:group_detail", args=[self.group.pk])).content.decode()
        self.assertEqual((html.count("<strong>Fuiste</strong>"), html.count("No fuiste")), (1, 0))


class PruneByChurchPolicyTests(PruneAttendanceTests):
    """A church that chose to keep names for fewer months loses them sooner; the other church does not."""

    def test_each_church_follows_its_own_periods(self):
        from policies.models import ChurchPolicy

        ChurchPolicy.objects.create(church=self.recent.group.church, attendance_identifiable_months=6, attendance_aggregate_months=12)
        policy.forget(self.recent.group.church)
        other_before = GroupAttendance.objects.filter(meeting=self.other_church).count()
        result = prune(timezone.localdate(), apply=False, church=self.other_church.group.church)
        self.assertEqual(result["meetings_to_totals"], 1)
        mine = prune(timezone.localdate(), apply=False, church=self.recent.group.church)
        self.assertGreaterEqual(mine["meetings_deleted"], 1)
        self.assertEqual(GroupAttendance.objects.filter(meeting=self.other_church).count(), other_before)
