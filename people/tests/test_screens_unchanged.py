"""With the modules off, the screens that existed before are what they were (decision 0004, rule 6).

The reference is rendered in the same run: a church with no settings row at all. Every other "off"
state must give the same bytes, and turning the module on must be the only thing that changes them.
The camp's own byte-level tests of the bar (events/tests, core/tests) run unchanged beside this one.
"""

import re

from django.test import override_settings
from django.urls import reverse

from events.tests.base import CampFixture
from people import flags
from people.models import ChurchSettings, Person

NEW_WORDS = ("Mi iglesia", "/mi-iglesia/")


def masked(response) -> str:
    # The CSRF token and the idle countdown (seconds left in the session) change between two requests by themselves.
    html = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", response.content.decode())
    return re.sub(r'data-idle-remaining="\d+"', "", html)


class ScreensUnchangedTests(CampFixture):
    def pages(self):
        return [
            (self.admin, reverse("events:list") + "?todos=1"), (self.admin, reverse("account")), (self.admin, reverse("ayuda")),
            (self.admin, reverse("events:admin_home", args=[self.event.slug])), (self.admin, reverse("church_settings")),
            (self.coord1, reverse("events:coord_home", args=[self.event.slug])), (self.coord1, reverse("account")),
            (self.tutor1, reverse("events:tutor_home", args=[self.event.slug])), (self.outsider, reverse("events:list") + "?todos=1"),
        ]

    def render_all(self) -> list[str]:
        pages = []
        for user, url in self.pages():
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            pages.append(masked(response))
        return pages

    def test_off_states_render_the_same_bytes_and_on_adds_the_links(self):
        reference = self.render_all()
        for words in NEW_WORDS:
            self.assertFalse(any(words in page for page in reference))
        Person.objects.create(church=self.church, user=self.admin, first_name="Ada")
        Person.objects.create(church=self.church, user=self.coord1, first_name="Carla")
        self.assertEqual(self.render_all(), reference)  # people exist, no row
        ChurchSettings.objects.create(church=self.church)
        self.assertEqual(self.render_all(), reference)  # a row with every flag off
        flags.set_enabled(self.church, "community", True)
        flags.set_enabled(self.church, "people", True)
        with override_settings(EMMAUS_V2_MODULES=False):
            self.assertEqual(self.render_all(), reference)  # flags on, kill switch off
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("account")), "Mi iglesia")
        self.client.force_login(self.tutor1)  # an event role without a person: still nothing new
        self.assertNotContains(self.client.get(reverse("events:list") + "?todos=1"), "Mi iglesia")

    def test_every_flag_is_covered_here(self):
        self.assertEqual(flags.MODULES, {"community", "people"})

    def test_home_router_is_unchanged_while_off(self):
        member = Person.objects.create(church=self.church, user=self.outsider, first_name="Óscar")
        self.client.force_login(self.outsider)
        self.assertRedirects(self.client.get(reverse("dashboard")), reverse("events:list"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("community:home")).status_code, 404)
        self.assertIsNotNone(member.pk)
