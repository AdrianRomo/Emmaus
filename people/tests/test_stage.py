"""The membership stage: a label the church's administration sets by hand, audited, never inferred."""

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from groups import services as groups
from groups.models import GroupAttendance, GroupMeeting
from groups.tests.base import CommunityFixture
from people import flags, services
from people.models import ChurchAuditEvent, Person


class StageTests(CommunityFixture):
    def url(self, person=None):
        return reverse("people:stage", args=[(person or self.no_account).pk])

    def stage(self, person=None):
        return Person.objects.get(pk=(person or self.no_account).pk).stage

    def test_the_administration_sets_it_and_the_audit_row_holds_ids_and_the_two_words(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url(), {"etapa": "asistente", "antes": ""}, follow=True)
        self.assertContains(response, "Guardamos la etapa.")
        self.assertContains(response, "Asistente, anotada el")
        person = Person.objects.get(pk=self.no_account.pk)
        self.assertEqual((person.stage, person.stage_changed_on), ("asistente", timezone.localdate()))
        self.client.post(self.url(), {"etapa": "miembro", "antes": "asistente"})
        rows = list(ChurchAuditEvent.objects.filter(action="persona_etapa").order_by("pk"))
        self.assertEqual([row.metadata for row in rows], [
            {"person_id": person.pk, "previous": "sin_etapa", "value": "asistente"},
            {"person_id": person.pk, "previous": "asistente", "value": "miembro"},
        ])
        for row in rows:
            self.assertNotIn("Nora", row.summary)
        # Saying the same again writes nothing and keeps the date.
        self.client.post(self.url(), {"etapa": "miembro", "antes": "miembro"})
        self.assertEqual(ChurchAuditEvent.objects.filter(action="persona_etapa").count(), 2)
        self.client.post(self.url(), {"etapa": "", "antes": "miembro"})
        self.assertEqual(self.stage(), "")

    def test_only_the_administration_of_this_church(self):
        data = {"etapa": "miembro", "antes": ""}
        self.assertEqual(self.client.post(self.url(), data).status_code, 302)  # signed out: to sign in
        for user, expected in ((self.member_user, 403), (self.leader_user, 403), (self.coord1, 403), (self.admin_b, 404)):
            self.client.force_login(user)
            self.assertEqual(self.client.post(self.url(), data).status_code, expected, user.email)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url(self.member_b), data).status_code, 404)  # a guessed id of church B
        self.assertEqual(self.client.get(self.url()).status_code, 405)
        self.assertEqual((self.stage(), self.stage(self.member_b)), ("", ""))
        hidden = Person.objects.create(church=self.church, first_name="Dora", merged_into=self.member)
        self.assertEqual(self.client.post(self.url(hidden), data).status_code, 404)
        flags.set_enabled(self.church, "people", False)
        self.assertEqual(self.client.post(self.url(), data).status_code, 404)
        self.assertEqual(self.stage(), "")
        self.assertFalse(ChurchAuditEvent.objects.filter(action="persona_etapa").exists())

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(self.url(), {"etapa": "miembro", "antes": ""}).status_code, 403)
        self.assertEqual(self.stage(), "")

    def test_a_word_outside_the_list_and_another_church_are_refused_by_the_service(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url(), {"etapa": "<b>lider</b>", "antes": ""}, follow=True)
        self.assertContains(response, "Elige una etapa de la lista.")
        self.assertEqual(self.stage(), "")
        with self.assertRaises(services.PeopleError):
            services.set_stage(self.church, self.member_b.pk, "miembro", self.admin, seen="")
        self.assertEqual(self.stage(self.member_b), "")

    def test_a_screen_opened_before_somebody_else_changed_it_changes_nothing(self):
        self.client.force_login(self.admin)
        services.set_stage(self.church, self.no_account.pk, "miembro", self.admin, seen="")
        response = self.client.post(self.url(), {"etapa": "visita", "antes": ""}, follow=True)
        self.assertContains(response, "Alguien más cambió la etapa")
        self.assertEqual(self.stage(), "miembro")
        self.assertEqual(ChurchAuditEvent.objects.filter(action="persona_etapa").count(), 1)

    def test_the_edit_form_never_writes_a_stage_and_a_stale_copy_does_not_bring_one_back(self):
        self.client.force_login(self.admin)
        stale = Person.objects.get(pk=self.no_account.pk)  # read before the stage was set
        services.set_stage(self.church, stale.pk, "miembro", self.admin, seen="")
        self.assertNotContains(self.client.get(reverse("people:edit", args=[stale.pk])), 'name="stage"')
        self.client.post(reverse("people:edit", args=[stale.pk]),
                         {"first_name": "Nora", "last_name": "Sin Cuenta", "preferred_contact_method": "email", "stage": "visita"})
        self.assertEqual(self.stage(), "miembro")
        stale.phone = "55 0000 0000"
        services.save_person(stale, self.admin, created=False)
        self.assertEqual(self.stage(), "miembro")

    def test_it_can_be_set_when_registering_a_person(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("people:create"), {"first_name": "Rita", "preferred_contact_method": "email", "stage": "visita"})
        rita = Person.objects.get(first_name="Rita")
        self.assertEqual((rita.stage, rita.stage_changed_on), ("visita", timezone.localdate()))

    def test_nothing_moves_a_stage_by_itself(self):
        """No inference from attendance (decision 0009): a list taken, a membership or its end leave it alone."""
        services.set_stage(self.church, self.member.pk, "ya_no_asiste", self.admin, seen="")
        groups.set_membership(self.group, self.member, self.admin)
        meeting = GroupMeeting.objects.create(group=self.group, date=timezone.localdate())
        GroupAttendance.objects.create(meeting=meeting, person=self.member)
        self.assertEqual(self.stage(self.member), "ya_no_asiste")
        self.assertEqual(self.stage(self.leader), "")

    def test_nobody_but_the_administration_reads_it(self):
        services.set_stage(self.church, self.member.pk, "ya_no_asiste", self.admin, seen="")
        self.client.force_login(self.member_user)
        for name in ("community:home", "community:profile"):
            self.assertNotContains(self.client.get(reverse(name)), "Ya no asiste")
        groups.set_membership(self.group, self.member, self.admin)
        self.client.force_login(self.leader_user)
        response = self.client.get(reverse("community:group_people", args=[self.group.pk]))
        self.assertContains(response, "Diego")
        self.assertNotContains(response, "Ya no asiste")
