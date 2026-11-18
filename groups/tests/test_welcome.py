"""The welcome of "Mi iglesia" (core.onboarding.for_member, groups.views.home, partials/ui/welcome.html).

The first time a person of the church opens "Mi iglesia" they read what the place is and three things to do first,
each a link; who guides a group reads one more line, "A tu cargo". "Entendido" puts it away for good and "Mi cuenta"
brings it back, as for every other welcome. It names nothing the person cannot open.
"""

from django.test import Client
from django.urls import reverse

from core.models import OnboardingState
from core.onboarding import KEYS, MEMBER_KEY, dismiss, is_dismissed
from core.test_onboarding import steps_of, welcome_of
from events.tests.test_ui import ENGLISH, analyse
from groups import services
from groups.models import GroupMembership
from groups.tests.base import CommunityFixture
from people import flags

HOME = reverse("community:home")


class MemberWelcomeTests(CommunityFixture):
    def home(self, user) -> str:
        self.client.force_login(user)
        response = self.client.get(HOME)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_a_person_of_the_church_reads_it_the_first_time_with_three_real_links(self):
        page = self.home(self.member_user)
        key, block = welcome_of(page)
        self.assertEqual(key, MEMBER_KEY)
        self.assertIn(MEMBER_KEY, KEYS)
        self.assertIn("Te damos la bienvenida, Diego.", block)
        self.assertIn(f"tu lugar en {self.church.name}.", block)
        for name, label in (("agenda:list", "Eventos"), ("community:groups", "Grupos"), ("community:profile", "Mis datos")):
            self.assertIn(f'<a href="{reverse(name)}">{label}</a>', block)
            self.client.force_login(self.member_user)
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertIn("busca un grupo y pide unirte", block)  # Diego has no group and one receives requests
        self.assertEqual(block.count("<li>"), 3)
        self.assertEqual(steps_of(block), [])
        self.assertNotIn("Primeros pasos", block)
        # Above the next-step panel, never instead of it
        self.assertIn('aria-labelledby="que-sigue"', page)
        self.assertLess(page.index('id="bienvenida"'), page.index('aria-labelledby="que-sigue"'))

    def test_it_names_nothing_the_person_cannot_open(self):
        _, block = welcome_of(self.home(self.member_user))
        for word in ("A tu cargo", "Personas", "Panorama", "Administración", "Configuración", "Servir", "Acompañar"):
            self.assertNotIn(word, block.replace("administración de tu iglesia", ""), word)
        self.assertNotIn(reverse("community:group_people", args=[self.group.pk]), block)

    def test_who_guides_a_group_reads_one_more_line_to_a_tu_cargo(self):
        page = self.home(self.leader_user)
        _, block = welcome_of(page)
        url = reverse("community:group_people", args=[self.group.pk])
        self.assertIn(f'<a href="{url}">A tu cargo</a>: las personas de Jóvenes Roca', block)
        self.assertEqual(block.count("<li>"), 4)
        self.assertIn("tus grupos y los demás grupos de la iglesia", block)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertIn("A tu cargo", page.replace(block, ""))  # the shell lists it too: the line points at something real

    def test_a_guide_of_an_archived_group_only_is_not_sent_to_a_tu_cargo(self):
        services.set_membership(self.other_group, self.member, self.admin, role=GroupMembership.Role.LEADER)
        self.other_group.status = self.other_group.Status.ARCHIVED
        self.other_group.save()
        _, block = welcome_of(self.home(self.member_user))
        self.assertNotIn("A tu cargo", block)

    def test_entendido_puts_it_away_for_good_and_mi_cuenta_brings_it_back(self):
        self.home(self.member_user)
        response = self.client.post(reverse("welcome_dismiss"), {"key": MEMBER_KEY, "siguiente": HOME})
        self.assertRedirects(response, HOME)
        self.assertIsNone(welcome_of(self.client.get(HOME).content.decode()))
        other = Client()  # a new session, another day
        other.force_login(self.member_user)
        self.assertIsNone(welcome_of(other.get(HOME).content.decode()))
        self.assertEqual(OnboardingState.objects.filter(user=self.member_user, key=MEMBER_KEY).count(), 1)
        account = self.client.get(reverse("account")).content.decode()
        self.assertIn("La bienvenida de Mi iglesia", account)
        self.assertRedirects(self.client.post(reverse("welcome_restore"), {"key": MEMBER_KEY}), reverse("account"))
        self.assertFalse(is_dismissed(self.member_user, MEMBER_KEY))
        self.assertIsNotNone(welcome_of(self.client.get(HOME).content.decode()))

    def test_putting_it_away_is_each_persons_own(self):
        dismiss(self.member_user, MEMBER_KEY)
        self.assertIsNone(welcome_of(self.home(self.member_user)))
        self.assertIsNotNone(welcome_of(self.home(self.leader_user)))

    def test_an_event_only_account_never_meets_it(self):
        """A tutor or a coordinator with no person in the church has no "Mi iglesia", so no welcome of it anywhere."""
        for user in (self.tutor1, self.coord2):
            self.client.force_login(user)
            self.assertEqual(self.client.get(HOME).status_code, 404)
            page = self.client.get(reverse("events:list") + "?todos=1").content.decode()
            found = welcome_of(page)
            self.assertTrue(found is None or found[0] != MEMBER_KEY)
            self.assertNotIn("La bienvenida de Mi iglesia", self.client.get(reverse("account")).content.decode())

    def test_with_the_module_off_there_is_no_mi_iglesia_and_no_welcome(self):
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(HOME).status_code, 404)
        self.assertFalse(OnboardingState.objects.filter(key=MEMBER_KEY).exists())

    def test_it_is_spanish(self):
        for user in (self.member_user, self.leader_user, self.admin):
            _, block = welcome_of(self.home(user))
            self.assertIsNone(ENGLISH.search(" ".join(analyse(block).text)), user.email)
