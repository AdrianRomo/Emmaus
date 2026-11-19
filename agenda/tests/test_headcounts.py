"""Issue #93 (wave 12), on synthetic data: "¿Cuántos vinimos?", the headcount of a gathering. Numbers typed by the
administration, no names, apart from the named attendance, a small trend per series, and a children's number
under the minimum reporting size never shown exactly. Each test names the defect it would catch."""

from datetime import time, timedelta
from types import SimpleNamespace

from django.db import IntegrityError, transaction
from django.urls import reverse

from agenda import headcounts, series, services
from agenda.models import EventHeadcount, EventRegistration, SeriesOccurrence
from agenda.tests.test_event_extras import ExtrasFixture
from core.models import Role
from events.models import Event
from core.services import assign_role
from events.tests.base import make_user
from overview import services as overview
from people import flags
from people.models import ChurchAuditEvent
from policies.models import ChurchPolicy


def count(total, adults=None, children=None):
    return SimpleNamespace(total=total, adults=adults, children=children)


class ToldTests(ExtrasFixture):
    def test_a_small_number_of_children_is_never_exact_and_takes_the_adults_with_it(self):
        """Defect: "3 niñas y niños" on a pastor's screen, or "81 adultas de 84" giving the 3 by subtraction."""
        self.assertEqual(headcounts.told(count(84, 81, 3), 5),
                         {"total": 84, "adults": None, "children": None, "children_words": "Menos de 5"})
        self.assertEqual(headcounts.told(count(84, None, 0), 5)["children_words"], "Menos de 5")  # none is not told from few
        self.assertEqual(headcounts.told(count(84, 70, 14), 5),
                         {"total": 84, "adults": 70, "children": 14, "children_words": ""})
        # Only the adults typed: what the total leaves for the children is still small.
        self.assertEqual(headcounts.told(count(84, 81), 5),
                         {"total": 84, "adults": None, "children": None, "children_words": "Menos de 5"})
        self.assertEqual(headcounts.told(count(84), 5), {"total": 84, "adults": None, "children": None, "children_words": ""})
        # The adults are the small part: nothing about the parts, and no "menos de" about children who are many.
        self.assertEqual(headcounts.told(count(20, 2, 18), 5), {"total": 20, "adults": None, "children": None, "children_words": ""})

    def test_the_churchs_own_minimum_applies(self):
        self.assertEqual(headcounts.told(count(50, 42, 8), 10)["children_words"], "Menos de 10")


class RecordTests(ExtrasFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_the_administration_types_and_corrects_the_count_from_the_first_day(self):
        url = self.url("headcount")
        early = self.client.get(url)
        self.assertContains(early, "El conteo se registra desde el primer día del evento")
        self.assertNotContains(early, 'name="total"')
        self.client.post(url, {"total": "84"})
        self.assertFalse(EventHeadcount.objects.exists())
        with self.assertRaises(services.AgendaError):
            headcounts.record(self.retiro, self.admin, total=84)
        self.start()
        page = self.client.get(url)
        self.assertContains(page, "<h1", count=1)
        self.assertContains(page, "btn--primary", count=1)
        self.assertContains(self.client.post(url, {"total": "84", "adults": "70", "children": "10"}), "suman 80")
        self.assertContains(self.client.post(url, {"total": "10", "children": "11"}), "No puede ser más que el total.")
        self.assertContains(self.client.post(url, {"total": "muchos"}), "Escribe un número, por ejemplo 84.")
        self.assertFalse(EventHeadcount.objects.exists())
        self.assertEqual(self.client.post(url, {"total": "84", "adults": "81", "children": "3"}).status_code, 302)
        row = EventHeadcount.objects.get()
        self.assertEqual((row.total, row.adults, row.children, row.recorded_by), (84, 81, 3, self.admin))
        self.assertContains(self.client.get(url), 'value="3"')  # shown back to who typed it, to correct it
        self.client.post(url, {"total": "90"})
        row.refresh_from_db()
        self.assertEqual((row.total, row.adults, row.children), (90, None, None))
        self.assertEqual(EventHeadcount.objects.count(), 1)
        audit = ChurchAuditEvent.objects.filter(action="evento_conteo")
        self.assertEqual(audit.count(), 2)
        for event in audit:  # that a count was written, never the numbers
            self.assertEqual(event.metadata, {"event_id": self.retiro.pk})
            self.assertNotIn("84", event.summary)
            self.assertNotIn("90", event.summary)

    def test_it_is_apart_from_the_named_attendance(self):
        """Defect: the count derived from, or changing, who was marked as present."""
        self.start()
        registration = services.register(self.retiro, self.member, self.member_user)
        headcounts.record(self.retiro, self.admin, total=50)
        registration.refresh_from_db()
        self.assertIsNone(registration.attended)
        services.record_attendance(self.retiro, [registration.pk], self.admin)
        self.assertEqual(EventHeadcount.objects.get().total, 50)
        self.assertEqual(EventRegistration.objects.filter(attended=True).count(), 1)

    def test_the_database_refuses_parts_larger_than_the_total(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventHeadcount.objects.create(event=self.retiro, total=5, children=6)

    def test_a_stale_form_corrects_the_one_row_instead_of_writing_a_second(self):
        """Defect: two administrators typing at once leaving two counts, or an error."""
        self.start()
        headcounts.record(self.retiro, self.admin, total=80)
        headcounts.record(self.retiro, self.admin, total=82, children=12)  # the second form, read before the first saved
        row = EventHeadcount.objects.get()
        self.assertEqual((row.total, row.children), (82, 12))

    def test_who_may_not_type_or_read_it(self):
        self.start()
        url = self.url("headcount")
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        for user, status in ((self.member_user, 403), (self.leader_user, 403), (self.admin_b, 404)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, status)
            self.assertEqual(self.client.post(url, {"total": "9"}).status_code, status)
        self.assertFalse(EventHeadcount.objects.exists())
        self.client.force_login(self.admin)
        strict = self.client_class(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        self.assertEqual(strict.post(url, {"total": "9"}).status_code, 403)
        flags.set_enabled(self.church, "community", False)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"total": "9"}).status_code, 404)
        self.assertFalse(EventHeadcount.objects.exists())


class TrendTests(ExtrasFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.pastor = make_user("pastora@prueba.mx", "Paula", "Pastora")
        assign_role(cls.pastor, cls.church, Role.PASTOR)
        cls.series = series.create_series(
            cls.church, cls.admin, name="Reunión general", weekdays=str(cls.today.weekday()), interval_weeks=1,
            starts_at=time(11, 0), duration_minutes=90, location="Templo", time_zone="America/Mexico_City",
            starts_on=cls.today + timedelta(days=1), published=True,
        )
        # Fourteen dates that already happened, as the scheduler would have written them week after week.
        cls.dates = []
        for weeks in range(14, 0, -1):
            day = cls.today - timedelta(weeks=weeks)
            event = Event.objects.create(church=cls.church, name="Reunión general", slug=f"reunion-general-{weeks}",
                                         kind=Event.Kind.OTHER, starts_on=day, ends_on=day, camp_tools=False)
            cls.dates.append(SeriesOccurrence.objects.create(series=cls.series, day=day, event=event))
        for number, row in enumerate(cls.dates):
            headcounts.record(row.event, cls.admin, total=60 + number, adults=57 + number, children=3)
        cls.big = cls.dates[-1]
        headcounts.record(cls.big.event, cls.admin, total=120, adults=100, children=20)

    def test_the_series_shows_its_last_twelve_dates_oldest_first(self):
        self.assertGreater(len(self.dates), headcounts.TREND_DATES)
        trend = headcounts.series_trend(self.series, 5)
        self.assertEqual(len(trend), headcounts.TREND_DATES)
        self.assertEqual([item["day"] for item in trend], [row.event.starts_on for row in self.dates[-headcounts.TREND_DATES:]])
        self.assertEqual((trend[-1]["total"], trend[-1]["children"], trend[-1]["share"]), (120, 20, 100))
        self.assertEqual((trend[0]["children"], trend[0]["adults"], trend[0]["children_words"]), (None, None, "Menos de 5"))
        self.client.force_login(self.admin)
        page = self.client.get(reverse("agenda:series_admin", args=[self.series.pk]))
        self.assertContains(page, '<h2 id="vinimos" class="heading">¿Cuántos vinimos?</h2>', html=True)
        self.assertContains(page, "Menos de 5")
        self.assertContains(page, "Corregir el conteo")

    def test_a_children_count_below_the_minimum_is_never_exact_for_a_pastor(self):
        """Defect: Panorama saying "3" for the children of a small church, or the adults that give it away."""
        self.client.force_login(self.pastor)
        page = self.client.get(reverse("overview:panorama"))
        self.assertContains(page, '<h2 id="p-vinimos" class="heading">¿Cuántos vinimos?</h2>', html=True)
        self.assertContains(page, "Reunión general")
        data = overview.build(self.church, None)
        dates = data["headcount_series"][0]["dates"]
        small = [item for item in dates if item["total"] != 120]
        self.assertEqual(len(small), headcounts.TREND_DATES - 1)
        for item in small:
            self.assertEqual((item["children"], item["adults"], item["children_words"]), (None, None, "Menos de 5"))
        table = page.content.decode().split('id="p-vinimos"')[1].split("</section>")[0]
        self.assertEqual(table.count("Menos de 5"), headcounts.TREND_DATES - 1 + 1)  # each small date, and the note
        for number in range(len(self.dates) - 1):
            self.assertNotIn(f"<td>{57 + number}</td>", table)  # the adults that would give the 3 away
        self.assertNotIn("<td>3</td>", table)
        self.assertIn("<td>20</td>", table)
        # The church raises its minimum: 20 children are still exact, 8 would not be.
        ChurchPolicy.objects.update_or_create(church=self.church, defaults={"min_reporting_size": 10})
        self.assertEqual(overview.build(self.church, None)["headcount_series"][0]["dates"][-1]["children"], 20)

    def test_panorama_and_the_series_are_scoped_to_the_church(self):
        self.assertEqual(headcounts.church_trends(self.church_b, 5), [])
        self.client.force_login(self.admin_b)
        self.assertNotContains(self.client.get(reverse("overview:panorama")), "Reunión general")
        self.assertEqual(self.client.get(reverse("agenda:series_admin", args=[self.series.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("agenda:headcount", args=[self.big.event.slug])).status_code, 404)
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(reverse("overview:panorama")).status_code, 404)

    def test_the_trend_costs_one_query_whatever_the_number_of_dates(self):
        with self.assertNumQueries(1):
            headcounts.church_trends(self.church, 5)

    def test_a_simple_event_has_a_count_and_no_trend(self):
        other = Event.objects.create(church=self.church, name="Cena", slug="cena-conteo", kind=Event.Kind.OTHER,
                                     starts_on=self.today, ends_on=self.today, camp_tools=False)
        headcounts.record(other, self.admin, total=30)
        self.assertEqual(len(headcounts.church_trends(self.church, 5)), 1)
