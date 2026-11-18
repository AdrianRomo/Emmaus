"""A person's own attendance on their group's page: theirs alone, since they joined, and never a score."""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from groups import services
from groups.models import GroupMeeting, GroupMembership

from .base import CommunityFixture

CAME = '<span class="status__mark" aria-hidden="true"></span>Fuiste</span>'  # the pill of a day the person came


class OwnAttendanceTests(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.today = timezone.localdate()
        cls.membership = services.set_membership(cls.group, cls.member, cls.admin)
        services.set_membership(cls.group, cls.no_account, cls.admin)
        # Everybody joined two months ago: what is listed starts the day a person joined.
        GroupMembership.objects.filter(group=cls.group).update(start_at=timezone.now() - timedelta(days=60))
        cls.membership.refresh_from_db()
        cls.url = reverse("community:group_detail", args=[cls.group.pk])

    def record(self, days_ago, present):
        return services.record_meeting(self.group, self.today - timedelta(days=days_ago), [p.pk for p in present], self.leader_user)

    def page(self, user):
        self.client.force_login(user)
        return self.client.get(self.url)

    def test_nothing_is_shown_without_recorded_meetings(self):
        self.assertNotContains(self.page(self.member_user), "Tu asistencia")

    def test_the_member_reads_only_their_own_days(self):
        self.record(14, [self.member, self.leader, self.no_account])
        self.record(7, [self.leader, self.no_account])
        response = self.page(self.member_user)
        self.assertContains(response, "Tu asistencia")
        html = response.content.decode()
        section = html[html.index('id="asistencia"'):html.index("Esto lo ves solo tú")]
        self.assertEqual(section.count(CAME), 1)  # each day ends in one pill: "Fuiste" or "No fuiste"
        self.assertEqual(section.count("No fuiste"), 1)
        self.assertLess(section.index("No fuiste"), section.index(CAME))  # newest first
        # Nobody else is on the page, and nothing compares or scores.
        for absent in ("Nora", "Sin Cuenta", "%", "racha", "seguidas", "promedio", " de 3", "reuniones/"):
            self.assertNotIn(absent, section, absent)
        self.assertNotContains(response, "Nora")

    def test_only_meetings_since_the_person_joined(self):
        self.record(7, [self.leader])
        GroupMembership.objects.filter(pk=self.membership.pk).update(start_at=timezone.now() - timedelta(days=3))
        self.assertNotContains(self.page(self.member_user), "Tu asistencia")
        self.record(1, [self.member])
        rows = services.own_attendance(GroupMembership.objects.get(pk=self.membership.pk))
        self.assertEqual([(row.date, row.came) for row in rows], [(self.today - timedelta(days=1), True)])

    def test_it_is_shown_to_nobody_else(self):
        self.record(7, [self.member, self.leader])
        # Somebody who is not in the group, and the administration looking at the group, read no "Tu asistencia".
        for user in (self.asked_user, self.admin, self.coord1):
            self.assertNotContains(self.page(user), "Tu asistencia", msg_prefix=user.email)
        # Who guides the group is in it: they read their own, like anybody.
        self.assertContains(self.page(self.leader_user), CAME)
        # Somebody who left reads nothing.
        services.end_membership(self.membership, self.admin)
        self.assertNotContains(self.page(self.member_user), "Tu asistencia")

    def test_the_last_eight_and_no_more(self):
        for week in range(1, 11):
            GroupMeeting.objects.create(group=self.group, date=self.today - timedelta(weeks=week), expected=3)
        GroupMembership.objects.filter(pk=self.membership.pk).update(start_at=timezone.now() - timedelta(weeks=20))
        html = self.page(self.member_user).content.decode()
        self.assertEqual(html.count("No fuiste"), services.OWN_ATTENDANCE_LIMIT)

    def test_a_meeting_that_kept_only_its_totals_says_nothing_about_the_person(self):
        meeting = self.record(7, [self.member, self.leader])
        meeting.attendances.all().delete()
        GroupMeeting.objects.filter(pk=meeting.pk).update(present_kept=2)
        self.assertNotContains(self.page(self.member_user), "Tu asistencia")  # never "No fuiste" for a list that is gone
