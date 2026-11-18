"""Issue #96, first slice, on synthetic data: the public notices of an event. The administration publishes them;
anybody with the event's public address reads them without an account; nothing about people is ever in them or
around them. Each test names the defect it would catch."""

import logging
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from urllib.parse import parse_qs, urlsplit

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from agenda import public, services
from agenda.models import EventNotice, EventPage
from agenda.tests.test_event_extras import ExtrasFixture
from core.models import Church
from events.models import Event, EventMembership, MembershipRole
from events.tests.base import make_user
from notices.models import Announcement
from people import flags
from people.models import ChurchAuditEvent

TITLE = "El camión sale a las 7:00"
BODY = "Lleguen media hora antes.\nLleven chamarra."


def public_url(event) -> str:
    return reverse("evento:detail", args=[event.slug])


class NoticesFixture(ExtrasFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        services.save_page(cls.retiro, cls.admin, public=True)
        services.save_page(cls.retiro_b, cls.admin_b, public=True)

    def publish(self, title=TITLE, body=BODY, event=None, actor=None, at=None) -> EventNotice:
        notice = services.save_public_notice(event or self.retiro, actor or self.admin, title=title, body=body)
        if at is not None:
            EventNotice.objects.filter(pk=notice.pk).update(published_at=at)
            notice.refresh_from_db()
        return notice


class PublicReadingTests(NoticesFixture):
    def test_no_section_when_there_are_no_notices(self):
        """Defect: an empty «Avisos» heading on every public page."""
        page = self.client.get(public_url(self.retiro))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'id="avisos"')
        self.assertNotContains(page, "Avisos")

    def test_anybody_reads_them_newest_first_with_the_local_hour_and_no_author(self):
        now = timezone.now()
        self.publish("Aviso de ayer", "Uno.", at=now - timedelta(days=1))
        self.publish("Aviso de hoy", "Dos.", at=now)
        # 13:05 UTC on 3 January is 07:05 in Mexico City, the event's time zone. This year's dates carry no year.
        year = timezone.localdate().year
        self.publish("Aviso viejo", "Tres.", at=datetime(year, 1, 3, 13, 5, tzinfo=dt_timezone.utc))
        page = self.client.get(public_url(self.retiro))
        self.assertContains(page, '<h2 id="avisos" class="heading">Avisos</h2>', count=1)
        self.assertContains(page, "<h1", count=1)
        text = page.content.decode()
        self.assertLess(text.index("Aviso de hoy"), text.index("Aviso de ayer"))
        self.assertLess(text.index("Aviso de ayer"), text.index("Aviso viejo"))
        self.assertLess(text.index('id="datos"'), text.index('id="avisos"'))
        self.assertIn("3 de enero a las 07:05</time>.", text)
        self.assertNotIn("Editado", text)
        for word in (self.admin.first_name, self.admin.last_name, self.admin.email, "csrfmiddlewaretoken"):
            self.assertNotIn(word, text)
        self.assertEqual(dict(page.cookies), {})  # no session, no cookie

    def test_a_date_of_another_year_says_the_year(self):
        """Defect: an old notice on a long event reading as if it were of this year."""
        year = timezone.localdate().year - 1
        notice = self.publish(at=datetime(year, 3, 3, 13, 5, tzinfo=dt_timezone.utc))
        EventNotice.objects.filter(pk=notice.pk).update(edited_at=datetime(year, 3, 4, 2, 30, tzinfo=dt_timezone.utc))
        public_page = self.client.get(public_url(self.retiro))
        self.assertContains(public_page, f"Publicado el <time datetime=\"{year}-03-03T07:05:00-06:00\">3 de marzo de {year} a las 07:05</time>")
        self.assertContains(public_page, f"3 de marzo de {year} a las 20:30</time>.")  # the evening before, local
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url("public_notices")), f"Publicado el 3 de marzo de {year} a las 07:05. Editado el 3 de marzo de {year} a las 20:30.")

    def test_a_time_zone_that_does_not_exist_falls_back_instead_of_failing(self):
        """Defect: ``ZoneInfoNotFoundError`` (a KeyError) answering 500 on the public page, the members' and the staff's."""
        self.publish()
        for zone in ("Nope/Zone", ""):
            Event.objects.filter(pk=self.retiro.pk).update(time_zone=zone)
            self.client.logout()
            self.assertContains(self.client.get(public_url(self.retiro)), "Publicado el ")
            self.client.force_login(self.member_user)
            self.assertContains(self.client.get(self.url()), "Publicado el ")
            self.client.force_login(self.admin)
            self.assertContains(self.client.get(self.url("public_notices")), "Publicado el ")

    def test_the_text_is_escaped_and_keeps_its_line_breaks(self):
        """Defect: markup typed by the administration running in every reader's browser."""
        self.publish("<script>alert(1)</script>", "<b>Fuerte</b>\nSegunda línea & más")
        text = self.client.get(public_url(self.retiro)).content.decode()
        self.assertNotIn("<script>alert(1)</script>", text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)
        self.assertIn("&lt;b&gt;Fuerte&lt;/b&gt;<br>Segunda línea &amp; más", text)

    def test_a_retired_notice_is_gone_and_the_section_with_the_last_one(self):
        keep, gone = self.publish("Se queda", "Sí."), self.publish("Se retira", "No.")
        services.retire_public_notice(gone, self.admin)
        page = self.client.get(public_url(self.retiro))
        self.assertContains(page, "Se queda")
        self.assertNotContains(page, "Se retira")
        services.retire_public_notice(keep, self.admin)
        self.assertNotContains(self.client.get(public_url(self.retiro)), 'id="avisos"')

    def test_an_edited_notice_says_so(self):
        """Defect: a notice that changed after people read it, with nothing telling them."""
        notice = self.publish(at=timezone.now() - timedelta(hours=3))
        same = services.save_public_notice(self.retiro, self.admin, notice, title=TITLE, body=BODY)
        self.assertIsNone(same.edited_at)  # saving without a change is not an edit
        self.assertNotContains(self.client.get(public_url(self.retiro)), "Editado")
        services.save_public_notice(self.retiro, self.admin, notice, title=TITLE, body="El camión sale a las 6:30.")
        notice.refresh_from_db()
        self.assertIsNotNone(notice.edited_at)
        page = self.client.get(public_url(self.retiro))
        self.assertContains(page, "El camión sale a las 6:30.")
        self.assertContains(page, ". Editado el ", count=1)

    def test_only_the_latest_twenty(self):
        now = timezone.now()
        EventNotice.objects.bulk_create([
            EventNotice(event=self.retiro, title=f"Aviso número {n:02d}.", body="x", published_at=now - timedelta(minutes=n))
            for n in range(23)
        ])
        text = self.client.get(public_url(self.retiro)).content.decode()
        self.assertEqual(text.count('<h3 class="row__title">Aviso número'), 20)
        self.assertIn("Aviso número 00.", text)
        self.assertIn("Aviso número 19.", text)
        self.assertNotIn("Aviso número 20.", text)

    def test_notices_of_another_event_or_church_never_show(self):
        """Defect: a query that forgets the event."""
        self.publish("Aviso del retiro", "De la iglesia A.")
        self.publish("Aviso de la vigilia", "De la iglesia B.", event=self.retiro_b, actor=self.admin_b)
        self.publish("Aviso de la conferencia", "De otro evento de A.", event=self.hidden)
        here = self.client.get(public_url(self.retiro))
        self.assertContains(here, "Aviso del retiro")
        self.assertNotContains(here, "Aviso de la vigilia")
        self.assertNotContains(here, "Aviso de la conferencia")
        there = self.client.get(public_url(self.retiro_b))
        self.assertContains(there, "Aviso de la vigilia")
        self.assertNotContains(there, "Aviso del retiro")

    def test_a_notice_for_the_members_never_reaches_the_public_page(self):
        """Defect: the public page reading ``notices.Announcement``."""
        Announcement.objects.create(church=self.church, title="Solo para la iglesia", body="Aviso interno reservado.",
                                    publish_on=timezone.localdate(), created_by=self.admin)
        self.publish()
        page = self.client.get(public_url(self.retiro))
        self.assertNotContains(page, "Solo para la iglesia")
        self.assertNotContains(page, "Aviso interno reservado.")

    def test_the_page_asks_no_table_of_people_and_only_reads(self):
        self.publish()
        with CaptureQueriesContext(connection) as queries:
            page = self.client.get(public_url(self.retiro))
        self.assertContains(page, TITLE)
        sql = " ".join(query["sql"] for query in queries.captured_queries)
        self.assertIn("agenda_eventnotice", sql)
        for table in ("agenda_eventregistration", "agenda_guardianconsent", "people_person", "events_eventparticipant",
                      "auth_user", "care_followup", "notices_announcement", "django_session"):
            self.assertNotIn(table, sql)
        for query in queries.captured_queries:
            self.assertTrue(query["sql"].lstrip().upper().startswith("SELECT"), query["sql"])
        self.assertLessEqual(len(queries), 7)
        self.assertEqual(dict(page.cookies), {})

    def test_who_is_signed_in_reads_the_same_bytes(self):
        self.publish()
        anonymous = self.client.get(public_url(self.retiro)).content
        for user in (self.member_user, self.admin, self.admin_b):
            self.client.force_login(user)
            self.assertEqual(self.client.get(public_url(self.retiro)).content, anonymous, user.email)

    def test_when_the_page_is_not_public_the_notices_are_unreachable_and_the_404_is_the_same(self):
        self.publish("Aviso que nadie de fuera lee", "Texto reservado del aviso.")

        def gone(why):
            response = self.client.get(public_url(self.retiro))
            missing = self.client.get(reverse("evento:detail", args=["no-existe"]))
            self.assertEqual((response.status_code, missing.status_code), (404, 404), why)
            self.assertEqual(response.content, missing.content, why)
            self.assertNotIn(b"reservado", response.content, why)

        self.set_page(public=False)
        gone("not public")
        self.set_page(public=True, published=False)
        gone("not published")
        self.set_page(published=True)
        self.assertContains(self.client.get(public_url(self.retiro)), "Aviso que nadie de fuera lee")
        Event.objects.filter(pk=self.retiro.pk).update(status=Event.Status.CLOSED)
        gone("closed")
        Event.objects.filter(pk=self.retiro.pk).update(status=Event.Status.ACTIVE)
        self.move(self.retiro, -5, -3)
        gone("over")
        self.move(self.retiro, 10, 12)
        flags.set_enabled(self.church, "community", False)
        gone("module off")
        flags.set_enabled(self.church, "community", True)
        self.assertEqual(self.client.get(public_url(self.retiro)).status_code, 200)
        Church.objects.filter(pk=self.church.pk).update(active=False)
        gone("church not active")

    def test_the_members_read_them_on_the_event_in_emmaus(self):
        self.client.force_login(self.member_user)
        self.assertNotContains(self.client.get(self.url()), 'id="avisos"')
        self.publish()
        gone = self.publish("Retirado", "No.")
        services.retire_public_notice(gone, self.admin)
        self.publish("Aviso de la vigilia", "De B.", event=self.retiro_b, actor=self.admin_b)
        detail = self.client.get(self.url())
        self.assertContains(detail, '<h2 id="avisos" class="heading">Avisos</h2>', count=1)
        self.assertContains(detail, TITLE)
        self.assertContains(detail, "Lleguen media hora antes.<br>Lleven chamarra.")
        self.assertContains(detail, "<h1", count=1)
        for word in ("Retirado", "Aviso de la vigilia", self.admin.last_name, "Editar"):
            self.assertNotContains(detail, word)


class AdministrationTests(NoticesFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def share_text(self, html: str) -> str:
        start = html.index('href="https://wa.me/?text=')
        href = html[start + 6:html.index('"', start + 6)].replace("&amp;", "&")
        parts = urlsplit(href)
        self.assertEqual((parts.scheme, parts.netloc, parts.path), ("https", "wa.me", "/"))
        return parse_qs(parts.query)["text"][0]

    def test_the_administration_publishes_edits_and_retires(self):
        page = self.client.get(self.url("page_edit"))
        self.assertContains(page, self.url("public_notices"))
        self.assertContains(page, "Todavía no tiene avisos.")
        empty = self.client.get(self.url("public_notices"))
        self.assertContains(empty, "<h1", count=1)
        self.assertContains(empty, "Publicar el aviso", count=1)
        self.assertContains(empty, "Este evento todavía no tiene avisos públicos.")
        # What never to write, said on the form in plain words.
        self.assertContains(empty, "Cualquiera con el enlace lo puede leer, sin cuenta. No escribas nombres ni datos personales "
                                   "de nadie, sobre todo de menores de edad.")
        self.assertNotContains(empty, "wa.me")  # nothing to tell anybody about yet
        response = self.client.post(self.url("public_notices"), {"title": f"  {TITLE} ", "body": BODY}, follow=True)
        self.assertContains(response, "Publicaste el aviso.")
        notice = EventNotice.objects.get()
        self.assertEqual((notice.event, notice.title, notice.body.replace("\r", ""), notice.created_by), (self.retiro, TITLE, BODY, self.admin))
        self.assertIsNotNone(notice.published_at)
        self.assertIsNone(notice.edited_at)
        self.assertContains(self.client.get(self.url("page_edit")), "1 aviso.")
        self.assertContains(self.client.get(public_url(self.retiro)), TITLE)

        edit = self.client.get(self.url("public_notice_edit", notice.pk))
        self.assertContains(edit, "Guardar cambios", count=1)
        self.assertContains(edit, "No escribas nombres ni datos personales")
        self.client.post(self.url("public_notice_edit", notice.pk), {"title": TITLE, "body": "Sale a las 6:30."})
        notice.refresh_from_db()
        self.assertEqual(notice.body, "Sale a las 6:30.")
        self.assertIsNotNone(notice.edited_at)
        self.assertContains(self.client.get(self.url("public_notices")), ". Editado el ")

        confirm = self.client.get(self.url("public_notice_retire", notice.pk))
        self.assertContains(confirm, "¿Quieres retirar este aviso?")
        self.assertContains(confirm, "sigue viendo su texto en esta sección hasta que lo borres para siempre")
        self.assertEqual(self.client.get(self.url("public_notice_delete", notice.pk)).status_code, 404)  # not retired yet
        self.assertEqual(self.client.post(self.url("public_notice_delete", notice.pk)).status_code, 404)
        self.assertFalse(EventNotice.objects.get(pk=notice.pk).retired)  # asking is not retiring
        response = self.client.post(self.url("public_notice_retire", notice.pk), follow=True)
        self.assertContains(response, "Retiraste el aviso.")
        self.assertTrue(EventNotice.objects.get(pk=notice.pk).retired)
        listed = self.client.get(self.url("public_notices"))
        self.assertContains(listed, "Retirado el ")
        self.assertContains(listed, "la administración de la iglesia sigue viendo su texto aquí hasta que lo borres")
        self.assertContains(listed, self.url("public_notice_delete", notice.pk))
        self.assertNotContains(listed, f'href="{self.url("public_notice_edit", notice.pk)}"')
        self.assertNotContains(self.client.get(public_url(self.retiro)), TITLE)
        # A retired notice is not edited or retired again: it is over.
        for name in ("public_notice_edit", "public_notice_retire"):
            self.assertEqual(self.client.get(self.url(name, notice.pk)).status_code, 404)
            self.assertEqual(self.client.post(self.url(name, notice.pk), {"title": "X", "body": "Y"}).status_code, 404)
        self.assertEqual(EventNotice.objects.get(pk=notice.pk).body, "Sale a las 6:30.")
        with self.assertRaises(services.AgendaError):
            services.save_public_notice(self.retiro, self.admin, EventNotice.objects.get(pk=notice.pk), title="X", body="Y")

    def test_a_retired_notice_is_deleted_for_good_and_its_text_is_gone(self):
        """Defect: what was said in public kept for ever with no way to remove it; or a notice in force deleted in one step."""
        notice = self.publish("Título reservado", "Cuerpo reservado del aviso.")
        other = self.publish("Otro aviso", "Se queda.")
        self.assertEqual(self.client.post(self.url("public_notice_delete", notice.pk)).status_code, 404)
        with self.assertRaises(services.AgendaError):
            services.delete_public_notice(notice, self.admin)
        self.assertTrue(EventNotice.objects.filter(pk=notice.pk).exists())
        services.retire_public_notice(notice, self.admin)
        confirm = self.client.get(self.url("public_notice_delete", notice.pk))
        self.assertContains(confirm, "¿Quieres borrar este aviso para siempre?")
        self.assertContains(confirm, "<h1", count=1)
        self.assertTrue(EventNotice.objects.filter(pk=notice.pk).exists())  # asking is not deleting
        response = self.client.post(self.url("public_notice_delete", notice.pk), follow=True)
        self.assertContains(response, "Borraste el aviso para siempre.")
        self.assertFalse(EventNotice.objects.filter(pk=notice.pk).exists())
        self.assertEqual(list(EventNotice.objects.values_list("pk", flat=True)), [other.pk])
        self.assertNotContains(response, "reservado")
        self.assertEqual(self.client.post(self.url("public_notice_delete", notice.pk)).status_code, 404)  # twice
        audit = ChurchAuditEvent.objects.get(action="evento_aviso_publico_borrado")
        self.assertEqual((audit.object_type, audit.object_id, audit.metadata), ("EventNotice", str(notice.pk), {"event_id": self.retiro.pk}))
        self.assertEqual(audit.actor, self.admin)
        for row in ChurchAuditEvent.objects.filter(action__startswith="evento_aviso_publico"):
            for word in ("reservado", "Título", "Cuerpo"):
                self.assertNotIn(word, row.summary)
                self.assertNotIn(word, str(row.metadata))

    def test_a_stale_copy_never_brings_a_retired_notice_back(self):
        """Defect: an edit that began before another administrator retired the notice writing ``retired_at=None`` back."""
        notice = self.publish()
        stale = EventNotice.objects.get(pk=notice.pk)
        self.assertContains(self.client.get(self.url("public_notice_edit", notice.pk)), "Guardar cambios")  # the form is open
        services.retire_public_notice(EventNotice.objects.get(pk=notice.pk), self.admin)
        with self.assertRaises(services.AgendaError) as refused:
            services.save_public_notice(self.retiro, self.admin, stale, title="Vuelve", body="No debe volver.")
        self.assertEqual(refused.exception.code, "retired")
        self.assertEqual(self.client.post(self.url("public_notice_edit", notice.pk), {"title": "Vuelve", "body": "No."}).status_code, 404)
        services.retire_public_notice(stale, self.admin)  # retiring it again from an old copy changes nothing
        fresh = EventNotice.objects.get(pk=notice.pk)
        self.assertTrue(fresh.retired)
        self.assertEqual((fresh.title, fresh.body, fresh.edited_at), (TITLE, BODY, None))
        self.assertNotContains(self.client.get(public_url(self.retiro)), TITLE)
        self.assertEqual(ChurchAuditEvent.objects.filter(action="evento_aviso_publico_retirado").count(), 1)
        self.assertFalse(ChurchAuditEvent.objects.filter(action="evento_aviso_publico_modificado").exists())
        # An edit from an old copy of a notice in force writes only the text: nothing else the copy holds.
        live = self.publish("En vigor", "Uno.")
        old = EventNotice.objects.get(pk=live.pk)
        moved = timezone.now() - timedelta(days=2)
        EventNotice.objects.filter(pk=live.pk).update(published_at=moved)
        services.save_public_notice(self.retiro, self.admin, old, title="En vigor", body="Dos.")
        live.refresh_from_db()
        self.assertEqual((live.body, live.published_at), ("Dos.", moved))

    def test_the_audit_says_which_notice_and_never_its_text(self):
        """Defect: what somebody wrote in a notice copied into the log, where it outlives the notice."""
        with self.assertLogs(level="DEBUG") as logs:
            self.client.post(self.url("public_notices"), {"title": "Título reservado", "body": "Cuerpo reservado del aviso."})
            notice = EventNotice.objects.get()
            self.client.post(self.url("public_notice_edit", notice.pk), {"title": "Título cambiado", "body": "Cuerpo cambiado del aviso."})
            self.client.post(self.url("public_notice_edit", notice.pk), {"title": "Título cambiado", "body": "Cuerpo cambiado del aviso."})
            self.client.post(self.url("public_notice_retire", notice.pk))
            logging.getLogger("agenda").debug("fin")  # assertLogs needs one line
        audits = list(ChurchAuditEvent.objects.filter(action__startswith="evento_aviso_publico").order_by("id"))
        self.assertEqual([audit.action for audit in audits], [
            "evento_aviso_publico_publicado", "evento_aviso_publico_modificado", "evento_aviso_publico_retirado"])
        for audit in audits:
            self.assertEqual(audit.metadata, {"event_id": self.retiro.pk})
            self.assertEqual((audit.object_type, audit.object_id), ("EventNotice", str(notice.pk)))
            self.assertEqual(audit.church, self.church)
            self.assertEqual(audit.actor, self.admin)
            for word in ("reservado", "cambiado", "Título", "Cuerpo"):
                self.assertNotIn(word, audit.summary)
        for line in logs.output:
            for word in ("reservado", "cambiado"):
                self.assertNotIn(word, line)

    def test_empty_or_too_long_notices_are_refused(self):
        response = self.client.post(self.url("public_notices"), {"title": " ", "body": ""})
        self.assertContains(response, "Escribe el título del aviso.")
        self.assertContains(response, "Escribe el aviso.")
        self.assertContains(self.client.post(self.url("public_notices"), {"title": "T", "body": "x" * 1201}),
                            "Escribe como máximo 1200 caracteres (ahora hay 1201).")
        self.assertContains(self.client.post(self.url("public_notices"), {"title": "x" * 121, "body": "B"}),
                            "Escribe como máximo 120 caracteres (ahora hay 121).")
        self.assertFalse(EventNotice.objects.exists())
        with self.assertRaises(services.AgendaError):
            services.save_public_notice(self.retiro, self.admin, title="Título", body="   ")

    def test_a_notice_of_invisible_characters_is_refused(self):
        """Defect: a notice that shows as an empty row because it holds only zero-width spaces."""
        invisible = "\u200b\u200b\ufeff\u2060"
        response = self.client.post(self.url("public_notices"), {"title": invisible, "body": "Texto."})
        self.assertContains(response, "Escribe el título del aviso.")
        response = self.client.post(self.url("public_notices"), {"title": "Título", "body": f"{invisible}\n\u00a0{invisible}"})
        self.assertContains(response, "Escribe el aviso.")
        self.assertFalse(EventNotice.objects.exists())
        for title, body in ((invisible, "Texto."), ("Título", invisible)):
            with self.assertRaises(services.AgendaError):
                services.save_public_notice(self.retiro, self.admin, title=title, body=body)
        notice = self.publish()
        response = self.client.post(self.url("public_notice_edit", notice.pk), {"title": TITLE, "body": invisible})
        self.assertContains(response, "Escribe el aviso.")
        self.assertEqual(EventNotice.objects.get(pk=notice.pk).body, BODY)
        # Invisible characters inside a real text are kept as typed: only an empty notice is refused.
        self.assertEqual(self.client.post(self.url("public_notices"), {"title": "Hora", "body": "A las\u200b 7."}).status_code, 302)

    def test_the_whatsapp_message_carries_the_church_and_the_link_and_nothing_else(self):
        """Defect: the text of a notice, or the event's name, travelling through groups nobody controls."""
        self.publish("Título reservado", "Cuerpo reservado del aviso.")
        html = self.client.get(self.url("public_notices")).content.decode()
        link = "http://testserver" + public_url(self.retiro) + "#avisos"
        self.assertIn(f'href="{link}"', html)
        self.assertIn('Avisar por WhatsApp que hay un aviso nuevo<span class="visually-hidden"> (se abre en otra pestaña)</span></a>', html)
        text = self.share_text(html)
        self.assertEqual(text, f"{self.church.name}: hay un aviso nuevo de un evento. No necesitas cuenta para leerlo: {link}")
        for word in ("reservado", "Retiro de jóvenes", "Título", self.admin.first_name):
            self.assertNotIn(word, text)
        self.assertEqual(public.notices_share_url("Iglesia X", "https://e.example/evento/a/#avisos"),
                         "https://wa.me/?text=Iglesia%20X%3A%20hay%20un%20aviso%20nuevo%20de%20un%20evento.%20No%20necesitas%20"
                         "cuenta%20para%20leerlo%3A%20https%3A%2F%2Fe.example%2Fevento%2Fa%2F%23avisos")

    def test_nothing_is_offered_to_share_while_no_notice_is_in_force(self):
        """Defect: a link to «#avisos» and a "there is news" message when the only notices are retired ones."""
        only = self.publish()
        services.retire_public_notice(only, self.admin)
        html = self.client.get(self.url("public_notices")).content.decode()
        self.assertIn("Retirado el ", html)
        self.assertNotIn("wa.me", html)
        self.assertNotIn("Ver los avisos en la página pública", html)
        self.assertNotIn("#avisos", html)
        self.publish("Otro", "En vigor.")
        html = self.client.get(self.url("public_notices")).content.decode()
        self.assertIn("wa.me", html)
        self.assertIn("Ver los avisos en la página pública", html)

    def test_the_screen_says_that_emmaus_sends_nothing_and_what_readers_see(self):
        """Defect: the administration believing that publishing a notice tells anybody."""
        self.publish()
        html = self.client.get(self.url("public_notices")).content.decode()
        self.assertIn("Emmaus no le envía nada a nadie: el aviso solo queda en la página del evento, así que comparte el enlace", html)
        self.assertIn("nadie recibe un mensaje: después comparte el enlace", html)
        self.assertIn("se leen los 20 avisos más recientes que sigan publicados", html)
        self.assertNotIn("funcionando", html)
        self.set_page(public=False)
        html = self.client.get(self.url("public_notices")).content.decode()
        self.assertIn("La página pública de este evento no está disponible ahora.", html)
        self.assertIn("Las personas de la iglesia sí los leen ya, en la página del evento en Mi iglesia.", html)
        self.assertIn("Emmaus no le envía nada a nadie.", html)
        self.assertNotIn("funcionando", html)
        self.set_page(published=False)
        html = self.client.get(self.url("public_notices")).content.decode()
        self.assertIn("Las personas de la iglesia tampoco los leen todavía, porque el evento no está publicado para la iglesia.", html)
        self.assertNotIn("sí los leen ya", html)

    def test_while_the_page_is_not_public_the_screen_says_so_and_offers_no_link(self):
        """Defect: the administration believing parents can read a notice nobody outside can open."""
        self.publish()
        for fields, why in (({"public": False}, "switched off"), ({"published": False}, "not published")):
            self.set_page(public=True, published=True)
            self.set_page(**fields)
            html = self.client.get(self.url("public_notices")).content.decode()
            self.assertIn("nadie de fuera los puede leer hasta que la página sea pública", html, why)
            self.assertNotIn("wa.me", html, why)
            self.assertNotIn(public_url(self.retiro), html, why)
            self.assertIn(TITLE, html, why)  # saved, and still managed here
        # Saved all the same: it shows as soon as the page is public again.
        self.assertEqual(self.client.post(self.url("public_notices"), {"title": "Otro aviso", "body": "Guardado."}).status_code, 302)
        self.set_page(public=True, published=True)
        self.assertContains(self.client.get(public_url(self.retiro)), "Otro aviso")
        self.assertNotIn("nadie de fuera", self.client.get(self.url("public_notices")).content.decode())

    def test_a_camp_has_them_exactly_as_a_simple_event(self):
        camp = Event.objects.create(church=self.church, name="Campamento de verano", slug="campamento-verano",
                                    starts_on=self.today + timedelta(days=30), ends_on=self.today + timedelta(days=33))
        self.assertTrue(camp.camp_tools)
        services.save_page(camp, self.admin, published=True, public=True)
        self.assertEqual(self.client.post(self.url("public_notices", event=camp), {"title": TITLE, "body": BODY}).status_code, 302)
        self.assertContains(self.client.get(public_url(camp)), TITLE)


class AccessTests(NoticesFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.notice = services.save_public_notice(cls.retiro, cls.admin, title=TITLE, body=BODY)
        cls.notice_b = services.save_public_notice(cls.retiro_b, cls.admin_b, title="Aviso de B", body="De B.")
        cls.notice_hidden = services.save_public_notice(cls.hidden, cls.admin, title="Aviso de la conferencia", body="Otro evento.")
        cls.retired = services.retire_public_notice(
            services.save_public_notice(cls.retiro, cls.admin, title="Aviso retirado", body="Ya no aplica."), cls.admin)
        cls.retired_hidden = services.retire_public_notice(
            services.save_public_notice(cls.hidden, cls.admin, title="Retirado de la conferencia", body="Otro evento."), cls.admin)
        # Roles of the event alone give nothing here: an administrator of this event without a church role, and
        # a coordinator of it who is a person of the church.
        cls.event_admin = make_user("admin-evento@prueba.mx", "Elena", "Del Evento")
        cls._person(cls.church, cls.event_admin)
        EventMembership.objects.create(event=cls.retiro, user=cls.event_admin, role=MembershipRole.ADMIN)
        EventMembership.objects.create(event=cls.retiro, user=cls.coord1, role=MembershipRole.COORDINATOR)

    BODY_POST = {"title": "Cambiado por quien no debe", "body": "Texto de quien no debe."}

    def paths(self, event=None, notice=None, retired=None):
        pk = (notice or self.notice).pk
        return [self.url("public_notices", event=event), self.url("public_notice_edit", pk, event=event),
                self.url("public_notice_retire", pk, event=event),
                self.url("public_notice_delete", (retired or self.retired).pk, event=event)]

    def unchanged(self):
        self.assertEqual(EventNotice.objects.count(), 5)
        for notice in (self.notice, self.notice_b, self.notice_hidden, self.retired, self.retired_hidden):
            fresh = EventNotice.objects.get(pk=notice.pk)
            self.assertEqual((fresh.title, fresh.body, fresh.retired_at, fresh.edited_at),
                             (notice.title, notice.body, notice.retired_at, None))

    def refused(self, paths, status):
        for path in paths:
            self.assertEqual(self.client.get(path).status_code, status, path)
            self.assertEqual(self.client.post(path, self.BODY_POST).status_code, status, path)
        self.unchanged()

    def test_the_admin_opens_every_page(self):
        self.client.force_login(self.admin)
        for path in self.paths():
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200, path)
            self.assertContains(page, "<h1", count=1)

    def test_a_member_or_a_guide_is_refused(self):
        for user in (self.member_user, self.leader_user):
            self.client.force_login(user)
            self.refused(self.paths(), 403)

    def test_a_role_of_the_event_alone_is_refused(self):
        """Defect: an event's own administrator or coordinator publishing for the church (invariant 2: an event
        role never grants a church role)."""
        for user in (self.event_admin, self.coord1):
            self.client.force_login(user)
            self.refused(self.paths(), 403)

    def test_every_post_needs_the_csrf_token(self):
        """Defect: another site making a signed-in administrator publish, change, retire or delete a notice."""
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        for path in self.paths():
            self.assertEqual(client.post(path, self.BODY_POST).status_code, 403, path)
        self.unchanged()
        # With the token of the form, the same request goes through.
        token = client.get(self.url("public_notices")).context["csrf_token"]
        response = client.post(self.url("public_notices"), {**self.BODY_POST, "csrfmiddlewaretoken": str(token)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EventNotice.objects.count(), 6)

    def test_the_admin_of_another_church_gets_a_404_by_guessed_address(self):
        self.client.force_login(self.admin_b)
        self.refused(self.paths(), 404)
        # Her own event's address with this church's notice id: the id alone opens nothing.
        self.refused(self.paths(event=self.retiro_b)[1:], 404)

    def test_a_notice_of_another_event_is_a_404(self):
        """The slug is of the admin's own event; the id belongs to another event of the church, or to another church."""
        self.client.force_login(self.admin)
        self.refused(self.paths(notice=self.notice_hidden, retired=self.retired_hidden)[1:]
                     + self.paths(notice=self.notice_b, retired=self.notice_b)[1:] + self.paths(event=self.hidden)[1:], 404)
        # The right event, the wrong state: a retired notice is not edited or retired, one in force is not deleted.
        self.refused([self.url("public_notice_edit", self.retired.pk), self.url("public_notice_retire", self.retired.pk),
                      self.url("public_notice_delete", self.notice.pk)], 404)

    def test_with_the_module_off_everything_is_a_404(self):
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.admin)
        self.refused(self.paths(), 404)
        self.assertEqual(self.client.get(public_url(self.retiro)).status_code, 404)

    def test_signed_out_goes_to_sign_in(self):
        self.client.logout()
        for path in self.paths():
            self.assertEqual(self.client.get(path).status_code, 302, path)
            self.assertEqual(self.client.post(path, self.BODY_POST).status_code, 302, path)
        self.unchanged()

    def test_the_service_refuses_a_notice_of_another_event(self):
        with self.assertRaises(ValueError):
            services.save_public_notice(self.retiro, self.admin, self.notice_hidden, title="X", body="Y")
        self.unchanged()
        self.assertFalse(EventPage.objects.filter(event=self.no_page).exists())
