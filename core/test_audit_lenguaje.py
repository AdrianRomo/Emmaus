"""Language findings of the UX audit in the accounts and discipleship screens.

* LANG-13: the "too similar" password error said "tu nombre de usuario"; people sign in with their e-mail.
* LANG-14: password-rule errors hung from "Escríbela otra vez" instead of "Contraseña nueva".
* LANG-02: "lecciónes" on the participant's and the mentor's home.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.forms import TuSetPasswordForm
from core.models import Lesson
from core.services import enroll_participant_in_path

from .tests import PASSWORD, EmmausFixture

TOO_SIMILAR = "La contraseña se parece demasiado a tu correo o a tu nombre. Elige otra."


class PasswordErrorsGoOnTheNewPasswordTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mariana.lopez@prueba.mx", email="mariana.lopez@prueba.mx", first_name="Mariana", last_name="López"
        )

    def form(self, first, second):
        form = TuSetPasswordForm(self.user, data={"new_password1": first, "new_password2": second})
        self.assertFalse(form.is_valid())
        return form

    def test_a_short_password_is_an_error_of_the_new_password(self):
        form = self.form("q7x", "q7x")
        self.assertEqual(form.errors["new_password1"], ["Esta contraseña es muy corta. Usa al menos 8 caracteres."])
        self.assertNotIn("new_password2", form.errors)

    def test_common_and_numeric_passwords_too(self):
        self.assertIn("Esta contraseña es muy común.", self.form("password123", "password123").errors["new_password1"])
        self.assertIn("Esta contraseña tiene solo números.", self.form("83920174", "83920174").errors["new_password1"])
        self.assertNotIn("new_password2", self.form("83920174", "83920174").errors)

    def test_the_confirmation_only_says_that_they_do_not_match(self):
        form = self.form("una-clave-larga-1", "otra-clave-larga-2")
        self.assertEqual(form.errors["new_password2"], ["Las dos contraseñas no coinciden. Escríbelas otra vez."])
        self.assertNotIn("new_password1", form.errors)
        # Both problems at once: each on its own field.
        form = self.form("q7x", "q7y")
        self.assertEqual(form.errors["new_password1"], ["Esta contraseña es muy corta. Usa al menos 8 caracteres."])
        self.assertEqual(form.errors["new_password2"], ["Las dos contraseñas no coinciden. Escríbelas otra vez."])

    def test_too_similar_says_correo_not_user_name(self):
        for similar in ("mariana.lopez", "MarianaLopez1"):
            errors = self.form(similar, similar).errors["new_password1"]
            self.assertIn(TOO_SIMILAR, errors, similar)
            self.assertNotIn("usuario", " ".join(errors))

    def test_a_good_password_is_accepted(self):
        form = TuSetPasswordForm(self.user, data={"new_password1": "faro-verde-2026", "new_password2": "faro-verde-2026"})
        self.assertTrue(form.is_valid(), form.errors)


class PasswordSetPageTests(TestCase):
    """The page's error summary names the field to fix, and links to it."""

    def test_the_summary_points_at_contrasena_nueva(self):
        user = User.objects.create_user(username="nuevo@prueba.mx", email="nuevo@prueba.mx", first_name="Nora", last_name="Nueva")
        user.set_unusable_password()
        user.save()
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        response = self.client.get(reverse("password_reset_confirm", args=[uid, default_token_generator.make_token(user)]), follow=True)
        set_url = response.redirect_chain[-1][0]
        response = self.client.post(set_url, {"new_password1": "q7x", "new_password2": "q7x"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<a href="#id_new_password1">Contraseña nueva: Esta contraseña es muy corta. Usa al menos 8 caracteres.</a>', html=False)
        self.assertNotContains(response, "Escríbela otra vez: Esta contraseña")
        response = self.client.post(set_url, {"new_password1": "nuevo.prueba", "new_password2": "nuevo.prueba"})
        self.assertContains(response, f"Contraseña nueva: {TOO_SIMILAR}")
        self.assertNotContains(response, "nombre de usuario")


class LessonPluralTests(EmmausFixture):
    def add_lesson(self, order):
        Lesson.objects.create(path=self.path, order=order, title=f"Lección {order}", body="Texto.", practical_step="Paso.")
        enroll_participant_in_path(self.participant, self.path)  # the progress rows the summary counts

    def note(self, user, url_name):
        self.client.logout()
        self.assertTrue(self.client.login(username=user.username, password=PASSWORD))
        response = self.client.get(reverse(url_name))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "ónes")
        return response

    def test_one_lesson(self):
        self.assertContains(self.note(self.participant, "participant_dashboard"), "Llevas 0 de 1 lección terminada.")
        self.assertContains(self.note(self.mentor, "mentor_dashboard"), "0 de 1 lección.")

    def test_two_and_seven_lessons(self):
        self.add_lesson(2)
        self.assertContains(self.note(self.participant, "participant_dashboard"), "Llevas 0 de 2 lecciones terminadas.")
        self.assertContains(self.note(self.mentor, "mentor_dashboard"), "0 de 2 lecciones.")
        for order in range(3, 8):
            self.add_lesson(order)
        self.assertContains(self.note(self.participant, "participant_dashboard"), "Llevas 0 de 7 lecciones terminadas.")
