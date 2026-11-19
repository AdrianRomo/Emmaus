"""«Mi iglesia» of who coordinates a team: a coming date where nobody serves (somebody was asked and every one asked
said no) waits on the home too, with «Pedir a alguien» to that date. A pending answer may still be a yes, so it does
not count; a date nobody was asked for is the team page's own row; somebody else's team never shows."""

from __future__ import annotations

from django.urls import reverse

from serving import services

from .test_serving import ServingFixture


class NobodyServesOnTheCoordinatorsHomeTests(ServingFixture):
    def home(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("community:home")).content.decode()

    def test_everyone_asked_said_no_raises_the_date(self):
        assignment = self.assign(self.member)
        services.respond(assignment, False, self.member_user)
        page = self.home(self.leader_user)
        self.assertIn("Nadie sirve el", page)
        self.assertIn("Equipo Bienvenida", page)
        self.assertIn(f'href="{reverse("serving:date_detail", args=[self.day.pk])}#quien-sirve">Pedir a alguien', page)

    def test_a_pending_answer_or_a_yes_does_not(self):
        declined = self.assign(self.member)
        services.respond(declined, False, self.member_user)
        self.assign(self.asked)  # still to answer: may be a yes
        self.assertNotIn("Nadie sirve el", self.home(self.leader_user))

    def test_who_only_serves_never_sees_it(self):
        assignment = self.assign(self.member)
        services.respond(assignment, False, self.member_user)
        self.assertNotIn("Nadie sirve el", self.home(self.member_user))
