"""The church's own colours must stay readable (audit A11Y-06) and their hover must change (UI-18)."""

from __future__ import annotations

import re

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from django.template import Context, Template

from .colors import contrast_ratio, hover_shade, parse_hex, relative_luminance
from .models import Church, FollowUpRequest, ParticipantQuestion, Role
from .services import assign_role
from .tests import EmmausFixture

PRIMARY_ERROR = "Elige un color más oscuro: necesita un contraste de al menos 4.5 a 1 sobre blanco."
BACKGROUND_ERROR = "Elige un color que se lea sobre el fondo y las superficies: necesita un contraste de al menos 4.5 a 1 con cada uno."
ACCENT_ERROR = "Elige un color más oscuro: necesita un contraste de al menos 3 a 1 sobre blanco."


class ColourArithmeticTests(TestCase):
    def test_contrast_ratio(self):
        self.assertAlmostEqual(contrast_ratio((0, 0, 0), (255, 255, 255)), 21.0, places=2)
        self.assertAlmostEqual(contrast_ratio(parse_hex("#0B6270"), (255, 255, 255)), 7.01, places=2)  # petróleo
        self.assertAlmostEqual(contrast_ratio(parse_hex("#F2C230"), (255, 255, 255)), 1.68, places=2)  # the audit's yellow, "1.67:1"

    def test_hover_shade_is_about_twelve_percent_darker(self):
        self.assertEqual(hover_shade("#B45309"), "#9e4908")  # 12 % already makes 1.23:1
        self.assertEqual(hover_shade("#0B6270"), "#09525e")  # 12 % is 1.19:1, so 16 %
        self.assertLess(relative_luminance(parse_hex(hover_shade("#B45309"))), relative_luminance(parse_hex("#B45309")))

    def test_black_goes_lighter_instead(self):
        self.assertEqual(hover_shade("#000000"), "#1f1f1f")

    def test_the_hover_is_always_visibly_different(self):
        # Refuter round 1: 12 % darker than #111111 is #0f0f0f, 1.01:1 against it. At least 1.2:1, lighter if need be.
        for colour in ("#111111", "#0B6270", "#000000", "#B45309", "#767676", "#1F3A5F"):
            with self.subTest(colour):
                hover = parse_hex(hover_shade(colour))
                self.assertGreaterEqual(contrast_ratio(hover, parse_hex(colour)), 1.2)
        self.assertGreater(relative_luminance(parse_hex(hover_shade("#111111"))), relative_luminance(parse_hex("#111111")))

    def test_anything_else_comes_back_unchanged(self):
        self.assertEqual(hover_shade("rojo"), "rojo")
        self.assertIsNone(parse_hex("#12345"))


class ChurchColourFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.church = Church.objects.create(name="Iglesia de colores", slug="iglesia-colores")
        cls.admin = User.objects.create_user(username="admin@colores.mx", email="admin@colores.mx", password="x-clave-larga-1")
        assign_role(cls.admin, cls.church, Role.CHURCH_ADMIN)

    def setUp(self):
        self.client.force_login(self.admin)

    def post(self, **colours):
        data = {
            "name": self.church.name,
            "public_welcome_text": "",
            "contact_email": "",
            "basic_disclaimer_text": "",
            "leaders_can_invite_participants": "on",
            "custom_theme_enabled": "on",
            "theme_primary_color": "#0B6270",
            "theme_accent_color": "#B85C38",
            "theme_secondary_color": "#596B54",
            "theme_background_color": "#FFFFFF",  # white pages, as Atrio: the boundaries below are the ones on white
            "theme_surface_color": "#FFFFFF",
            "theme_text_color": "#111111",
            "theme_muted_text_color": "#6D6A64",
            "theme_border_color": "#D9D9D6",
        }
        data.update(colours)
        if data.get("custom_theme_enabled") is None:
            del data["custom_theme_enabled"]
        return self.client.post(reverse("church_settings"), data)

    def test_a_light_primary_colour_is_refused_with_what_to_do(self):
        response = self.post(theme_primary_color="#F2C230")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].errors["theme_primary_color"], [PRIMARY_ERROR])
        html = response.content.decode()
        self.assertIn(f'<span class="error-message" id="id_theme_primary_color-error">{PRIMARY_ERROR}</span>', html)
        self.assertRegex(html, r'<input type="color" name="theme_primary_color"[^>]*aria-invalid="true"')
        self.church.refresh_from_db()
        self.assertEqual(self.church.theme_primary_color, "#111111")  # nothing was saved
        self.assertFalse(self.church.custom_theme_enabled)

    def test_a_light_accent_is_refused(self):
        response = self.post(theme_accent_color="#F2C230")
        self.assertEqual(response.context["form"].errors["theme_accent_color"], [ACCENT_ERROR])

    def test_the_thresholds_are_exact(self):
        # #777777 is 4.48:1 on white, #767676 4.54:1; #959595 is 2.995:1, #949494 3.03:1
        self.assertIn("theme_primary_color", self.post(theme_primary_color="#777777").context["form"].errors)
        self.assertRedirects(self.post(theme_primary_color="#767676"), reverse("church_settings"))
        self.assertIn("theme_accent_color", self.post(theme_accent_color="#959595").context["form"].errors)
        self.assertRedirects(self.post(theme_accent_color="#949494"), reverse("church_settings"))

    def test_readable_colours_are_saved(self):
        response = self.post(theme_primary_color="#0B6270", theme_accent_color="#B85C38")
        self.assertRedirects(response, reverse("church_settings"))
        self.church.refresh_from_db()
        self.assertTrue(self.church.custom_theme_enabled)
        self.assertEqual(self.church.theme_primary_color, "#0B6270")

    def test_not_checked_while_the_church_uses_the_emmaus_palette(self):
        # Stored but not applied: editing the church's name must not fail on colours nobody sees
        response = self.post(custom_theme_enabled=None, theme_primary_color="#F2C230")
        self.assertRedirects(response, reverse("church_settings"))

    def test_the_audits_grey_text_is_refused(self):
        # A11Y-06 evidence: text #8A8A8A on the default background #F0F0EE is 3.45:1, 432 axe violations
        response = self.post(theme_background_color="#F0F0EE", theme_text_color="#8A8A8A")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].errors["theme_text_color"], [BACKGROUND_ERROR])

    def test_light_secondary_text_is_refused(self):
        response = self.post(theme_muted_text_color="#A0A0A0")
        self.assertEqual(response.context["form"].errors["theme_muted_text_color"], [BACKGROUND_ERROR])

    def test_text_is_checked_on_the_surface_too(self):
        # Readable on the page background, not on a dark surface
        response = self.post(theme_surface_color="#333333", theme_text_color="#111111")
        self.assertIn("theme_text_color", response.context["form"].errors)

    def test_the_primary_colour_must_read_on_the_background_and_the_surface(self):
        self.assertEqual(self.post(theme_background_color="#0B6270").context["form"].errors["theme_primary_color"], [BACKGROUND_ERROR])
        self.assertEqual(self.post(theme_surface_color="#09525E").context["form"].errors["theme_primary_color"], [BACKGROUND_ERROR])

    def test_the_form_says_the_rule_before_it_fails(self):
        html = self.client.get(reverse("church_settings")).content.decode()
        self.assertIn("Necesita un contraste de al menos 4.5 a 1 sobre blanco.", html)
        self.assertIn("Necesita un contraste de al menos 3 a 1 sobre blanco.", html)

    def test_the_page_theme_has_a_darker_hover(self):
        self.assertRedirects(self.post(theme_primary_color="#B45309"), reverse("church_settings"))
        html = self.client.get(reverse("church_settings")).content.decode()
        tokens = dict(re.findall(r"--(petroleo|petroleo-hover):(#[0-9a-fA-F]{6})", html))
        self.assertEqual(tokens["petroleo"], "#B45309")
        self.assertEqual(tokens["petroleo-hover"], "#9e4908")
        self.assertLess(relative_luminance(parse_hex(tokens["petroleo-hover"])), relative_luminance(parse_hex(tokens["petroleo"])))


class DiscipleshipStatusMarkTests(EmmausFixture):
    """UI-07, refuter round 1: no ended or waiting state of the discipleship module is drawn as "not started"."""

    def render_partial(self, value, label):
        template = Template('{% include "core/rotulo/_acompanamiento_estado.html" with value=value label=label %}')
        return template.render(Context({"value": value, "label": label}))

    def test_ended_states_are_closed(self):
        for value, label in (("cancelled", "Cancelado"), ("rejected", "Rechazado"), ("closed", "Cerrada")):
            with self.subTest(value):
                self.assertIn(f'<span class="status status--closed"><span class="status__mark" aria-hidden="true"></span>{label}</span>', self.render_partial(value, label))

    def test_other_states_keep_their_marks(self):
        for value, kind in (("answered", "done"), ("escalated", "review"), ("in_progress", "progress"), ("open", "pending"), ("draft", "pending")):
            with self.subTest(value):
                self.assertIn(f"status--{kind}", self.render_partial(value, "x"))

    def test_a_cancelled_follow_up_on_the_participant_page_is_closed(self):
        FollowUpRequest.objects.create(church=self.church, participant=self.participant, reason="Platicar", status=FollowUpRequest.Status.CANCELLED)
        self.client.force_login(self.mentor)
        html = self.client.get(reverse("participant_detail", args=[self.profile.id])).content.decode()
        self.assertIn('<span class="status status--closed"><span class="status__mark" aria-hidden="true"></span>Cancelado</span>', html)

    def test_a_question_passed_to_the_pastor_waits_with_the_half_mark(self):
        ParticipantQuestion.objects.create(
            church=self.church, participant=self.participant, question_text="¿Cómo oro?", status=ParticipantQuestion.Status.ESCALATED
        )
        self.client.force_login(self.pastor)
        html = self.client.get(reverse("church_dashboard")).content.decode()
        self.assertIn('<span class="status status--review"><span class="status__mark" aria-hidden="true"></span>Pasada al pastor</span>', html)
        self.assertNotIn('status--pending"><span class="status__mark" aria-hidden="true"></span>Pasada al pastor', html)
