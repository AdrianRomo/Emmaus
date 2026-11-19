"""One answer to "which church is this request about" (issue #37): the landing page, the logo and the colours.

Two churches with their own logo and colours. Each reader sees the church of their request; a page
that is about a church or an event (the public pages, the camp) wears that church whoever reads it;
signed out, the first active church, as always.
"""

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import reverse

from agenda import services as agenda
from core import tenancy
from core.authz import SESSION_KEY
from core.models import Church, Role
from core.services import assign_role
from events.models import Event, EventMembership, MembershipRole
from events.tests.base import make_user
from resources.tests.approving import approve
from sitio import services as sitio

from .test_church_logo import MediaRootMixin, png_bytes
from .test_current_church import SWITCH, TwoChurchFixture

COLOUR_A, COLOUR_B = "#112233", "#aa5500"


def logo(church) -> str:
    return f'src="/iglesia/{church.slug}/logotipo/'


class TwoBrandsFixture(MediaRootMixin, TwoChurchFixture):
    def setUp(self):
        super().setUp()
        for church, colour in ((self.church, COLOUR_A), (self.church_b, COLOUR_B)):
            Church.objects.filter(pk=church.pk).update(custom_theme_enabled=True, theme_primary_color=colour)
            church.refresh_from_db()
            self.give_logo(church, png_bytes(300, 100))

    def assert_wears(self, response, church, other, colour, other_colour, where=""):
        self.assertEqual(response.status_code, 200, where)
        self.assertContains(response, logo(church), msg_prefix=where)
        self.assertNotContains(response, logo(other), msg_prefix=where)
        self.assertContains(response, f"--petroleo:{colour};", msg_prefix=where)
        self.assertNotContains(response, other_colour, msg_prefix=where)

    def assert_a(self, response, where=""):
        self.assert_wears(response, self.church, self.church_b, COLOUR_A, COLOUR_B, where)

    def assert_b(self, response, where=""):
        self.assert_wears(response, self.church_b, self.church, COLOUR_B, COLOUR_A, where)


class ReaderTests(TwoBrandsFixture):
    def test_each_reader_sees_the_church_of_their_request(self):
        for user, check in ((self.member_user, self.assert_a), (self.admin, self.assert_a), (self.member_b_user, self.assert_b),
                            (self.admin_b, self.assert_b)):
            self.client.force_login(user)
            for name in ("community:home", "account", "ayuda"):
                check(self.client.get(reverse(name)), f"{user.email} {name}")

    def test_an_account_of_two_churches_wears_the_one_it_chose(self):
        for user in (self.dual, self.dual_member):
            self.client.force_login(user)
            for name in ("community:home", "account", "ayuda", "notices:list"):
                self.assert_a(self.client.get(reverse(name)), name)
            self.client.post(SWITCH, {"church": self.church_b.pk})
            for name in ("community:home", "account", "ayuda", "notices:list"):
                self.assert_b(self.client.get(reverse(name)), name)
            self.client.post(SWITCH, {"church": self.church.pk})
            self.assert_a(self.client.get(reverse("community:home")))

    def test_a_forged_choice_wears_nothing_of_the_other_church(self):
        self.client.force_login(self.member_user)
        session = self.client.session
        session[SESSION_KEY] = self.church_b.pk
        session.save()
        self.assert_a(self.client.get(reverse("community:home")))

    def test_signed_out_it_is_the_first_active_church(self):
        """The legacy rule, kept: nobody chose anything, and a session value without an account means nothing."""
        session = self.client.session
        session[SESSION_KEY] = self.church_b.pk
        session.save()
        for name in ("landing", "login", "password_reset"):
            response = self.client.get(reverse(name))
            self.assertContains(response, logo(self.church), msg_prefix=name)
            self.assertNotContains(response, logo(self.church_b), msg_prefix=name)
        self.assertEqual(self.client.get(reverse("landing")).context["church"], self.church)
        Church.objects.filter(pk=self.church.pk).update(active=False)
        self.assertEqual(self.client.get(reverse("landing")).context["church"], self.church_b)

    def test_an_account_with_no_church_keeps_the_first_active_church_and_no_colours(self):
        """Somebody who only works in an event: the logo of the public pages, and no church's colours (as before)."""
        self.client.force_login(self.coord2)
        response = self.client.get(reverse("account"))
        self.assertContains(response, logo(self.church))
        self.assertNotContains(response, "--petroleo:#")

    def test_the_function(self):
        request = RequestFactory().get("/")
        request.user, request.session = AnonymousUser(), {SESSION_KEY: self.church_b.pk}
        self.assertEqual(tenancy.current_church(request), self.church)
        request.user = self.dual
        self.assertEqual(tenancy.current_church(request), self.church_b)
        self.assertEqual(tenancy.resolve(request).others, [self.church])
        self.assertEqual(tenancy.resolve(request).role.church, self.church_b)  # the role held there, not the first one
        request.session = {}
        self.assertEqual(tenancy.current_church(request), self.church)
        self.assertEqual(tenancy.resolve(request).role.church, self.church)
        request.user = self.member_b_user
        self.assertEqual(tenancy.current_church(request), self.church_b)
        self.assertEqual(tenancy.resolve(request).others, [])
        request.user = self.coord2  # no church at all: the public pages' church
        self.assertIsNone(tenancy.resolve(request).church)
        self.assertEqual(tenancy.current_church(request), self.church)
        # The legacy fallback: a role in a church that was closed still names that church for the old screens
        closed = Church.objects.create(name="Iglesia cerrada", slug="cerrada", active=False)
        only_closed = make_user("cerrada@prueba.mx")
        assign_role(only_closed, closed, Role.PASTOR)
        request.user = only_closed
        self.assertEqual(tenancy.current_church(request), closed)
        self.assertEqual(tenancy.resolve(request).role.church, closed)


class ClosedChurchTests(TwoBrandsFixture):
    def test_a_closed_church_offers_no_community_page(self):
        """The account's only church was closed: no "Mi iglesia" is offered (its views answer 404), and the old
        screens keep that church's logo, as before."""
        Church.objects.filter(pk=self.church.pk).update(active=False)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("community:home")).status_code, 404)
        response = self.client.get(reverse("account"))
        self.assertContains(response, logo(self.church))
        self.assertNotContains(response, "/mi-iglesia/")
        self.assertNotContains(response, 'class="rail')


class PagesAboutAChurchTests(TwoBrandsFixture):
    """A page about a church wears that church, whoever reads it and whatever they chose."""

    def readers(self):
        yield "sin cuenta", None
        yield "de la iglesia A", self.member_user
        yield "de las dos, en A", self.dual

    def test_the_public_event_page(self):
        event_b = Event.objects.get(slug="vigilia-iglesia-b")
        agenda.save_page(event_b, self.admin_b, public=True)
        for label, user in self.readers():
            self.client.logout()
            if user:
                self.client.force_login(user)
            self.assert_b(self.client.get(reverse("evento:detail", args=[event_b.slug])), label)

    def test_the_public_home_of_a_church(self):
        sitio.save_home(self.church_b, self.admin_b, welcome="Bienvenida de la iglesia B.", address="Avenida B 99")
        # The administration asks with the review; a pastor of the church approves, and that makes it public (#99).
        home = sitio.request_publication(self.church_b, self.admin_b, sitio.page_content(self.church_b).digest())
        approve(self.church_b, "sitio", home)
        for label, user in self.readers():
            self.client.logout()
            if user:
                self.client.force_login(user)
            self.assert_b(self.client.get(reverse("iglesia:home", args=[self.church_b.slug])), label)

    def test_an_event_keeps_its_churchs_logo_and_title_whatever_was_chosen(self):
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.ACTIVE)
        EventMembership.objects.create(event=self.event, user=self.dual_member, role=MembershipRole.COORDINATOR)
        self.client.force_login(self.dual_member)
        url = reverse("events:coord_home", args=[self.event.slug])

        def brand_and_title(response):
            html = response.content.decode()
            self.assertEqual(response.status_code, 200)
            return html.split('<div class="rail__brand">', 1)[1].split("</a>", 1)[0], html.split("<title>", 1)[1].split("</title>", 1)[0]

        before = brand_and_title(self.client.get(url))
        self.assertIn(logo(self.church), before[0])
        self.assertIn("Iglesia de prueba", before[1])
        self.client.post(SWITCH, {"church": self.church_b.pk})
        response = self.client.get(url)
        self.assertEqual(brand_and_title(response), before)
        head = response.content.decode().split('class="menu-panel__head">', 1)[1].split("<button", 1)[0]
        self.assertIn(logo(self.church), head)
        self.assertNotIn(logo(self.church_b), head)
