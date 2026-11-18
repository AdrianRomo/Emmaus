"""Group meetings and attendance: who guides a group says who came; nobody else reads it."""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from groups import services
from groups.models import Group, GroupAttendance, GroupMeeting, GroupMembership
from people import flags
from people.models import ChurchAuditEvent

from .base import CommunityFixture


class MeetingTests(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        services.set_membership(cls.group, cls.member, cls.admin, role=GroupMembership.Role.MEMBER)
        cls.today = timezone.localdate()
        cls.yesterday = cls.today - timedelta(days=1)

    def url(self, day=None, group=None):
        group = group or self.group
        if day is None:
            return reverse("community:group_meetings", args=[group.pk])
        return reverse("community:meeting_record", args=[group.pk, day.isoformat()])

    def test_the_guide_passes_the_list_and_corrects_it(self):
        self.client.force_login(self.leader_user)
        page = self.client.get(self.url(self.yesterday))
        self.assertContains(page, "Diego Hernández Cruz")
        self.assertContains(page, "Gala Guía")
        self.assertNotContains(page, "checked")
        response = self.client.post(self.url(self.yesterday), {"vino": [self.member.pk, self.leader.pk]}, follow=True)
        self.assertContains(response, "vinieron 2 personas de 2.")
        self.assertContains(response, "Vinieron 2 de 2")
        meeting = GroupMeeting.objects.get(group=self.group, date=self.yesterday)
        self.assertEqual((meeting.expected, meeting.recorded_by), (2, self.leader_user))
        # Opening the day again shows the list; saving it again replaces it.
        self.assertContains(self.client.get(self.url(self.yesterday)), "Este día ya tiene lista.")
        response = self.client.post(self.url(self.yesterday), {"vino": [self.leader.pk]}, follow=True)
        self.assertContains(response, "vino 1 persona de 2.")
        self.assertEqual(list(meeting.attendances.values_list("person_id", flat=True)), [self.leader.pk])
        self.assertEqual(GroupMeeting.objects.filter(group=self.group).count(), 1)

    def test_correcting_a_list_keeps_who_came_and_left_the_group_since(self):
        self.client.force_login(self.leader_user)
        self.client.post(self.url(self.yesterday), {"vino": [self.member.pk, self.leader.pk]})
        services.end_membership(GroupMembership.objects.get(group=self.group, person=self.member), self.admin)
        page = self.client.get(self.url(self.yesterday))
        self.assertNotContains(page, "Diego Hernández Cruz")  # no longer in the group: not on the form
        response = self.client.post(self.url(self.yesterday), {"vino": [self.leader.pk]}, follow=True)
        meeting = GroupMeeting.objects.get(group=self.group, date=self.yesterday)
        self.assertEqual(set(meeting.attendances.values_list("person_id", flat=True)), {self.member.pk, self.leader.pk})
        self.assertEqual(meeting.expected, 2)  # how many were in the group that day, not today
        self.assertContains(response, "vinieron 2 personas de 2.")

    def test_a_group_with_nobody_says_zero_people(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("community:group_people", args=[self.other_group.pk]))
        self.assertContains(response, "0 personas en el grupo")
        self.assertNotContains(response, "QuerySet")
        self.assertContains(self.client.get(self.url(group=self.other_group)), "0 personas en el grupo")

    def test_only_people_of_the_group_can_be_marked(self):
        self.client.force_login(self.leader_user)
        self.client.post(self.url(self.yesterday), {"vino": [self.asked.pk, self.member_b.pk, "x", self.member.pk]})
        self.assertEqual(list(GroupAttendance.objects.values_list("person_id", flat=True)), [self.member.pk])

    def test_the_audit_row_carries_the_count_and_no_name(self):
        self.client.force_login(self.leader_user)
        self.client.post(self.url(self.yesterday), {"vino": [self.member.pk]})
        row = ChurchAuditEvent.objects.get(action="grupo_reunion")
        self.assertIn("1 de 2", row.summary)
        self.assertNotIn("Diego", row.summary)
        self.assertEqual(row.metadata, {"group_id": self.group.pk})

    def test_a_day_that_has_not_come_does_not_exist(self):
        self.client.force_login(self.leader_user)
        tomorrow = self.today + timedelta(days=1)
        self.assertEqual(self.client.get(self.url(tomorrow)).status_code, 404)
        self.assertEqual(self.client.post(self.url(tomorrow), {"vino": [self.member.pk]}).status_code, 404)
        self.assertEqual(self.client.get(reverse("community:meeting_record", args=[self.group.pk, "ayer"])).status_code, 404)
        self.assertFalse(GroupMeeting.objects.exists())
        with self.assertRaises(services.GroupError):
            services.record_meeting(self.group, tomorrow, [self.member.pk], self.leader_user)

    def test_who_reads_and_who_does_not(self):
        services.record_meeting(self.group, self.yesterday, [self.member.pk], self.leader_user)
        for user, expected in (
            (self.leader_user, 200), (self.admin, 200),
            (self.member_user, 403),   # in the group, but does not guide it
            (self.coord1, 403),        # a former coordinator of the camp holds nothing here
            (self.admin_b, 404),       # another church: the group does not exist
        ):
            self.client.force_login(user)
            for url in (self.url(), self.url(self.yesterday)):
                self.assertEqual(self.client.get(url).status_code, expected, (user.email, url))
            self.assertEqual(self.client.post(self.url(self.yesterday), {"vino": []}).status_code, 302 if expected == 200 else expected)
        # A member's own pages say nothing about who came.
        self.client.force_login(self.member_user)
        for name, args in (("community:home", []), ("community:group_detail", [self.group.pk])):
            response = self.client.get(reverse(name, args=args))
            self.assertNotContains(response, "Vino 1 de")
            self.assertNotContains(response, "reuniones/")

    def test_the_module_off_hides_the_pages(self):
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.client.get(self.url(self.yesterday)).status_code, 404)

    def test_the_date_field_opens_that_day(self):
        self.client.force_login(self.leader_user)
        response = self.client.get(self.url() + f"?fecha={self.yesterday.isoformat()}")
        self.assertRedirects(response, self.url(self.yesterday))
        self.assertContains(self.client.get(self.url() + "?fecha=12/11", follow=True), "Escribe la fecha con el calendario")

    def test_an_archived_group_takes_no_list(self):
        services.archive_group(self.group, self.admin)
        self.client.force_login(self.admin)
        response = self.client.post(self.url(self.yesterday), {"vino": [self.member.pk]}, follow=True)
        self.assertContains(response, "Este grupo está archivado")
        self.assertFalse(GroupMeeting.objects.exists())


class MeetingDueTests(CommunityFixture):
    def due_group(self, **changes):
        Group.objects.filter(pk=self.group.pk).update(**changes)
        self.group.refresh_from_db()
        return self.group

    def test_the_last_meeting_waits_for_its_list_until_it_has_one(self):
        today = timezone.localdate()
        last = today - timedelta(days=2)
        group = self.due_group(meeting_day=last.weekday(), created_at=timezone.now() - timedelta(days=30))
        self.assertEqual(group.last_meeting_day(today), last)
        self.assertEqual(services.meeting_due(group, today), last)
        self.client.force_login(self.leader_user)
        GroupMembership.objects.filter(group=group).exclude(person=self.leader).delete()
        type(self.pending).objects.filter(pk=self.pending.pk).delete()  # no request waiting: the list is the next step
        home = self.client.get(reverse("community:home"))
        self.assertContains(home, "¿Quién vino a Jóvenes Roca?")
        self.assertContains(home, reverse("community:meeting_record", args=[group.pk, last.isoformat()]))
        self.assertContains(self.client.get(reverse("community:group_meetings", args=[group.pk])), "Pasar lista")
        services.record_meeting(group, last, [], self.leader_user)
        self.assertIsNone(services.meeting_due(group, today))
        self.assertNotContains(self.client.get(reverse("community:home")), "¿Quién vino a")

    def test_no_list_is_asked_before_the_group_existed_or_without_a_day(self):
        today = timezone.localdate()
        last = today - timedelta(days=2)
        self.assertIsNone(services.meeting_due(self.due_group(meeting_day=last.weekday()), today))  # created today
        self.assertIsNone(services.meeting_due(self.due_group(meeting_day=None), today))

    def test_a_request_waiting_comes_before_the_list(self):
        today = timezone.localdate()
        self.due_group(meeting_day=(today - timedelta(days=1)).weekday(), created_at=timezone.now() - timedelta(days=30))
        self.client.force_login(self.leader_user)
        home = self.client.get(reverse("community:home"))
        self.assertContains(home, "quiere unirse a")
        self.assertNotContains(home, "¿Quién vino a")
