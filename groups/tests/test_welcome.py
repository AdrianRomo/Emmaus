"""The welcome of "Mi iglesia" (core.onboarding.for_member, groups.views.home, partials/ui/welcome.html).

The first time a person of the church opens "Mi iglesia" they read what the place is and three things to do first,
each a link; who guides a group reads one more line, "A tu cargo". "Entendido" puts it away for good and "Mi cuenta"
brings it back, as for every other welcome. It names nothing the person cannot open.
"""

from django.urls import reverse

from core.models import OnboardingState
from core.onboarding import KEYS, MEMBER_KEY, dismiss, is_dismissed
from core.test_onboarding import welcome_of
from groups.tests.base import CommunityFixture

HOME = reverse("community:home")


class MemberWelcomeTests(CommunityFixture):
    """The course correction of 2026-11-18: "Mi iglesia" opens on what waits for the person and on the days
    that come. It carries no welcome panel and no list of links: the navigation is where the places are."""

    def home(self, user) -> str:
        self.client.force_login(user)
        response = self.client.get(HOME)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_home_has_no_welcome_panel_for_a_member_or_a_guide(self):
        for user in (self.member_user, self.leader_user):
            page = self.home(user)
            self.assertIsNone(welcome_of(page))
            self.assertNotIn("Para empezar", page)
            self.assertNotIn("Entendido", page)
        self.assertFalse(OnboardingState.objects.filter(key=MEMBER_KEY).exists())

    def test_what_waits_for_a_guide_is_the_first_thing_on_the_page(self):
        page = self.home(self.leader_user)
        main = page[page.index("<main"):]
        self.assertIn('aria-labelledby="que-sigue"', main)
        self.assertLess(main.index('aria-labelledby="que-sigue"'), main.index('class="heading'))

    def test_the_key_still_exists_for_who_put_it_away_before(self):
        self.assertIn(MEMBER_KEY, KEYS)
        dismiss(self.member_user, MEMBER_KEY)
        self.assertTrue(is_dismissed(self.member_user, MEMBER_KEY))
