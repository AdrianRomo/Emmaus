"""The independent review of wave 12 (sign-up without an account), on synthetic data. Each test is one of the
reviewer's reproductions and fails on the code it reviewed: a script that drops its cookie, a per-event cap that
answered as if nothing happened, one church's backlog holding back another's e-mails, «Agregar a personas» for a
visitor who was promised that their data ends with the event, links that ignored a church or a module switched
off, a typed name in the church's log, and free text left inside a request."""

import json
import secrets
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from agenda import services, subscriptions, visitors
from agenda.models import (
    EventPage,
    EventQuestion,
    EventRegistration,
    GuardianConsent,
    MailOutcome,
    RegistrationAnswer,
    VisitorRequest,
)
from agenda.tests.test_visitors import ADDRESS, BASE, OTHER, VisitorFixture, form_url
from agenda.visitor_views import KEEP, KEEP_NAME
from events.models import Event
from people import flags
from people.models import ChurchAuditEvent, Person

FULL = "Por ahora no podemos recibir más inscripciones para este evento."
LATER = "Intenta más tarde o pregunta en la iglesia."
DEAD = "Este enlace ya no sirve"


class EventBoundTests(VisitorFixture):
    def test_the_bounds_per_event(self):
        self.assertEqual(visitors.PENDING_PER_EVENT, 200)
        self.assertEqual(visitors.NEW_PER_EVENT_PER_HOUR, 60)

    def test_a_script_that_drops_its_cookie_is_slowed_per_event_and_told_so(self):
        """Defect: the throttle was per browser session; a client without the cookie was never slowed."""
        with mock.patch.object(visitors, "NEW_PER_EVENT_PER_HOUR", 3):
            for number in range(3):
                self.assertEqual(self.ask(f"bot{number}@demo{number}.invalid", client=Client()).status_code, 302)
            new, known = self.ask("bot9@demo9.invalid", client=Client()), self.ask("bot0@demo0.invalid", client=Client())
            for page in (new, known):
                self.assertContains(page, FULL)
                self.assertContains(page, LATER)
                self.assertContains(page, "No guardamos nada de lo que escribiste.")
                self.assertNotContains(page, "@demo")
            self.assertEqual(new.content, known.content)  # about the event: the same page for every address
            self.assertEqual(VisitorRequest.objects.filter(event=self.retiro).count(), 3)
            self.assertContains(self.client.get(form_url(self.retiro)), FULL)  # nobody types for nothing
            self.assertEqual(self.ask("otra@demo.invalid", event=self.retiro_b, client=Client()).status_code, 302)  # another event
            with self.later(hours=1, minutes=1):
                self.assertEqual(self.ask("bot9@demo9.invalid", client=Client()).status_code, 302)
        self.assertEqual(VisitorRequest.objects.filter(event=self.retiro).count(), 4)

    def test_at_the_pending_cap_the_page_says_so_and_stores_nothing(self):
        """Defect: at the cap the request was dropped while the page promised an e-mail."""
        with mock.patch.object(visitors, "PENDING_PER_EVENT", 2):
            self.requested()
            self.requested(OTHER)
            page = self.client.get(form_url(self.retiro))
            self.assertContains(page, FULL)
            self.assertNotContains(page, 'name="email"')
            answers = [self.ask(address, client=Client()) for address in ("nueva@demo.invalid", ADDRESS)]
            self.assertEqual(answers[0].content, answers[1].content)
            self.assertContains(answers[0], FULL)
            self.assertNotContains(answers[0], "te enviaremos un correo")
        self.assertEqual(VisitorRequest.objects.filter(event=self.retiro).count(), 2)
        self.assertEqual(visitors.request(self.retiro, first_name="Ana", email="ana@demo.invalid"), None)  # under the cap again

    def test_a_request_that_lapsed_does_not_keep_the_event_at_its_cap(self):
        with mock.patch.object(visitors, "PENDING_PER_EVENT", 1):
            self.requested()
            self.assertFalse(visitors.taking_requests(self.retiro))
            with self.later(days=visitors.QUEUE_DAYS, hours=1):
                self.assertTrue(visitors.taking_requests(self.retiro))


class FairSendingTests(VisitorFixture):
    def make(self, event, address, when, **fields) -> VisitorRequest:
        return VisitorRequest.objects.create(
            event=event, first_name="X", email=address, email_hash=subscriptions.address_hash(address),
            limit_hash=subscriptions.limit_hash(address), key=secrets.token_urlsafe(32), created_at=when, **fields)

    def sent_to(self) -> list[str]:
        return [address for message in mail.outbox for address in message.to]

    def test_one_churchs_backlog_never_delays_another(self):
        """Defect: the pass read the oldest requests server-wide; junk of church A filled the window for days."""
        now = timezone.now()
        for number in range(6):
            self.make(self.retiro, f"junk{number}@junk{number}.invalid", now - timedelta(minutes=30 - number))
        legit = self.make(self.retiro_b, "real.person@demo.invalid", now)
        with mock.patch.object(visitors, "PENDING_PER_EVENT", 2), mock.patch.object(visitors, "CONFIRMATIONS_PER_CHURCH_PER_DAY", 1):
            done = visitors.send_due(BASE)
        legit.refresh_from_db()
        self.assertEqual(legit.confirm_attempts, 1)
        self.assertEqual(sorted(self.sent_to()), ["junk0@junk0.invalid", "real.person@demo.invalid"])  # one per church, oldest first
        self.assertEqual((done["confirmations"], done["left"]), (2, 5))

    def test_a_church_at_its_cap_takes_no_turn_and_holds_no_place(self):
        now = timezone.now()
        self.make(self.retiro, "ya@junk.invalid", now - timedelta(hours=3), confirm_attempts=1,
                  confirm_last_attempt_at=now - timedelta(hours=2), confirm_outcome=MailOutcome.SENT)
        for number in range(4):
            self.make(self.retiro, f"junk{number}@junk{number}.invalid", now - timedelta(minutes=30 - number))
        self.make(self.retiro_b, "real.person@demo.invalid", now)
        with mock.patch.object(visitors, "PENDING_PER_EVENT", 2), mock.patch.object(visitors, "CONFIRMATIONS_PER_CHURCH_PER_DAY", 1), \
                mock.patch.object(visitors, "CONFIRMATIONS_PER_PASS", 1):
            done = visitors.send_due(BASE)
        self.assertEqual(self.sent_to(), ["real.person@demo.invalid"])
        self.assertEqual(done["left"], 4)

    def test_inside_a_church_one_events_backlog_never_delays_another(self):
        services.save_page(self.hidden, self.admin, published=True, public=True, visitor_signup=True)
        now = timezone.now()
        for number in range(6):
            self.make(self.retiro, f"junk{number}@junk{number}.invalid", now - timedelta(minutes=30 - number))
        self.make(self.hidden, "real.person@demo.invalid", now)
        with mock.patch.object(visitors, "PENDING_PER_EVENT", 2), mock.patch.object(visitors, "CONFIRMATIONS_PER_PASS", 2):
            visitors.send_due(BASE)
        self.assertEqual(sorted(self.sent_to()), ["junk0@junk0.invalid", "real.person@demo.invalid"])

    def test_a_mail_domain_at_its_pace_does_not_hold_back_the_rest_of_its_event(self):
        now = timezone.now()
        for number in range(4):
            self.make(self.retiro, f"junk{number}@junk.invalid", now - timedelta(minutes=30 - number))
        self.make(self.retiro, "real.person@demo.invalid", now)
        with mock.patch.object(visitors, "DOMAIN_CONFIRMATIONS_PER_HOUR", 1):
            visitors.send_due(BASE)
        self.assertEqual(sorted(self.sent_to()), ["junk0@junk.invalid", "real.person@demo.invalid"])


class ContactConsentTests(VisitorFixture):
    def test_the_form_offers_one_optional_box_unticked_and_says_what_is_true_either_way(self):
        page = self.client.get(form_url(self.retiro))
        self.assertContains(page, 'name="keep"', count=1)
        self.assertNotIn("checked", page.content.decode().split('name="keep"')[1].split(">")[0])
        self.assertContains(page, KEEP_NAME)  # no phone is asked: the box speaks of the name only
        self.assertContains(page, "Si no lo marcas, nadie puede agregarte y no se queda nada.")
        self.set_page(visitor_phone=True)
        page = self.client.get(form_url(self.retiro))
        self.assertContains(page, KEEP)
        self.assertEqual(KEEP, "La iglesia puede guardar mi nombre y mi teléfono para invitarme a otras actividades.")
        self.assertContains(page, "Entonces se quedan tu nombre, tu teléfono y tu inscripción a este evento")
        self.assertEqual(self.ask(phone="55 5550 0101").status_code, 302)  # unticked is fine
        self.assertFalse(self.request_row().contact_consent)
        self.assertEqual(self.ask(OTHER, phone="55 5550 0102", keep="on").status_code, 302)
        self.assertTrue(self.request_row(OTHER).contact_consent)

    def test_the_answer_and_when_it_was_given_travel_to_the_registration(self):
        row = self.requested(contact_consent=True)
        _, yes = visitors.confirm(row)
        self.assertEqual((yes.visitor_contact_consent, yes.visitor_contact_consent_at), (True, row.created_at))
        no = self.signed_up(OTHER)
        self.assertFalse(no.visitor_contact_consent)
        self.assertIsNotNone(no.visitor_contact_consent_at)  # asked, and the answer was no

    def test_agregar_a_personas_only_with_permission_and_the_service_refuses_otherwise(self):
        """Defect: a permanent ``Person`` (and follow-ups) made from a visitor who was told everything ends with the event."""
        self.set_page(visitor_phone=True)
        without = self.signed_up(phone="55 1234 9876")
        allowed = self.signed_up(OTHER, first_name="Otra", last_name="Persona Demo", phone="55 4321 0000", contact_consent=True)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("agenda:registrations", args=[self.retiro.slug]))
        self.assertContains(page, "Agregar a personas de la iglesia", count=1)
        self.assertContains(page, reverse("agenda:guest_promote", args=[self.retiro.slug, allowed.pk]))
        self.assertNotContains(page, reverse("agenda:guest_promote", args=[self.retiro.slug, without.pk]))
        self.assertContains(page, "Dio permiso de guardar su nombre y su teléfono para invitarle a otras actividades.")
        self.assertContains(page, f"No dio permiso de guardar sus datos: se borran {self.retiro.retention_days} días después del evento.")
        url = reverse("agenda:guest_promote", args=[self.retiro.slug, without.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 404)
        for existing in (None, self.member):
            with self.assertRaises(services.AgendaError) as refused:
                services.promote_guest(without, self.admin, existing=existing)
            self.assertEqual(refused.exception.code, "no_contact_consent")
        self.assertFalse(Person.objects.filter(phone="55 1234 9876").exists())
        self.assertIsNone(EventRegistration.objects.get(pk=without.pk).person_id)
        person = services.promote_guest(allowed, self.admin)
        self.assertEqual((person.full_name, person.phone, person.email), ("Otra Persona Demo", "55 4321 0000", ""))

    def test_a_guest_the_team_wrote_at_the_door_keeps_todays_way_in(self):
        self.move(self.retiro, 0, 2)
        walkup = services.register_walkup(self.retiro, self.admin, first_name="Llegó", last_name="A La Puerta", seen_adult=True)
        self.assertTrue(walkup.may_join_people)
        self.assertEqual(services.promote_guest(walkup, self.admin).full_name, "Llegó A La Puerta")

    def test_at_retention_nothing_stays_of_a_visitor_unless_they_allowed_it_and_were_added(self):
        self.set_page(visitor_phone=True)
        question = EventQuestion.objects.create(event=self.retiro, text="¿Te quedas a comer?", kind="si_no")
        plain = self.signed_up(phone="55 1234 9876", answers={str(question.pk): "Sí"})
        kept = self.signed_up(OTHER, first_name="Otra", last_name="Persona Demo", phone="55 4321 0000", contact_consent=True)
        allowed_not_added = self.signed_up("tercera@demo.invalid", first_name="Tercera", phone="55 3030 4040", contact_consent=True)
        person = services.promote_guest(kept, self.admin)
        Event.objects.filter(pk=self.retiro.pk).update(
            starts_on=self.today - timedelta(days=self.retiro.retention_days + 3),
            ends_on=self.today - timedelta(days=self.retiro.retention_days + 1))
        with self.later(days=visitors.QUEUE_DAYS, hours=1):  # the requests are long over by then too
            done = visitors.delete_due()
        self.assertEqual((done["requests"], done["registrations"], done["contacts"]), (3, 2, 1))
        for gone in (plain, allowed_not_added):
            self.assertFalse(EventRegistration.objects.filter(pk=gone.pk).exists())
        self.assertFalse(RegistrationAnswer.objects.exists())
        self.assertFalse(Person.objects.filter(phone__in=["55 1234 9876", "55 3030 4040"]).exists())
        self.assertFalse(VisitorRequest.objects.filter(event=self.retiro).exists())
        for word in ("Valentina", "Tercera", "9876", ADDRESS, "tercera@"):
            self.assertFalse(ChurchAuditEvent.objects.filter(summary__icontains=word).exists(), word)
        kept.refresh_from_db()
        self.assertEqual((kept.person_id, kept.guest_email, kept.visitor_key), (person.pk, "", None))
        self.assertTrue(kept.visitor_contact_consent)
        person.refresh_from_db()
        self.assertEqual((person.full_name, person.phone), ("Otra Persona Demo", "55 4321 0000"))


class ClosedLinkTests(VisitorFixture):
    """Defect: the confirmation and private links ignored the module, the church and the event's state."""

    def states(self):
        """Each state that closes the links, as (name, close, reopen)."""
        church, event = self.church, self.retiro

        def church_active(value):
            church.active = value
            church.save(update_fields=["active"])

        return [
            ("módulo apagado", lambda: flags.set_enabled(church, "community", False), lambda: flags.set_enabled(church, "community", True)),
            ("iglesia inactiva", lambda: church_active(False), lambda: church_active(True)),
            ("evento archivado", lambda: Event.objects.filter(pk=event.pk).update(status=Event.Status.ARCHIVED),
             lambda: Event.objects.filter(pk=event.pk).update(status=Event.Status.ACTIVE)),
            ("sin publicar", lambda: self.set_page(published=False), lambda: self.set_page(published=True)),
            ("no pública", lambda: self.set_page(public=False), lambda: self.set_page(public=True)),
            ("opción apagada", lambda: self.set_page(visitor_signup=False), lambda: self.set_page(visitor_signup=True)),
        ]

    def test_the_confirmation_link_is_the_dead_link_and_signs_nobody_up(self):
        row = self.requested()
        url = self.confirm_url(row)
        for name, close, reopen in self.states():
            with self.subTest(name):
                close()
                try:
                    for method in (self.client.get, self.client.post):
                        answer = method(url)
                        self.assertContains(answer, DEAD, status_code=404)
                        self.assertNotContains(answer, self.retiro.name, status_code=404)
                    self.assertFalse(EventRegistration.objects.exists())
                finally:
                    reopen()
        flags.set_enabled(self.church, "community", False)
        self.assertEqual(visitors.confirm(row), ("sin_modulo", None))  # the service refuses too
        flags.set_enabled(self.church, "community", True)
        self.assertContains(self.client.post(url), "Listo, ya tienes tu lugar")  # everything open again: it works

    def test_the_private_link_still_cancels_and_shows_nothing_else(self):
        for number, (name, close, reopen) in enumerate(self.states()):
            with self.subTest(name):
                registration = self.signed_up(f"visita{number}@demo.invalid")
                path = visitors.manage_path(registration)
                close()
                try:
                    page = self.client.get(path)
                    self.assertContains(page, "Tu inscripción")
                    self.assertContains(page, "Por ahora esta página no muestra nada del evento.")
                    for word in (self.retiro.name, self.retiro.location, self.church.name, "Valentina"):
                        self.assertNotContains(page, word)
                    self.assertContains(page, "Cancelar mi inscripción")
                    self.assertContains(self.client.get(path + "?cancelar=1"), "Sí, cancelar mi inscripción")
                    done = self.client.post(path, {"accion": "cancelar"})
                    self.assertContains(done, "Cancelaste tu inscripción.")
                    self.assertNotContains(done, self.retiro.name)
                    self.assertEqual(EventRegistration.objects.get(pk=registration.pk).status, "cancelada")
                finally:
                    reopen()
        self.assertEqual(len(mail.outbox), 0)

    def test_on_the_waiting_list_the_private_link_still_lets_the_visitor_leave_and_never_accept(self):
        self.set_page(capacity=1, waiting_list=True)
        holder = services.register(self.retiro, self.member, self.member_user)
        waiting = self.signed_up()
        self.assertEqual(waiting.status, "en_espera")
        path = visitors.manage_path(waiting)
        flags.set_enabled(self.church, "community", False)
        page = self.client.get(path)
        self.assertContains(page, "Salir de la lista de espera")
        self.assertNotContains(page, "Aceptar mi lugar")
        services.cancel(holder, self.member_user)  # the place is offered to the visitor
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, "ofrecida")
        self.client.post(path, {"accion": "aceptar"})
        self.assertEqual(EventRegistration.objects.get(pk=waiting.pk).status, "ofrecida")  # not from a closed link
        self.assertContains(self.client.post(path, {"accion": "salir"}), "Saliste de la lista de espera.")
        self.assertEqual(EventRegistration.objects.get(pk=waiting.pk).status, "cancelada")


class AuditNameTests(VisitorFixture):
    """Defect: a self-signed visitor's typed name reached the church's log through the authorization, its
    withdrawal and «Agregar a personas»."""

    TYPED = ("zuleima", "tecleado", "7777")

    def visitor(self, number, **fields):
        return self.signed_up(f"zuleima{number}@demo.invalid", first_name="Zuleima",
                              last_name=f"Tecleado {number}", phone="55 7777 4321", **fields)

    def test_no_audit_path_that_touches_a_visitor_row_writes_what_the_visitor_typed(self):
        self.set_page(capacity=1, waiting_list=True, visitor_phone=True)
        first, second, third, fourth = (self.visitor(number) for number in range(1, 5))
        self.assertEqual([row.status for row in (first, second, third, fourth)], ["inscrita", "en_espera", "en_espera", "en_espera"])
        services.remove_from_waiting(fourth, self.admin)              # the administration takes one off the list
        services.cancel(first, None)                                  # a visitor cancels: the next one is offered the place
        services.accept_offer(EventRegistration.objects.get(pk=second.pk), None)
        services.cancel(EventRegistration.objects.get(pk=second.pk), None)
        self.assertEqual(EventRegistration.objects.get(pk=third.pk).status, "ofrecida")
        with self.later(hours=services.policy.waiting_offer_hours(self.church) + 1):
            services.advance(self.retiro)                             # the offer runs out
        fifth = self.visitor(5, contact_consent=True)
        sixth = self.visitor(6)
        services.leave_waiting(sixth, None)                           # a visitor leaves the list
        services.save_page(self.retiro, self.admin, requires_guardian_consent=True)  # resettled: waits for the age
        fifth = EventRegistration.objects.get(pk=fifth.pk)
        services.confirm_guest_age(fifth, self.admin, is_minor=True)
        services.record_consent(fifth, self.admin, guardian_name="Marta Tutora", relationship="madre", method="papel",
                                guardian_phone="55 9999 0000", allows_participation=True)
        services.revoke_consent(EventRegistration.objects.get(pk=fifth.pk), self.admin)
        services.promote_guest(EventRegistration.objects.get(pk=fifth.pk), self.admin)
        services.ask_to_accompany(EventRegistration.objects.get(pk=fifth.pk), self.admin)
        self.move(self.retiro, 0, 2)
        services.record_attendance(self.retiro, [], self.admin)
        rows = list(ChurchAuditEvent.objects.filter(church=self.church))
        actions = {row.action for row in rows}
        for action in ("evento_inscripcion_visita", "evento_lista_espera", "evento_lista_oferta", "evento_lista_vencida",
                       "evento_inscripcion_cancelada", "evento_visita_edad", "evento_autorizacion", "evento_autorizacion_retirada",
                       "persona_creada", "evento_visita_persona", "acompanar_creado", "evento_asistencia"):
            self.assertIn(action, actions)
        for row in rows:
            written = f"{row.summary} {json.dumps(row.metadata, ensure_ascii=False)}".casefold()
            for word in self.TYPED:
                self.assertNotIn(word, written, (row.action, row.summary))
        self.assertTrue(GuardianConsent.objects.filter(registration=fifth).exists())


class FreeTextTests(VisitorFixture):
    def test_turning_the_minors_rule_on_clears_free_text_waiting_inside_a_request(self):
        """Defect: ``drop_free_text`` left what a visitor typed in ``VisitorRequest.answers``."""
        typed = EventQuestion.objects.create(event=self.retiro, text="¿Algo que debamos saber?", kind="texto")
        yes = EventQuestion.objects.create(event=self.retiro, text="¿Te quedas a comer?", kind="si_no")
        row = self.requested(answers={str(typed.pk): "Algo que escribí yo", str(yes.pk): "Sí"})
        other = self.requested(OTHER, event=self.retiro_b, answers={"999": "De otro evento"})
        services.save_page(self.retiro, self.admin, requires_guardian_consent=True)
        row.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(row.answers, {str(yes.pk): "Sí"})
        self.assertEqual(other.answers, {"999": "De otro evento"})
        self.assertTrue(EventPage.objects.get(event=self.retiro).requires_guardian_consent)
