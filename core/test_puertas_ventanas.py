"""The doors, «Mi cuenta» and the help on «Ventanas» (docs/v2/DESIGN_SYSTEM.md), served only with the community
module on. A door names the church (its logo, or its name above the title) and asks one thing; what a door says
about an account, a password or an error is the same as before, word for word, for every address."""

from __future__ import annotations

import re

from django.core import mail
from django.test import Client, override_settings
from django.urls import reverse

from core import onboarding
from groups.tests.base import CommunityFixture
from people import flags

KICKER = '<p class="public-event__church">Iglesia de prueba</p>'
RESET_SENTENCE = (
    "Si el correo corresponde a una cuenta de Emmaus, en unos minutos recibirás un enlace para crear tu contraseña, "
    "también si nunca la creaste."
)
FOLDED = re.compile(r'<details class="fold" id="(tarea-[\w-]+)"( open)?>\s*<summary>')


def main_of(response) -> str:
    html = response.content.decode()
    return html[html.index("<main"):html.index("</main>")]


class DoorTests(CommunityFixture):
    def test_each_door_names_the_church_above_its_one_question(self):
        doors = [reverse("landing"), reverse("login"), reverse("password_reset"), reverse("password_reset_done"), reverse("join")]
        for url in doors:
            with self.subTest(url=url):
                main = main_of(Client().get(url))
                self.assertEqual(main.count(KICKER), 1)
                self.assertLess(main.index(KICKER), main.index("<h1"))
                self.assertEqual(len(re.findall(r"<h1\b", main)), 1)
                self.assertLessEqual(main.count("btn--primary"), 1)

    def test_signed_in_the_rail_names_the_church_and_the_page_does_not(self):
        self.client.force_login(self.member_user)
        self.assertNotIn(KICKER, main_of(self.client.get(reverse("join"))))

    def test_the_landing_says_its_promise_and_the_promises_without_a_paragraph(self):
        main = main_of(Client().get(reverse("landing")))
        self.assertNotIn('class="lede"', main)
        # A church that uses Emmaus for its life: the promise is that life, and only promises true for any member
        self.assertIn("Tu iglesia, entre domingo y domingo.", main)
        for promise in ("Cada persona ve solo lo que le toca.", "Tú decides cómo te contactan."):
            self.assertIn(promise, main)
        self.assertNotIn("Platicar con cada joven", main)
        self.assertNotIn("Cada pregunta se puede dejar sin contestar.", main)
        self.assertIn(f'href="{reverse("login")}"', main)
        self.assertIn(f'href="{reverse("join")}"', main)

    def test_sign_in_keeps_the_shared_computer_note_and_says_the_same_for_any_wrong_address(self):
        known = Client().post(reverse("login"), {"username": "admin@prueba.mx", "password": "equivocada"})
        unknown = Client().post(reverse("login"), {"username": "nadie@prueba.mx", "password": "equivocada"})
        for response in (known, unknown):
            self.assertIn("Si usas una computadora compartida, cierra sesión al terminar.", main_of(response))
        strip = lambda html: re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"|value="[^"]*@prueba\.mx"', "", html)  # noqa: E731
        self.assertEqual(strip(main_of(known)), strip(main_of(unknown)))

    def test_a_password_reset_answers_the_same_page_for_any_address(self):
        known = Client().post(reverse("password_reset"), {"email": "admin@prueba.mx"}, follow=True)
        unknown = Client().post(reverse("password_reset"), {"email": "nadie@prueba.mx"}, follow=True)
        self.assertEqual(main_of(known), main_of(unknown))
        self.assertIn(RESET_SENTENCE, main_of(known))
        self.assertNotIn("confirm__title", main_of(known))  # nothing claims that a message left
        self.assertEqual(len(mail.outbox), 1)  # only the known address was written to

    def test_an_invitation_names_its_church_and_keeps_both_notices(self):
        from core.models import Invitation, Role

        invitation = Invitation.objects.create(church=self.church, email="nueva@prueba.mx", role=Role.PARTICIPANT)
        main = main_of(Client().get(reverse("accept_invitation", args=[invitation.token])))
        self.assertIn(KICKER, main)
        self.assertIn("Tu iglesia puede usar Emmaus para guiarte en las lecciones, recibir tus preguntas y coordinar un "
                      "acompañamiento personal con líderes de confianza.", main)
        self.assertIn('name="accept_privacy"', main)
        self.assertIn('name="accept_emergency_disclaimer"', main)
        self.assertIn("nueva@prueba.mx", main)

    def test_an_error_page_names_no_church(self):
        with override_settings(DEBUG=False):
            for client in (Client(), self.client):
                self.client.force_login(self.member_user)
                main = main_of(client.get("/no-existe/"))
                self.assertNotIn("Iglesia de prueba", main)
                self.assertIn("No encontramos esta página", main)

    def test_with_the_module_off_the_doors_are_what_they_were(self):
        flags.set_enabled(self.church, "community", False)
        for url in (reverse("landing"), reverse("login"), reverse("password_reset"), reverse("join")):
            with self.subTest(url=url):
                html = Client().get(url).content.decode()
                self.assertNotIn("public-event__church", html)
                self.assertNotIn('data-ui="2"', html)


class AccountTests(CommunityFixture):
    def test_every_row_has_its_leading_mark_and_where_the_person_takes_part(self):
        self.client.force_login(self.coord1)
        main = main_of(self.client.get(reverse("account")))
        rows = re.findall(r'<li class="row( [^"]*)?">', main)
        self.assertTrue(rows)
        self.assertTrue(all("row--lead" in (extra or "") for extra in rows), rows)
        self.assertIn(f'href="{reverse("events:home", args=[self.event.slug])}"', main)
        self.assertIn(f'href="{reverse("password_change")}"', main)
        self.assertIn(f'href="{reverse("community:profile")}">Mis datos</a>', main)
        self.assertIn("Para cambiar tu correo, pide a la administración.", main)
        self.assertEqual(main.count("btn--primary"), 1)

    def test_a_welcome_comes_back_in_place(self):
        onboarding.dismiss(self.member_user, onboarding.MEMBER_KEY)
        self.client.force_login(self.member_user)
        main = main_of(self.client.get(reverse("account")))
        self.assertIn('<section class="block" id="bienvenidas"', main)
        self.assertRegex(main, r'<form class="row__end" method="post" action="/cuenta/bienvenida/mostrar/\?clave=mi-iglesia" '
                               r'data-inline="bienvenidas">')
        response = self.client.post(reverse("welcome_restore") + "?clave=mi-iglesia", {"key": onboarding.MEMBER_KEY})
        self.assertRedirects(response, reverse("account"), fetch_redirect_response=False)
        self.assertNotIn('id="bienvenidas"', main_of(self.client.get(reverse("account"))))  # the region leaves


class EventHelpTests(CommunityFixture):
    def page(self, user):
        self.client.force_login(user)
        response = self.client.get(reverse("events:ayuda", args=[self.event.slug]))
        self.assertEqual(response.status_code, 200)
        return main_of(response)

    def test_each_role_finds_its_five_tasks_folded_and_the_account(self):
        for user in (self.coord1, self.tutor1, self.pastor, self.admin):
            with self.subTest(user=user.email):
                main = self.page(user)
                tasks = [name for name, _ in FOLDED.findall(main)]
                self.assertEqual(tasks, ["tarea-1", "tarea-2", "tarea-3", "tarea-4", "tarea-5", "tarea-cuenta"])
                self.assertNotIn('class="heading"', main)
                links = re.findall(r'<p class="more"><a href="([^"]+)">', main)
                self.assertEqual(len(links), 6)
                for href in links:
                    self.assertLess(self.client.get(href.replace("&amp;", "&")).status_code, 400, href)  # a computer may be asked first

    def test_what_to_do_before_a_risk_report_is_never_folded_away(self):
        main = self.page(self.coord1)
        self.assertIn(("tarea-5", " open"), FOLDED.findall(main))
        self.assertIn("Si alguien está en peligro inmediato, llama al 911", main)
        self.assertNotIn("Reportes", main)
        self.assertNotIn("Configuración", main)
