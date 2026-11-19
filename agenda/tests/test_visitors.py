"""Issue #93 (wave 12), on synthetic data: signing up for one event WITHOUT an account. Off by default, adults
only and never where the event protects minors, proof of contact before a place is held, no oracle at the form,
the same capacity, waiting list and ``standing`` as every other sign-up, a private link to cancel, and automatic
endings. Each test names the defect it would catch."""

from datetime import timedelta
from unittest import mock

from django.core import mail, signing
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from agenda import questions, services, subscriptions, visitors
from agenda.forms import EventPageForm
from agenda.models import (
    EventPage,
    EventQuestion,
    EventRegistration,
    RegistrationAnswer,
    SubscriptionSuppression,
    VisitorRequest,
)
from agenda.tests.test_public_notices import NoticesFixture, public_url
from core import scheduler
from events.models import Event
from people import flags
from people.models import ChurchAuditEvent, Person

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
BASE = "https://emmaus.prueba.mx"
ADDRESS = "visita.de.prueba@demo.invalid"
OTHER = "otra.visita@demo.invalid"
NAME = {"first_name": "Valentina", "last_name": "Visita Demo"}


def form_url(event) -> str:
    return reverse("evento:visitor_signup", args=[event.slug])


@override_settings(EMAIL_BACKEND=LOCMEM, EMMAUS_PUBLIC_BASE_URL=BASE, EMMAUS_SCHEDULED_COMMUNITY_JOBS=True)
class VisitorFixture(NoticesFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        services.save_page(cls.retiro, cls.admin, visitor_signup=True)
        services.save_page(cls.retiro_b, cls.admin_b, visitor_signup=True)

    def ask(self, address=ADDRESS, event=None, client=None, **extra):
        data = {**NAME, "email": address, "adult": "on", **extra}
        return (client or self.client).post(form_url(event or self.retiro), data)

    def request_row(self, address=ADDRESS, event=None) -> VisitorRequest:
        return VisitorRequest.objects.get(event=event or self.retiro, email_hash=subscriptions.address_hash(address))

    def requested(self, address=ADDRESS, event=None, **fields) -> VisitorRequest:
        visitors.request(event or self.retiro, **{**NAME, "email": address, **fields})
        return self.request_row(address, event)

    def confirm_url(self, row) -> str:
        return visitors.confirm_path(visitors.confirm_token(row))

    def signed_up(self, address=ADDRESS, event=None, **fields) -> EventRegistration:
        """A visitor who asked and pressed the button, as the two pages leave them."""
        state, registration = visitors.confirm(self.requested(address, event, **fields))
        self.assertEqual(state, "ok")
        return registration

    def later(self, **delta):
        return mock.patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(**delta))


class OfferTests(VisitorFixture):
    def test_off_by_default_and_the_public_page_is_as_it_was(self):
        """Defect: the option on for an event nobody decided it for."""
        self.assertFalse(EventPage._meta.get_field("visitor_signup").default)
        services.save_page(self.retiro, self.admin, visitor_signup=False)
        page = self.client.get(public_url(self.retiro))
        self.assertContains(page, "Entrar para inscribirme")
        self.assertNotContains(page, form_url(self.retiro))
        self.assertEqual(self.client.get(form_url(self.retiro)).status_code, 404)
        self.assertEqual(self.ask().status_code, 404)
        self.assertFalse(VisitorRequest.objects.exists())

    def test_the_public_page_offers_a_link_and_sets_no_cookie_and_reads_no_people(self):
        """Defect: a form (and its CSRF cookie) on the page that is cached and shared, or a query on people."""
        self.signed_up()
        with CaptureQueriesContext(connection) as captured:
            page = self.client.get(public_url(self.retiro))
        self.assertContains(page, f'<a class="btn btn--primary btn--large" href="{form_url(self.retiro)}">Inscribirme</a>', html=True)
        self.assertContains(page, "btn--primary", count=1)
        self.assertEqual(dict(page.cookies), {})
        self.assertNotIn("csrfmiddlewaretoken", page.content.decode())
        tables = " ".join(query["sql"] for query in captured)
        for table in ("people_person", "agenda_eventregistration", "agenda_visitorrequest", "auth_user", "django_session"):
            self.assertNotIn(table, tables)
        self.assertNotContains(page, "Valentina")

    def test_only_while_everything_it_needs_holds(self):
        """Defect: the form answering for an event that is not public, takes no sign-up, closed or cannot send."""
        self.assertEqual(self.client.get(form_url(self.retiro)).status_code, 200)
        for change in ({"public": False}, {"published": False}, {"registration": "cerrada"},
                       {"registration_closes_on": timezone.localdate() - timedelta(days=1)}):
            before = {name: getattr(EventPage.objects.get(pk=self.page.pk), name) for name in change}
            self.set_page(**change)
            self.assertEqual(self.client.get(form_url(self.retiro)).status_code, 404, change)
            self.assertEqual(self.ask().status_code, 404, change)
            self.set_page(**before)
        for settings in ({"EMMAUS_PUBLIC_BASE_URL": ""}, {"EMMAUS_SCHEDULED_COMMUNITY_JOBS": False},
                         {"EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"}):
            with self.settings(**settings):
                self.assertEqual(self.client.get(form_url(self.retiro)).status_code, 404, settings)
                self.assertNotContains(self.client.get(public_url(self.retiro)), form_url(self.retiro))
        flags.set_enabled(self.church, "community", False)
        self.assertEqual(self.client.get(form_url(self.retiro)).status_code, 404)
        self.assertEqual(self.client.get("/evento/no-existe/inscribirme/").status_code, 404)
        self.assertFalse(VisitorRequest.objects.exists())

    def test_where_the_server_cannot_send_the_administration_reads_that_the_form_is_not_offered_and_why(self):
        """The option on and no e-mail that confirms: the public page offers nothing, and the event's page says so."""
        admin = Client()
        admin.force_login(self.admin)
        page_url = reverse("agenda:page_edit", args=[self.retiro.slug])
        self.assertNotContains(admin.get(page_url), "la página pública no la ofrece")
        ask = "Pide a quien administra el servidor que lo revise"
        with self.settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend", EMMAUS_PUBLIC_BASE_URL=""):
            page = admin.get(page_url)
            self.assertContains(page, "pero la página pública no la ofrece: falta configurar el correo del sistema y la dirección "
                                      f"pública de Emmaus. {ask}.")
            self.assertNotContains(page, "EMMAUS_")
            services.save_page(self.retiro, self.admin, visitor_signup=False)
            self.assertNotContains(admin.get(page_url), "la página pública no la ofrece")  # off: nothing to explain

    def test_the_form_asks_for_the_statement_of_age_and_a_phone_only_when_the_event_asks(self):
        page = self.client.get(form_url(self.retiro))
        self.assertContains(page, "<h1", count=1)
        self.assertContains(page, "Soy mayor de edad")
        self.assertNotContains(page, 'name="phone"')
        refused = self.client.post(form_url(self.retiro), {**NAME, "email": ADDRESS})
        self.assertContains(refused, "Esta inscripción es solo para personas mayores de edad.")
        self.assertFalse(VisitorRequest.objects.exists())
        self.set_page(visitor_phone=True)
        self.assertContains(self.client.get(form_url(self.retiro)), 'name="phone"')
        self.assertContains(self.ask(), "Escribe tu teléfono.")
        self.assertEqual(self.ask(phone="55 5550 0199").status_code, 302)
        self.assertEqual(self.request_row().phone, "55 5550 0199")

    def test_csrf_is_checked_on_every_post(self):
        """Defect: another site signing somebody up, confirming or cancelling for them."""
        strict = Client(enforce_csrf_checks=True)
        data = {**NAME, "email": ADDRESS, "adult": "on"}
        self.assertEqual(strict.post(form_url(self.retiro), data).status_code, 403)
        row = self.requested(OTHER)
        self.assertEqual(strict.post(self.confirm_url(row)).status_code, 403)
        registration = self.signed_up("tercera@demo.invalid")
        self.assertEqual(strict.post(visitors.manage_path(registration), {"accion": "cancelar"}).status_code, 403)
        self.assertEqual(EventRegistration.objects.get(pk=registration.pk).status, "inscrita")
        self.assertFalse(VisitorRequest.objects.filter(email=ADDRESS).exists())
        # With the cookie this page set and its token, the form goes through.
        token = strict.get(form_url(self.retiro)).context["csrf_token"]
        self.assertEqual(strict.post(form_url(self.retiro), {**data, "csrfmiddlewaretoken": str(token)}).status_code, 302)


class MinorsTests(VisitorFixture):
    def test_the_option_cannot_be_on_for_an_event_that_protects_minors(self):
        """Defect: an inbox standing in for the look of the team at somebody's age."""
        with self.assertRaises(services.AgendaError) as refused:
            services.save_page(self.retiro, self.admin, requires_guardian_consent=True, visitor_signup=True)
        self.assertEqual(refused.exception.code, "minors_no_visitors")
        self.assertFalse(EventPage.objects.get(pk=self.page.pk).requires_guardian_consent)
        data = {"published": "on", "public": "on", "registration": "abierta", "visitor_signup": "on",
                "requires_guardian_consent": "on", "description": ""}
        form = EventPageForm(data, instance=EventPage.objects.get(pk=self.page.pk), event=self.retiro)
        self.assertFalse(form.is_valid())
        self.assertIn(services.MINORS_NO_VISITORS, form.errors["visitor_signup"])
        self.client.force_login(self.admin)
        page = self.client.post(reverse("agenda:page_edit", args=[self.retiro.slug]), data)
        self.assertContains(page, "Este evento cuida a los menores de edad")
        self.assertFalse(EventPage.objects.get(pk=self.page.pk).requires_guardian_consent)

    def test_the_form_needs_a_public_page_that_takes_sign_ups(self):
        for data in ({"published": "on", "registration": "abierta", "visitor_signup": "on"},
                     {"published": "on", "public": "on", "registration": "cerrada", "visitor_signup": "on"}):
            form = EventPageForm({"description": "", **data}, instance=EventPage.objects.get(pk=self.page.pk), event=self.retiro)
            self.assertFalse(form.is_valid())
            self.assertIn("visitor_signup", form.errors)

    def test_turning_the_rule_on_later_hides_the_form_and_settles_the_visitors_again(self):
        """Defect: a self-declared adult staying confirmed in an event that now protects minors."""
        registration = self.signed_up()
        waiting = self.requested(OTHER)
        self.assertEqual(registration.status, "inscrita")
        self.assertFalse(registration.guest_age_checked)
        with mock.patch("agenda.services.resettle_event", wraps=services.resettle_event) as resettle:
            services.save_page(self.retiro, self.admin, requires_guardian_consent=True)
        self.assertTrue(resettle.called)
        page = EventPage.objects.get(pk=self.page.pk)
        self.assertFalse(page.visitor_signup)  # and it stays off if the rule is lifted later
        registration.refresh_from_db()
        self.assertEqual((registration.status, registration.pending_reason), ("pendiente", "age"))
        self.assertEqual(self.client.get(form_url(self.retiro)).status_code, 404)
        public_page = self.client.get(public_url(self.retiro))
        self.assertNotContains(public_page, form_url(self.retiro))
        self.assertContains(public_page, "Este evento cuida a los menores de edad: quien no tiene cuenta se inscribe en")
        # A request made before the rule: its link is the dead link and signs nobody up (the option is off now).
        answer = self.client.post(self.confirm_url(waiting))
        self.assertContains(answer, "Este enlace ya no sirve", status_code=404)
        self.assertEqual(EventRegistration.objects.filter(event=self.retiro).count(), 1)
        mine = self.client.get(visitors.manage_path(registration))  # nothing of the event; cancelling still works
        self.assertContains(mine, "Por ahora esta página no muestra nada del evento.")
        self.assertContains(mine, "Cancelar mi inscripción")
        self.assertNotContains(mine, self.retiro.name)
        # Only the team's word confirms: the existing step for a visitor.
        services.confirm_guest_age(registration, self.admin, is_minor=False)
        registration.refresh_from_db()
        self.assertEqual(registration.status, "inscrita")

    def test_standing_never_confirms_a_self_declared_adult_where_the_rule_is_on(self):
        self.set_page(requires_guardian_consent=True)
        page = EventPage.objects.get(pk=self.page.pk)
        self.assertEqual(tuple(services.standing(self.retiro, page, person=None, said_minor=False, age_checked=False)), ("pendiente", "age"))
        with self.assertRaises(services.AgendaError) as refused:
            services.register_visitor(self.retiro, key="k" * 20, first_name="Ana", email=ADDRESS)
        self.assertEqual(refused.exception.code, "menores")


class NoOracleTests(VisitorFixture):
    def test_the_answer_is_the_same_whatever_the_address(self):
        """Defect: the form telling whether an address already signed up, asked, or is refused."""
        self.signed_up("ya.inscrita@demo.invalid")
        self.requested("ya.pidio@demo.invalid")
        for _ in range(subscriptions.UNANSWERED_PER_EVENT):
            SubscriptionSuppression.objects.update_or_create(
                page=self.page, address_hash=subscriptions.limit_hash("rechazada@demo.invalid"),
                defaults={"unanswered": subscriptions.UNANSWERED_PER_EVENT})
        answers = []
        for address in ("nueva@demo.invalid", "ya.inscrita@demo.invalid", "ya.pidio@demo.invalid", "rechazada@demo.invalid"):
            client = Client()
            response = self.ask(address, client=client)
            self.assertEqual(response.status_code, 302)
            page = client.get(response["Location"])
            answers.append((response["Location"], page.status_code, page.content))
            self.assertNotIn(address.encode(), page.content)
            self.assertNotIn(b"Valentina", page.content)
        self.assertEqual(len(set(answers)), 1)
        self.assertEqual(VisitorRequest.objects.filter(event=self.retiro).count(), 3)  # new, asked, and the confirmed one
        self.assertEqual(len(mail.outbox), 0)  # nothing leaves inside the request

    def test_one_request_per_inbox_however_it_is_spelled(self):
        """Defect: ``Visita+1@`` and ``visita@`` each getting a confirmation, or each holding a place."""
        self.ask(ADDRESS)
        self.ask(ADDRESS.upper())
        self.ask(ADDRESS.replace("@", "+otra@"))
        self.assertEqual(VisitorRequest.objects.count(), 1)
        registration = self.signed_up()
        self.assertTrue(visitors.same_inbox(self.retiro, ADDRESS.replace("@", "+x@").upper()))
        again = services.register_visitor(self.retiro, key="otra-llave-distinta", first_name="Otra", email=ADDRESS.replace("@", "+x@"))
        self.assertEqual(again.pk, registration.pk)
        self.assertEqual(EventRegistration.objects.filter(event=self.retiro).count(), 1)


class MailBombTests(VisitorFixture):
    def test_the_hard_limits_per_address_are_shared_with_the_subscriptions(self):
        """Defect: the form used to fill somebody's inbox, by itself or together with «Avisos por correo»."""
        services.save_page(self.retiro, self.admin, notice_mail=True)
        subscriptions.request(self.retiro, ADDRESS)  # one unanswered confirmation of the other kind
        self.requested()                                # the second
        self.assertEqual(SubscriptionSuppression.objects.get(page=self.page).unanswered, 2)
        with self.later(hours=visitors.CONFIRM_HOURS + 24 * visitors.QUEUE_DAYS + 1):
            visitors.delete_due()
            self.assertFalse(VisitorRequest.objects.exists())
            self.ask()  # the third for this event: refused, silently
            self.assertFalse(VisitorRequest.objects.exists())
        self.assertEqual(subscriptions.UNANSWERED_PER_EVENT, 2)
        self.assertEqual(subscriptions.UNANSWERED_PER_CHURCH, 4)
        self.assertEqual(subscriptions.ADDRESS_REQUESTS_PER_DAY, 3)

    def test_one_email_per_request_and_the_pass_is_paced(self):
        for number in range(visitors.CONFIRMATIONS_PER_PASS + 3):
            self.requested(f"visita{number}@demo.invalid")
        done = visitors.run_pass(BASE)
        self.assertEqual((done["confirmations"], done["left"]), (visitors.CONFIRMATIONS_PER_PASS, 3))
        self.assertEqual(len(mail.outbox), visitors.CONFIRMATIONS_PER_PASS)
        self.assertEqual(visitors.run_pass(BASE)["confirmations"], 3)
        self.assertEqual(visitors.run_pass(BASE)["confirmations"], 0)  # never a second e-mail for a request
        self.assertEqual(len(mail.outbox), visitors.CONFIRMATIONS_PER_PASS + 3)

    def test_the_email_is_content_free(self):
        """Defect: the visitor's name, phone or answers travelling by e-mail."""
        self.requested(phone="55 5550 0123")
        visitors.run_pass(BASE)
        message = mail.outbox[0]
        self.assertEqual(message.to, [ADDRESS])
        self.assertEqual(message.subject, "Emmaus: confirma tu inscripción a un evento")
        for word in ("Valentina", "Visita Demo", "5550"):
            self.assertNotIn(word, message.body + message.subject)
        self.assertIn(self.retiro.name, message.body)
        self.assertIn(f"{BASE}/evento/inscripcion/confirmar/", message.body)

    def test_the_table_of_requests_is_bounded(self):
        with mock.patch.object(visitors, "PENDING_PER_EVENT", 2):
            for number in range(4):
                self.ask(f"lleno{number}@demo.invalid", client=Client())
        self.assertEqual(VisitorRequest.objects.count(), 2)


class ConfirmTests(VisitorFixture):
    def test_opening_the_link_asks_for_a_press_and_pressing_signs_up(self):
        """Defect: a mail scanner that follows links holding a place for somebody."""
        self.ask()
        visitors.run_pass(BASE)
        path = next(line for line in mail.outbox[0].body.splitlines() if line.startswith(BASE))[len(BASE):]
        opened = self.client.get(path)
        self.assertContains(opened, "Sí, inscribirme")
        self.assertFalse(EventRegistration.objects.exists())
        done = self.client.post(path)
        self.assertContains(done, "Listo, ya tienes tu lugar")
        row = EventRegistration.objects.get()
        self.assertEqual((row.event, row.person, row.status), (self.retiro, None, "inscrita"))
        self.assertEqual((row.guest_first_name, row.guest_last_name, row.guest_email), ("Valentina", "Visita Demo", ADDRESS))
        self.assertTrue(row.is_visitor_signup)
        self.assertFalse(row.guest_age_checked)  # self-declared, never verified
        self.assertFalse(row.guest_is_minor)
        self.assertIsNone(row.registered_by)
        self.assertContains(done, visitors.manage_path(row))
        # Nothing became a person or an account, and the log names nobody.
        self.assertFalse(Person.objects.filter(first_name="Valentina").exists())
        event = ChurchAuditEvent.objects.get(action="evento_inscripcion_visita")
        self.assertNotIn("Valentina", event.summary)
        self.assertEqual(event.metadata, {"event_id": self.retiro.pk, "registration_id": row.pk})
        self.assertEqual(SubscriptionSuppression.objects.get(page=self.page).unanswered, 0)

    def test_reusing_the_link_shows_the_same_result_and_holds_one_place(self):
        row = self.requested()
        first = self.client.post(self.confirm_url(row))
        second = self.client.post(self.confirm_url(row))
        opened = self.client.get(self.confirm_url(row))
        for answer in (first, second, opened):
            self.assertEqual(answer.status_code, 200)
            self.assertContains(answer, "Inscripción confirmada")
        self.assertEqual(EventRegistration.objects.count(), 1)
        self.assertEqual(ChurchAuditEvent.objects.filter(action="evento_inscripcion_visita").count(), 1)

    def test_an_expired_tampered_or_foreign_link_gets_one_answer(self):
        """Defect: a link that works past its 48 hours, with another kind's signature, or pointed at another event."""
        row = self.requested()
        good = visitors.confirm_token(row)
        wrong = [
            good[:-2] + ("aa" if not good.endswith("aa") else "bb"),
            signing.dumps(row.key, salt=subscriptions.CONFIRM_SALT),   # the subscriptions' link
            signing.dumps(row.key, salt=visitors.MANAGE_SALT),         # the private link's signature
            signing.dumps("no-such-key", salt=visitors.CONFIRM_SALT),
            signing.dumps(row.pk, salt=visitors.CONFIRM_SALT),
        ]
        answers = set()
        for token in wrong:
            for method in (self.client.get, self.client.post):
                response = method(visitors.confirm_path(token))
                self.assertEqual(response.status_code, 404)
                answers.add(response.content)
        self.assertEqual(len(answers), 1)
        with self.later(hours=visitors.CONFIRM_HOURS, minutes=1):
            self.assertEqual(self.client.post(visitors.confirm_path(good)).status_code, 404)
        self.assertFalse(EventRegistration.objects.exists())

    def test_a_link_only_ever_acts_on_its_own_event(self):
        """Defect: a request for one event signing somebody up for another (of another church)."""
        row = self.requested(event=self.retiro_b)
        self.client.post(self.confirm_url(row))
        self.assertEqual(list(EventRegistration.objects.values_list("event_id", flat=True)), [self.retiro_b.pk])
        self.assertFalse(ChurchAuditEvent.objects.filter(church=self.church, action="evento_inscripcion_visita").exists())

    def test_a_request_whose_email_was_sent_expires_after_48_hours(self):
        row = self.requested()
        visitors.run_pass(BASE)
        with self.later(hours=47):
            self.assertIsNotNone(visitors.for_confirmation(visitors.confirm_token(row)))
        with self.later(hours=49):
            self.assertEqual(visitors.delete_due()["requests"], 1)
        self.assertFalse(VisitorRequest.objects.exists())


class CapacityTests(VisitorFixture):
    def test_the_last_place_taken_between_the_form_and_the_link(self):
        """Defect: a confirmed request going over the capacity, or failing without saying so."""
        self.set_page(capacity=1)
        row = self.requested()
        services.register(self.retiro, self.member, self.member_user)
        answer = self.client.post(self.confirm_url(row))
        self.assertContains(answer, "No se hizo tu inscripción")
        self.assertContains(answer, "Ya no hay lugares.")
        self.assertEqual(EventRegistration.objects.filter(event=self.retiro).count(), 1)
        self.assertIsNone(self.request_row().confirmed_at)
        # The place is freed: the same link, still within its hours, works.
        services.cancel(EventRegistration.objects.get(person=self.member), self.member_user)
        self.assertContains(self.client.post(self.confirm_url(row)), "Listo, ya tienes tu lugar")

    def test_two_requests_for_the_last_place_give_it_once(self):
        """Defect: two confirmations read "one place left" and both take it."""
        self.set_page(capacity=1)
        first, second = self.requested(), self.requested(OTHER)
        states = [visitors.confirm(first)[0], visitors.confirm(second)[0]]
        self.assertEqual(states, ["ok", "full"])
        self.assertEqual(services.taken(self.retiro), 1)

    def test_the_page_lock_is_taken_before_any_registration_row(self):
        """Defect: a lock order that deadlocks on PostgreSQL against a member's sign-up (person, page, registration)."""
        row = self.requested()
        with CaptureQueriesContext(connection) as captured:
            visitors.confirm(row)
        sql = [query["sql"] for query in captured]
        request = next(i for i, text in enumerate(sql) if "agenda_visitorrequest" in text and text.lstrip().upper().startswith("SELECT"))
        page = next(i for i, text in enumerate(sql) if 'FROM "agenda_eventpage"' in text)
        registration = next(i for i, text in enumerate(sql) if 'FROM "agenda_eventregistration"' in text)
        self.assertLess(request, page)
        self.assertLess(page, registration)

    def test_full_with_a_waiting_list_puts_the_visitor_in_line_and_says_so(self):
        self.set_page(capacity=1, waiting_list=True)
        holder = services.register(self.retiro, self.member, self.member_user)
        row = self.requested()
        answer = self.client.post(self.confirm_url(row))
        self.assertContains(answer, "Quedaste en la lista de espera")
        self.assertContains(answer, "todavía no tienes lugar")
        self.assertNotContains(answer, "ya tienes tu lugar")
        waiting = EventRegistration.objects.get(person__isnull=True)
        self.assertEqual(waiting.status, "en_espera")
        self.assertEqual(services.taken(self.retiro), 1)
        # The place is freed: it is OFFERED, by the existing rule, with one content-free e-mail to the private link.
        with self.captureOnCommitCallbacks(execute=True):
            services.cancel(holder, self.member_user, base_url=BASE)
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, "ofrecida")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [ADDRESS])
        self.assertIn(visitors.manage_path(waiting), mail.outbox[0].body)
        self.assertNotIn("Valentina", mail.outbox[0].body)
        mine = self.client.get(visitors.manage_path(waiting))
        self.assertContains(mine, "Aceptar mi lugar")
        accepted = self.client.post(visitors.manage_path(waiting), {"accion": "aceptar"})
        self.assertContains(accepted, "Aceptaste tu lugar.")
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, "inscrita")
        for event in ChurchAuditEvent.objects.filter(church=self.church, object_id=str(waiting.pk)):
            self.assertNotIn("Valentina", event.summary)

    def test_a_visitor_the_team_wrote_at_the_door_still_cannot_wait_in_line(self):
        """Defect: the relaxed constraint letting a row nobody can reach onto the waiting list."""
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            EventRegistration.objects.create(event=self.retiro, guest_first_name="Sin enlace", status="en_espera",
                                             waiting_since=timezone.now())


class PrivateLinkTests(VisitorFixture):
    def test_cancelling_asks_first_frees_the_place_and_the_list_advances(self):
        self.set_page(capacity=1, waiting_list=True)
        registration = self.signed_up()
        waiting = services.register(self.retiro, self.member, self.member_user)
        self.assertEqual(waiting.status, "en_espera")
        path = visitors.manage_path(registration)
        page = self.client.get(path)
        self.assertContains(page, "Cancelar mi inscripción")
        self.assertContains(page, "Valentina Visita Demo")  # their own name, on their own page
        self.assertContains(self.client.get(path + "?cancelar=1"), "Sí, cancelar mi inscripción")
        registration.refresh_from_db()
        self.assertEqual(registration.status, "inscrita")  # asking changed nothing
        done = self.client.post(path, {"accion": "cancelar"})
        self.assertContains(done, "Cancelaste tu inscripción.")
        registration.refresh_from_db()
        waiting.refresh_from_db()
        self.assertEqual((registration.status, waiting.status), ("cancelada", "ofrecida"))
        self.assertContains(self.client.get(path), "ya no está vigente")
        self.assertNotIn("Valentina", ChurchAuditEvent.objects.get(action="evento_inscripcion_cancelada").summary)

    def test_not_once_the_event_started(self):
        registration = self.signed_up()
        self.move(self.retiro, 0, 2)
        path = visitors.manage_path(registration)
        self.assertNotContains(self.client.get(path), "Cancelar mi inscripción")
        refused = self.client.post(path, {"accion": "cancelar"})
        self.assertContains(refused, "El evento ya empezó")
        registration.refresh_from_db()
        self.assertEqual(registration.status, "inscrita")

    def test_a_tampered_link_or_another_kind_of_link_opens_nothing(self):
        registration = self.signed_up()
        good = visitors.manage_token(registration)
        for token in (good[:-2] + "zz", signing.dumps(registration.visitor_key, salt=visitors.CONFIRM_SALT),
                      signing.dumps(registration.pk, salt=visitors.MANAGE_SALT), signing.dumps("otra", salt=visitors.MANAGE_SALT)):
            path = reverse("evento:visitor_manage", args=[token])
            self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(self.client.post(path, {"accion": "cancelar"}).status_code, 404)
        registration.refresh_from_db()
        self.assertEqual(registration.status, "inscrita")

    def test_a_stale_copy_does_not_bring_a_cancelled_registration_back(self):
        """Defect: two tabs; the second, read before the first cancelled, overwriting the cancellation."""
        registration = self.signed_up()
        stale = EventRegistration.objects.get(pk=registration.pk)
        services.cancel(registration, None)
        services.cancel(stale, None)  # idempotent, from the row as the database has it
        self.assertEqual(ChurchAuditEvent.objects.filter(action="evento_inscripcion_cancelada").count(), 1)
        self.assertEqual(EventRegistration.objects.get(pk=registration.pk).status, "cancelada")

    def test_the_private_page_shows_no_other_person_and_no_count(self):
        self.set_page(capacity=30)
        services.register(self.retiro, self.member, self.member_user)
        other = self.signed_up(OTHER, first_name="Otra", last_name="Persona Distinta")
        page = self.client.get(visitors.manage_path(self.signed_up())).content.decode()
        for word in ("Otra", "Persona Distinta", self.member.first_name, "lugares", OTHER, ADDRESS):
            self.assertNotIn(word, page)
        self.assertTrue(other.pk)


class StaffTests(VisitorFixture):
    def test_the_list_marks_who_signed_up_without_an_account_and_counts_requests_as_a_number(self):
        self.set_page(visitor_phone=True)
        self.signed_up(phone="55 5550 0142", contact_consent=True)
        self.requested(OTHER, first_name="Zacarías", last_name="Sinconfirmar")
        self.client.force_login(self.admin)
        page = self.client.get(reverse("agenda:registrations", args=[self.retiro.slug]))
        self.assertContains(page, "Se inscribió sin cuenta, correo confirmado,")
        self.assertContains(page, "Valentina Visita Demo")
        self.assertContains(page, ADDRESS)
        self.assertContains(page, "1 inscripción sin cuenta espera que confirmen el correo")
        for word in ("Zacarías", "Sinconfirmar", OTHER):
            self.assertNotContains(page, word)
        self.assertNotContains(page, "Anular registro")  # that is for what the team wrote
        self.assertContains(page, "Agregar a personas de la iglesia")

    def test_what_a_visitor_typed_is_escaped(self):
        self.signed_up(first_name="<script>alert(1)</script>", last_name="<b>Negritas</b>")
        self.client.force_login(self.admin)
        page = self.client.get(reverse("agenda:registrations", args=[self.retiro.slug])).content.decode()
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        mine = self.client.get(visitors.manage_path(EventRegistration.objects.get())).content.decode()
        self.assertNotIn("<b>Negritas</b>", mine)

    def test_adding_the_visitor_to_people_is_the_existing_manual_audited_step(self):
        registration = self.signed_up(contact_consent=True)
        self.client.force_login(self.admin)
        url = reverse("agenda:guest_promote", args=[self.retiro.slug, registration.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url)
        registration.refresh_from_db()
        self.assertIsNotNone(registration.person_id)
        self.assertEqual(registration.person.created_from, Person.CreatedFrom.EVENT)
        self.assertEqual(registration.person.email, "")  # the address was given for this event only
        self.assertTrue(ChurchAuditEvent.objects.filter(action="evento_visita_persona").exists())
        # The private link keeps working for the same registration, and the follow-up is the existing one.
        self.assertEqual(self.client.get(visitors.manage_path(registration)).status_code, 200)
        self.assertEqual(self.client.get(reverse("agenda:visit_follow_up", args=[self.retiro.slug, registration.pk])).status_code, 200)

    def test_only_the_church_administration_reads_the_list(self):
        registration = self.signed_up()
        url = reverse("agenda:registrations", args=[self.retiro.slug])
        self.assertEqual(self.client.get(url).status_code, 302)  # signed out
        for user, status in ((self.member_user, 403), (self.admin_b, 404)):  # a plain member; staff of another church
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, status)
        self.client.force_login(self.admin_b)
        other = reverse("agenda:guest_promote", args=[self.retiro_b.slug, registration.pk])
        self.assertEqual(self.client.get(other).status_code, 404)  # a guessed id of another church's event


class EndingsTests(VisitorFixture):
    def test_the_deletions_are_a_deleting_job_and_ignore_every_switch(self):
        """Defect: switching Emmaus off keeping what it promised to delete."""
        self.assertIn(scheduler.VISITOR_SIGNUPS, scheduler.DELETING_JOBS)
        self.assertEqual(scheduler.skipped(scheduler.VISITOR_SIGNUPS), "")
        self.requested()
        flags.set_enabled(self.church, "community", False)
        with self.settings(EMMAUS_SCHEDULED_COMMUNITY_JOBS=False, EMMAUS_V2_MODULES=False), \
                self.later(days=visitors.QUEUE_DAYS, hours=1):
            from core.management.commands.run_scheduled_jobs import visitor_signups

            done = visitor_signups()
        self.assertEqual(done["requests"], 1)
        self.assertEqual(done["confirmations"], 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(VisitorRequest.objects.exists())

    def test_nothing_is_mailed_while_the_church_does_not_use_the_module(self):
        self.requested()
        flags.set_enabled(self.church, "community", False)
        self.assertEqual(visitors.run_pass(BASE)["confirmations"], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_the_events_own_retention_ends_the_visitors_registration_and_every_answer(self):
        question = EventQuestion.objects.create(event=self.retiro, text="¿Te quedas a comer?", kind="si_no")
        visitor = self.signed_up(answers={str(question.pk): "Sí"})
        promoted = self.signed_up(OTHER, contact_consent=True)
        services.promote_guest(promoted, self.admin)
        member = services.register(self.retiro, self.member, self.member_user)
        questions.save_answers(member, {str(question.pk): "No"})
        self.assertEqual(RegistrationAnswer.objects.count(), 2)
        self.assertEqual(visitors.delete_due()["answers"], 0)  # the event has not even happened
        Event.objects.filter(pk=self.retiro.pk).update(
            starts_on=self.today - timedelta(days=self.retiro.retention_days + 3), ends_on=self.today - timedelta(days=self.retiro.retention_days + 1))
        done = visitors.delete_due()
        self.assertEqual((done["answers"], done["registrations"], done["contacts"]), (2, 1, 1))
        self.assertFalse(EventRegistration.objects.filter(pk=visitor.pk).exists())
        promoted.refresh_from_db()
        self.assertEqual((promoted.guest_email, promoted.visitor_key), ("", None))
        self.assertTrue(EventRegistration.objects.filter(pk=member.pk).exists())
        self.assertEqual(visitors.delete_due(), {"requests": 0, "answers": 0, "registrations": 0, "contacts": 0})
