"""Issue #51: duplicate a simple event of the church from "Eventos de la iglesia". Its page (not published), its
frequent questions and its programme moved to the new day; never who signed up, the authorizations, the opinions or
the attendance. Each test names the defect it would catch."""

import re
from datetime import date, time, timedelta

from django.test import Client
from django.urls import reverse

from agenda import duplicate as dup
from agenda import services
from agenda.models import EventFaq, EventPage, EventRegistration, EventScheduleItem, GuardianConsent
from events.models import AuditEvent, Event, Sede
from events.services import duplicate as camp_dup
from feedback.models import EventFeedback, FeedbackGiven
from people.models import ChurchAuditEvent

from .test_agenda import AgendaFixture


def relations():
    return {rel.get_accessor_name(): rel for rel in Event._meta.related_objects}


def count(event, accessor) -> int:
    rel = relations()[accessor]
    return rel.related_model._default_manager.filter(**{rel.field.name: event}).count()


class DuplicateSimpleFixture(AgendaFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.first = cls.today - timedelta(days=30)
        cls.noche = Event.objects.create(
            church=cls.church, name="Noche de alabanza 2026", slug="noche-2026", kind=Event.Kind.CONFERENCE,
            location="Templo central", time_zone="America/Cancun", camp_tools=False, join_by_name=False,
            starts_on=cls.first, ends_on=cls.first + timedelta(days=1),
        )
        cls.sede = Sede.objects.create(church=cls.church, name="Satélite", slug="satelite")
        cls.noche.sedes.add(cls.sede)
        EventPage.objects.create(
            event=cls.noche, published=True, description="Una noche para cantar juntos.", starts_at=time(19, 0),
            registration="abierta", capacity=80, registration_closes_on=cls.first - timedelta(days=2),
            requires_guardian_consent=True,
        )
        EventFaq.objects.create(event=cls.noche, question="¿Hay estacionamiento?", answer="Sí, atrás del templo.", position=1)
        EventFaq.objects.create(event=cls.noche, question="¿Puedo llevar niños?", answer="Sí.", position=2)
        item = lambda day, hour, title, **kw: EventScheduleItem.objects.create(  # noqa: E731
            event=cls.noche, day=cls.first + timedelta(days=day), starts_at=time(hour, 0), title=title, **kw
        )
        item(0, 19, "Bienvenida", place="Atrio")
        item(0, 20, "Alabanza", ends_at=time(21, 30))
        item(1, 10, "Desayuno")
        # People's data: none of it may reach a copy.
        registration = EventRegistration.objects.create(event=cls.noche, person=cls.member, attended=True)
        EventRegistration.objects.create(event=cls.noche, guest_first_name="Visita", guest_is_minor=True, status="pendiente")
        GuardianConsent.objects.create(registration=registration, guardian_name="Mamá", relationship="madre", method="papel")
        EventFeedback.objects.create(event=cls.noche, rating="bien", recommend="si", comment="Más sillas")
        FeedbackGiven.objects.create(event=cls.noche, token="b" * 64)

    def setUp(self):
        self.client.force_login(self.admin)

    def url(self, event=None):
        return reverse("agenda:duplicate", args=[(event or self.noche).slug])

    def post(self, **data):
        values = {"name": "Noche de alabanza 2027", "slug": "", "starts_on": "", "ends_on": "", **data}
        return self.client.post(self.url(), values)

    def copy(self, **data) -> Event:
        response = self.post(**data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["form"].errors)
        return Event.objects.get(name=data.get("name", "Noche de alabanza 2027"), church=self.church)


class WhatIsCopiedTests(DuplicateSimpleFixture):
    def test_every_model_that_points_at_an_event_has_a_decision(self):
        """Defect: a table added later copied or forgotten without anybody deciding (as for a camp)."""
        self.assertEqual(set(dup.RELATIONS), set(relations()))
        self.assertEqual(set(dup.RELATIONS), set(camp_dup.RELATIONS))

    def test_each_table_is_copied_on_purpose_or_empty_in_the_copy(self):
        new_day = self.today + timedelta(days=60)
        before = {accessor: count(self.noche, accessor) for accessor in dup.RELATIONS}
        for accessor in ("page", "faqs", "schedule_items", "registrations", "feedback", "feedback_given"):
            self.assertGreater(before[accessor], 0, accessor)
        copy = self.copy(starts_on=new_day.isoformat())
        expected = {"page": 1, "faqs": 2, "schedule_items": 3}
        for accessor, decision in dup.RELATIONS.items():
            if decision == camp_dup.NEVER:
                self.assertEqual(count(copy, accessor), 0, f"{accessor} must be empty in the copy")
            else:
                self.assertEqual(count(copy, accessor), expected[accessor], accessor)
        self.assertFalse(GuardianConsent.objects.filter(registration__event=copy).exists())
        self.assertFalse(AuditEvent.objects.filter(event=copy).exists())
        # the original, untouched
        self.assertEqual({accessor: count(self.noche, accessor) for accessor in dup.RELATIONS}, before)
        self.assertTrue(EventPage.objects.get(event=self.noche).published)

    def test_the_basics_and_the_type(self):
        """Decision 0015: the copy of a simple event is a simple event."""
        copy = self.copy(starts_on="2027-05-07")
        self.assertFalse(copy.camp_tools)
        self.assertFalse(copy.join_by_name)
        self.assertEqual((copy.kind, copy.location, copy.time_zone), ("conferencia", "Templo central", "America/Cancun"))
        self.assertEqual(copy.church, self.church)
        self.assertEqual(copy.created_by, self.admin)
        self.assertEqual(copy.slug, "noche-de-alabanza-2027")
        self.assertNotEqual(copy.join_code, self.noche.join_code)
        self.assertEqual(list(copy.sedes.all()), [self.sede])
        self.assertEqual(copy.status, Event.Status.ACTIVE)  # as every simple event: what hides it is its page
        for name in ("events:home", "events:admin_home", "events:duplicate"):
            self.assertEqual(self.client.get(reverse(name, args=[copy.slug])).status_code, 404, name)

    def test_it_lasts_as_long_as_the_original_unless_told(self):
        copy = self.copy(starts_on="2027-05-07")
        self.assertEqual((copy.starts_on, copy.ends_on), (date(2027, 5, 7), date(2027, 5, 8)))
        other = self.copy(name="Otra noche", starts_on="2027-05-07", ends_on="2027-05-09")
        self.assertEqual(other.ends_on, date(2027, 5, 9))

    def test_the_page_comes_unpublished(self):
        """Defect: last year's page, with last year's words, shown to the whole church at once."""
        copy = self.copy(starts_on="2027-05-07")
        page = EventPage.objects.get(event=copy)
        self.assertFalse(page.published)
        self.assertEqual(
            (page.description, page.starts_at, page.registration, page.capacity, page.requires_guardian_consent),
            ("Una noche para cantar juntos.", time(19, 0), "abierta", 80, True),
        )
        self.assertEqual(page.registration_closes_on, date(2027, 5, 5))  # two days before, as in the original
        self.assertEqual(page.created_by, self.admin)
        member = Client()  # not the administrator's browser: the "Listo" message waiting there names the event
        member.force_login(self.member_user)
        self.assertNotContains(member.get(reverse("agenda:list")), "Noche de alabanza 2027")
        self.assertEqual(member.get(reverse("agenda:detail", args=[copy.slug])).status_code, 404)

    def test_the_questions_come_in_their_order(self):
        copy = self.copy()
        self.assertEqual(
            list(copy.faqs.values_list("question", "answer", "position")),
            [("¿Hay estacionamiento?", "Sí, atrás del templo.", 1), ("¿Puedo llevar niños?", "Sí.", 2)],
        )

    def test_the_programme_moves_to_the_new_first_day(self):
        """Defect: the programme of the new event on last year's days."""
        copy = self.copy(starts_on="2027-05-07")
        self.assertEqual(
            list(copy.schedule_items.values_list("day", "starts_at", "ends_at", "title", "place")),
            [
                (date(2027, 5, 7), time(19, 0), None, "Bienvenida", "Atrio"),
                (date(2027, 5, 7), time(20, 0), time(21, 30), "Alabanza", ""),
                (date(2027, 5, 8), time(10, 0), None, "Desayuno", ""),
            ],
        )
        self.assertEqual(self.noche.schedule_items.filter(day=self.first).count(), 2)  # the original keeps its days

    def test_without_a_day_the_programme_is_not_copied_and_the_screen_says_so(self):
        page = self.client.get(self.url())
        self.assertContains(page, "Si no escribes el día, el programa no se copia.")
        self.assertContains(page, "Sin día, el programa no se copia: cada momento necesita un día.")
        response = self.post()
        copy = Event.objects.get(name="Noche de alabanza 2027")
        self.assertEqual((copy.starts_on, copy.ends_on), (None, None))
        self.assertEqual(copy.schedule_items.count(), 0)
        self.assertEqual(copy.faqs.count(), 2)
        self.assertIsNone(EventPage.objects.get(event=copy).registration_closes_on)
        self.assertContains(self.client.get(response["Location"]), "El programa no se copió porque el evento todavía no tiene día.")

    def test_with_a_day_nothing_is_said_about_the_programme(self):
        response = self.post(starts_on="2027-05-07")
        self.assertNotContains(self.client.get(response["Location"]), "El programa no se copió")

    def test_an_original_without_page_still_gets_one(self):
        """Every simple event has a page row (agenda.simple.create_simple_event); the copy too."""
        bare = Event.objects.create(church=self.church, name="Convivio", slug="convivio", camp_tools=False, starts_on=self.first)
        result = dup.duplicate_simple_event(bare, self.admin, name="Convivio 2", slug="convivio-2")
        self.assertFalse(EventPage.objects.get(event=result.event).published)

    def test_a_camp_is_not_duplicated_as_a_simple_event(self):
        """Decision 0015: a copy never changes the type."""
        self.assertTrue(self.retiro.camp_tools)
        self.assertEqual(self.client.get(self.url(self.retiro)).status_code, 404)
        self.assertEqual(self.client.post(self.url(self.retiro), {"name": "Retiro sencillo", "slug": ""}).status_code, 404)
        with self.assertRaises(ValueError):
            dup.duplicate_simple_event(self.retiro, self.admin, name="Retiro sencillo", slug="retiro-sencillo")
        self.assertFalse(Event.objects.filter(name="Retiro sencillo").exists())


class ScreenAndAccessTests(DuplicateSimpleFixture):
    def test_the_events_page_offers_it_for_simple_events_only(self):
        # «Duplicar» lives on the event's own page (the row of "Eventos de la iglesia" leads there)
        html = self.client.get(reverse("agenda:page_edit", args=[self.noche.slug])).content.decode()
        self.assertIn(f'href="{self.url()}">Duplicar<span class="visually-hidden"> Noche de alabanza 2026</span></a>', html)
        camp = self.client.get(reverse("agenda:page_edit", args=[self.retiro.slug])).content.decode()
        self.assertNotIn(self.url(self.retiro), camp)
        self.assertNotIn(self.url(self.retiro), self.client.get(reverse("agenda:admin_list")).content.decode())

    def test_the_page_says_what_is_copied_and_suggests_a_name(self):
        html = self.client.get(self.url()).content.decode()
        self.assertEqual(len(re.findall(r"<h1\b", html)), 1)
        self.assertRegex(html, r'<label class="label" for="id_name">Nombre del evento nuevo</label>')
        self.assertIn('value="Noche de alabanza 2027"', html)
        self.assertIn("Nunca se copian las inscripciones, las autorizaciones, la asistencia ni las opiniones.", html)
        self.assertIn("La página queda sin publicar", html)

    def test_it_lands_on_the_new_events_page(self):
        response = self.post(starts_on="2027-05-07")
        copy = Event.objects.get(slug="noche-de-alabanza-2027")
        self.assertRedirects(response, reverse("agenda:page_edit", args=[copy.slug]), fetch_redirect_response=False)
        self.assertContains(self.client.get(response["Location"]), "Noche de alabanza 2027 ya está creado, sin publicar.")

    def test_it_is_audited_in_the_churchs_log(self):
        before = ChurchAuditEvent.objects.count()
        copy = self.copy()
        row = ChurchAuditEvent.objects.order_by("-id").first()
        self.assertEqual(ChurchAuditEvent.objects.count(), before + 1)
        self.assertEqual((row.church, row.actor, row.action), (self.church, self.admin, "evento_sencillo_creado"))
        self.assertEqual(row.summary, "Creó el evento Noche de alabanza 2027 como copia de Noche de alabanza 2026")
        self.assertEqual(row.metadata, {"source": "duplicado", "event_id": self.noche.pk})
        self.assertEqual(row.object_id, str(copy.pk))

    def test_another_churchs_administrator_gets_404(self):
        """Defect: an administrator copying another church's event (its page, its questions) into their own."""
        events = Event.objects.count()
        self.client.force_login(self.admin_b)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.post().status_code, 404)
        self.assertEqual(Event.objects.count(), events)

    def test_a_member_cannot_duplicate(self):
        events = Event.objects.count()
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(Event.objects.count(), events)

    def test_the_name_is_required_and_the_days_make_sense(self):
        events = Event.objects.count()
        self.assertContains(self.post(name=""), "Escribe el nombre del evento nuevo.")
        self.assertContains(self.post(starts_on="2027-05-07", ends_on="2027-05-01"), "no puede ser anterior")
        self.assertEqual(Event.objects.count(), events)

    def test_all_or_nothing(self):
        from unittest import mock

        events = Event.objects.count()
        with mock.patch.object(dup, "record_church", side_effect=RuntimeError("falla")):
            with self.assertRaises(RuntimeError):
                dup.duplicate_simple_event(self.noche, self.admin, name="A medias", slug="a-medias")
        self.assertEqual(Event.objects.count(), events)

    def test_services_module_still_publishes_the_copy(self):
        """The copy is an ordinary simple event afterwards: publishing its page shows it to the church."""
        copy = self.copy(starts_on=(self.today + timedelta(days=40)).isoformat())
        services.save_page(copy, self.admin, published=True)
        member = Client()
        member.force_login(self.member_user)
        self.assertContains(member.get(reverse("agenda:list")), "Noche de alabanza 2027")
