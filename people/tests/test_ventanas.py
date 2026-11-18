"""What «Ventanas» promises on the church's people (docs/v2/DESIGN_SYSTEM.md, "People and gatherings", "Rows"):
a person is a circle and a group an arch, wherever either is drawn; the directory shows no more than it did; a
quiet removal asks first; the search runs on Enter."""

import re

from django.urls import reverse

from groups import services as groups
from groups.tests.base import CommunityFixture
from people import services


def monograms(html: str) -> list[str]:
    """The class of every monogram drawn on a page, in order."""
    return re.findall(r'<span class="(mono[^"]*)" aria-hidden="true">', html)


class PeopleOnVentanasTests(CommunityFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_a_person_is_a_circle_and_their_groups_are_arches(self):
        groups.set_membership(self.group, self.member, self.admin)
        groups.set_membership(self.other_group, self.member, self.admin)
        html = self.client.get(reverse("people:detail", args=[self.member.pk])).content.decode()
        drawn = monograms(html[html.index("<main"):])
        self.assertEqual(drawn[0], "mono mono--lg")  # the person: a circle
        self.assertEqual(drawn[1:], ["mono mono--grupo", "mono mono--grupo"])  # «Jóvenes Roca» and the other: arches
        section = html[html.index('id="grupos"'):html.index('id="datos"')]
        self.assertIn(f'href="{reverse("community:group_people", args=[self.group.pk])}"', section)  # the row is the way in

    def test_the_directory_shows_the_name_and_whether_they_can_sign_in_and_nothing_more(self):
        html = self.client.get(reverse("people:list")).content.decode()
        listing = html[html.index('class="rows personas-lista"'):html.index("</ul>", html.index('class="rows personas-lista"'))]
        self.assertIn("Diego Hernández Cruz", listing)
        for private in ("55 1111 2222", "diego@prueba.mx", "Jóvenes Roca", "Campamento A"):
            self.assertNotIn(private, listing)
        self.assertTrue(all(css == "mono" for css in monograms(listing)))  # every person a circle, one size
        self.assertNotIn(">Buscar</button>", html)  # the search runs on Enter, as the camper list's

    def test_unlinking_is_a_quiet_action_that_asks_first(self):
        person = services.create_person_from_participant(self.p4, self.admin)
        html = self.client.get(reverse("people:detail", args=[person.pk])).content.decode()
        button = re.search(r'<button class="btn btn--link btn--quieto" type="submit" data-confirm="([^"]+)"', html)
        self.assertIsNotNone(button)
        self.assertIn("No se borra nada del evento ni a la persona.", button.group(1))
        self.assertIn("Quitar un vínculo no borra nada del evento ni a la persona.", html)  # the note stays without JS

    def test_the_events_to_bring_from_lead_with_their_day_and_say_their_state(self):
        html = self.client.get(reverse("people:from_event_list")).content.decode()
        self.assertIn('class="date-block', html)
        self.assertIn("por traer</span>", html)
        self.assertIn("Solo pasan el nombre y la sede.", html)  # what crosses over is still said
