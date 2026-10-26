"""The first account and the forgotten-password page, from an empty database.

The first administrator used to land on the discipleship overview and read about "mentores"; an account added
to a team that never set a password asked for a reset and never got an e-mail.
"""

import contextlib
import io
import re
from unittest import skipUnless

from django.contrib.auth.models import User
from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Church
from core.views import EMERGENCY_NOTICE
from events.models import AuthThrottle

PASSWORD = "UnaClaveLarga-2026"
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
CONSOLE = "django.core.mail.backends.console.EmailBackend"


class FirstAccountTests(TestCase):
    def setup_post(self):
        return self.client.post(reverse("initial_setup"), {
            "church_name": "Iglesia Gracia", "church_slug": "gracia", "admin_name": "Ana Administra",
            "admin_email": "ana@gracia.mx", "admin_password": PASSWORD,
        })

    def test_setup_lands_on_the_event_list_where_the_only_sign_is_crear_campamento(self):
        response = self.setup_post()
        self.assertRedirects(response, reverse("events:list"))
        page = self.client.get(reverse("events:list")).content.decode()
        signs = re.findall(r'<section class="sign"[^>]*>(.*?)</section>', page, re.S)
        self.assertEqual(len(signs), 1)
        self.assertIn("Crear campamento", signs[0])

    def test_the_setup_page_is_role_neutral_and_keeps_the_notice_and_911(self):
        html = self.client.get(reverse("initial_setup")).content.decode()
        self.assertNotRegex(html.lower(), r"mentor")
        self.assertIn("la persona que la administra", html)
        self.assertIn("Lee el aviso para participantes, aquí abajo.", html)
        self.assertIn(Church._meta.get_field("basic_disclaimer_text").default, html)
        self.assertIn(EMERGENCY_NOTICE, html)


@override_settings(EMAIL_BACKEND=LOCMEM)
class ForgottenPasswordWithoutPasswordTests(TestCase):
    def setUp(self):
        self.done = reverse("password_reset_done")

    def account(self, email, *, usable=False, active=True):
        user = User.objects.create_user(username=email, email=email, first_name="Sara", last_name="Sinclave")
        if usable:
            user.set_password(PASSWORD)
        else:
            user.set_unusable_password()
        user.is_active = active
        user.save()
        return user

    def ask(self, email):
        return self.client.post(reverse("password_reset"), {"email": email})

    def test_an_account_that_never_set_a_password_gets_the_link_to_create_one(self):
        self.account("sara@prueba.mx")
        response = self.ask("Sara@Prueba.mx")
        self.assertRedirects(response, self.done)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual((message.to, message.subject), (["sara@prueba.mx"], "Emmaus: crea tu contraseña"))
        self.assertIn("todavía no tiene contraseña", message.body)
        link = re.search(r"http://testserver(/cuenta/contrasena/\S+/)", message.body).group(1)
        page = self.client.get(link, follow=True)
        self.assertContains(page, 'name="new_password1"')  # a working set-password link, not "enlace no válido"

    def test_an_unknown_email_gets_the_same_page_and_no_email(self):
        self.assertRedirects(self.ask("nadie@prueba.mx"), self.done)
        self.assertEqual(len(mail.outbox), 0)

    def test_an_inactive_account_gets_nothing(self):
        self.account("baja@prueba.mx", active=False)
        self.assertRedirects(self.ask("baja@prueba.mx"), self.done)
        self.assertEqual(len(mail.outbox), 0)

    def test_an_account_with_a_password_still_gets_the_ordinary_reset_email_only(self):
        self.account("con.clave@prueba.mx", usable=True)
        self.ask("con.clave@prueba.mx")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Emmaus: crea una nueva contraseña")

    def test_the_same_throttle_applies(self):
        self.account("sara@prueba.mx")
        for _ in range(5):
            self.assertRedirects(self.ask("sara@prueba.mx"), self.done)
        self.assertEqual(len(mail.outbox), 3)

    def test_every_spelling_that_reaches_the_account_spends_one_counter(self):
        """Django's reset compares NFKC + casefold, so "ſara" reaches "sara": both must count against one key."""
        self.account("sara@prueba.mx")
        for email in ("sara@prueba.mx", "SARA@prueba.mx", "ſara@prueba.mx", "Sara@Prueba.MX"):
            self.ask(email)
        self.assertEqual(AuthThrottle.objects.get(key="reset:sara@prueba.mx").attempts, 4)
        self.assertEqual(AuthThrottle.objects.filter(key__startswith="reset:").count(), 1)

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_with_no_email_configured_the_link_is_never_written_to_the_log(self):
        self.account("sara@prueba.mx")
        written = io.StringIO()
        with contextlib.redirect_stdout(written), contextlib.redirect_stderr(written):
            response = self.ask("sara@prueba.mx")
        self.assertRedirects(response, self.done)  # the same page as always
        self.assertNotIn("/cuenta/contrasena/", written.getvalue())

    @skipUnless(connection.vendor == "postgresql", "PostgreSQL folds the dotless ı in UPPER(); SQLite's iexact is ASCII only")
    def test_dotless_i_spellings_do_not_multiply_the_emails(self):
        """email__iexact matches "lılı" to "lili" on PostgreSQL; Django's own comparison does not."""
        self.account("lili@prueba.mx")
        for email in ("lili@prueba.mx", "lılı@prueba.mx", "lıli@prueba.mx", "lilı@prueba.mx"):
            for _ in range(5):
                self.ask(email)
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual({tuple(m.to) for m in mail.outbox}, {("lili@prueba.mx",)})
