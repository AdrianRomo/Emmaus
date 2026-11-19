"""A follow-up is given only to someone who could open it (browser QA, 2026-11-18): a tutor sees their own
coordinadores' camperos, a confidential follow-up is for those with sensitive access, a safety report for them and the
administration. Offered otherwise, the task disappeared for its new owner. Whoever has it now stays in the list."""

from __future__ import annotations

from django.urls import reverse

from events.forms import FollowUpForm, assignable
from events.models import FollowUp

from .base import CampFixture


class AssignableToWhoCanOpenItTests(CampFixture):
    def follow_up(self, **extra):
        return FollowUp.objects.create(event=self.event, participant=self.p1, created_by=self.coord1, **extra)

    def offered(self, follow_up):
        return set(FollowUpForm(instance=follow_up, event=self.event, can_write_notes=True).fields["assigned_to"].queryset)

    def test_an_ordinary_one_goes_to_its_own_tutor_not_another_teams(self):
        offered = self.offered(self.follow_up())
        self.assertIn(self.tutor1, offered)  # p1's coordinadora is supervised by tutor1
        self.assertNotIn(self.tutor2, offered)  # another team: could not open it
        self.assertTrue({self.pastor, self.care, self.coord1} <= offered)

    def test_a_confidential_one_only_to_who_has_sensitive_access(self):
        offered = self.offered(self.follow_up(confidential=True))
        self.assertIn(self.care, offered)
        self.assertFalse({self.pastor, self.tutor1, self.tutor2} & offered)

    def test_whoever_has_it_and_could_not_open_it_stays_shown_and_is_said_why(self):
        follow_up = self.follow_up(assigned_to=self.tutor2)  # given before this rule
        self.assertNotIn(self.tutor2, assignable(follow_up, self.event)[0])  # not offered to anyone else's choice
        self.client.force_login(self.admin)
        page = self.client.get(reverse("events:follow_ups", args=[self.event.slug])).content.decode()
        self.assertIn(f'<option value="{self.tutor2.id}" selected>Tomás Dos (no puede ver este seguimiento)</option>', page)

    def test_a_post_for_someone_who_could_not_open_it_is_refused(self):
        follow_up = self.follow_up()
        form = FollowUpForm({"status": follow_up.status, "assigned_to": self.tutor2.pk}, instance=follow_up, event=self.event, can_write_notes=True)
        self.assertFalse(form.is_valid())
        self.assertIn("assigned_to", form.errors)

    def test_its_page_keeps_them_when_a_status_is_saved(self):
        follow_up = self.follow_up(assigned_to=self.tutor2)
        form = FollowUpForm({"status": "en_proceso", "assigned_to": self.tutor2.pk}, instance=follow_up, event=self.event, can_write_notes=True)
        self.assertIn("Tomás Dos (no puede ver este seguimiento)", str(form["assigned_to"]))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["assigned_to"], self.tutor2)

