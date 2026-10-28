"""The signed-in user's role is visible ("it gets confusing when they log in").

Every event screen's bar says the event role on its own line, under the event's name and its "Campamentos" link
(partials/ui/bar.html, context_role from both event shells). Each role's home says it once more as a sentence,
"Tu rol: coordinador.", the formula of the event list's rows. The kiosk has no bar and says no role; base.html keeps
the church role under the church's name.
"""

import re

from django.test import Client, SimpleTestCase
from django.urls import reverse

from events.models import Interview

from .base import make_user
from .test_camp_visual import css_rules, declarations, media_applies, stylesheet
from .test_ux_marco import MarcoFixture, chrome

BAR_EVENT = re.compile(r'<p class="bar__event">.*?</p>', re.S)


def bar_event(html):
    """The one <p class="bar__event"> of the bar."""
    found = BAR_EVENT.findall(chrome(html))
    assert len(found) == 1, found
    return found[0]


def body(html):
    """The page itself: from <main> on, after the bar and the "Menú" panel."""
    return html.split("<main", 1)[1]


class RoleInTheBarTests(MarcoFixture):
    def homes(self):
        """(label, html) of each role's event home; a superuser is an administrator of every event."""
        root = make_user("raiz@prueba.mx", "Raúl", "Raíz")
        root.is_superuser = True
        root.save()
        return [
            ("Coordinador", self.page(self.coord1, self.url("coord_home"), station=True)),
            ("Tutor", self.page(self.tutor1, self.url("tutor_home"))),
            ("Pastor", self.page(self.pastor, self.url("pastor_home"))),
            ("Administrador", self.page(self.admin, self.url("admin_home"))),  # Ada: church admin, no membership
            ("Administrador", self.page(root, self.url("admin_home"))),
        ]

    def test_the_bar_says_the_role_under_the_event_and_its_link(self):
        everything = reverse("events:list") + "?todos=1"
        for label, html in self.homes():
            with self.subTest(role=label):
                self.assertRegex(
                    bar_event(html),
                    rf'^<p class="bar__event">Campamento A <a href="{re.escape(everything)}">Campamentos</a>'
                    rf"<span>{label}</span></p>$",
                )

    def test_a_coordinator_without_a_computer_sees_the_role_too(self):
        html = self.page(self.coord2, self.url("coord_home"))
        self.assertIn("<span>Coordinador</span></p>", bar_event(html))

    def test_screens_other_than_home_say_the_role_only_in_the_bar(self):
        pages = [
            ("Coordinador", self.page(self.coord1, self.url("my_participants"), station=True)),
            ("Administrador", self.page(self.admin, self.url("participants"))),
        ]
        for label, html in pages:
            with self.subTest(role=label):
                self.assertIn(f"<span>{label}</span></p>", bar_event(html))
                self.assertNotIn("Tu rol:", html)


class RoleSentenceOnHomeTests(MarcoFixture):
    def test_each_home_says_the_role_once_in_the_page(self):
        for sentence, html in [
            ("Tu rol: coordinador.", self.page(self.coord1, self.url("coord_home"), station=True)),
            ("Tu rol: tutor.", self.page(self.tutor1, self.url("tutor_home"))),
            ("Tu rol: pastor.", self.page(self.pastor, self.url("pastor_home"))),
            ("Tu rol: administrador.", self.page(self.admin, self.url("admin_home"))),
        ]:
            with self.subTest(sentence=sentence):
                self.assertEqual(html.count("Tu rol:"), 1)
                self.assertEqual(body(html).count(sentence), 1)
                self.assertNotIn("Tu rol", chrome(html))

    def test_the_sentence_sits_under_each_greeting(self):
        coord = self.page(self.coord1, self.url("coord_home"), station=True)
        self.assertRegex(coord, r'<h1 class="voice coord__hello">Hola, Carla\.</h1>\s*<p class="coord__role">Tu rol: coordinador\.</p>')
        tutor = self.page(self.tutor1, self.url("tutor_home"))
        self.assertRegex(
            tutor,
            r'<header class="crew-head">\s*<div class="crew-head__text">\s*<h1 class="voice crew-head__title">[^<]*</h1>\s*'
            r'<p class="crew-head__meta">Tu rol: tutor\.</p>\s*</div>',
        )
        # The leaders' homes add it to the sentences under the title, in the same <p>
        pastor = self.page(self.pastor, self.url("pastor_home"))
        self.assertRegex(pastor, r'<p class="report-head__meta">[^<]*\. Tu rol: pastor\.</p>')
        admin = self.page(self.admin, self.url("admin_home"))
        self.assertRegex(admin, r'<p class="report-head__meta">[^<]*\. Tu rol: administrador\.</p>')


class PagesThatDoNotChangeTests(MarcoFixture):
    def test_base_html_keeps_the_church_role_under_the_church(self):
        html = self.page(self.admin, reverse("events:list") + "?todos=1")
        self.assertIn('<p class="bar__event">Iglesia de prueba<span>Administración de la iglesia</span></p>', html)

    def test_the_kiosk_says_no_role(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2, mode="supervisada")
        interview = Interview.objects.get(participant=self.p2)
        self.client.post(self.url("interview_handoff", interview.public_id))
        response = self.client.get(reverse("kiosk:home"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn("Tu rol", html)
        self.assertNotIn("bar__event", html)


class RoleStylesTests(SimpleTestCase):
    def test_the_leader_bar_puts_the_role_on_its_own_line(self):
        """The leader's .bar__event is a wrapping flex row past 600px: the role takes the next line, under
        "Campamento A  Campamentos"."""
        span = ".bar--leader .bar__event span"
        rules = [media for media, selectors, body in css_rules(stylesheet()) if span in [s.strip() for s in selectors.split(",")]]
        self.assertEqual(rules, ["@media (min-width: 601px)"])
        for width in (1440, 820, 601):
            with self.subTest(width=width):
                self.assertEqual(declarations(stylesheet(), span, width).get("flex-basis"), "100%")
                self.assertEqual(declarations(stylesheet(), ".bar--leader .bar__event", width).get("flex-wrap"), "wrap")
        self.assertFalse(media_applies(rules[0], 600))
        self.assertEqual(declarations(stylesheet(), ".bar__event", 600).get("display"), "none")  # phones hide it all

    def test_the_home_sentences_look_like_their_neighbours(self):
        station = declarations(stylesheet(), ".coord__station")
        self.assertEqual(declarations(stylesheet(), ".coord__role"), station)
        meta = declarations(stylesheet(), ".report-head__meta")
        crew = declarations(stylesheet(), ".crew-head__meta")
        self.assertEqual((crew.get("color"), crew.get("font-size")), (meta["color"], meta["font-size"]))
        self.assertEqual((crew["color"], crew["font-size"]), ("var(--ink-2)", "var(--t-small)"))
