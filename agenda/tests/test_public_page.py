"""Decision 0010, phase A, on synthetic data: the public page of an event says what the event is to
anybody with the address, and never anything about people. Everything that is not such a page is
the same 404."""

from datetime import time, timedelta
from urllib.parse import parse_qs, urlsplit

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from agenda import services
from agenda.models import EventPage, EventRegistration
from agenda.tests.test_event_extras import CONSENT, ExtrasFixture
from core.models import Church
from core.templatetags.shell import icon
from events.models import Event, EventParticipant
from people import flags
from people.models import Person


def public_url(event) -> str:
    return reverse("evento:detail", args=[event.slug])


class PublicFixture(ExtrasFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        services.save_page(cls.retiro, cls.admin, public=True, waiting_list=True, requires_guardian_consent=True)
        services.save_faq(cls.retiro, cls.admin, question="¿Qué llevo?", answer="Ropa abrigadora y tu Biblia.")
        services.save_schedule_item(cls.retiro, cls.admin, day=cls.retiro.starts_on, starts_at=time(18, 0), title="Bienvenida",
                                    place="Salón grande")
        # People around the event: a minor with a guardian's authorization, somebody whose age nobody
        # confirmed, two on the waiting list (one of them with an offer), a cancellation and, once the
        # event starts, a visitor at the door.
        Person.objects.filter(pk=cls.member.pk).update(birth_date=cls.today - timedelta(days=15 * 365))
        cls.member.refresh_from_db()
        services.save_page(cls.retiro, cls.admin, capacity=3)
        minor = services.register(cls.retiro, cls.member, cls.member_user)
        services.record_consent(minor, cls.admin, **CONSENT)
        held = services.register(cls.retiro, cls.admin_person, cls.admin)
        services.register(cls.retiro, cls.coord_person, cls.coord1)  # unknown age: stays pending
        services.register(cls.retiro, cls.leader, cls.leader_user)
        services.register(cls.retiro, cls.asked, cls.asked_user)
        services.cancel(held, cls.admin)  # the first of the list gets the offer
        Event.objects.filter(pk=cls.retiro.pk).update(starts_on=cls.today)
        cls.retiro.refresh_from_db()
        services.register_walkup(cls.retiro, cls.admin, first_name="Vera", last_name="Visita Reservada", phone="55 7777 0000",
                                 seen_adult=True, override=["llena"], override_reason="llego")
        assert set(EventRegistration.objects.filter(event=cls.retiro).values_list("status", flat=True)) >= {
            "inscrita", "pendiente", "ofrecida", "en_espera", "cancelada"}, "the fixture needs a row in every state"

    # Everything that names or counts a person and must never be on the page, as bytes.
    def private(self):
        people = [self.member, self.leader, self.asked, self.admin_person, self.coord_person, self.no_account]
        words = ["Vera", "Visita Reservada", "55 7777 0000", "Marta", "Tutora Reservada", "55 9999 0000"]
        for person in people:
            words += [person.first_name, person.last_name, person.email, person.phone]
        words += ["lista de espera", "Lugares", "lugares", "inscritas", "inscritos", "Quedan", "pendiente", "autoriz",
                  "ofrecimos", "Mi iglesia", 'class="rail"', 'class="tabs"', "csrfmiddlewaretoken"]
        return [word.encode() for word in words if word]


class PublicPageTests(PublicFixture):
    def test_anybody_reads_what_the_event_is(self):
        response = self.client.get(public_url(self.retiro))
        self.assertEqual(response.status_code, 200)
        for text in ("Retiro de jóvenes", "Tres días para descansar.", "Retiro", f'<li>{icon("reloj", "sm")}19:30</li>',
                     f'<li>{icon("lugar", "sm")}Rancho El Olivo</li>', "¿Qué llevo?", "Ropa abrigadora y tu Biblia.",
                     "La inscripción se hace en Emmaus, con la cuenta de quien va a ir.", "Entrar para inscribirme",
                     self.church.name):
            self.assertContains(response, text)
        # The programme: the hour drawn, then where.
        self.assertContains(
            response,
            '<li class="row row--lead"><span class="hora">18:00</span><div class="row__main"><p class="row__title">Bienvenida</p>'
            f'<ul class="meta"><li>{icon("lugar", "sm")}Salón grande</li></ul></div></li>',
            html=True,
        )
        self.assertContains(response, "<h1", count=1)
        self.assertContains(response, 'class="bar bar--public"')
        self.assertContains(response, '<meta name="robots" content="noindex, nofollow">')
        # The way in returns to the event's page in Emmaus.
        self.assertContains(response, f'href="{reverse("login")}?next=%2Fmi-iglesia%2Feventos%2Fretiro-jovenes%2F"')
        self.assertEqual(dict(response.cookies), {})  # reading it leaves no session and no cookie behind

    def test_nothing_about_people_is_in_the_bytes(self):
        content = self.client.get(public_url(self.retiro)).content
        for word in self.private():
            self.assertNotIn(word, content, word.decode())
        for row in EventRegistration.objects.filter(event=self.retiro):
            self.assertNotIn(f"/{row.pk}/".encode(), content)

    def test_the_page_never_asks_a_table_of_people(self):
        with CaptureQueriesContext(connection) as queries:
            self.client.get(public_url(self.retiro))
        sql = " ".join(query["sql"] for query in queries.captured_queries)
        for table in ("agenda_eventregistration", "agenda_guardianconsent", "people_person", "events_eventparticipant",
                      "auth_user", "care_followup"):
            self.assertNotIn(table, sql)
        self.assertLessEqual(len(queries), 7)  # one more since issue #96: the event's public notices

    def test_who_is_signed_in_is_not_treated_differently(self):
        anonymous = self.client.get(public_url(self.retiro)).content
        for user in (self.member_user, self.admin, self.member_b_user, self.admin_b):
            self.client.force_login(user)
            self.assertEqual(self.client.get(public_url(self.retiro)).content, anonymous, user.email)

    def test_it_only_reads(self):
        for method in (self.client.post, self.client.put, self.client.delete):
            self.assertEqual(method(public_url(self.retiro)).status_code, 405)

    def test_what_it_says_about_signing_up_never_says_whether_there_is_room(self):
        self.set_page(capacity=1)  # full, with a waiting list: the page says the same
        full = self.client.get(public_url(self.retiro))
        self.assertContains(full, "La inscripción se hace en Emmaus, con la cuenta de quien va a ir.")
        self.assertContains(full, "Entrar para inscribirme")
        self.set_page(registration_closes_on=self.today - timedelta(days=1))
        closed = self.client.get(public_url(self.retiro))
        self.assertContains(closed, "La inscripción a este evento ya cerró.")
        self.assertNotContains(closed, "Entrar para inscribirme")
        self.assertNotContains(closed, "La inscripción se hace en Emmaus")
        self.set_page(registration="cerrada")
        self.assertContains(self.client.get(public_url(self.retiro)), "La inscripción a este evento no se hace desde Emmaus.")


class NoOracleTests(PublicFixture):
    def gone(self, event, why):
        """The answer for ``event`` is, byte for byte, the answer for an address that never existed."""
        response = self.client.get(public_url(event))
        missing = self.client.get(reverse("evento:detail", args=["no-existe"]))
        self.assertEqual((response.status_code, missing.status_code), (404, 404), why)
        self.assertEqual(response.content, missing.content, why)
        self.assertNotIn(event.name.encode(), response.content, why)

    def test_everything_that_is_not_a_public_page_is_the_same_404(self):
        self.gone(self.retiro_b, "not public")
        self.gone(self.no_page, "no page")
        EventPage.objects.filter(event=self.hidden).update(public=True)
        self.gone(self.hidden, "public but not published")
        for status in (Event.Status.DRAFT, Event.Status.CLOSED):
            Event.objects.filter(pk=self.retiro.pk).update(status=status)
            self.gone(self.retiro, f"event {status}")
        Event.objects.filter(pk=self.retiro.pk).update(status=Event.Status.ACTIVE)
        self.assertEqual(self.client.get(public_url(self.retiro)).status_code, 200)
        flags.set_enabled(self.church, "community", False)
        self.gone(self.retiro, "module off")
        flags.set_enabled(self.church, "community", True)
        Church.objects.filter(pk=self.church.pk).update(active=False)
        self.gone(self.retiro, "church not active")
        Church.objects.filter(pk=self.church.pk).update(active=True)
        self.move(self.retiro, -5, -3)
        self.gone(self.retiro, "over")
        self.move(self.retiro, 0, 2)
        self.assertEqual(self.client.get(public_url(self.retiro)).status_code, 200)
        self.set_page(public=False)
        self.gone(self.retiro, "switched off")
        # Signed in, a member of the church gets the same refusal as a stranger: no hint that it exists.
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(public_url(self.retiro)).status_code, 404)
        self.assertEqual(self.client.get(reverse("evento:detail", args=["no-existe"])).status_code, 404)

    def test_with_settings_kill_switch_off_every_public_page_is_gone(self):
        with self.settings(EMMAUS_V2_MODULES=False):
            self.assertEqual(self.client.get(public_url(self.retiro)).status_code, 404)

    def test_another_churchs_event_is_its_own_page_and_shows_nothing_of_this_church(self):
        self.assertEqual(self.client.get(public_url(self.retiro_b)).status_code, 404)
        services.save_page(self.retiro_b, self.admin_b, public=True)
        services.register(self.retiro_b, self.member_b, self.member_b_user)
        content = self.client.get(public_url(self.retiro_b)).content
        self.assertIn("Vigilia de la iglesia B".encode(), content)
        self.assertIn(b"Iglesia B", content)
        for word in [self.church.name.encode(), "Retiro de jóvenes".encode(), b"Bruno", *self.private()]:
            self.assertNotIn(word, content)
        # A member of church A, signed in, reads church B's public page exactly as anybody does.
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(public_url(self.retiro_b)).content, content)

    def test_a_camp_shows_its_page_and_nothing_of_the_camp(self):
        camp = Event.objects.create(church=self.church, name="Campamento de verano", slug="campamento-verano",
                                    starts_on=self.today + timedelta(days=30), ends_on=self.today + timedelta(days=33))
        self.assertTrue(camp.camp_tools)
        EventParticipant.objects.create(event=camp, number=7, full_name="Camila Campera Reservada", age=15, room="Cabaña 4",
                                        church_group="Jóvenes Reservados")
        self.assertEqual(self.client.get(public_url(camp)).status_code, 404)
        services.save_page(camp, self.admin, published=True, public=True, description="Cuatro días en la sierra.")
        content = self.client.get(public_url(camp)).content
        self.assertIn("Campamento de verano".encode(), content)
        for word in ("Camila", "Campera Reservada", "Cabaña 4", "Jóvenes Reservados", camp.join_code or "sin-codigo"):
            self.assertNotIn(word.encode(), content)


class AdministrationTests(PublicFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_the_page_edit_screen_gives_the_address_and_a_generic_whatsapp_message(self):
        html = self.client.get(self.url("page_edit")).content.decode()
        link = "http://testserver" + public_url(self.retiro)
        self.assertIn(f'value="{link}"', html)
        self.assertIn('data-copy="enlace-publico"', html)
        start = html.index('href="https://wa.me/?text=')
        href = html[start + 6:html.index('"', start + 6)].replace("&amp;", "&")
        parts = urlsplit(href)
        self.assertEqual((parts.scheme, parts.netloc, parts.path), ("https", "wa.me", "/"))
        text = parse_qs(parts.query)["text"][0]
        self.assertEqual(text, f"{self.church.name}: aquí está la información de un evento. No necesitas cuenta para leerla: {link}")
        self.assertNotIn("Retiro de jóvenes", text)

    def test_no_address_is_offered_while_the_page_does_not_answer(self):
        self.set_page(public=False)
        html = self.client.get(self.url("page_edit")).content.decode()
        self.assertNotIn("wa.me", html)
        self.assertNotIn(public_url(self.retiro), html)
        self.set_page(public=True, published=False)
        html = self.client.get(self.url("page_edit")).content.decode()
        self.assertNotIn("wa.me", html)
        self.assertIn("la página pública todavía no funciona", html)

    def test_the_administration_switches_it_on_the_form_and_only_the_administration(self):
        self.set_page(public=False)
        data = {"published": "on", "public": "on", "description": "Tres días.", "registration": "abierta", "capacity": "3"}
        self.assertEqual(self.client.post(self.url("page_edit"), data).status_code, 302)
        self.assertTrue(EventPage.objects.get(pk=self.page.pk).public)
        self.assertEqual(self.client.get(public_url(self.retiro)).status_code, 200)
        self.set_page(public=False)
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.post(self.url("page_edit"), data).status_code, 403)
        self.assertFalse(EventPage.objects.get(pk=self.page.pk).public)

    def test_a_copy_of_the_event_is_never_public(self):
        self.client.post(self.url("duplicate"), {"name": "Retiro de jóvenes 2", "starts_on": "", "ends_on": ""})
        copies = EventPage.objects.exclude(event=self.retiro).filter(event__name__startswith="Retiro de jóvenes")
        for page in copies:
            self.assertFalse(page.public)
            self.assertFalse(page.published)
