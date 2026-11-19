"""One sign-in error across Emmaus: an unknown e-mail, a wrong password and an inactive account read the same (the door
never says whether an account exists); «¿Olvidaste tu contraseña?» stays beside «Entrar»; who to ask about an event is
a separate line, only for someone who arrived from a camp's link."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.forms import SIGN_IN_ERROR

PASSWORD = "Una-contraseña-larga-1"


class OneSignInErrorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        User.objects.create_user(username="ana@example.org", email="ana@example.org", password=PASSWORD)
        User.objects.create_user(username="baja@example.org", email="baja@example.org", password=PASSWORD, is_active=False)

    def attempt(self, email, password, next_url=""):
        url = reverse("login") + (f"?next={next_url}" if next_url else "")
        data = {"username": email, "password": password}
        if next_url:
            data["next"] = next_url
        return self.client.post(url, data).content.decode()

    def test_unknown_wrong_and_inactive_say_the_same(self):
        pages = [
            self.attempt("nadie@example.org", PASSWORD),
            self.attempt("ana@example.org", "incorrecta"),
            self.attempt("baja@example.org", PASSWORD),
        ]
        for page in pages:
            self.assertIn(SIGN_IN_ERROR, page)
            self.assertNotIn("desactivada", page)
            self.assertNotIn("administración del evento", page)
            self.assertNotIn('id="ayuda-evento"', page)

    def test_the_forgotten_password_link_stays_beside_entrar(self):
        page = self.client.get(reverse("login")).content.decode()
        entrar = page.index('type="submit">Entrar</button>')
        self.assertIn("¿Olvidaste tu contraseña?</a>", page[entrar:entrar + 200])

    def test_from_a_camp_link_the_event_help_is_a_line_of_its_own(self):
        for next_url in ("/eventos/jovenes-2026/mi-inicio/", "/e/ABC123/", "/unirme/"):
            with self.subTest(next=next_url):
                page = self.attempt("ana@example.org", "incorrecta", next_url)
                self.assertIn(SIGN_IN_ERROR, page)
                self.assertIn('<p class="aside-note" id="ayuda-evento">Si no puedes entrar al evento, pide ayuda a su administración.</p>', page)
        self.assertNotIn('id="ayuda-evento"', self.attempt("ana@example.org", "incorrecta", "/mi-iglesia/"))
        self.assertNotIn('id="ayuda-evento"', self.client.get(reverse("login") + "?next=/eventos/").content.decode())  # only after a failed attempt
