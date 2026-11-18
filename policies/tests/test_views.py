"""Políticas de la iglesia: the form, the review before a sensitive change, the audit rows, and who may."""

from django.test import override_settings
from django.urls import reverse

from core.models import Role
from core.services import assign_role
from events.tests.base import make_user
from groups.tests.base import CommunityFixture
from people import flags, policy
from people.models import ChurchAuditEvent
from policies import services
from policies.models import ChurchPolicy

URL = reverse("church_policies")
DEFAULTS = {
    "min_reporting_size": 5, "feedback_open_days": 14,
    "attendance_identifiable_months": 12, "attendance_aggregate_months": 24,
}  # the two switches are off: an unchecked box is not posted


def data(**changes) -> dict:
    values = {**DEFAULTS, **changes}
    return {key: value for key, value in values.items() if value is not False}


class ChurchPoliciesTests(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.pastor_user = make_user("pastor-sin-admin@prueba.mx", "Pablo", "Pastor")
        assign_role(cls.pastor_user, cls.church, Role.PASTOR)
        cls._person(cls.church, cls.pastor_user)

    def setUp(self):
        self.client.force_login(self.admin)

    def audit(self, church=None):
        return ChurchAuditEvent.objects.filter(church=church or self.church, action="politica_cambiada")

    def review_and_confirm(self, **changes):
        page = self.client.post(URL, data(**changes))
        self.assertTemplateUsed(page, "policies/review.html")
        return self.client.post(URL, {"paso": "confirmar", "revision": page.context["token"]}, follow=True)

    # ---- the form -------------------------------------------------------------------------------
    def test_the_form_shows_the_values_in_force_the_groups_and_the_floors(self):
        page = self.client.get(URL)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.content.decode().count("<h1"), 1)
        for heading in ("Privacidad de los totales", "Opiniones de los eventos", "Avisos urgentes", "Menores de edad",
                        "Conservación de la asistencia"):
            self.assertContains(page, f'class="heading">{heading}</h2>')
        self.assertEqual(page.context["form"].initial, {
            **DEFAULTS, "urgent_notice_pastors": False, "minors_need_consent_by_default": False,
        })
        self.assertContains(page, "El mínimo es 5")
        self.assertContains(page, "El mínimo es 6")
        self.assertContains(page, "aviso de privacidad de tu iglesia")
        self.assertContains(page, "Hoy nada se borra solo")
        self.assertContains(page, "Revisar los cambios")
        self.assertFalse(ChurchPolicy.objects.exists())  # looking creates nothing

    def test_posting_what_is_already_in_force_saves_nothing(self):
        response = self.client.post(URL, data(), follow=True)
        self.assertRedirects(response, URL)
        self.assertContains(response, "No cambiaste nada")
        self.assertFalse(ChurchPolicy.objects.exists())
        self.assertFalse(self.audit().exists())

    # ---- floors and ceilings on write -----------------------------------------------------------
    def test_values_outside_the_platform_bounds_are_refused_with_the_reason(self):
        cases = [
            ("min_reporting_size", 4, "Emmaus no permite menos de 5"),
            ("min_reporting_size", 21, "Escribe 20 o menos"),
            ("feedback_open_days", 6, "Escribe 7 o más"),
            ("feedback_open_days", 31, "Escribe 30 o menos"),
            ("attendance_identifiable_months", 5, "Escribe 6 o más"),
            ("attendance_identifiable_months", 25, "Escribe 24 o menos"),
            ("attendance_aggregate_months", 11, "Escribe 12 o más"),
            ("attendance_aggregate_months", 61, "Escribe 60 o menos"),
            ("min_reporting_size", "", "Escribe un número."),
            ("min_reporting_size", "muchos", "Escribe un número entero."),
            ("min_reporting_size", -3, "Emmaus no permite menos de 5"),
        ]
        for field, value, words in cases:
            page = self.client.post(URL, data(**{field: value}))
            self.assertTemplateUsed(page, "policies/form.html")
            self.assertContains(page, "Hay un problema")
            self.assertContains(page, words)
            self.assertIn(field, page.context["form"].errors, (field, value))
        self.assertContains(page, "<title>Error: Políticas de la iglesia")
        self.assertFalse(ChurchPolicy.objects.exists())
        self.assertFalse(self.audit().exists())
        self.assertNotIn("policy_review", self.client.session)

    def test_the_totals_cannot_be_kept_for_less_time_than_the_names(self):
        page = self.client.post(URL, data(attendance_identifiable_months=20, attendance_aggregate_months=18))
        self.assertContains(page, "Escribe 20 o más: los totales no se pueden conservar menos tiempo que quién asistió.")
        self.assertFalse(ChurchPolicy.objects.exists())

    def test_the_write_path_refuses_what_the_form_would_refuse(self):
        wanted = services.current(self.church)
        for field, value in [("min_reporting_size", 4), ("min_reporting_size", True), ("attendance_aggregate_months", 61),
                             ("urgent_notice_pastors", "sí"), ("attendance_aggregate_months", 12 - 1)]:
            with self.assertRaises(services.PolicyError):
                services.save(self.church, self.admin, {**wanted, field: value})
        with self.assertRaises(services.PolicyError):
            services.save(self.church, self.admin, {**wanted, "attendance_identifiable_months": 24, "attendance_aggregate_months": 20})
        with self.assertRaises(services.PolicyError):
            services.save(self.church, self.admin, {"min_reporting_size": 6})
        self.assertFalse(ChurchPolicy.objects.exists())

    # ---- the review -----------------------------------------------------------------------------
    def test_a_sensitive_change_is_reviewed_first_and_saved_only_when_confirmed(self):
        page = self.client.post(URL, data(min_reporting_size=8, urgent_notice_pastors="on"))
        self.assertTemplateUsed(page, "policies/review.html")
        self.assertContains(page, "Confirmar los cambios")
        self.assertContains(page, "Mínimo de personas para mostrar un total")
        self.assertContains(page, "Antes: 5 personas.")
        self.assertContains(page, "<strong>8 personas</strong>")
        self.assertContains(page, "Antes: Solo la administración de la iglesia.")
        self.assertContains(page, "<strong>La administración de la iglesia y los pastores</strong>")
        self.assertNotContains(page, "Días para dar una opinión")  # what does not change is not listed
        self.assertEqual([change.field for change in page.context["changes"]], ["min_reporting_size", "urgent_notice_pastors"])
        self.assertFalse(ChurchPolicy.objects.exists())  # nothing saved yet
        self.assertEqual(policy.min_reporting_size(self.church), 5)

        response = self.client.post(URL, {"paso": "confirmar", "revision": page.context["token"]}, follow=True)
        self.assertRedirects(response, URL)
        self.assertContains(response, "Guardamos los 2 cambios")
        row = ChurchPolicy.objects.get(church=self.church)
        self.assertEqual((row.min_reporting_size, row.urgent_notice_pastors), (8, True))
        self.assertIsNone(row.feedback_open_days)  # untouched values keep following the product default
        self.assertEqual(policy.min_reporting_size(self.church), 8)  # the object that read 5 above reads again
        self.assertEqual(policy.urgent_notice_roles(self.church), frozenset({"church_admin", "pastor"}))
        self.assertEqual(response.context["form"].initial["min_reporting_size"], 8)
        self.assertNotIn("policy_review", self.client.session)

    def test_confirming_without_a_review_saves_nothing(self):
        attempts = [
            {"paso": "confirmar"},
            {"paso": "confirmar", "revision": "inventado"},
            {"paso": "confirmar", "revision": "", **data(min_reporting_size=9)},
            {"paso": "confirmar", "revision": "inventado", **data(min_reporting_size=9, urgent_notice_pastors="on")},
        ]
        for attempt in attempts:
            response = self.client.post(URL, attempt, follow=True)
            self.assertRedirects(response, URL)
            self.assertContains(response, "No se guardó nada: primero revisa los cambios")
        self.assertFalse(ChurchPolicy.objects.exists())
        self.assertFalse(self.audit().exists())

    def test_a_wrong_token_spends_the_review_and_a_confirmation_works_once(self):
        page = self.client.post(URL, data(min_reporting_size=9))
        token = page.context["token"]
        self.client.post(URL, {"paso": "confirmar", "revision": token + "x"})
        self.client.post(URL, {"paso": "confirmar", "revision": token})  # the review is gone
        self.assertFalse(ChurchPolicy.objects.exists())
        self.review_and_confirm(min_reporting_size=9)
        self.assertEqual(self.audit().count(), 1)
        self.client.post(URL, {"paso": "confirmar", "revision": self.client.session.get("policy_review", {}).get("token", "x")})
        self.assertEqual(self.audit().count(), 1)

    def test_confirming_saves_what_was_reviewed_not_what_the_confirmation_posts(self):
        page = self.client.post(URL, data(min_reporting_size=9))
        self.client.post(URL, {"paso": "confirmar", "revision": page.context["token"], **data(min_reporting_size=20, urgent_notice_pastors="on")})
        row = ChurchPolicy.objects.get(church=self.church)
        self.assertEqual((row.min_reporting_size, row.urgent_notice_pastors), (9, False))

    def test_going_back_to_edit_keeps_what_was_typed(self):
        self.client.post(URL, data(min_reporting_size=9, attendance_aggregate_months=30))
        page = self.client.get(URL + "?editar=1")
        self.assertEqual(page.context["form"].initial["min_reporting_size"], 9)
        self.assertEqual(page.context["form"].initial["attendance_aggregate_months"], 30)
        self.assertEqual(self.client.get(URL).context["form"].initial["min_reporting_size"], 5)

    def test_the_days_of_the_opinions_alone_are_saved_without_a_review(self):
        response = self.client.post(URL, data(feedback_open_days=21), follow=True)
        self.assertRedirects(response, URL)
        self.assertContains(response, "Guardamos el cambio.")
        self.assertEqual(policy.feedback_open_days(self.church), 21)
        event = self.audit().get()
        self.assertEqual(event.metadata, {"policy": "feedback_open_days", "previous": 14, "value": 21})

    def test_the_days_of_the_opinions_with_a_sensitive_change_wait_for_the_review_too(self):
        page = self.client.post(URL, data(feedback_open_days=21, minors_need_consent_by_default="on"))
        self.assertTemplateUsed(page, "policies/review.html")
        self.assertEqual(policy.feedback_open_days(self.church), 14)
        self.assertContains(page, "Antes: 14 días.")

    # ---- the audit ------------------------------------------------------------------------------
    def test_every_value_changed_leaves_one_audit_row_in_words(self):
        self.review_and_confirm(
            min_reporting_size=10, feedback_open_days=10, urgent_notice_pastors="on", minors_need_consent_by_default="on",
            attendance_identifiable_months=6, attendance_aggregate_months=36,
        )
        rows = {event.metadata["policy"]: event for event in self.audit()}
        self.assertEqual(set(rows), set(services.FIELDS))
        self.assertEqual(self.audit().count(), 6)
        self.assertEqual(rows["min_reporting_size"].summary, "Mínimo de personas para mostrar un total: cambió de «5 personas» a «10 personas».")
        self.assertEqual(rows["min_reporting_size"].metadata, {"policy": "min_reporting_size", "previous": 5, "value": 10})
        self.assertEqual(
            rows["urgent_notice_pastors"].summary,
            "Quién puede publicar un aviso urgente: cambió de «Solo la administración de la iglesia» a "
            "«La administración de la iglesia y los pastores».",
        )
        self.assertEqual(rows["urgent_notice_pastors"].metadata, {"policy": "urgent_notice_pastors", "previous": False, "value": True})
        self.assertEqual(rows["attendance_identifiable_months"].metadata["value"], 6)
        for event in rows.values():
            self.assertEqual((event.actor, event.object_type), (self.admin, "ChurchPolicy"))
        self.assertEqual(policy.attendance_retention(self.church), (6, 36))
        self.assertIs(policy.minors_consent_default(self.church), True)

        self.review_and_confirm(  # one value back: one more row, and only for it
            min_reporting_size=10, feedback_open_days=10, urgent_notice_pastors="on",
            attendance_identifiable_months=6, attendance_aggregate_months=36,
        )
        self.assertEqual(self.audit().count(), 7)
        self.assertEqual(self.audit().first().metadata, {"policy": "minors_need_consent_by_default", "previous": True, "value": False})

    # ---- who may ---------------------------------------------------------------------------------
    def test_only_the_church_administration_reads_or_changes_it(self):
        for user in (self.member_user, self.leader_user, self.pastor_user, self.coord1):
            self.client.force_login(user)
            self.assertEqual(self.client.get(URL).status_code, 403, user.email)
            self.assertEqual(self.client.post(URL, data(min_reporting_size=9)).status_code, 403, user.email)
            self.assertEqual(self.client.post(URL, {"paso": "confirmar", "revision": "x"}).status_code, 403, user.email)
        self.client.logout()
        self.assertEqual(self.client.get(URL).status_code, 302)
        self.assertEqual(self.client.post(URL, data(min_reporting_size=9)).status_code, 302)
        self.assertFalse(ChurchPolicy.objects.exists())

    def test_each_administration_reads_and_changes_only_its_own_church(self):
        ChurchPolicy.objects.create(church=self.church, min_reporting_size=13)
        self.client.force_login(self.admin_b)
        page = self.client.get(URL)
        self.assertEqual(page.context["form"].initial["min_reporting_size"], 5)  # not church A's 13
        self.assertNotContains(page, 'value="13"')
        self.review_and_confirm(min_reporting_size=7)
        self.assertEqual(ChurchPolicy.objects.get(church=self.church_b).min_reporting_size, 7)
        self.assertEqual(ChurchPolicy.objects.get(church=self.church).min_reporting_size, 13)
        self.assertEqual(policy.min_reporting_size(self.church), 13)
        self.assertEqual(self.audit(self.church_b).count(), 1)
        self.assertFalse(self.audit(self.church).exists())

    def test_a_review_of_one_church_cannot_be_confirmed_in_another(self):
        """Somebody who administers two churches reviews in one and switches: the review does not follow."""
        assign_role(self.admin, self.church_b, Role.CHURCH_ADMIN)
        self._person(self.church_b, self.admin)
        page = self.client.post(URL, data(min_reporting_size=9))
        token = page.context["token"]
        session = self.client.session
        self.assertEqual(session["policy_review"]["church"], self.church.pk)
        session["policy_review"] = {**session["policy_review"], "church": self.church_b.pk + 1000}
        session.save()
        response = self.client.post(URL, {"paso": "confirmar", "revision": token}, follow=True)
        self.assertContains(response, "No se guardó nada")
        self.assertFalse(ChurchPolicy.objects.exists())

    # ---- the module ------------------------------------------------------------------------------
    def test_with_the_module_off_the_page_does_not_exist_and_configuracion_does_not_link_it(self):
        settings_url = reverse("church_settings")
        self.assertContains(self.client.get(settings_url), f'href="{URL}"')
        flags.set_enabled(self.church, "community", False)
        self.assertEqual(self.client.get(URL).status_code, 404)
        self.assertEqual(self.client.post(URL, data(min_reporting_size=9)).status_code, 404)
        page = self.client.get(settings_url)
        self.assertNotContains(page, URL)
        self.assertNotContains(page, "Políticas de la iglesia")
        flags.set_enabled(self.church, "community", True)
        with override_settings(EMMAUS_V2_MODULES=False):
            self.assertEqual(self.client.get(URL).status_code, 404)
            self.assertNotContains(self.client.get(settings_url), "Políticas de la iglesia")

    def test_the_navigation_marks_configuracion_on_the_page(self):
        from core.shell import build_shell

        request = self.client.get(URL).wsgi_request
        shell = build_shell(request, self.church, [], show_events=False)
        active = [item["label"] for section in shell["sections"] for item in section["items"] if item["active"]]
        self.assertEqual(active, ["Configuración"])
