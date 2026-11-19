"""Peticiones de oración (issue #97): off until a pastor switches it on; only the church's pastors read."""

import logging
from datetime import date, timedelta
from io import StringIO
from unittest import mock

from django.contrib import admin as django_admin
from django.core import mail
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from care.models import FollowUp
from core.models import Role, UserChurchRole
from core.services import assign_role
from events.tests.base import make_user
from groups.tests.base import CommunityFixture
from oracion import access, services
from oracion.models import PrayerPolicy, PrayerRequest, ReaderPreference
from people import flags, merge, policy
from people.models import ChurchAuditEvent, Person
from people.services import link_participant

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
PHONE = "800\u00a0911\u00a02000"  # as the page writes it: with no-break spaces
MARK = "Texto-secreto-7391"
BODY = f"{MARK} <script>alert(1)</script> & pido oración"
REPLY_MARK = "Respuesta-secreta-5522"
REPLY = f"{REPLY_MARK} <b>oro por ti</b>"

MINE = reverse("oracion:mine")
NEW = reverse("oracion:new")
INBOX = reverse("oracion:inbox")
TERMS = reverse("oracion:terms")
READER_EMAIL = reverse("oracion:reader_email")
HOME = reverse("community:home")


def url(name, prayer_id):
    return reverse(f"oracion:{name}", args=[prayer_id])


class PrayerFixture(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.pastor = make_user("pablo@prueba.mx", "Pablo", "Pastor")
        assign_role(cls.pastor, cls.church, Role.PASTOR)
        cls.pastor_person = cls._person(cls.church, cls.pastor)
        cls.pastor2 = make_user("paula@prueba.mx", "Paula", "Pastora")
        assign_role(cls.pastor2, cls.church, Role.PASTOR)
        cls._person(cls.church, cls.pastor2)
        cls.pastor_b = make_user("pastor-b@prueba.mx", "Benito", "Pastor Be")
        assign_role(cls.pastor_b, cls.church_b, Role.PASTOR)
        cls._person(cls.church_b, cls.pastor_b)
        cls.root = make_user("root@prueba.mx", "Raíz", "Superusuario")
        cls.root.is_superuser = cls.root.is_staff = True
        cls.root.save()
        cls._person(cls.church, cls.root)
        # A known minor with an account: by the date of birth.
        cls.minor_user = make_user("menor@prueba.mx", "Mía", "Menor")
        today = timezone.localdate()
        cls.minor = cls._person(cls.church, cls.minor_user, birth_date=date(today.year - 15, 1, 1))
        # The owner of a follow-up about the writer: a care owner is not a reader.
        FollowUp.objects.create(church=cls.church, person=cls.asked, owner=cls.coord_person, reason="contacto")

    def turn_on(self, church=None, actor=None, **changes):
        church, actor = church or self.church, actor or self.pastor
        values = {"emergency_line": "", "retention_days": 90, **changes}
        return services.switch_on(
            church, actor, accepted=True, token=services.readers_token(access.readers(church)), **values
        )

    def write(self, person=None, body=BODY, only=None) -> PrayerRequest:
        person = person or self.asked
        readers = access.readers(person.church, apart_from=person.user)
        return services.create(
            person.church, person, body=body, token=services.readers_token(readers, services.terms(person.church)),
            only_reader_id=only.pk if only else None,
        )

    def token_for(self, user, church=None):
        church = church or self.church
        return services.readers_token(access.readers(church, apart_from=user), services.terms(church))

    def reader_requests(self, prayer_id):
        """Every address a reader uses, as (method, url, data)."""
        return [
            ("get", INBOX, {}),
            ("post", READER_EMAIL, {"avisar": "si"}),
            ("get", url("read", prayer_id), {}),
            ("post", url("mark_read", prayer_id), {}),
            ("post", url("reply", prayer_id), {"reply": "x"}),
            ("post", url("mark_talked", prayer_id), {}),
            ("get", TERMS, {}),
            ("post", TERMS, {"paso": "apagar"}),
            ("post", TERMS, {"paso": "encender", "accepted": "on", "retention_days": "90", "promesas": "x"}),
        ]

    def assert_no_reader_route_answers(self, user, prayer, *, also_inbox=True):
        self.client.force_login(user)
        before = PrayerRequest.objects.filter(pk=prayer.pk).values().get()
        for method, address, data in self.reader_requests(prayer.pk):
            if not also_inbox and address in (INBOX, READER_EMAIL, TERMS):
                continue
            response = getattr(self.client, method)(address, data)
            self.assertEqual(response.status_code, 404, f"{user.email} {method} {address}")
            self.assertNotIn(MARK, response.content.decode())
        self.assertEqual(PrayerRequest.objects.filter(pk=prayer.pk).values().get(), before)


class JourneyTests(PrayerFixture):
    def test_a_pastor_switches_it_on_somebody_writes_and_a_pastor_answers(self):
        # Off: the pastor finds the door, reads the promises and must accept them.
        self.client.force_login(self.pastor)
        page = self.client.get(HOME)
        self.assertContains(page, INBOX)
        page = self.client.get(TERMS)
        self.assertContains(page, "Está apagado")
        self.assertContains(page, "Pablo Pastor, Paula Pastora")
        self.assertContains(page, "llama al 911")
        self.assertContains(page, PHONE)
        self.assertEqual(page.content.decode().count("<h1"), 1)
        token = services.readers_token(access.readers(self.church))
        response = self.client.post(TERMS, {"paso": "encender", "retention_days": "90", "emergency_line": "", "promesas": token})
        self.assertContains(response, services.NOT_ACCEPTED)
        self.assertFalse(services.is_on(self.church))
        response = self.client.post(
            TERMS, {"paso": "encender", "retention_days": "90", "emergency_line": "", "promesas": token, "accepted": "on"}
        )
        self.assertRedirects(response, TERMS)
        row = PrayerPolicy.objects.get(church=self.church)
        self.assertEqual((row.enabled, row.accepted_by, row.promises_version), (True, self.pastor, services.PROMISES_VERSION))
        self.assertIsNotNone(row.accepted_at)
        audit = ChurchAuditEvent.objects.get(action="oracion_politica")
        self.assertEqual((audit.actor, audit.metadata), (self.pastor, {"value": True}))

        # The writer reads who reads, by name, before writing.
        self.client.force_login(self.asked_user)
        self.assertContains(self.client.get(HOME), MINE)
        page = self.client.get(NEW)
        self.assertContains(page, "<strong>Lo leen:</strong> Pablo Pastor, Paula Pastora.")
        self.assertContains(page, "Escribe sobre ti. No pongas nombres ni datos de otras personas.")
        self.assertContains(page, "No es un servicio de emergencia ni de respuesta inmediata.")
        self.assertContains(page, services.EMERGENCY_911)
        self.assertContains(page, "Línea de la Vida")
        self.assertContains(page, PHONE)
        self.assertContains(page, "a los 90 días")
        self.assertEqual(page.content.decode().count("<h1"), 1)
        response = self.client.post(NEW, {"body": BODY, "reader": "", "lectores": self.token_for(self.asked_user)})
        self.assertRedirects(response, MINE)
        prayer = PrayerRequest.objects.get()
        self.assertEqual((prayer.church, prayer.person, prayer.author, prayer.body, prayer.restricted, prayer.only_reader),
                         (self.church, self.asked, self.asked_user, BODY, False, None))
        self.assertEqual(prayer.expires_at.date(), (prayer.created_at + timedelta(days=90)).date())
        page = self.client.get(MINE)
        self.assertContains(page, "Todavía no la han leído.")
        self.assertContains(page, "&lt;script&gt;alert(1)&lt;/script&gt; &amp; pido oración")
        self.assertNotContains(page, "<script>alert(1)")

        # A pastor sees who and when in the list, the words only on the request's own page.
        self.client.force_login(self.pastor2)
        page = self.client.get(HOME)
        self.assertContains(page, '1<span class="visually-hidden"> petición sin leer</span>')
        page = self.client.get(INBOX)
        self.assertContains(page, "Pía Pidió")
        self.assertContains(page, "Sin leer")
        self.assertNotContains(page, MARK)
        page = self.client.get(url("read", prayer.pk))
        self.assertContains(page, "&lt;script&gt;alert(1)&lt;/script&gt; &amp; pido oración")
        self.assertNotContains(page, "<script>alert(1)")
        self.assertNotContains(page, "pidio@prueba.mx")  # the page gives a pastor no contact data
        self.assertRedirects(self.client.post(url("mark_read", prayer.pk)), url("read", prayer.pk))
        prayer.refresh_from_db()
        self.assertIsNotNone(prayer.read_at)
        self.assertNotContains(self.client.get(HOME), "sin leer</span>")

        # The writer is told "Leída" with the date, never by whom.
        self.client.force_login(self.asked_user)
        page = self.client.get(MINE)
        self.assertContains(page, "<strong>Leída</strong>")
        self.assertNotContains(page, "Paula")
        self.assertNotContains(page, "Pablo")

        # One short answer, read only by the writer; a second one is refused.
        self.client.force_login(self.pastor)
        self.assertRedirects(self.client.post(url("reply", prayer.pk), {"reply": REPLY}), url("read", prayer.pk))
        response = self.client.post(url("reply", prayer.pk), {"reply": "Otra"})
        self.assertContains(response, services.ALREADY_REPLIED)
        prayer.refresh_from_db()
        self.assertEqual(prayer.reply, REPLY)
        self.assertNotContains(self.client.get(url("read", prayer.pk)), 'name="reply"')
        self.assertRedirects(self.client.post(url("mark_talked", prayer.pk)), url("read", prayer.pk))
        self.client.force_login(self.asked_user)
        page = self.client.get(MINE)
        self.assertContains(page, f"{REPLY_MARK} &lt;b&gt;oro por ti&lt;/b&gt;")
        self.assertContains(page, "Hablaron en persona.")
        self.assertNotContains(page, "Pablo")

    def test_a_reply_or_a_talk_also_marks_it_as_read(self):
        self.turn_on()
        first, second = self.write(), self.write(self.leader)
        services.reply(self.church, self.pastor, first.pk, "Oro por ti")
        services.mark_talked(self.church, self.pastor, second.pk)
        for prayer in (first, second):
            prayer.refresh_from_db()
            self.assertIsNotNone(prayer.read_at)
        self.assertEqual(services.unread_count(self.pastor, self.church), 0)

    def test_the_church_writes_its_own_line_in_place_of_the_second_sentence(self):
        self.turn_on(emergency_line="Línea de la iglesia: 55 0000 0000, de 9:00 a 18:00")
        self.client.force_login(self.asked_user)
        page = self.client.get(NEW)
        self.assertContains(page, "Línea de la iglesia: 55 0000 0000, de 9:00 a 18:00")
        self.assertContains(page, services.EMERGENCY_911)  # the 911 sentence is never replaced
        self.assertNotContains(page, PHONE)
        self.client.force_login(self.pastor)
        page = self.client.get(TERMS)
        self.assertNotContains(page, "estas dos frases")
        self.assertContains(page, "Hoy aparece la de tu iglesia.")
        self.assertContains(page, "<p>Línea de la iglesia: 55 0000 0000, de 9:00 a 18:00</p>")


class SwitchTests(PrayerFixture):
    def post_on(self, user, **changes):
        self.client.force_login(user)
        data = {
            "paso": "encender", "retention_days": "90", "emergency_line": "", "accepted": "on",
            "promesas": services.readers_token(access.readers(self.church)), **changes,
        }
        return self.client.post(TERMS, data)

    def test_only_a_pastor_of_the_church_switches_it(self):
        for user in (self.admin, self.leader_user, self.root, self.coord1, self.asked_user):
            with self.subTest(user=user.email):
                self.assertEqual(self.post_on(user).status_code, 404)
                self.assertEqual(self.client.get(TERMS).status_code, 404)
                self.assertFalse(services.is_on(self.church))
                with self.assertRaises(services.PrayerError):
                    self.turn_on(actor=user)
        # A pastor of another church acts in their own church: this one stays off.
        self.assertEqual(self.post_on(self.pastor_b, promesas=services.readers_token(access.readers(self.church_b))).status_code, 302)
        self.assertTrue(services.is_on(self.church_b))
        self.assertFalse(services.is_on(self.church))
        with self.assertRaises(services.PrayerError):
            self.turn_on(actor=self.pastor_b)
        self.assertFalse(PrayerPolicy.objects.filter(church=self.church).exists())
        self.assertRedirects(self.post_on(self.pastor), TERMS)
        self.assertTrue(services.is_on(self.church))
        # ...and only a pastor switches it off.
        for user in (self.admin, self.root):
            self.client.force_login(user)
            self.assertEqual(self.client.post(TERMS, {"paso": "apagar"}).status_code, 404)
            with self.assertRaises(services.PrayerError):
                services.switch_off(self.church, user)
        self.assertTrue(services.is_on(self.church))

    def test_the_administration_reads_the_state_and_cannot_change_it(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("church_policies"))
        self.assertContains(page, "Peticiones de oración: apagadas.")
        self.assertNotContains(page, TERMS)
        self.turn_on()
        self.assertContains(self.client.get(reverse("church_policies")), "Peticiones de oración: encendidas.")

    def test_it_is_not_switched_on_when_who_reads_changed_under_the_page(self):
        token = services.readers_token(access.readers(self.church))
        newcomer = make_user("nuevo-pastor@prueba.mx", "Nahúm", "Nuevo")
        assign_role(newcomer, self.church, Role.PASTOR)
        response = self.post_on(self.pastor, promesas=token)
        self.assertContains(response, services.PROMISES_CHANGED)
        self.assertContains(response, "Nahúm Nuevo")  # the page again, with today's names
        self.assertFalse(services.is_on(self.church))

    def test_the_days_only_go_down_and_the_line_is_one_plain_line(self):
        for days in ("91", "6", "0", "abc"):
            self.assertEqual(self.post_on(self.pastor, retention_days=days).status_code, 200)
        for line in ("Entra a http://ejemplo.invalid", "www.ejemplo.invalid", "<b>Llama</b>", "x" * 161):
            self.assertEqual(self.post_on(self.pastor, emergency_line=line).status_code, 200, line)
        self.assertFalse(services.is_on(self.church))
        for line in ("http://x.invalid", "<i>", "a{b}", "x" * 161):
            with self.assertRaises(services.PrayerError):
                services.clean_line(line)
            with self.assertRaises(services.PrayerError):
                self.turn_on(emergency_line=line)
        self.assertEqual(services.clean_line("  uno\n dos  "), "uno dos")  # always stored as one line
        self.assertRedirects(self.post_on(self.pastor, retention_days="30"), TERMS)
        self.assertEqual(policy.prayer_retention_days(self.church), 30)
        # A number written by hand outside the bounds never lengthens the 90 days, nor goes under the week.
        PrayerPolicy.objects.filter(church=self.church).update(retention_days=365)
        self.assertEqual(policy.prayer_retention_days(self.church), 90)
        PrayerPolicy.objects.filter(church=self.church).update(retention_days=1)
        self.assertEqual(policy.prayer_retention_days(self.church), 7)
        self.assertEqual(policy.prayer_retention_days(self.church_b), 90)
        self.assertEqual(policy.prayer_retention_days(None), 90)

    def test_changing_the_days_never_moves_a_request_already_written(self):
        self.turn_on()
        prayer = self.write()
        self.client.force_login(self.pastor)
        self.assertRedirects(self.client.post(TERMS, {"paso": "guardar", "retention_days": "7", "emergency_line": ""}), TERMS)
        self.assertEqual(PrayerRequest.objects.get(pk=prayer.pk).expires_at, prayer.expires_at)
        later = self.write(self.leader)
        self.assertEqual(later.expires_at.date(), (later.created_at + timedelta(days=7)).date())
        audit = ChurchAuditEvent.objects.filter(action="oracion_politica").order_by("-id").first()
        self.assertEqual(audit.metadata, {"value": 7})

    def test_off_stops_new_requests_and_leaves_the_old_ones_to_be_read_only(self):
        self.turn_on()
        prayer = self.write()
        self.client.force_login(self.pastor)
        self.assertRedirects(self.client.post(TERMS, {"paso": "apagar"}), TERMS)
        self.assertFalse(services.is_on(self.church))
        # Readers: the list and the words, and nothing to do with them.
        self.assertContains(self.client.get(INBOX), "Pía Pidió")
        page = self.client.get(url("read", prayer.pk))
        self.assertContains(page, MARK)
        for name in ("mark_read", "reply", "mark_talked"):
            self.assertNotContains(page, url(name, prayer.pk))
        for name, data in (("mark_read", {}), ("reply", {"reply": "x"}), ("mark_talked", {})):
            self.assertEqual(self.client.post(url(name, prayer.pk), data).status_code, 404, name)
        for call in (services.mark_read, services.mark_talked):
            with self.assertRaises(services.Gone):
                call(self.church, self.pastor, prayer.pk)
        with self.assertRaises(services.Gone):
            services.reply(self.church, self.pastor, prayer.pk, "x")
        prayer.refresh_from_db()
        self.assertEqual((prayer.read_at, prayer.reply, prayer.talked_at), (None, "", None))
        # Writers: no form, nothing stored; who wrote keeps their page and can delete.
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.get(NEW).status_code, 404)
        self.assertEqual(self.client.get(MINE).status_code, 404)
        response = self.client.post(NEW, {"body": "x", "lectores": self.token_for(self.leader_user)})
        self.assertEqual(response.status_code, 404)
        with self.assertRaises(services.PrayerError):
            self.write(self.leader)
        self.assertNotContains(self.client.get(HOME), MINE)
        self.client.force_login(self.asked_user)
        self.assertEqual(self.client.get(NEW).status_code, 404)
        self.assertContains(self.client.get(MINE), "ya no recibe peticiones nuevas")
        self.assertContains(self.client.get(HOME), MINE)
        self.assertRedirects(self.client.post(url("delete", prayer.pk)), HOME)
        self.assertEqual(PrayerRequest.objects.count(), 0)

    def test_never_on_means_no_row_and_404_for_who_would_write(self):
        self.client.force_login(self.asked_user)
        for address in (MINE, NEW):
            self.assertEqual(self.client.get(address).status_code, 404)
        self.assertEqual(self.client.post(NEW, {"body": BODY, "lectores": "x"}).status_code, 404)
        self.assertNotContains(self.client.get(HOME), "oracion")
        self.assertEqual(PrayerRequest.objects.count(), 0)

    def test_with_the_module_off_only_the_writers_own_page_and_delete_answer(self):
        self.turn_on()
        prayer = self.write()
        services.reply(self.church, self.pastor, prayer.pk, REPLY)
        flags.set_enabled(self.church, "community", False)
        for user in (self.pastor, self.asked_user, self.admin, self.leader_user):
            self.client.force_login(user)
            for address in (NEW, INBOX, TERMS, url("read", prayer.pk)):
                self.assertEqual(self.client.get(address).status_code, 404, address)
            for address in (NEW, url("mark_read", prayer.pk), url("reply", prayer.pk), TERMS, READER_EMAIL):
                self.assertEqual(self.client.post(address, {"paso": "apagar", "body": "x"}).status_code, 404, address)
            if user != self.asked_user:  # nobody else's page shows it, and nobody else deletes it
                self.assertEqual(self.client.get(MINE).status_code, 404)
                self.assertEqual(self.client.get(url("delete", prayer.pk)).status_code, 404)
                self.assertEqual(self.client.post(url("delete", prayer.pk)).status_code, 404)
        self.assertEqual(PrayerRequest.objects.count(), 1)
        # The writer: a plain page with what they wrote, the answer and the way to delete; no way to write.
        self.client.force_login(self.asked_user)
        page = self.client.get(MINE)
        self.assertContains(page, MARK)
        self.assertContains(page, REPLY_MARK)
        self.assertContains(page, "ya no recibe peticiones nuevas")
        self.assertContains(page, services.EMERGENCY_911)
        self.assertNotContains(page, f'href="{NEW}"')
        self.assertNotContains(page, f'href="{HOME}"')
        self.assertContains(self.client.get(url("delete", prayer.pk)), "¿Borrar esta petición de oración?")
        response = self.client.post(url("delete", prayer.pk))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PrayerRequest.objects.count(), 0)
        self.assertEqual(self.client.get(MINE).status_code, 404)

    def test_the_module_off_and_the_switch_off_and_the_kill_switch_do_not_keep_the_writer_from_deleting(self):
        self.turn_on()
        prayer = self.write()
        services.switch_off(self.church, self.pastor)
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.asked_user)
        with self.settings(EMMAUS_V2_MODULES=False):
            self.assertContains(self.client.get(MINE), MARK)
            self.assertEqual(self.client.post(url("delete", prayer.pk)).status_code, 302)
        self.assertEqual(PrayerRequest.objects.count(), 0)

    def test_a_newer_text_of_the_promises_needs_a_pastor_to_accept_again(self):
        self.turn_on()
        prayer = self.write()
        with mock.patch.object(services, "PROMISES_VERSION", "2099-01"):
            self.assertFalse(services.is_on(self.church))
            self.assertEqual((services.terms(self.church).enabled, services.terms(self.church).outdated), (False, True))
            # Nothing new; what was written is read and not worked on; its writer keeps it.
            self.client.force_login(self.leader_user)
            self.assertEqual(self.client.get(NEW).status_code, 404)
            with self.assertRaises(services.PrayerError):
                self.write(self.leader)
            self.client.force_login(self.asked_user)
            self.assertContains(self.client.get(MINE), MARK)
            self.client.force_login(self.pastor)
            self.assertContains(self.client.get(url("read", prayer.pk)), MARK)
            self.assertEqual(self.client.post(url("mark_read", prayer.pk)).status_code, 404)
            page = self.client.get(TERMS)
            self.assertContains(page, "Cambió lo que Emmaus promete")
            self.assertContains(page, 'name="paso" value="encender"')
            with self.assertRaises(services.PrayerError):
                services.change_terms(self.church, self.pastor, emergency_line="", retention_days=30)
            # Only the acceptance of the text in force switches it back on, and it is recorded again.
            self.turn_on(actor=self.pastor2)
            row = PrayerPolicy.objects.get(church=self.church)
            self.assertEqual((row.promises_version, row.accepted_by), ("2099-01", self.pastor2))
            self.assertTrue(services.is_on(self.church))
            self.assertEqual(self.client.post(url("mark_read", prayer.pk)).status_code, 302)
        self.assertFalse(services.is_on(self.church))  # and the constant decides, not the row alone


class DenialTests(PrayerFixture):
    def setUp(self):
        self.turn_on()
        self.prayer = self.write()

    def test_nobody_but_the_pastors_of_the_church_reads(self):
        """The administration, a guide, the owner of a follow-up about the writer, a superuser, a
        pastor of another church, a known minor, the writer and another adult: 404 on every address."""
        for user in (self.admin, self.leader_user, self.coord1, self.root, self.pastor_b, self.minor_user, self.asked_user, self.member_user):
            with self.subTest(user=user.email):
                # The pastor of church B acts in church B, where the list and the switch are theirs: only the ids.
                self.assert_no_reader_route_answers(user, self.prayer, also_inbox=user != self.pastor_b)
                self.assertFalse(access.readable(user, self.church).exists())
                self.assertEqual(services.unread_count(user, self.church), 0)
                for call in (services.mark_read, services.mark_talked):
                    with self.assertRaises(services.Gone):
                        call(self.church, user, self.prayer.pk)
                with self.assertRaises(services.Gone):
                    services.reply(self.church, user, self.prayer.pk, "x")
        self.assertTrue(services.is_on(self.church))
        self.assertFalse(ReaderPreference.objects.exists())

    def test_the_test_above_fails_without_the_rule(self):
        with mock.patch("oracion.access.can_approve", return_value=True):
            self.client.force_login(self.admin)
            self.assertContains(self.client.get(url("read", self.prayer.pk)), MARK)

    def test_signed_out_goes_to_the_sign_in_page(self):
        self.client.logout()
        addresses = [(method, address, data) for method, address, data in self.reader_requests(self.prayer.pk)]
        addresses += [("get", MINE, {}), ("get", NEW, {}), ("post", NEW, {"body": "x"}), ("get", url("delete", self.prayer.pk), {}),
                      ("post", url("delete", self.prayer.pk), {})]
        for method, address, data in addresses:
            response = getattr(self.client, method)(address, data)
            self.assertEqual(response.status_code, 302, address)
            self.assertIn("/cuenta/entrar/", response["Location"])
        self.assertTrue(PrayerRequest.objects.filter(pk=self.prayer.pk, read_at__isnull=True).exists())

    def test_a_pastor_who_lost_the_role_loses_access_at_once(self):
        self.client.force_login(self.pastor)
        self.assertContains(self.client.get(url("read", self.prayer.pk)), MARK)
        UserChurchRole.objects.filter(user=self.pastor, church=self.church).delete()
        self.assert_no_reader_route_answers(self.pastor, self.prayer)
        self.assertNotContains(self.client.get(HOME), INBOX)
        # The same when the role became another one, and when the account was closed.
        assign_role(self.pastor, self.church, Role.CHURCH_ADMIN)
        self.assert_no_reader_route_answers(self.pastor, self.prayer)
        self.pastor2.is_active = False
        self.pastor2.save()
        self.assertFalse(access.readable(self.pastor2, self.church).exists())

    def test_a_guessed_id_of_another_church_or_of_nothing_is_a_404(self):
        self.turn_on(self.church_b, self.pastor_b)
        theirs = self.write(self.member_b)
        self.assert_no_reader_route_answers(self.pastor, theirs, also_inbox=False)
        self.client.force_login(self.pastor)
        self.assertNotContains(self.client.get(INBOX), "Bruno")
        for name in ("read", "mark_read", "reply", "mark_talked"):
            response = self.client.post(url(name, 999_999), {"reply": "x"}) if name != "read" else self.client.get(url(name, 999_999))
            self.assertEqual(response.status_code, 404)
        # The writer's own addresses: nobody deletes or sees somebody else's.
        for user in (self.pastor, self.admin, self.leader_user, self.root, self.member_b_user, self.pastor_b):
            self.client.force_login(user)
            for prayer in (self.prayer, theirs):
                if prayer.author_id == user.pk:
                    continue
                self.assertEqual(self.client.get(url("delete", prayer.pk)).status_code, 404)
                self.assertEqual(self.client.post(url("delete", prayer.pk)).status_code, 404)
        self.assertEqual(PrayerRequest.objects.count(), 2)

    def test_a_pastor_of_two_churches_sees_only_the_church_of_the_request(self):
        self.turn_on(self.church_b, self.pastor_b)
        theirs = self.write(self.member_b, body="Otra-iglesia-9911")
        assign_role(self.pastor, self.church_b, Role.PASTOR)
        self.client.force_login(self.pastor)
        page = self.client.get(INBOX)
        self.assertContains(page, "Pía Pidió")
        self.assertNotContains(page, "Bruno")
        self.assertEqual(self.client.get(url("read", theirs.pk)).status_code, 404)
        self.assertEqual(services.unread_count(self.pastor, self.church), 1)
        self.client.post(reverse("switch_church"), {"church": self.church_b.pk})
        page = self.client.get(INBOX)
        self.assertContains(page, "Bruno Miembro")
        self.assertNotContains(page, "Pía")
        self.assertEqual(self.client.get(url("read", self.prayer.pk)).status_code, 404)
        self.assertContains(self.client.get(url("read", theirs.pk)), "Otra-iglesia-9911")

    def test_the_pastor_who_was_not_chosen_does_not_read(self):
        only = self.write(self.leader, body="Solo-para-uno-4410", only=self.pastor)
        self.assertEqual((only.restricted, only.only_reader), (True, self.pastor))
        self.assert_no_reader_route_answers(self.pastor2, only, also_inbox=False)
        self.client.force_login(self.pastor2)
        self.assertNotContains(self.client.get(INBOX), "Gala")
        self.assertEqual(services.unread_count(self.pastor2, self.church), 1)
        self.assertEqual(services.unread_count(self.pastor, self.church), 2)
        self.client.force_login(self.pastor)
        self.assertContains(self.client.get(INBOX), "Solo para ti.")
        self.assertContains(self.client.get(url("read", only.pk)), "Solo-para-uno-4410")
        self.client.post(url("mark_read", only.pk))
        # The writer chose one reader, so the page may name them.
        self.client.force_login(self.leader_user)
        self.assertContains(self.client.get(MINE), "por Pablo Pastor")
        # The chosen pastor leaves: it never widens to the others.
        UserChurchRole.objects.filter(user=self.pastor, church=self.church).delete()
        self.assert_no_reader_route_answers(self.pastor2, only, also_inbox=False)
        self.client.force_login(self.leader_user)
        self.assertContains(self.client.get(MINE), "Hoy nadie la puede leer.")
        self.pastor.delete()
        only.refresh_from_db()
        self.assertEqual((only.restricted, only.only_reader), (True, None))
        self.assertFalse(access.readable(self.pastor2, self.church).filter(pk=only.pk).exists())

    def test_the_form_offers_one_reader_and_refuses_one_who_is_not(self):
        self.client.force_login(self.leader_user)
        page = self.client.get(NEW)
        self.assertContains(page, "Solo Pablo Pastor")
        self.assertContains(page, "Solo Paula Pastora")
        token = self.token_for(self.leader_user)
        for bad in (str(self.admin.pk), str(self.pastor_b.pk), "999999", "x"):
            response = self.client.post(NEW, {"body": "Hola", "reader": bad, "lectores": token})
            self.assertEqual(response.status_code, 200, bad)
        self.assertEqual(PrayerRequest.objects.filter(person=self.leader).count(), 0)
        with self.assertRaises(services.PrayerError):
            services.create(self.church, self.leader, body="Hola", token=token, only_reader_id=self.admin.pk)
        self.assertRedirects(self.client.post(NEW, {"body": "Hola", "reader": str(self.pastor2.pk), "lectores": token}), MINE)
        self.assertEqual(PrayerRequest.objects.get(person=self.leader).only_reader, self.pastor2)

    def test_a_pastor_does_not_read_their_own_request_as_a_reader(self):
        own = self.write(self.pastor_person, body="Del-pastor-3301")
        self.client.force_login(self.pastor)
        self.assertContains(self.client.get(NEW), "<strong>Lo lee:</strong> Paula Pastora,")  # the other pastor only
        self.assertEqual(self.client.get(url("read", own.pk)).status_code, 404)
        self.client.force_login(self.pastor2)
        self.assertContains(self.client.get(url("read", own.pk)), "Del-pastor-3301")


class WriterTests(PrayerFixture):
    def setUp(self):
        self.turn_on()

    def post(self, user, body=BODY, **extra):
        self.client.force_login(user)
        return self.client.post(NEW, {"body": body, "lectores": self.token_for(user), **extra})

    def test_a_known_minor_does_not_see_it_at_all(self):
        flagged_user = make_user("campera@prueba.mx", "Mateo", "Ramírez Díaz")
        flagged = self._person(self.church, flagged_user)
        link_participant(flagged, self.p4, self.admin)  # no date of birth; a campero of 16
        for user, person in ((self.minor_user, self.minor), (flagged_user, flagged)):
            with self.subTest(user=user.email):
                self.assertTrue(access.known_minor(person))
                self.client.force_login(user)
                self.assertEqual(self.client.get(MINE).status_code, 404)
                self.assertEqual(self.client.get(NEW).status_code, 404)
                self.assertEqual(self.post(user).status_code, 404)
                self.assertNotContains(self.client.get(HOME), "oracion")
                with self.assertRaises(services.PrayerError):
                    self.write(person)
        self.assertEqual(PrayerRequest.objects.count(), 0)

    def test_who_counts_as_an_adult(self):
        today = timezone.localdate()
        self.assertFalse(access.known_minor(self.asked))  # nothing known and nothing that marks them as a minor
        self.assertFalse(access.known_minor(self.member))  # went to a camp at 19
        grown = Person(church=self.church, first_name="A", birth_date=date(today.year - 18, today.month, today.day))
        self.assertFalse(access.known_minor(grown))
        almost = Person(church=self.church, first_name="B", birth_date=date(today.year - 18, today.month, today.day) + timedelta(days=1))
        self.assertTrue(access.known_minor(almost))
        self.assertFalse(access.may_write(self.no_account))  # no account: nobody to show it to
        self.assertFalse(access.may_write(None))

    def test_somebody_marked_as_a_minor_at_a_door_is_not_an_adult_by_default(self):
        from agenda.models import EventRegistration

        EventRegistration.objects.create(event=self.event, person=self.asked, guest_is_minor=True)
        self.assertTrue(access.known_minor(self.asked))
        self.assertEqual(self.post(self.asked_user).status_code, 404)

    def test_without_anybody_to_read_nothing_is_collected(self):
        UserChurchRole.objects.filter(church=self.church, role=Role.PASTOR).delete()
        self.client.force_login(self.asked_user)
        page = self.client.get(NEW)
        self.assertContains(page, "Por ahora no está disponible.")
        self.assertNotContains(page, "<textarea")
        self.assertContains(page, services.EMERGENCY_911)
        response = self.client.post(NEW, {"body": BODY, "lectores": "x"})
        self.assertNotContains(response, "<textarea")
        with self.assertRaises(services.PrayerError):
            services.create(self.church, self.asked, body=BODY, token=services.readers_token([], services.terms(self.church)))
        self.assertEqual(PrayerRequest.objects.count(), 0)
        # The only pastor of a church has nobody to write to.
        self.turn_on(self.church_b, self.pastor_b)
        self.client.force_login(self.pastor_b)
        self.assertContains(self.client.get(NEW), "Por ahora no está disponible.")

    def test_it_is_not_saved_when_who_reads_changed_while_writing(self):
        token = self.token_for(self.asked_user)
        newcomer = make_user("nuevo-pastor@prueba.mx", "Nahúm", "Nuevo")
        assign_role(newcomer, self.church, Role.PASTOR)
        self.client.force_login(self.asked_user)
        response = self.client.post(NEW, {"body": BODY, "lectores": token})
        self.assertContains(response, services.READERS_CHANGED)
        self.assertContains(response, "Nahúm Nuevo")
        self.assertContains(response, MARK)  # what they typed is still in their own form
        self.assertEqual(PrayerRequest.objects.count(), 0)
        for missing in ({}, {"lectores": ""}, {"lectores": "x"}):
            self.client.post(NEW, {"body": BODY, **missing})
        self.assertEqual(PrayerRequest.objects.count(), 0)

    def test_it_is_not_saved_when_the_days_or_the_line_changed_while_writing(self):
        self.client.force_login(self.asked_user)
        page = self.client.get(NEW)
        self.assertContains(page, 'autocomplete="off"')
        token = self.token_for(self.asked_user)
        services.change_terms(self.church, self.pastor, emergency_line="", retention_days=7)
        response = self.client.post(NEW, {"body": BODY, "lectores": token})
        self.assertContains(response, "a los 7 días")  # the form again, with what is true now
        self.assertContains(response, "No se guardó nada")
        self.assertEqual(PrayerRequest.objects.count(), 0)
        token = self.token_for(self.asked_user)
        services.change_terms(self.church, self.pastor, emergency_line="Línea nueva 55 1111 0000", retention_days=7)
        response = self.client.post(NEW, {"body": BODY, "lectores": token})
        self.assertContains(response, "Línea nueva 55 1111 0000")
        self.assertEqual(PrayerRequest.objects.count(), 0)
        with self.assertRaises(services.PrayerError):
            services.create(self.church, self.asked, body=BODY, token=token)
        self.assertRedirects(self.client.post(NEW, {"body": BODY, "lectores": self.token_for(self.asked_user)}), MINE)
        prayer = PrayerRequest.objects.get()
        self.assertEqual(prayer.expires_at.date(), (prayer.created_at + timedelta(days=7)).date())

    def test_the_text_is_one_and_short(self):
        self.assertContains(self.post(self.asked_user, body="  "), "Escribe tu petición.")
        self.assertContains(self.post(self.asked_user, body="x" * 1001), "Escribe 1,000 caracteres o menos.")
        for body in ("", "x" * 1001):
            with self.assertRaises(services.PrayerError):
                self.write(body=body)
        self.assertRedirects(self.post(self.asked_user, body="x" * 1000), MINE)
        fields = {field.name for field in PrayerRequest._meta.get_fields()}
        self.assertEqual(fields, {
            "id", "church", "person", "author", "body", "restricted", "only_reader", "created_at", "expires_at", "read_at", "reply",
            "replied_at", "talked_at",
        })
        with self.assertRaises(services.PrayerError):
            services.reply(self.church, self.pastor, PrayerRequest.objects.get().pk, "x" * 501)

    def test_a_person_keeps_at_most_five(self):
        for _ in range(services.MAX_KEPT):
            self.write()
        self.assertContains(self.post(self.asked_user), services.TOO_MANY)
        self.assertEqual(PrayerRequest.objects.count(), services.MAX_KEPT)
        page = self.client.get(MINE)
        self.assertContains(page, "Borra una para escribir otra.")
        self.assertNotContains(page, f'href="{NEW}"')

    def test_the_writer_deletes_it_with_its_reply(self):
        prayer = self.write()
        services.reply(self.church, self.pastor, prayer.pk, REPLY)
        audits = ChurchAuditEvent.objects.count()
        self.client.force_login(self.asked_user)
        page = self.client.get(url("delete", prayer.pk))
        self.assertContains(page, "¿Borrar esta petición de oración?")
        self.assertTrue(PrayerRequest.objects.filter(pk=prayer.pk).exists())
        self.assertRedirects(self.client.post(url("delete", prayer.pk)), MINE)
        self.assertFalse(PrayerRequest.objects.exists())
        self.assertEqual(ChurchAuditEvent.objects.count(), audits)  # deleting leaves nothing behind
        self.client.force_login(self.pastor)
        self.assertNotContains(self.client.get(INBOX), "Pía")
        self.assertEqual(self.client.get(url("read", prayer.pk)).status_code, 404)
        self.assertEqual(self.client.post(url("reply", prayer.pk), {"reply": "x"}).status_code, 404)

    def test_a_post_without_the_csrf_token_is_refused(self):
        prayer = self.write()
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.asked_user)
        self.assertEqual(strict.post(NEW, {"body": "Otra", "lectores": self.token_for(self.asked_user)}).status_code, 403)
        self.assertEqual(strict.post(url("delete", prayer.pk)).status_code, 403)
        strict.force_login(self.pastor)
        for address, data in ((url("mark_read", prayer.pk), {}), (url("reply", prayer.pk), {"reply": "x"}),
                              (url("mark_talked", prayer.pk), {}), (TERMS, {"paso": "apagar"}), (READER_EMAIL, {"avisar": "si"})):
            self.assertEqual(strict.post(address, data).status_code, 403, address)
        prayer.refresh_from_db()
        self.assertEqual((PrayerRequest.objects.count(), prayer.read_at, prayer.reply), (1, None, ""))
        self.assertTrue(services.is_on(self.church))

    def test_reader_addresses_take_no_get(self):
        prayer = self.write()
        self.client.force_login(self.pastor)
        for name in ("mark_read", "reply", "mark_talked"):
            self.assertEqual(self.client.get(url(name, prayer.pk)).status_code, 405)
        self.assertEqual(self.client.get(READER_EMAIL).status_code, 405)


class RaceTests(PrayerFixture):
    def setUp(self):
        self.turn_on()
        self.prayer = self.write()

    def test_two_pastors_answer_and_the_first_answer_stays(self):
        services.reply(self.church, self.pastor, self.prayer.pk, "La primera")
        with self.assertRaises(services.PrayerError):
            services.reply(self.church, self.pastor2, self.prayer.pk, "La segunda")
        self.assertEqual(PrayerRequest.objects.get().reply, "La primera")

    def test_a_stale_copy_never_moves_the_day_it_was_read(self):
        stale = PrayerRequest.objects.get(pk=self.prayer.pk)
        services.mark_read(self.church, self.pastor, self.prayer.pk)
        first = PrayerRequest.objects.get().read_at
        services.mark_read(self.church, self.pastor2, stale.pk)
        services.mark_talked(self.church, self.pastor2, stale.pk)
        services.reply(self.church, self.pastor2, stale.pk, "Oro por ti")
        row = PrayerRequest.objects.get()
        self.assertEqual(row.read_at, first)
        self.assertEqual(row.body, BODY)

    def test_the_writer_deleted_it_while_a_pastor_had_it_open(self):
        self.client.force_login(self.pastor)
        self.client.get(url("read", self.prayer.pk))
        services.delete_own(self.church, self.asked_user, self.prayer.pk)
        for name, data in (("mark_read", {}), ("reply", {"reply": "x"}), ("mark_talked", {})):
            self.assertEqual(self.client.post(url(name, self.prayer.pk), data).status_code, 404)
        self.assertFalse(PrayerRequest.objects.exists())

    def test_it_was_switched_off_while_somebody_was_writing(self):
        token = self.token_for(self.leader_user)
        services.switch_off(self.church, self.pastor2)
        with self.assertRaises(services.PrayerError):
            services.create(self.church, self.leader, body="Tarde", token=token)
        self.assertEqual(PrayerRequest.objects.count(), 1)


class NowhereElseTests(PrayerFixture):
    """The words stay in their table: not in a log, an audit row, an e-mail or anybody else's screen."""

    def journey(self):
        self.client.force_login(self.asked_user)
        self.client.post(NEW, {"body": BODY, "lectores": self.token_for(self.asked_user)})
        prayer = PrayerRequest.objects.get()
        self.client.force_login(self.pastor)
        self.client.get(INBOX)
        self.client.get(url("read", prayer.pk))
        self.client.post(url("mark_read", prayer.pk))
        self.client.post(url("reply", prayer.pk), {"reply": REPLY})
        self.client.post(url("reply", prayer.pk), {"reply": REPLY})  # refused: also silent
        self.client.post(url("mark_talked", prayer.pk))
        self.client.force_login(self.admin)
        self.client.get(url("read", prayer.pk))  # a denial
        return prayer

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_not_in_a_log_an_audit_row_or_an_e_mail(self):
        self.turn_on(emergency_line="Línea-propia-8080")
        services.set_wants_email(self.church, self.pastor, True)
        with self.assertLogs(level="DEBUG") as logs:
            logging.getLogger("oracion.prueba").debug("para que haya al menos una línea")
            prayer = self.journey()
            call_command("prune_prayer_requests", stdout=StringIO())
        written = "\n".join(logs.output) + "\n".join(str(record.__dict__) for record in logs.records)
        for secret in (MARK, REPLY_MARK, "pido oración", "oro por ti", "Línea-propia-8080"):
            self.assertNotIn(secret, written)
        rows = list(ChurchAuditEvent.objects.filter(action__startswith="oracion"))
        self.assertEqual(
            sorted(row.action for row in rows),
            ["oracion_abierta", "oracion_atendida", "oracion_atendida", "oracion_atendida", "oracion_politica"],
        )
        for row in ChurchAuditEvent.objects.all():
            stored = f"{row.summary} {row.metadata} {row.actor_label}"
            for secret in (MARK, REPLY_MARK, "pido oración", "oro por ti", "Línea-propia-8080"):
                self.assertNotIn(secret, stored)
            if row.action.startswith("oracion"):
                self.assertNotIn("Pía", stored)
                # Ids and fixed words: never the person who wrote, and the writer is never the actor of a row.
                self.assertNotIn("person_id", row.metadata)
                self.assertNotEqual(row.actor_id, self.asked_user.pk)
                self.assertIn(row.object_type, ("PrayerRequest", "PrayerPolicy"))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual((message.to, message.subject), ([self.pastor.email], "Emmaus: hay una petición de oración nueva"))
        self.assertIn("Hay una petición de oración nueva en Emmaus", message.body)
        self.assertIn(INBOX, message.body)
        for secret in (MARK, "pido oración", "Pía", "Pidió", str(prayer.pk) + "/"):
            self.assertNotIn(secret, message.body + message.subject)

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_an_e_mail_only_to_a_reader_of_that_request_who_asked_for_it(self):
        self.turn_on()
        self.journey()
        self.assertEqual(mail.outbox, [])  # nobody asked: the default is no e-mail
        self.client.force_login(self.pastor2)
        self.assertRedirects(self.client.post(READER_EMAIL, {"avisar": "si"}), INBOX)
        self.assertContains(self.client.get(INBOX), 'name="avisar" value="si" aria-describedby="avisar-ayuda" checked')
        services.set_wants_email(self.church, self.pastor, True)
        self.client.force_login(self.leader_user)
        self.client.post(NEW, {"body": "Para una", "reader": str(self.pastor.pk), "lectores": self.token_for(self.leader_user)})
        self.assertEqual([message.to for message in mail.outbox], [[self.pastor.email]])
        # The writer is never mailed, and who left the role is not mailed either.
        UserChurchRole.objects.filter(user=self.pastor, church=self.church).delete()
        mail.outbox.clear()
        self.client.force_login(self.coord1)
        self.client.post(NEW, {"body": "Para todos", "lectores": self.token_for(self.coord1)})
        self.assertEqual([message.to for message in mail.outbox], [[self.pastor2.email]])
        self.client.force_login(self.pastor2)
        self.client.post(READER_EMAIL, {})
        mail.outbox.clear()
        self.write(self.admin_person)
        services.notify_readers(PrayerRequest.objects.filter(person=self.admin_person).get(), "https://emmaus.invalid/x/")
        self.assertEqual(mail.outbox, [])

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_writing_and_deleting_again_and_again_is_not_a_stream_of_e_mails(self):
        from events.models import AuthThrottle

        self.turn_on()
        services.set_wants_email(self.church, self.pastor, True)
        for _ in range(services.MAIL_PER_HOUR + 3):
            prayer = self.write()
            services.notify_readers(prayer, "https://emmaus.invalid/mi-iglesia/oracion/recibidas/")
            services.delete_own(self.church, self.asked_user, prayer.pk)
        self.assertEqual(len(mail.outbox), services.MAIL_PER_HOUR)
        # The counter is the reader's: nothing in it says who wrote.
        self.assertEqual(list(AuthThrottle.objects.values_list("key", flat=True)), [f"oracion-aviso:{self.church.pk}:{self.pastor.pk}"])

    def test_a_mail_that_fails_changes_nothing_for_the_writer(self):
        self.turn_on()
        services.set_wants_email(self.church, self.pastor, True)
        self.client.force_login(self.asked_user)
        with mock.patch("oracion.services.notify.notify", side_effect=RuntimeError(MARK)), self.assertLogs("oracion.views", "ERROR") as logs:
            response = self.client.post(NEW, {"body": BODY, "lectores": self.token_for(self.asked_user)})
        self.assertRedirects(response, MINE)
        self.assertNotIn(MARK, "\n".join(logs.output))
        self.assertEqual(PrayerRequest.objects.count(), 1)

    def test_not_on_anybody_elses_screen_and_no_number_for_who_does_not_read(self):
        self.turn_on()
        prayer = self.journey()
        second = self.write(self.leader, body="Otra-más-2207")
        screens = [HOME, reverse("care:mine"), reverse("notices:list"), reverse("agenda:list"), reverse("community:profile")]
        staff = [reverse("overview:panorama"), reverse("care:admin_list"), reverse("people:detail", args=[self.asked.pk]),
                 reverse("people:list"), reverse("church_policies")]
        for user, addresses in ((self.admin, screens + staff), (self.coord1, screens), (self.root, screens),
                                (self.member_user, screens), (self.pastor_b, screens)):
            self.client.force_login(user)
            for address in addresses:
                html = self.client.get(address).content.decode()
                with self.subTest(user=user.email, address=address):
                    self.assertEqual(html.count("<h1"), 1)  # the page itself answered
                    for secret in (MARK, REPLY_MARK, "Otra-más-2207", "sin leer"):
                        self.assertNotIn(secret, html)
                    if user != self.pastor_b:  # in church B that pastor has the door to church B's own list
                        self.assertNotIn(INBOX, html)
                        self.assertNotIn("Peticiones de oración</span>", html)
        # The pastor's own number is of what is unread for them, and Panorama says nothing about it.
        self.client.force_login(self.pastor)
        self.assertContains(self.client.get(HOME), '1<span class="visually-hidden"> petición sin leer</span>')
        panorama = self.client.get(reverse("overview:panorama")).content.decode()
        for secret in (MARK, "Otra-más-2207", "Peticiones de oración", "peticiones de oración"):
            self.assertNotIn(secret, panorama.split('<div class="panorama">', 1)[1].split("</main>", 1)[0])
        self.assertEqual(str(prayer), f"Petición de oración {prayer.pk}")
        self.assertNotIn(MARK, repr(prayer) + repr(second))

    def test_no_page_of_the_django_admin(self):
        for model in (PrayerRequest, PrayerPolicy, ReaderPreference):
            self.assertFalse(django_admin.site.is_registered(model))
        self.client.force_login(self.root)
        self.assertEqual(self.client.get("/admin/oracion/prayerrequest/").status_code, 404)


class RetentionTests(PrayerFixture):
    def setUp(self):
        self.turn_on()

    def age(self, prayer, days):
        moment = timezone.now() - timedelta(days=days)
        PrayerRequest.objects.filter(pk=prayer.pk).update(created_at=moment, expires_at=moment + timedelta(days=90))

    def test_ninety_days_after_it_was_written_it_is_deleted_with_its_reply(self):
        old, recent, replied = self.write(), self.write(self.leader), self.write(self.coord_person)
        services.reply(self.church, self.pastor, replied.pk, REPLY)
        self.age(old, 91)
        self.age(replied, 90)
        self.age(recent, 89)
        self.assertEqual(services.prune(apply=False), 2)
        self.assertEqual(PrayerRequest.objects.count(), 3)
        self.assertEqual(services.prune(), 2)
        self.assertEqual(list(PrayerRequest.objects.values_list("pk", flat=True)), [recent.pk])
        self.assertEqual(services.prune(), 0)

    def test_nothing_outlives_the_platforms_ninety_days_whatever_its_row_says(self):
        prayer = self.write()
        PrayerRequest.objects.filter(pk=prayer.pk).update(
            created_at=timezone.now() - timedelta(days=91), expires_at=timezone.now() + timedelta(days=500)
        )
        self.assertEqual(services.prune(), 1)

    def test_a_shorter_period_of_the_church_is_the_date_of_a_new_request(self):
        PrayerPolicy.objects.filter(church=self.church).update(retention_days=7)
        prayer = self.write()
        PrayerRequest.objects.filter(pk=prayer.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(services.prune(), 1)

    def test_a_request_past_its_date_is_gone_for_every_screen_before_the_job_runs(self):
        prayer, fresh = self.write(), self.write(self.leader, body="Vigente-6612")
        self.client.force_login(self.pastor)
        self.client.post(url("mark_read", fresh.pk))
        PrayerRequest.objects.filter(pk=prayer.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertTrue(PrayerRequest.objects.filter(pk=prayer.pk).exists())  # the job has not run
        page = self.client.get(INBOX)
        self.assertNotContains(page, "Pía")
        self.assertContains(page, "Gala Guía")
        self.assertNotContains(self.client.get(HOME), "sin leer</span>")
        self.assertEqual(services.unread_count(self.pastor, self.church), 0)
        self.assertEqual(self.client.get(url("read", prayer.pk)).status_code, 404)
        for name, data in (("mark_read", {}), ("reply", {"reply": "x"}), ("mark_talked", {})):
            self.assertEqual(self.client.post(url(name, prayer.pk), data).status_code, 404, name)
        for call in (services.mark_read, services.mark_talked):
            with self.assertRaises(services.Gone):
                call(self.church, self.pastor, prayer.pk)
        self.assertEqual(PrayerRequest.objects.get(pk=prayer.pk).read_at, None)
        self.client.force_login(self.asked_user)
        self.assertNotContains(self.client.get(MINE), MARK)
        self.assertEqual(self.client.get(url("delete", prayer.pk)).status_code, 404)
        self.assertFalse(access.own(self.asked_user, self.church).exists())
        # The same for one older than the platform's 90 days, whatever its row says.
        PrayerRequest.objects.filter(pk=fresh.pk).update(created_at=timezone.now() - timedelta(days=91))
        self.assertFalse(access.readable(self.pastor, self.church).exists())
        self.assertFalse(access.own(self.leader_user, self.church).exists())
        services.switch_off(self.church, self.pastor)
        self.assertEqual(self.client.get(MINE).status_code, 404)  # no door left to something that is gone
        self.client.force_login(self.leader_user)
        self.assertNotContains(self.client.get(HOME), MINE)

    def test_the_scheduled_job_deletes_whatever_the_switches_say(self):
        """Through the command: the church's module off, its own switch off, and each of the two settings
        that stop the community jobs."""
        services.switch_off(self.church, self.pastor)
        flags.set_enabled(self.church, "community", False)
        for off in ({}, {"EMMAUS_SCHEDULED_COMMUNITY_JOBS": False}, {"EMMAUS_V2_MODULES": False}):
            with self.subTest(off=off), self.settings(**off):
                self.turn_on()
                prayer, kept = self.write(), self.write(self.leader)
                services.switch_off(self.church, self.pastor)
                self.age(prayer, 100)
                out = StringIO()
                with self.assertLogs("emmaus.scheduler", "INFO") as logs:
                    call_command("run_scheduled_jobs", stdout=out)
                self.assertIn("peticiones de oración: 1 vencidas borradas", out.getvalue())
                self.assertEqual(list(PrayerRequest.objects.values_list("pk", flat=True)), [kept.pk])
                line = next(output for output in logs.output if "oracion_retencion" in output)
                self.assertIn('"deleted": 1', line)
                self.assertNotIn(MARK, "\n".join(logs.output) + out.getvalue())
                PrayerRequest.objects.all().delete()

    def test_the_command_says_how_many_and_deletes_only_with_apply(self):
        prayer = self.write()
        self.age(prayer, 100)
        out = StringIO()
        call_command("prune_prayer_requests", stdout=out)
        self.assertIn("vencidas: 1. No se borró nada", out.getvalue())
        self.assertEqual(PrayerRequest.objects.count(), 1)
        call_command("prune_prayer_requests", "--apply", stdout=out)
        self.assertEqual(PrayerRequest.objects.count(), 0)

    def test_the_writers_page_says_the_day(self):
        prayer = self.write()
        self.client.force_login(self.asked_user)
        from groups.templatetags.community import long_date

        self.assertContains(self.client.get(MINE), f"Emmaus la borra el {long_date(timezone.localtime(prayer.expires_at))}")


class MergeTests(PrayerFixture):
    """A request belongs to the account that wrote it: joining two records of a person moves nothing,
    counts nothing, and is never refused or even changed by a request, so a preview tells nothing."""

    def setUp(self):
        self.turn_on()
        self.twin = Person.objects.create(church=self.church, first_name="Pía", last_name="Pidió", email=self.asked.email)

    def stated(self, survivor, absorbed):
        survivor.refresh_from_db()
        absorbed.refresh_from_db()
        preview = merge.build_preview(self.church, survivor, absorbed)
        return (preview.token, preview.refusals, preview.moves, preview.stays, preview.move_lines, preview.stay_lines, preview.account)

    def join(self, survivor, absorbed):
        token = self.stated(survivor, absorbed)[0]
        return merge.merge(self.church, survivor.pk, absorbed.pk, self.admin, choices={}, token=token)

    def assert_writer_keeps_it(self, prayer):
        self.client.force_login(self.asked_user)
        self.assertContains(self.client.get(MINE), MARK)
        self.assertContains(self.client.get(url("delete", prayer.pk)), "¿Borrar esta petición de oración?")
        self.client.force_login(self.pastor)
        self.assertContains(self.client.get(INBOX), "Pía Pidió")
        self.assertContains(self.client.get(url("read", prayer.pk)), MARK)

    def test_the_preview_and_the_merge_are_the_same_with_and_without_a_request(self):
        for survivor, absorbed in ((self.asked, self.twin), (self.twin, self.asked)):
            without = self.stated(survivor, absorbed)
            self.assertEqual(without[1], [])
            prayer = self.write()
            self.assertEqual(self.stated(survivor, absorbed), without)
            self.assertNotIn("oracion.PrayerRequest.person", without[2])
            record = self.join(survivor, absorbed)
            self.assertNotIn("oraci", str(record.manifest).lower())
            row = PrayerRequest.objects.get()
            self.assertEqual((row.person_id, row.author_id), (self.asked.pk, self.asked_user.pk))  # nothing moved
            self.assert_writer_keeps_it(prayer)
            merge.undo(self.church, record.pk, self.admin)
            self.assertEqual(PrayerRequest.objects.get().author_id, self.asked_user.pk)
            self.assert_writer_keeps_it(prayer)
            self.client.force_login(self.asked_user)
            self.assertRedirects(self.client.post(url("delete", prayer.pk)), MINE)
        self.assertEqual(merge.undecided_relations(), [])
        self.assertIn(("oracion.PrayerRequest", "person"), merge.NOTHING_MOVES)
        self.assertFalse(hasattr(merge, "UNMERGEABLE"))

    def test_the_writer_deletes_after_a_merge_moved_the_account_to_the_other_record(self):
        prayer = self.write()
        self.join(self.twin, self.asked)  # the account now belongs to the record that was the twin
        self.assertEqual(Person.objects.get(pk=self.twin.pk).user, self.asked_user)
        self.client.force_login(self.asked_user)
        self.assertRedirects(self.client.post(url("delete", prayer.pk)), MINE)
        self.assertEqual(PrayerRequest.objects.count(), 0)

    def test_the_account_of_the_surviving_record_never_sees_what_another_account_wrote(self):
        prayer = self.write()
        # The record lost its account, and is then joined to the record of another account.
        Person.objects.filter(pk=self.asked.pk).update(user=None)
        other_user = make_user("otra-pia@prueba.mx", "Pía", "Pidió")
        other = self._person(self.church, other_user)
        Person.objects.filter(pk=other.pk).update(email=self.asked.email)
        self.join(other, self.asked)
        self.client.force_login(other_user)
        self.assertNotContains(self.client.get(MINE), MARK)
        self.assertEqual(self.client.get(url("delete", prayer.pk)).status_code, 404)
        self.assertEqual(self.client.post(url("delete", prayer.pk)).status_code, 404)
        self.assertFalse(access.own(other_user, self.church).exists())
        self.assertFalse(services.delete_own(self.church, other_user, prayer.pk))
        self.assertTrue(PrayerRequest.objects.filter(pk=prayer.pk, author=self.asked_user).exists())


class SeedTests(PrayerFixture):
    def test_the_plain_seed_has_no_prayer_text_and_the_flag_adds_two_synthetic_ones(self):
        with self.settings(DEBUG=True):
            call_command("seed_community_demo", "--password", "Clave-de-prueba-1", stdout=StringIO())
            self.assertEqual(PrayerRequest.objects.count(), 0)
            self.assertFalse(PrayerPolicy.objects.exists())
            for _ in range(2):  # twice: the second run adds nothing
                call_command("seed_community_demo", "--password", "Clave-de-prueba-1", "--oracion", stdout=StringIO())
        rows = PrayerRequest.objects.all()
        self.assertEqual(rows.count(), 2)
        self.assertTrue(all(row.body.startswith("Texto de demostración, inventado:") for row in rows))
        row = PrayerPolicy.objects.get()
        self.assertTrue(row.enabled)
        self.assertEqual(row.accepted_by.email, "pastor.iglesia@demo.invalid")
