"""Marco: one bar for every signed-in screen, one name for the event list, "Ayuda" on every bar (UI-14, UI-44, A11Y-08,
LANG-40, UX-17, A11Y-17).

base.html, events/rotulo/shell_coordinator.html and shell_leader.html include partials/ui/bar.html, which includes
partials/ui/menu_panel.html. These tests read the rendered markup, the stylesheet (with the small parser of
test_camp_visual) and the one literal of emmaus.js that has to follow it.
"""

import re
from unittest import mock

from django.conf import settings
from django.test import Client, SimpleTestCase
from django.urls import reverse

from core.models import Church, Role
from core.services import assign_role
from events.models import EventMembership

from .base import CampFixture, make_user
from .test_camp_visual import css_rules, declarations, media_applies, stylesheet

# The bar's shape, the same in the three shells: the brand first (four spaces in, byte for byte), then the links, then
# "Menú" and the logout.
BAR_SHAPE = re.compile(
    r'<header class="bar[^"]*">\n  <div class="bar__inner">\n    <(?:a|span) class="(?:wordmark|brand brand--bar)"'
    r'.*?<nav class="bar__nav" aria-label="[^"]+">.*?</nav>\s*<div class="bar__end">.*?</header>',
    re.S,
)
MENU_BUTTON = (
    '<button class="btn btn--small btn--secondary menu-toggle" type="button" data-menu-open aria-controls="menu-panel" '
    'aria-expanded="false">Menú</button>'
)
LINK = re.compile(r'<a href="([^"]*)"[^>]*>([^<]*)</a>')
TASK = re.compile(r'<h2 id="tarea-[\w-]+" class="heading">')
OLD_NAMES = ("Cambiar de evento", "Campamentos y eventos")
MARKED_AYUDA = re.compile(r'<a href="([^"]+)" aria-current="page">Ayuda</a>')


def chrome(html):
    """The bar and the "Menú" panel: from <header> to <main>."""
    return html.split("<header", 1)[1].split("<main", 1)[0]


def bar_links(html):
    nav = html.split('<nav class="bar__nav"', 1)[1].split("</nav>", 1)[0]
    return LINK.findall(nav)


def panel_links(html):
    nav = html.split('<nav class="menu-panel__nav"', 1)[1].split("</nav>", 1)[0]
    return LINK.findall(nav)


def who(html):
    return re.search(r'<p class="menu-panel__who">([^<]*)</p>', html).group(1)


class MarcoFixture(CampFixture):
    def page(self, user, url, station=False):
        client = self.login(user, Client())
        if station:
            self.choose_station(client)
        response = client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()

    def event_pages(self):
        """(role, html) for the four event roles: the coordinator shell, then the leader shell three times."""
        return [
            ("coordinador", self.page(self.coord1, self.url("coord_home"), station=True)),
            ("tutor", self.page(self.tutor1, self.url("tutor_home"))),
            ("pastor", self.page(self.pastor, self.url("pastor_home"))),
            ("administrador", self.page(self.admin, self.url("admin_home"))),
        ]

    def base_pages(self):
        """base.html for a church admin: the event list and a discipleship page."""
        return [
            ("lista", self.page(self.admin, reverse("events:list") + "?todos=1")),
            ("discipulado", self.page(self.admin, reverse("church_dashboard"))),
        ]


class OneBarTests(MarcoFixture):
    def test_the_three_shells_draw_the_same_bar_and_one_panel(self):
        for name, html in self.event_pages() + self.base_pages():
            with self.subTest(page=name):
                self.assertRegex(html, BAR_SHAPE)
                self.assertEqual(html.count('<header class="bar'), 1)
                self.assertEqual(html.count('id="menu-panel"'), 1)
                self.assertEqual(html.count(MENU_BUTTON), 1)
                # A direct child of <body>, between the bar and <main>: emmaus.js makes every other child inert
                self.assertRegex(html, r'</header>\s*<div id="menu-panel" class="menu-panel" role="dialog" aria-modal="true" '
                                       r'aria-labelledby="menu-panel-title" hidden>')
                self.assertLess(html.index('id="menu-panel"'), html.index("<main"))

    def test_the_bar_reads_brand_place_links_then_menu(self):
        html = self.page(self.coord1, self.url("coord_home"), station=True)
        order = [html.index(marker) for marker in ('class="wordmark"', 'class="bar__event"', 'class="here"', 'class="bar__nav"',
                                                   'class="bar__end"')]
        self.assertEqual(order, sorted(order))
        base = self.page(self.admin, reverse("church_dashboard"))  # base.html used to draw "Menú" before its links
        self.assertLess(base.index('class="bar__nav"'), base.index('class="bar__end"'))

    def test_the_coordinator_bar_gives_its_links_a_row_when_it_says_where_you_are(self):
        """Brand, event, "Estás en", five links and the logout need about 1250px in one row (more with a logo)."""
        self.assertIn('<header class="bar">', self.page(self.coord2, self.url("coord_home")))
        self.assertIn('<header class="bar bar--stacked">', self.page(self.coord1, self.url("coord_home"), station=True))


class CampamentosTests(MarcoFixture):
    def test_the_old_names_are_gone_from_the_bars(self):
        for name, html in self.event_pages():
            for old in OLD_NAMES:
                self.assertNotIn(old, html, name)
        # events_list.html keeps "Campamentos y eventos" as its title (another package); its bar does not say it
        for name, html in self.base_pages():
            for old in OLD_NAMES:
                self.assertNotIn(old, chrome(html), name)

    def test_campamentos_opens_the_whole_list_from_every_bar(self):
        everything = reverse("events:list") + "?todos=1"
        for name, html in self.event_pages():
            with self.subTest(page=name):
                self.assertIn(f'<p class="bar__event">Campamento A <a href="{everything}">Campamentos</a></p>', html)
                self.assertEqual(panel_links(html)[-1], (everything, "Campamentos"))
        for name, html in self.base_pages():
            with self.subTest(page=name):
                self.assertIn(f'<a class="btn btn--small btn--secondary bar__switch" href="{everything}">Campamentos</a>', html)
                self.assertEqual(panel_links(html)[-1], (everything, "Campamentos"))

    def test_no_bar_links_the_bare_list(self):
        """The bare list jumps back to the last event (events_home): the admin's nav item and base.html's button did."""
        bare = f'href="{reverse("events:list")}"'
        for name, html in self.event_pages() + self.base_pages():
            self.assertNotIn(bare, chrome(html), name)
        self.assertNotIn("Campamentos", [label for _, label in bar_links(self.page(self.admin, self.url("admin_home")))])

    def test_campamentos_reaches_the_list_after_working_in_an_event(self):
        """base.html's button linked the bare list, which sent a church admin back to the last event."""
        client = self.login(self.admin, Client())
        client.get(self.url("admin_home"))  # the session now remembers this event
        html = client.get(reverse("church_dashboard")).content.decode()
        href = re.search(r'<a class="btn btn--small btn--secondary bar__switch" href="([^"]+)">', html).group(1)
        response = client.get(href)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "events/events_list.html")


class WhoTests(MarcoFixture):
    def test_the_panel_says_who_you_are_for_every_role(self):
        self.assertEqual(who(self.page(self.coord1, self.url("coord_home"), station=True)), "Carla Uno, coordinador, en Computadora 1")
        self.assertEqual(who(self.page(self.tutor1, self.url("tutor_home"))), "Teresa Uno, tutor")
        self.assertEqual(who(self.page(self.care, self.url("pastor_home"))), "Celia Cuidado, pastor, con acceso confidencial")
        self.assertEqual(who(self.page(self.admin, reverse("church_dashboard"))), "Ada Admin, administración de la iglesia")

    def test_a_coordinator_with_confidential_access_is_told_so(self):
        """The coordinator's panel said "coordinador" by hand and never the confidential access."""
        EventMembership.objects.filter(pk=self.m_coord1.pk).update(can_view_sensitive=True)
        html = self.page(self.coord1, self.url("coord_home"), station=True)
        self.assertEqual(who(html), "Carla Uno, coordinador, en Computadora 1, con acceso confidencial")


class AyudaLinkTests(MarcoFixture):
    def test_every_event_bar_ends_its_links_with_ayuda(self):
        help_url = self.url("ayuda")
        for name, html in self.event_pages():
            with self.subTest(page=name):
                self.assertEqual(bar_links(html)[-1], (help_url, "Ayuda"))
                self.assertEqual(panel_links(html)[-2], (help_url, "Ayuda"))

    def test_base_bars_link_the_church_help(self):
        mentor = make_user("mentora@prueba.mx", "Marta", "Mentora")
        assign_role(mentor, self.church, Role.MENTOR)
        pages = self.base_pages()
        pages.append(("mentor", self.page(mentor, reverse("mentor_dashboard"))))
        pages.append(("sin rol de iglesia", self.page(self.coord1, reverse("events:list") + "?todos=1")))
        for name, html in pages:
            with self.subTest(page=name):
                self.assertEqual(bar_links(html)[-1], (reverse("ayuda"), "Ayuda"))

    def test_the_discipleship_link_sits_before_ayuda_in_the_leader_bar(self):
        Church.objects.filter(pk=self.church.pk).update(discipleship_enabled=True)
        html = self.page(self.admin, self.url("admin_home"))
        self.assertEqual(bar_links(html)[-2:], [(reverse("church_dashboard"), "Discipulado"), (self.url("ayuda"), "Ayuda")])
        self.assertEqual(panel_links(html)[-3][1], "Discipulado")


class AyudaPageTests(MarcoFixture):
    ROLES = ("coord1", "tutor1", "pastor", "admin")

    def test_each_role_gets_five_tasks(self):
        for username in self.ROLES:
            with self.subTest(role=username):
                html = self.page(getattr(self, username), self.url("ayuda"), station=username == "coord1")
                self.assertEqual(len(TASK.findall(html)), 5)
                self.assertEqual(len(re.findall(r"<h1\b", html)), 1)
                self.assertIn("Lo más común en Campamento A, en cinco pasos cortos.", html)
                self.assertIn(f'<a href="{self.url("ayuda")}" aria-current="page">Ayuda</a>', html)
                self.assertEqual(chrome(html).count('aria-current="page"'), 2)  # Ayuda, in the bar and in the panel
                closing = "quien instaló Emmaus" if username == "admin" else "la administración del evento"
                self.assertIn(f"¿Algo no está aquí? Pregunta a {closing}.", html)

    def test_every_task_links_to_a_screen_the_role_can_open(self):
        for username in self.ROLES:
            client = self.login(getattr(self, username), Client())
            if username == "coord1":
                self.choose_station(client)
            html = client.get(self.url("ayuda")).content.decode()
            links = re.findall(r'<p class="more"><a href="([^"]+)">', html)
            self.assertEqual(len(links), 5, username)
            for href in links:
                with self.subTest(role=username, href=href):
                    self.assertEqual(client.get(href.replace("&amp;", "&")).status_code, 200)

    def test_the_coordinator_help_never_names_the_leaders_screens(self):
        html = self.page(self.coord1, self.url("ayuda"), station=True)
        self.assertNotIn("Reportes", html)
        self.assertNotIn("Configuración", html)

    def test_someone_outside_the_event_cannot_open_it(self):
        self.login(self.other_coord)
        self.assertEqual(self.client.get(self.url("ayuda")).status_code, 403)

    def test_only_the_help_you_are_on_is_marked(self):
        """Both help routes are named "ayuda": the 403 page of the event's help (base.html) marked the church's one."""
        self.login(self.other_coord)
        response = self.client.get(self.url("ayuda"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(MARKED_AYUDA.findall(response.content.decode()), [])
        html = self.page(self.tutor1, self.url("ayuda"))
        self.assertEqual(MARKED_AYUDA.findall(html), [self.url("ayuda")] * 2)  # the bar and the panel
        html = self.page(self.tutor1, reverse("ayuda"))
        self.assertEqual(MARKED_AYUDA.findall(html), [reverse("ayuda")] * 2)

    def test_the_tutor_help_prints_the_interruption_constant(self):
        """metrics.STALE_MINUTES says when an interview counts as interrumpida; no page says another number."""
        self.login(self.tutor1)
        with mock.patch("events.services.metrics.STALE_MINUTES", 35):
            response = self.client.get(self.url("ayuda"))
        self.assertContains(response, "si lleva 35 minutos o más sin cambios")
        self.assertNotContains(response, "20 minutos")


class ChurchAyudaTests(MarcoFixture):
    def test_signed_out_goes_to_sign_in(self):
        response = Client().get(reverse("ayuda"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("login")))

    def test_someone_without_a_church_role_can_read_it(self):
        html = self.page(self.coord1, reverse("ayuda"))
        self.assertEqual(len(re.findall(r"<h1\b", html)), 1)
        self.assertIn("Únete a un evento con el código", html)
        self.assertIn(f'<a href="{reverse("events:list")}?todos=1">Ir a Campamentos</a>', html)
        self.assertEqual(len(TASK.findall(html)), 3)
        self.assertIn(f'<a href="{reverse("ayuda")}" aria-current="page">Ayuda</a>', html)

    def test_discipleship_is_explained_only_where_it_is_on(self):
        self.assertNotIn("tarea-discipulado", self.page(self.admin, reverse("ayuda")))
        Church.objects.filter(pk=self.church.pk).update(discipleship_enabled=True)
        html = self.page(self.admin, reverse("ayuda"))
        self.assertIn("Sigue tu camino o acompaña a tus participantes", html)
        self.assertEqual(len(TASK.findall(html)), 4)
        self.assertIn("¿Algo no está aquí? Pregunta a quien instaló Emmaus.", html)


class ResponsiveBarTests(SimpleTestCase):
    def test_wide_screens_keep_one_row(self):
        for width in (1440, 1281):
            with self.subTest(width=width):
                self.assertNotIn("flex-basis", declarations(stylesheet(), ".bar__nav", width))
                self.assertEqual(declarations(stylesheet(), ".menu-toggle", width).get("display"), "none")

    def test_up_to_1280_the_links_take_their_own_row(self):
        """A pastor's one row with a church logo over about 113px wide overflowed from 1181 to about 1248px."""
        for width in (1280, 1200):
            with self.subTest(width=width):
                self.assertEqual(declarations(stylesheet(), ".bar__nav", width).get("flex-basis"), "100%")

    def test_at_820_the_links_take_their_own_row_and_menu_stays_hidden(self):
        nav = declarations(stylesheet(), ".bar__nav", 820)
        self.assertNotEqual(nav.get("display"), "none")
        self.assertEqual((nav.get("flex-basis"), nav.get("order"), nav.get("flex-wrap")), ("100%", "10", "wrap"))
        self.assertEqual(declarations(stylesheet(), ".menu-toggle", 820).get("display"), "none")
        self.assertNotEqual(declarations(stylesheet(), ".bar__logout", 820).get("display"), "none")

    def test_at_600_menu_holds_the_links(self):
        for width in (760, 600, 390):
            with self.subTest(width=width):
                self.assertEqual(declarations(stylesheet(), ".bar__nav", width).get("display"), "none")
                self.assertEqual(declarations(stylesheet(), ".bar__logout", width).get("display"), "none")
                self.assertEqual(declarations(stylesheet(), ".menu-toggle", width).get("display"), "inline-flex")
        self.assertNotEqual(declarations(stylesheet(), ".bar__nav", 761).get("display"), "none")

    def test_the_script_closes_the_panel_where_menu_disappears(self):
        js = (settings.BASE_DIR / "static" / "js" / "emmaus.js").read_text()
        line = next(line for line in js.splitlines() if "var narrow = window.matchMedia(" in line)
        self.assertIn("max-width: 760px", line)
        self.assertNotIn("1180px", line)
        shown = [media for media, selectors, body in css_rules(stylesheet())
                 if ".menu-toggle" in [s.strip() for s in selectors.split(",")] and "inline-flex" in body]
        self.assertEqual(shown, ["@media (max-width: 760px)"])
        self.assertTrue(media_applies(shown[0], 760) and not media_applies(shown[0], 761))


# ---- Integration seams (2026-10-27) ----------------------------------------------------------------------


class HelpNamesRealRowsTests(CampFixture):
    """Every row title the administration help quotes must exist on the administration home."""

    def test_every_quoted_row_title_is_on_the_admin_home(self):
        self.login(self.admin)
        help_html = self.client.get(self.url("ayuda")).content.decode()
        home_html = self.client.get(self.url("admin_home")).content.decode()
        quoted = set(re.findall(r"el renglón “([^”]+)”", help_html))
        self.assertTrue(quoted, "the admin help should quote at least one row")
        for title in quoted:
            self.assertIn(f'<p class="row__title">{title}</p>', home_html, title)
