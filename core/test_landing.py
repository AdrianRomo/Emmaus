"""The public front door (Atrio, docs/design-direction.md): one promise, one way in, the code for volunteers.

One column like the sign-in page: the church's logo when it has one, the headline as the only h1, the lede,
"Entrar a Emmaus", the link for a volunteer who has an event's code, three promises and who uses Emmaus.
"""

import re

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from events.tests.test_ui import ENGLISH, analyse

from .models import Church
from .test_church_logo import MediaRootMixin, png_bytes

HEADLINE = "Platicar con cada joven, sin perder a nadie."
LEDE = "Emmaus ayuda al equipo del campamento a conversar con cada campero sobre su fe y a recordar a quién buscar después."
PROMISES = (
    "Cada pregunta se puede dejar sin contestar.",
    "Cada persona ve solo lo que le toca.",
    "Si alguien necesita seguimiento, queda anotado.",
)
JOIN = "¿Vas a ayudar en un evento? Escribe el código"
WITHOUT_A_CHURCH = "Emmaus es la herramienta de la iglesia para entrevistar y acompañar."


def main(response) -> str:
    return response.content.decode().split("<main", 1)[1].split("</main>", 1)[0]


class LandingTests(MediaRootMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.church = Church.objects.create(name="Iglesia del Camino", slug="camino")

    def get(self):
        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)
        return response

    def test_the_headline_is_the_one_heading(self):
        response = self.get()
        html = response.content.decode()
        self.assertEqual(re.findall(r"<h1\b[^>]*>([^<]*)</h1>", html), [HEADLINE])
        self.assertContains(response, f'<h1 class="voice page-head__title">{HEADLINE}</h1>', html=False)
        self.assertContains(response, f'<p class="lede">{LEDE}</p>', html=False)
        self.assertContains(response, "<title>Platicar con cada joven, sin perder a nadie · Emmaus</title>", html=False)

    def test_one_way_in_and_the_code_right_under_it(self):
        response = self.get()
        body = main(response)
        button = f'<a class="btn btn--primary btn--large" href="{reverse("login")}">Entrar a Emmaus</a>'
        join = f'<a class="auth-card__link" href="{reverse("join")}">{JOIN}</a>'
        self.assertEqual(body.count(button), 1)
        self.assertEqual(body.count("btn--primary"), 1)  # one filled button on the page
        self.assertEqual(body.count(join), 1)
        # Directly under the button: nothing but the link's own paragraph between them
        between = body.split(button, 1)[1].split(join, 1)[0]
        self.assertEqual(between.strip(), '<p class="mt-4">')

    def test_three_promises_in_one_sentence_each_without_icons(self):
        body = main(self.get())
        promises = re.search(r'<ul class="rows mt-8">(.*?)</ul>', body, re.DOTALL)
        self.assertIsNotNone(promises)
        self.assertEqual(re.findall(r'<li class="row">([^<]*)</li>', promises.group(1)), list(PROMISES))
        for marked in ("<svg", "<img", "promises", "icon", "✓", "✔"):
            self.assertNotIn(marked, body, marked)

    def test_in_this_order(self):
        body = main(self.get())
        order = [body.index(text) for text in (HEADLINE, LEDE, "Entrar a Emmaus", JOIN, *PROMISES, "usa Emmaus")]
        self.assertEqual(order, sorted(order))

    def test_the_footer_names_the_church(self):
        response = self.get()
        self.assertContains(response, '<p class="aside-note">Iglesia del Camino usa Emmaus en sus campamentos.</p>', html=False)
        self.assertNotContains(response, "discipulado")  # a camp-only church is not promised discipleship
        self.assertNotContains(response, WITHOUT_A_CHURCH)

    def test_a_church_with_discipleship_says_so_in_the_footer(self):
        Church.objects.filter(pk=self.church.pk).update(discipleship_enabled=True)
        self.assertContains(self.get(), "Iglesia del Camino usa Emmaus en sus campamentos y en su discipulado.")

    def test_the_church_named_is_the_first_active_one(self):
        Church.objects.filter(pk=self.church.pk).update(active=False)
        Church.objects.create(name="Iglesia de la Colina", slug="colina")
        response = self.get()
        self.assertContains(response, "Iglesia de la Colina usa Emmaus en sus campamentos.")
        self.assertNotContains(response, "Iglesia del Camino")

    def test_without_an_active_church_the_page_still_draws(self):
        Church.objects.filter(pk=self.church.pk).update(active=False)
        response = self.get()
        self.assertContains(response, f'<p class="aside-note">{WITHOUT_A_CHURCH}</p>', html=False)
        self.assertNotContains(response, "usa Emmaus")
        self.assertNotContains(response, "auth-card__brand")
        self.assertContains(response, HEADLINE)
        self.assertContains(response, f'href="{reverse("join")}"')

    def test_with_no_church_at_all_the_landing_offers_setup(self):
        Church.objects.all().delete()
        self.assertRedirects(self.client.get(reverse("landing")), reverse("initial_setup"))

    def test_signed_in_visitors_go_to_their_dashboard(self):
        self.client.force_login(User.objects.create_user("lucia@prueba.mx", "lucia@prueba.mx", "Una-clave-larga-123"))
        self.assertRedirects(self.client.get(reverse("landing")), reverse("dashboard"), fetch_redirect_response=False)

    def test_spanish_and_accessible(self):
        response = self.get()
        html = response.content.decode()
        self.assertIn('lang="es-MX"', html)
        self.assertIn('class="skip-link"', html)
        self.assertIn('href="#contenido"', html)
        parsed = analyse(html)
        match = ENGLISH.search(" ".join(parsed.text))
        self.assertIsNone(match, f"texto en inglés «{match.group(0) if match else ''}»")
        for tag, attrs in parsed.controls:  # the page has no fields; if one is ever added, it needs its label
            self.assertTrue(attrs.get("id") in parsed.labels_for or attrs.get("aria-label"), (tag, attrs))
        self.assertNotIn("·", main(response))  # data as sentences (the <title> separator comes from base.html)
        self.assertNotIn("→", main(response))

    def test_the_logo_leads_and_is_not_drawn_twice(self):
        self.give_logo(self.church, png_bytes(300, 100))
        response = self.get()
        html = response.content.decode()
        self.assertContains(response, '<div class="auth-card__brand"><img class="brand__logo brand__logo--auth" src="/iglesia/camino/logotipo/claro/?v=', html=False)
        self.assertContains(response, 'alt="Iglesia del Camino" width="216" height="72">', html=False)
        self.assertLess(html.index("auth-card__brand"), html.index("<h1"))
        # Like the sign-in page: no bar at all, so the logo shows once and nothing says "Emmaus"; the church names the tab
        self.assertNotContains(response, 'class="wordmark"', html=False)
        self.assertNotContains(response, 'class="bar bar--public"', html=False)
        self.assertContains(response, '<img class="brand__logo', count=1, html=False)
        self.assertContains(response, "<title>Platicar con cada joven, sin perder a nadie · Iglesia del Camino</title>", html=False)

    def test_without_a_logo_there_is_no_second_wordmark(self):
        response = self.get()
        self.assertNotContains(response, "auth-card__brand")
        self.assertContains(response, 'class="wordmark"', count=1)  # only the bar's
