""""Mi cuenta" (/cuenta/): where am I, as a page. The name, the e-mail, every event and church role, the password."""

from __future__ import annotations

from django.test import Client
from django.urls import reverse

from core.models import ParticipantProfile
from core.tests import EmmausFixture
from events.tests.base import PASSWORD, CampFixture, make_user
from events.tests.test_ui import ENGLISH, analyse


class AccountPageTests(CampFixture):
    def test_it_lists_where_the_person_takes_part_and_only_that(self):
        self.login(self.coord1)
        response = self.client.get(reverse("account"))
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn("Campamento A", page)
        self.assertIn("Tu rol: coordinador.", page)
        self.assertNotIn("Campamento B", page)
        self.assertIn(f'href="{self.url("home")}"', page)
        self.assertIn('href="/cuenta/contrasena/cambiar/"', page)
        self.assertIn("coord1@prueba.mx", page)
        text = " ".join(analyse(page).text)
        self.assertIsNone(ENGLISH.search(text))

    def test_the_care_team_reads_its_confidential_access_and_the_church_admin_its_church(self):
        self.login(self.care)
        self.assertIn("Tu rol: pastor, con acceso confidencial.", self.client.get(reverse("account")).content.decode())
        self.login(self.admin)
        page = self.client.get(reverse("account")).content.decode()
        self.assertIn("En <strong>Iglesia de prueba</strong>: administración de la iglesia.", page)
        self.assertIn("Campamento A", page)
        self.assertIn("Campamento B", page)

    def test_someone_with_nothing_reads_how_to_join(self):
        nobody = make_user("nadie@prueba.mx", "Nadia", "Nadie")
        self.login(nobody)
        page = self.client.get(reverse("account")).content.decode()
        self.assertIn("Aún no formas parte de ningún evento ni de una iglesia.", page)
        self.assertIn(f'href="{reverse("join")}"', page)

    def test_the_name_is_saved_and_a_blank_first_name_is_refused(self):
        self.login(self.coord1)
        response = self.client.post(reverse("account"), {"cuenta-first_name": "Carlota", "cuenta-last_name": "Uno Dos"}, follow=True)
        self.assertRedirects(response, reverse("account"))
        self.coord1.refresh_from_db()
        self.assertEqual((self.coord1.first_name, self.coord1.last_name), ("Carlota", "Uno Dos"))
        self.assertContains(response, "Guardamos tus datos.")
        response = self.client.post(reverse("account"), {"cuenta-first_name": "", "cuenta-last_name": "Uno"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Escribe tu nombre.")
        self.coord1.refresh_from_db()
        self.assertEqual(self.coord1.first_name, "Carlota")

    def test_the_greeting_follows_the_new_name(self):
        self.login(self.coord1)
        self.client.post(reverse("account"), {"cuenta-first_name": "Carlota", "cuenta-last_name": "Uno"})
        self.assertIn("Hola, Carlota.", self.client.get(self.url("coord_home")).content.decode())

    def test_signed_out_it_asks_to_sign_in(self):
        response = self.client.get(reverse("account"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_the_bar_and_the_menu_link_it_on_every_shell(self):
        for user, url in ((self.coord1, self.url("coord_home")), (self.pastor, self.url("pastor_home")), (self.admin, reverse("events:list") + "?todos=1")):
            self.login(user)
            page = self.client.get(url).content.decode()
            self.assertIn('<a class="btn btn--small btn--link bar__account" href="/cuenta/">Mi cuenta</a>', page)
            self.assertIn('<p class="menu-panel__account"><a href="/cuenta/">Mi cuenta</a></p>', page)
        page = self.client.get(reverse("account")).content.decode()
        self.assertIn('bar__account" href="/cuenta/" aria-current="page">Mi cuenta</a>', page)
        self.assertIn(reverse("account"), self.client.get(self.url("ayuda")).content.decode())


class PasswordChangeTests(CampFixture):
    def test_the_wrong_current_password_is_refused_and_the_right_one_changes_it(self):
        self.login(self.coord1)
        url = reverse("password_change")
        page = self.client.get(url).content.decode()
        self.assertIn("Cambiar contraseña", page)
        self.assertIn("Contraseña actual", page)
        response = self.client.post(url, {"old_password": "no-es", "new_password1": "otra-clave-segura-99", "new_password2": "otra-clave-segura-99"})
        self.assertContains(response, "La contraseña actual no es la correcta.")
        response = self.client.post(url, {"old_password": PASSWORD, "new_password1": "otra-clave-segura-99", "new_password2": "otra-clave-segura-99"}, follow=True)
        self.assertRedirects(response, reverse("account"))
        self.assertContains(response, "Tu contraseña cambió. Sigues dentro de Emmaus.")
        self.assertTrue(Client().login(username=self.coord1.username, password="otra-clave-segura-99"))
        # Still signed in after the change (Django rotates the session hash)
        self.assertEqual(self.client.get(self.url("coord_home")).status_code, 200)

    def test_the_rules_hang_from_the_new_password_in_tu_voice(self):
        self.login(self.coord1)
        response = self.client.post(reverse("password_change"), {"old_password": PASSWORD, "new_password1": "corta", "new_password2": "corta"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="#id_new_password1"')
        self.assertNotContains(response, "usted")


class ParticipantAccountTests(EmmausFixture):
    def test_a_participant_says_how_to_be_contacted(self):
        self.client.force_login(self.participant)
        page = self.client.get(reverse("account")).content.decode()
        self.assertIn("¿Cómo prefieres que te contacten?", page)
        response = self.client.post(reverse("account"), {
            "cuenta-first_name": "Pat", "cuenta-last_name": "Participant",
            "contacto-preferred_contact_method": ParticipantProfile.ContactMethod.WHATSAPP, "contacto-phone_number": "55 1234 5678",
        })
        self.assertRedirects(response, reverse("account"))
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.preferred_contact_method, self.profile.phone_number), ("whatsapp", "55 1234 5678"))
        # The welcome's step is done now
        page = self.client.get(reverse("participant_dashboard")).content.decode()
        self.assertRegex(page, r'status--done">.*?Listo</span>\s*<div class="welcome__step-text">\s*<p class="welcome__step-title">Di cómo prefieres que te contacten')

    def test_a_mentor_has_no_contact_fields(self):
        self.client.force_login(self.mentor)
        self.assertNotIn("¿Cómo prefieres que te contacten?", self.client.get(reverse("account")).content.decode())
