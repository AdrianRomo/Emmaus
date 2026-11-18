"""Events for the people of the church: the published page, signing up, cancelling, who came."""

from datetime import time, timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from access.models import RoleAssignment
from agenda import services
from agenda.models import EventPage, EventRegistration
from events.models import Event, EventMembership, EventParticipant
from groups.tests.base import CommunityFixture
from people import flags
from people.models import ChurchAuditEvent

URLS = ["detail", "register", "cancel", "page_edit", "registrations"]


class AgendaFixture(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.today = timezone.localdate()
        cls.retiro = cls._future(cls.church, "Retiro de jóvenes", "retiro-jovenes", 10, 12, location="Rancho El Olivo")
        cls.page = services.save_page(
            cls.retiro, cls.admin, published=True, registration="abierta", description="Tres días para descansar.",
            starts_at=time(19, 30),
        )
        cls.hidden = cls._future(cls.church, "Conferencia sin publicar", "conferencia-oculta", 20, 20)
        services.save_page(cls.hidden, cls.admin, published=False, registration="abierta")
        cls.no_page = cls._future(cls.church, "Evento sin página", "sin-pagina", 5, 5)
        cls.retiro_b = cls._future(cls.church_b, "Vigilia de la iglesia B", "vigilia-b", 10, 10)
        services.save_page(cls.retiro_b, cls.admin_b, published=True, registration="abierta")

    @classmethod
    def _future(cls, church, name, slug, start, end, **extra) -> Event:
        return Event.objects.create(
            church=church, name=name, slug=slug, kind=Event.Kind.RETREAT,
            starts_on=cls.today + timedelta(days=start), ends_on=cls.today + timedelta(days=end), **extra,
        )

    def url(self, name="detail", event=None):
        return reverse(f"agenda:{name}", args=[(event or self.retiro).slug])

    def set_page(self, **fields):
        EventPage.objects.filter(pk=self.page.pk).update(**fields)

    def move(self, event, start, end):
        Event.objects.filter(pk=event.pk).update(
            starts_on=self.today + timedelta(days=start), ends_on=self.today + timedelta(days=end)
        )
        event.refresh_from_db()


class VisibilityTests(AgendaFixture):
    def setUp(self):
        self.client.force_login(self.member_user)

    def test_an_unpublished_event_is_invisible_to_a_member(self):
        for event in (self.hidden, self.no_page):
            self.assertEqual(self.client.get(self.url(event=event)).status_code, 404)
            self.assertEqual(self.client.post(self.url("register", event)).status_code, 404)
        page = self.client.get(reverse("agenda:list"))
        self.assertNotContains(page, "Conferencia sin publicar")
        self.assertNotContains(page, "Evento sin página")
        self.assertFalse(EventRegistration.objects.exists())

    def test_a_published_event_shows_in_the_list_and_on_its_page(self):
        page = self.client.get(reverse("agenda:list"))
        self.assertContains(page, "<h1", count=1)
        self.assertContains(page, "Lo que viene en la iglesia")
        self.assertNotContains(page, "Tus inscripciones")
        self.assertContains(page, "Retiro de jóvenes")
        self.assertContains(page, "a las 19:30, en Rancho El Olivo.")
        self.assertContains(page, "Inscripción abierta")
        self.assertNotContains(page, "Vigilia de la iglesia B")
        self.assertNotContains(page, "Administrar los eventos")
        detail = self.client.get(self.url())
        self.assertContains(detail, "<h1", count=1)
        self.assertContains(detail, "Tres días para descansar.")
        self.assertContains(detail, "Inscribirme")
        self.assertContains(
            detail,
            "Inscribirte le avisa a tu iglesia que quieres ir. Si eres menor de edad, tu mamá, papá o tutor también "
            "tiene que estar de acuerdo: la iglesia te dirá cómo.",
        )
        self.assertNotContains(detail, "Quedan")
        self.assertNotContains(detail, "Inscritos")

    def test_a_draft_or_ended_event_leaves_the_list(self):
        Event.objects.filter(pk=self.retiro.pk).update(status=Event.Status.DRAFT)
        self.assertNotContains(self.client.get(reverse("agenda:list")), "Retiro de jóvenes")
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        Event.objects.filter(pk=self.retiro.pk).update(status=Event.Status.ACTIVE)
        self.move(self.retiro, -5, -1)
        self.assertNotContains(self.client.get(reverse("agenda:list")), "Retiro de jóvenes")
        self.assertContains(self.client.get(self.url()), "Este evento ya terminó.")

    def test_an_event_without_end_date_counts_by_its_first_day(self):
        Event.objects.filter(pk=self.retiro.pk).update(starts_on=self.today, ends_on=None)
        self.assertContains(self.client.get(reverse("agenda:list")), "Retiro de jóvenes")
        Event.objects.filter(pk=self.retiro.pk).update(starts_on=self.today - timedelta(days=1))
        self.assertNotContains(self.client.get(reverse("agenda:list")), "Retiro de jóvenes")

    def test_places_left_only_when_there_is_a_limit(self):
        self.set_page(capacity=12)
        self.assertContains(self.client.get(self.url()), "Quedan 12 lugares")
        services.register(self.retiro, self.leader, self.leader_user)
        self.assertContains(self.client.get(self.url()), "Quedan 11 lugares")


class RegistrationTests(AgendaFixture):
    def setUp(self):
        self.client.force_login(self.member_user)

    def test_signing_up_and_what_the_person_reads(self):
        self.assertEqual(self.client.get(self.url("register")).status_code, 405)
        response = self.client.post(self.url("register"), follow=True)
        self.assertContains(response, "Listo. Te inscribiste a Retiro de jóvenes.")
        self.assertContains(response, "<strong>Te inscribiste.</strong>")
        self.assertContains(response, "Cancelar mi inscripción")
        self.assertNotContains(response, 'btn--on-dark')
        row = EventRegistration.objects.get()
        self.assertEqual((row.event, row.person, row.status, row.attended), (self.retiro, self.member, "inscrita", None))
        page = self.client.get(reverse("agenda:list"))
        self.assertContains(page, "Tus inscripciones")
        self.assertContains(page, "Te inscribiste")
        # A second tap changes nothing.
        self.client.post(self.url("register"))
        self.assertEqual(EventRegistration.objects.count(), 1)
        self.assertEqual(ChurchAuditEvent.objects.filter(action="evento_inscripcion").count(), 1)

    def test_a_registration_is_not_a_camp_participant_and_gives_no_role(self):
        before = (EventParticipant.objects.count(), EventMembership.objects.count(), RoleAssignment.objects.count())
        self.client.post(self.url("register"))
        self.assertEqual(EventRegistration.objects.count(), 1)
        self.assertEqual(
            (EventParticipant.objects.count(), EventMembership.objects.count(), RoleAssignment.objects.count()), before
        )
        self.assertFalse(EventParticipant.objects.filter(event=self.retiro).exists())
        self.assertFalse(EventMembership.objects.filter(event=self.retiro).exists())

    def refused(self, message, code):
        response = self.client.post(self.url("register"), follow=True)
        self.assertContains(response, message)
        self.assertNotContains(response, 'btn--on-dark')
        self.assertFalse(EventRegistration.objects.filter(person=self.member, status="inscrita").exists())
        with self.assertRaises(services.AgendaError) as caught:
            services.register(self.retiro, self.member, self.member_user)
        self.assertEqual((caught.exception.message, caught.exception.code), (message, code))

    def test_refused_when_registration_is_not_in_emmaus(self):
        self.set_page(registration="cerrada")
        self.refused("La inscripción a este evento no se hace desde Emmaus. Pregunta en tu iglesia.", "no_registration")

    def test_refused_after_the_closing_date(self):
        self.set_page(registration_closes_on=self.today)
        self.client.post(self.url("register"))  # the last day still counts
        self.assertEqual(EventRegistration.objects.count(), 1)
        EventRegistration.objects.all().delete()
        self.set_page(registration_closes_on=self.today - timedelta(days=1))
        self.refused("La inscripción a este evento ya cerró.", "registration_over")

    def test_refused_when_full(self):
        self.set_page(capacity=1)
        services.register(self.retiro, self.leader, self.leader_user)
        self.refused("Ya no hay lugares.", "full")
        self.assertContains(self.client.get(reverse("agenda:list")), "Ya no hay lugares")

    def test_refused_when_the_event_ended(self):
        self.move(self.retiro, -3, -1)
        self.refused("Este evento ya terminó.", "ended")

    def test_refused_when_not_published_or_of_another_church(self):
        with self.assertRaises(services.AgendaError) as caught:
            services.register(self.hidden, self.member, self.member_user)
        self.assertEqual(caught.exception.code, "not_published")
        with self.assertRaises(services.AgendaError) as caught:
            services.register(self.retiro_b, self.member, self.member_user)
        self.assertEqual(caught.exception.code, "other_church")
        self.assertFalse(EventRegistration.objects.exists())

    def test_the_last_place_is_counted_with_the_page_locked(self):
        """The count happens inside the transaction, after the page row is locked: whoever arrives
        second reads the first registration. Cancelled rows do not take a place."""
        self.set_page(capacity=2)
        gone = services.register(self.retiro, self.asked, self.asked_user)
        services.cancel(gone, self.asked_user)
        services.register(self.retiro, self.leader, self.leader_user)
        with CaptureQueriesContext(connection) as queries:
            services.register(self.retiro, self.member, self.member_user)
        sql = [query["sql"] for query in queries if "agenda_" in query["sql"]]
        self.assertIn("agenda_eventpage", sql[0])  # the lock comes first
        count_at = next(index for index, text in enumerate(sql) if "COUNT(" in text)
        insert_at = next(index for index, text in enumerate(sql) if text.startswith("INSERT"))
        self.assertLess(0, count_at)
        self.assertLess(count_at, insert_at)
        with self.assertRaises(services.AgendaError):
            services.register(self.retiro, self.coord_person, self.coord1)
        self.assertEqual(services.taken(self.retiro), 2)

    def test_cancelling_and_signing_up_again_reuse_one_row(self):
        self.client.post(self.url("register"))
        row = EventRegistration.objects.get()
        confirm = self.client.get(self.url("cancel"))
        self.assertContains(confirm, "¿Quieres cancelar tu inscripción a Retiro de jóvenes?")
        self.assertEqual(EventRegistration.objects.get().status, "inscrita")
        response = self.client.post(self.url("cancel"), follow=True)
        self.assertContains(response, "Cancelaste tu inscripción a Retiro de jóvenes.")
        row.refresh_from_db()
        self.assertEqual(row.status, "cancelada")
        self.assertIsNotNone(row.cancelled_at)
        self.assertContains(self.client.get(self.url()), "Inscribirme")
        self.assertEqual(self.client.get(self.url("cancel")).status_code, 404)
        self.client.post(self.url("register"))
        again = EventRegistration.objects.get()
        self.assertEqual((again.pk, again.status, again.cancelled_at), (row.pk, "inscrita", None))

    def test_no_cancelling_once_the_event_started(self):
        self.client.post(self.url("register"))
        self.move(self.retiro, 0, 2)
        self.assertNotContains(self.client.get(self.url()), "Cancelar mi inscripción")
        for response in (self.client.get(self.url("cancel"), follow=True), self.client.post(self.url("cancel"), follow=True)):
            self.assertContains(response, "El evento ya empezó")
        self.assertEqual(EventRegistration.objects.get().status, "inscrita")

    def test_who_is_registered_keeps_seeing_an_event_that_was_unpublished(self):
        self.client.post(self.url("register"))
        self.set_page(published=False)
        self.assertContains(self.client.get(reverse("agenda:list")), "Retiro de jóvenes")
        self.assertContains(self.client.get(self.url()), "Cancelar mi inscripción")
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_too_many_attempts_wait(self):
        for _ in range(10):
            self.client.post(self.url("register", self.hidden))  # 404 before the counter
        self.set_page(registration="cerrada")
        for _ in range(10):
            self.client.post(self.url("register"))
        self.set_page(registration="abierta")
        response = self.client.post(self.url("register"), follow=True)
        self.assertContains(response, "Demasiados intentos.")
        self.assertFalse(EventRegistration.objects.exists())


class ServiceListTests(AgendaFixture):
    def test_upcoming_for_returns_what_the_person_may_see(self):
        later = self._future(self.church, "Conferencia de enero", "conferencia-enero", 40, 40)
        services.save_page(later, self.admin, published=True)
        sooner = self._future(self.church, "Servicio especial", "servicio-especial", 2, 2)
        services.save_page(sooner, self.admin, published=True, registration="abierta")
        ended = self._future(self.church, "Ya pasó", "ya-paso", -4, -2)
        services.save_page(ended, self.admin, published=True)
        registration = services.register(self.retiro, self.member, self.member_user)
        events = services.upcoming_for(self.member, self.church)
        self.assertEqual([event.slug for event in events], ["servicio-especial", "retiro-jovenes", "conferencia-enero"])
        self.assertEqual([event.my_registration for event in events], [None, registration, None])
        self.assertEqual([event.page.published for event in events], [True, True, True])
        self.assertEqual([event.state for event in events], ["abierta", "inscrita", "sin_inscripcion"])
        # Another day, another answer; another church, nothing of this one.
        self.assertEqual(services.upcoming_for(self.member, self.church, today=self.today + timedelta(days=30)), [later])
        self.assertEqual([event.slug for event in services.upcoming_for(self.member_b, self.church_b)], ["vigilia-b"])

    def test_my_registrations_are_active_and_not_ended(self):
        kept = services.register(self.retiro, self.member, self.member_user)
        other = self._future(self.church, "Otro", "otro-evento", 3, 3)
        services.save_page(other, self.admin, published=True, registration="abierta")
        services.cancel(services.register(other, self.member, self.member_user), self.member_user)
        services.register(self.retiro, self.leader, self.leader_user)
        rows = services.my_registrations(self.member)
        self.assertEqual(rows, [kept])
        with self.assertNumQueries(0):
            self.assertEqual(rows[0].event.name, "Retiro de jóvenes")
        self.assertEqual(services.my_registrations(self.member, today=self.today + timedelta(days=13)), [])
        self.assertEqual(services.my_registrations(self.member, today=self.today + timedelta(days=12)), [kept])


class AdministrationTests(AgendaFixture):
    def test_a_member_cannot_open_the_administration(self):
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(reverse("agenda:admin_list")).status_code, 403)
        for name in ("page_edit", "registrations"):
            self.assertEqual(self.client.get(self.url(name)).status_code, 403)
            self.assertEqual(self.client.post(self.url(name), {"published": "on", "vino": [self.member.pk]}).status_code, 403)
        self.client.force_login(self.leader_user)  # guiding a group is not administering the church
        self.assertEqual(self.client.get(self.url("registrations")).status_code, 403)

    def test_the_list_says_what_is_published_and_how_many_signed_up(self):
        services.register(self.retiro, self.member, self.member_user)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("agenda:admin_list"))
        self.assertContains(page, "<h1", count=1)
        for name in ("Retiro de jóvenes", "Conferencia sin publicar", "Evento sin página", self.event.name):
            self.assertContains(page, name)
        self.assertNotContains(page, "Vigilia de la iglesia B")
        self.assertContains(page, "Publicada", count=1)
        self.assertContains(page, "Sin publicar", count=4)  # the fixture's second camp too
        self.assertContains(page, "1 persona inscrita")
        self.assertContains(page, self.url("page_edit"))
        self.assertContains(page, self.url("registrations"))
        self.assertContains(self.client.get(reverse("agenda:list")), "Administrar los eventos")
        self.assertContains(self.client.get(self.url(event=self.hidden)), "solo la administración de la iglesia la ve")

    def test_publishing_an_event(self):
        self.client.force_login(self.admin)
        form = self.client.get(self.url("page_edit", self.no_page))
        self.assertContains(form, "Publicar para la iglesia")
        self.assertContains(form, "Guardar la página del evento", count=1)
        response = self.client.post(
            self.url("page_edit", self.no_page),
            {"published": "on", "description": "Ven.", "starts_at": "18:00", "registration": "abierta", "capacity": "20",
             "registration_closes_on": (self.today + timedelta(days=4)).isoformat()},
            follow=True,
        )
        self.assertContains(response, "Publicaste Evento sin página para la iglesia.")
        page = EventPage.objects.get(event=self.no_page)
        self.assertEqual((page.published, page.capacity, page.starts_at, page.created_by), (True, 20, time(18, 0), self.admin))
        audit = ChurchAuditEvent.objects.filter(action="evento_publicado").latest("id")
        self.assertEqual(audit.metadata, {"event_id": self.no_page.pk})
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(self.url(event=self.no_page)), "Quedan 20 lugares")

    def test_the_form_refuses_what_makes_no_sense(self):
        self.client.force_login(self.admin)
        Event.objects.filter(pk=self.no_page.pk).update(starts_on=None, ends_on=None)
        response = self.client.post(self.url("page_edit", self.no_page), {"published": "on", "registration": "cerrada"})
        self.assertContains(response, "Este evento no tiene fecha de inicio.")
        response = self.client.post(
            self.url("page_edit"),
            {"registration": "abierta", "capacity": "0", "description": "x" * 801,
             "registration_closes_on": (self.today + timedelta(days=30)).isoformat()},
        )
        self.assertContains(response, "Escribe cuántos lugares hay")
        self.assertContains(response, "no puede ser después de que termine el evento")
        self.assertContains(response, "800")
        self.assertFalse(EventPage.objects.filter(event=self.no_page).exists())
        self.page.refresh_from_db()
        self.assertTrue(self.page.published)
        self.no_page.refresh_from_db()
        with self.assertRaises(services.AgendaError):
            services.save_page(self.no_page, self.admin, published=True)
        self.assertFalse(EventPage.objects.filter(event=self.no_page).exists())

    def test_who_signed_up_with_how_to_reach_them(self):
        services.register(self.retiro, self.member, self.member_user)
        self.client.force_login(self.admin)
        page = self.client.get(self.url("registrations"))
        self.assertContains(page, "<h1", count=1)
        self.assertContains(page, "Diego Hernández Cruz")
        self.assertContains(page, 'href="tel:')
        self.assertContains(page, 'href="mailto:diego@prueba.mx"')
        self.assertContains(page, "La asistencia se registra desde el primer día del evento")
        self.assertNotContains(page, "Guardar la asistencia")
        self.client.post(self.url("registrations"), {"vino": [self.member.pk]})
        self.assertIsNone(EventRegistration.objects.get().attended)

    def test_attendance_is_saved_and_corrected_only_for_who_signed_up(self):
        for person, user in ((self.member, self.member_user), (self.leader, self.leader_user), (self.asked, self.asked_user)):
            services.register(self.retiro, person, user)
        services.cancel(EventRegistration.objects.get(person=self.asked), self.asked_user)
        ids = {row.person_id: row.pk for row in EventRegistration.objects.all()}  # the form sends registration ids
        other = services.register(self.retiro_b, self.member_b, self.member_b_user)
        self.move(self.retiro, 0, 2)
        self.client.force_login(self.admin)
        page = self.client.get(self.url("registrations"))
        self.assertContains(page, "Guardar la asistencia", count=1)
        self.assertNotContains(page, "checked")
        response = self.client.post(
            self.url("registrations"),
            {"vino": [ids[self.member.pk], ids[self.asked.pk], other.pk, 999999, "x"]}, follow=True,
        )
        self.assertContains(response, "Vino 1 de 2 inscritos.")
        self.assertIsNone(EventRegistration.objects.get(pk=other.pk).attended)  # another church's row is not touched
        rows = {row.person_id: row for row in EventRegistration.objects.filter(event=self.retiro)}
        self.assertEqual(len(rows), 3)
        self.assertIs(rows[self.member.pk].attended, True)
        self.assertEqual(rows[self.member.pk].checked_in_by, self.admin)
        self.assertIsNotNone(rows[self.member.pk].checked_in_at)
        self.assertIs(rows[self.leader.pk].attended, False)
        self.assertIsNone(rows[self.asked.pk].attended)  # cancelled: not on the list
        # Corrected: saving again replaces the answer.
        response = self.client.post(self.url("registrations"), {"vino": [ids[self.member.pk], ids[self.leader.pk]]}, follow=True)
        self.assertContains(response, "Vinieron 2 de 2 inscritos.")
        self.assertContains(response, "Ya guardaste la asistencia.")
        self.client.post(self.url("registrations"), {"vino": [ids[self.leader.pk]]})
        rows = {row.person_id: row for row in EventRegistration.objects.filter(event=self.retiro)}
        self.assertIs(rows[self.member.pk].attended, False)
        self.assertIsNone(rows[self.member.pk].checked_in_at)
        self.assertIs(rows[self.leader.pk].attended, True)

    def test_audit_rows_carry_ids_and_counts_and_no_contact_data(self):
        registration = services.register(self.retiro, self.member, self.member_user)
        services.cancel(registration, self.member_user)
        registration = services.register(self.retiro, self.member, self.member_user)
        self.move(self.retiro, 0, 2)
        services.record_attendance(self.retiro, [registration.pk], self.admin)
        rows = list(ChurchAuditEvent.objects.filter(action__startswith="evento_", church=self.church))
        self.assertEqual(
            sorted({row.action for row in rows}),
            ["evento_asistencia", "evento_inscripcion", "evento_inscripcion_cancelada", "evento_pagina", "evento_publicado"],
        )
        for row in rows:
            text = f"{row.summary} {row.metadata}"
            for secret in ("55 1111 2222", "5511112222", "diego@prueba.mx", "@"):
                self.assertNotIn(secret, text)
            self.assertLessEqual(set(row.metadata), {"event_id", "person_id", "registration_id"})
        attendance = next(row for row in rows if row.action == "evento_asistencia")
        self.assertIn("1 de 1", attendance.summary)
        self.assertNotIn("Diego", attendance.summary)
        self.assertEqual(attendance.metadata, {"event_id": self.retiro.pk})


class IsolationTests(AgendaFixture):
    def test_an_event_of_another_church_is_a_404_on_every_url(self):
        services.register(self.retiro_b, self.member_b, self.member_b_user)
        for user in (self.member_user, self.admin):
            self.client.force_login(user)
            for name in URLS:
                # A member is refused the administration's pages before any event is looked up (403 whatever
                # the slug), and "inscribirme" answers 405 to a GET whatever the slug: neither says the event exists.
                denied = 403 if user == self.member_user and name in ("page_edit", "registrations") else 404
                got = self.client.get(self.url(name, self.retiro_b)).status_code
                self.assertEqual(got, 405 if name == "register" else denied, (user, name))
                response = self.client.post(self.url(name, self.retiro_b), {"published": "", "vino": [self.member_b.pk]})
                self.assertEqual(response.status_code, denied, (user, name))
            self.assertNotContains(self.client.get(reverse("agenda:list")), "Vigilia de la iglesia B")
        self.assertEqual(EventRegistration.objects.filter(event=self.retiro_b).count(), 1)
        self.assertTrue(EventPage.objects.get(event=self.retiro_b).published)
        self.assertIsNone(EventRegistration.objects.get(event=self.retiro_b).attended)
        # And the other way round.
        self.client.force_login(self.admin_b)
        for name in URLS:
            self.assertEqual(self.client.post(self.url(name)).status_code, 404, name)

    def test_with_the_module_off_everything_is_a_404(self):
        flags.set_enabled(self.church, "community", False)
        for user in (self.member_user, self.admin):
            self.client.force_login(user)
            for path in [reverse("agenda:list"), reverse("agenda:admin_list")] + [self.url(name) for name in URLS]:
                self.assertEqual(self.client.get(path).status_code, 404, path)
                self.assertEqual(self.client.post(path).status_code, 404, path)
        self.assertFalse(EventRegistration.objects.exists())

    def test_signed_out_goes_to_sign_in(self):
        self.assertEqual(self.client.get(reverse("agenda:list")).status_code, 302)
        self.assertEqual(self.client.post(self.url("register")).status_code, 302)
        self.assertFalse(EventRegistration.objects.exists())
