"""Series (issue #93, first slice) on synthetic data: what the church does every week is a template, and each
date is an ordinary event written once, ahead of time."""

from datetime import UTC, date, datetime, time, timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from agenda import duplicate, series, services
from agenda.models import EventNotice, EventPage, EventRegistration, EventSeries, SeriesOccurrence
from agenda.simple import update_simple_event
from agenda.tests.test_agenda import AgendaFixture
from core import scheduler
from core.models import SchedulerHeartbeat
from events.models import Event
from people import flags
from people.models import ChurchAuditEvent

MONDAY = date(2026, 11, 16)  # the suite's "today" for the rule: a Monday, so the weeks are easy to count
SUNDAYS = [date(2026, 11, 22) + timedelta(weeks=n) for n in range(8)]


def make(church, actor=None, today=MONDAY, **fields) -> EventSeries:
    values = {"name": "Servicio dominical", "weekdays": "6", "starts_at": time(10, 0), "location": "Templo central",
              "starts_on": today, **fields}
    return series.create_series(church, actor, today=today, **values)


class SeriesFixture(AgendaFixture):
    def days(self, item, **filters):
        return list(SeriesOccurrence.objects.filter(series=item, **filters).order_by("day").values_list("day", flat=True))

    def date_of(self, item, day) -> SeriesOccurrence:
        return SeriesOccurrence.objects.select_related("event", "event__page").get(series=item, day=day)


class RuleTests(SeriesFixture):
    def test_a_weekly_series_writes_one_ordinary_event_per_week_for_the_window(self):
        item = make(self.church, self.admin)
        self.assertEqual(series.WINDOW_WEEKS, 8)
        self.assertEqual(self.days(item), SUNDAYS)
        for row in SeriesOccurrence.objects.filter(series=item).select_related("event", "event__page"):
            event, page = row.event, row.event.page
            self.assertEqual((event.church, event.name, event.location, event.time_zone), (self.church, "Servicio dominical", "Templo central", "America/Mexico_City"))
            self.assertEqual((event.starts_on, event.ends_on, event.status), (row.day, row.day, Event.Status.ACTIVE))
            self.assertFalse(event.camp_tools)  # a simple event: never the camp's screens
            self.assertFalse(event.join_by_name)
            self.assertEqual(event.slug, f"servicio-dominical-{row.day:%Y-%m-%d}")
            # The conservative start: not published, not public, no sign-up.
            self.assertEqual((page.starts_at, page.published, page.public, page.registration, page.capacity),
                             (time(10, 0), False, False, "cerrada", None))

    def test_days_of_the_week_and_every_n_weeks(self):
        twice = make(self.church, name="Oración", weekdays="1,3")
        self.assertEqual(self.days(twice)[:4], [date(2026, 11, 17), date(2026, 11, 19), date(2026, 11, 24), date(2026, 11, 26)])
        self.assertEqual(series.rule_words(twice), "cada martes y jueves")
        fortnight = make(self.church, name="Jóvenes", weekdays="5", interval_weeks=2)
        self.assertEqual(self.days(fortnight), [date(2026, 11, 21) + timedelta(weeks=2 * n) for n in range(4)])
        self.assertEqual(series.rule_words(fortnight), "cada 2 semanas, los sábados")
        self.assertEqual(series.rule_words(EventSeries(weekdays="6")), "cada domingo")

    def test_it_starts_and_ends_on_its_days(self):
        item = make(self.church, starts_on=date(2026, 12, 1), ends_on=date(2026, 12, 20))
        self.assertEqual(self.days(item), [date(2026, 12, 6), date(2026, 12, 13), date(2026, 12, 20)])

    def test_a_second_run_writes_nothing_and_the_window_rolls(self):
        item = make(self.church, self.admin)
        events = Event.objects.count()
        self.assertEqual(series.generate(item, today=MONDAY), 0)
        self.assertEqual(Event.objects.count(), events)
        self.assertEqual(series.generate(item, today=MONDAY + timedelta(days=7)), 1)
        self.assertEqual(self.days(item)[-1], SUNDAYS[-1] + timedelta(weeks=1))
        self.assertEqual(SeriesOccurrence.objects.filter(series=item).count(), 9)

    def test_two_passes_cannot_write_the_same_day_twice(self):
        """The constraint is the rule: a pass that did not see the day (a stale read) writes nothing."""
        item = make(self.church)
        row = self.date_of(item, SUNDAYS[0])
        events = Event.objects.count()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SeriesOccurrence.objects.create(series=item, day=SUNDAYS[0], event=self.retiro)
        with mock.patch.object(SeriesOccurrence.objects, "filter", wraps=SeriesOccurrence.objects.filter) as stale:
            stale.side_effect = lambda *args, **kwargs: (
                SeriesOccurrence.objects.none() if "day__in" in kwargs else SeriesOccurrence.objects.get_queryset().filter(*args, **kwargs))
            self.assertEqual(series.generate(item, today=MONDAY), 0)
        self.assertEqual(Event.objects.count(), events)  # the event of the refused day went with it
        self.assertEqual(self.date_of(item, SUNDAYS[0]).event_id, row.event_id)

    def test_an_address_taken_by_another_event_does_not_lose_the_day(self):
        Event.objects.create(church=self.church_b, name="Otro", slug=f"servicio-dominical-{SUNDAYS[0]:%Y-%m-%d}")
        item = make(self.church)
        self.assertEqual(self.date_of(item, SUNDAYS[0]).event.slug, f"servicio-dominical-{SUNDAYS[0]:%Y-%m-%d}-2")

    def test_a_date_takes_what_a_copy_takes_and_never_people(self):
        self.assertLessEqual(set(series.TEMPLATE_EVENT_FIELDS) - {"name"}, set(duplicate.EVENT_FIELDS))
        self.assertLessEqual(set(series.TEMPLATE_PAGE_FIELDS) - {"published", "public"}, set(duplicate.PAGE_FIELDS))
        self.assertEqual(duplicate.RELATIONS["series_occurrence"], "never")
        item = make(self.church, self.admin, registration="abierta", published=True)
        first = self.date_of(item, SUNDAYS[0]).event
        services.register(first, self.member, self.member_user)
        services.save_public_notice(first, self.admin, title="Hoy empezamos tarde", body="A las 10:30.")
        series.generate(item, today=MONDAY + timedelta(days=7))
        newest = self.date_of(item, SUNDAYS[-1] + timedelta(weeks=1)).event
        self.assertFalse(EventRegistration.objects.filter(event=newest).exists())
        self.assertFalse(EventNotice.objects.filter(event=newest).exists())
        self.assertEqual(EventRegistration.objects.filter(event__series_occurrence__series=item).count(), 1)

    def test_each_date_has_its_own_places(self):
        item = make(self.church, self.admin, registration="abierta", published=True, capacity=1)
        first, second = (self.date_of(item, day).event for day in SUNDAYS[:2])
        services.register(first, self.member, self.member_user)
        with self.assertRaises(services.AgendaError):
            services.register(first, self.leader, self.leader_user)
        self.assertTrue(services.register(second, self.leader, self.leader_user).is_active)

    def test_ending_stops_new_dates_and_leaves_the_ones_that_exist(self):
        item = make(self.church, self.admin, published=True)
        series.set_active(item, self.admin, False)
        later = MONDAY + timedelta(weeks=3)
        self.assertEqual(series.generate(item, today=later), 0)
        self.assertEqual(series.generate_due(today=later), {"series": 0, "created": 0, "failed": 0})
        self.assertEqual(self.days(item), SUNDAYS)
        self.assertTrue(all(EventPage.objects.filter(event__series_occurrence__series=item).values_list("published", flat=True)))
        series.set_active(item, self.admin, True, today=later)
        self.assertEqual(len(self.days(item)), 11)


class ChangeTests(SeriesFixture):
    def setUp(self):
        self.item = make(self.church, self.admin, registration="abierta", published=True)
        self.patcher = mock.patch("django.utils.timezone.localdate", return_value=MONDAY)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def fresh(self) -> EventSeries:
        return EventSeries.objects.get(pk=self.item.pk)

    def change(self, **fields):
        return series.update_series(self.fresh(), self.admin, today=MONDAY, **fields)

    def test_a_change_reaches_only_the_dates_nobody_touched_and_nobody_signed_up_for(self):
        signed, by_page, by_data, plain = (self.date_of(self.item, day) for day in SUNDAYS[:4])
        services.register(signed.event, self.member, self.member_user)
        services.save_page(by_page.event, self.admin, description="Hoy con bautizos.")
        update_simple_event(by_data.event, self.admin, location="Parque central")
        applied = self.change(location="Auditorio", starts_at=time(11, 30))
        self.assertEqual(applied.changed, 5)
        signed, by_page, by_data, plain = (self.date_of(self.item, day) for day in SUNDAYS[:4])
        self.assertEqual((plain.event.location, plain.event.page.starts_at), ("Auditorio", time(11, 30)))
        # Somebody is signed up: never moved without a person deciding it, and shown to the administration.
        self.assertEqual((signed.event.location, signed.event.page.starts_at), ("Templo central", time(10, 0)))
        self.assertEqual([row.pk for row in series.to_review(self.fresh(), MONDAY)], [signed.pk])
        self.assertIn(signed.pk, [row.pk for row in applied.kept if row.has_people])
        # Changed by hand: what the person wrote stays.
        self.assertEqual((by_page.event.page.description, by_page.event.page.starts_at), ("Hoy con bautizos.", time(10, 0)))
        self.assertEqual(by_data.event.location, "Parque central")
        self.assertIsNotNone(by_page.edited_at)
        self.assertIsNotNone(by_data.edited_at)

    def test_a_date_that_passed_is_never_changed(self):
        with mock.patch("django.utils.timezone.localdate", return_value=MONDAY + timedelta(weeks=2)):
            series.update_series(self.item, self.admin, today=MONDAY + timedelta(weeks=2), location="Auditorio")
        self.assertEqual(self.date_of(self.item, SUNDAYS[1]).event.location, "Templo central")
        self.assertEqual(self.date_of(self.item, SUNDAYS[2]).event.location, "Auditorio")

    def test_another_day_of_the_week_retires_the_untouched_dates_and_keeps_the_ones_with_people(self):
        signed = self.date_of(self.item, SUNDAYS[0])
        services.register(signed.event, self.member, self.member_user)
        applied = self.change(weekdays="5")
        self.assertEqual(applied.retired, 7)
        self.assertEqual(self.days(self.item, status="fuera"), SUNDAYS[1:])
        self.assertFalse(EventPage.objects.filter(event__series_occurrence__status="fuera", published=True).exists())
        self.assertEqual(self.days(self.item, day__week_day=7, status="activa"),
                         [date(2026, 11, 21) + timedelta(weeks=n) for n in range(8)])
        kept = self.date_of(self.item, SUNDAYS[0])
        self.assertEqual((kept.status, kept.event.page.published, kept.event.starts_on), ("activa", True, SUNDAYS[0]))
        review = series.to_review(self.fresh(), MONDAY)
        self.assertEqual([(row.pk, row.off_rule) for row in review], [(kept.pk, True)])
        # Back to Sundays: the retired dates come back, none is written twice.
        self.change(weekdays="6")
        self.assertEqual(self.days(self.item, status="activa", day__week_day=1), SUNDAYS)
        self.assertEqual(SeriesOccurrence.objects.filter(series=self.item).count(), 16)
        self.assertTrue(self.date_of(self.item, SUNDAYS[3]).event.page.published)

    def test_this_day_there_is_none_keeps_the_row_and_touches_nobody(self):
        row = self.date_of(self.item, SUNDAYS[1])
        registration = services.register(row.event, self.member, self.member_user)
        series.call_off(row, self.admin)
        row = self.date_of(self.item, SUNDAYS[1])
        self.assertEqual((row.status, row.cancelled_by, row.event.page.published), ("cancelada", self.admin, False))
        registration.refresh_from_db()
        self.assertEqual(registration.status, "inscrita")
        # Not written again, not published again by a change of the series, not open to a new sign-up.
        EventSeries.objects.filter(pk=self.item.pk).update(generated_through=None)
        self.assertEqual(series.generate(self.item, today=MONDAY), 0)
        self.change(location="Auditorio")
        row = self.date_of(self.item, SUNDAYS[1])
        self.assertEqual((row.status, row.event.page.published, row.event.location), ("cancelada", False, "Templo central"))
        with self.assertRaises(services.AgendaError):
            services.register(row.event, self.leader, self.leader_user)
        series.restore(row, self.admin)
        row = self.date_of(self.item, SUNDAYS[1])
        # On again, but somebody is signed up: it waits, not published, for the administration to review it.
        self.assertEqual((row.status, row.cancelled_at, row.event.page.published), ("activa", None, False))
        self.assertEqual([(found.pk, found.why) for found in series.to_review(self.fresh(), MONDAY)], [(row.pk, "sin_publicar")])

    def test_a_stale_copy_never_undoes_what_somebody_else_did(self):
        stale_row = self.date_of(self.item, SUNDAYS[1])
        stale_series = EventSeries.objects.get(pk=self.item.pk)
        series.call_off(self.date_of(self.item, SUNDAYS[1]), self.admin)
        series.set_active(self.item, self.admin, False)
        # The copies read before: the day is still called off once, the series is still ended.
        series.call_off(stale_row, self.admin)
        self.assertEqual(ChurchAuditEvent.objects.filter(action="serie_fecha").count(), 1)
        self.assertIsNone(stale_row.cancelled_at)
        with self.assertRaises(series.SeriesEnded):  # the copy says it is active; the row says it ended
            series.update_series(stale_series, self.admin, today=MONDAY, location="Auditorio")
        self.assertFalse(EventSeries.objects.get(pk=self.item.pk).active)
        self.assertFalse(Event.objects.filter(series_occurrence__series=self.item, location="Auditorio").exists())
        self.assertEqual(self.date_of(self.item, SUNDAYS[1]).status, "cancelada")
        series.restore(stale_row, self.admin)
        series.restore(stale_row, self.admin)
        self.assertEqual(ChurchAuditEvent.objects.filter(action="serie_fecha").count(), 2)

    def test_the_audit_carries_numbers_and_fixed_words_never_what_somebody_typed(self):
        self.change(name="Nombre <b>escrito</b>")
        series.call_off(self.date_of(self.item, SUNDAYS[0]), self.admin)
        rows = ChurchAuditEvent.objects.filter(action__startswith="serie_")
        self.assertEqual(set(rows.values_list("action", flat=True)), {"serie_creada", "serie_fechas", "serie_modificada", "serie_fecha"})
        for row in rows:
            self.assertNotIn("escrito", row.summary + str(row.metadata))
            self.assertNotIn("Servicio", row.summary + str(row.metadata))


class ScreenFixture(SeriesFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # From tomorrow on: whether today's hour already passed must not change what these tests see.
        tomorrow = cls.today + timedelta(days=1)
        cls.item = series.create_series(cls.church, cls.admin, name="Servicio dominical", weekdays="6", starts_at=time(10, 0),
                                        location="Templo central", starts_on=tomorrow, published=True)
        cls.item_b = series.create_series(cls.church_b, cls.admin_b, name="Culto de la iglesia B", weekdays="6", starts_at=time(9, 0),
                                          starts_on=tomorrow, published=True)
        cls.rows = list(SeriesOccurrence.objects.filter(series=cls.item).select_related("event").order_by("day"))

    def admin_urls(self, item=None, row=None):
        item, row = item or self.item, row or self.rows[1]
        gets = [reverse("agenda:series_create"), reverse("agenda:series_admin", args=[item.pk]), reverse("agenda:series_edit", args=[item.pk])]
        posts = [reverse("agenda:series_end", args=[item.pk]), reverse("agenda:series_resume", args=[item.pk]),
                 reverse("agenda:series_call_off", args=[item.pk, row.pk]), reverse("agenda:series_restore", args=[item.pk, row.pk])]
        return gets, posts

    def form(self, **changes) -> dict:
        data = {"name": "Reunión de jóvenes", "weekdays": ["4"], "interval_weeks": "1", "starts_at": "19:30", "duration_minutes": "120",
                "location": "Salón 2", "time_zone": "America/Mexico_City", "starts_on": self.today.isoformat(), "ends_on": "",
                "description": "Para jóvenes.", "registration": "cerrada", "capacity": ""}
        return {**data, **changes}

    def member_url(self, item=None):
        return reverse("agenda:series", args=[(item or self.item).pk])

    def unchanged(self):
        return (list(EventSeries.objects.order_by("pk").values_list("name", "active")),
                list(SeriesOccurrence.objects.order_by("pk").values_list("status", flat=True)))


class DenialTests(ScreenFixture):
    def every(self, expected, client=None):
        client = client or self.client
        before = self.unchanged()
        gets, posts = self.admin_urls()
        for url in gets:
            self.assertEqual(client.get(url).status_code, expected, url)
        for url in [*gets[:1], *gets[2:], *posts]:
            self.assertEqual(client.post(url, self.form()).status_code, expected, url)
        self.assertEqual(self.unchanged(), before)

    def test_signed_out_goes_to_the_way_in(self):
        self.every(302)
        self.assertIn(reverse("login"), self.client.get(self.admin_urls()[0][1])["Location"])
        self.assertEqual(self.client.get(self.member_url()).status_code, 302)

    def test_a_member_and_a_leader_do_not_administer(self):
        for user in (self.member_user, self.leader_user):
            self.client.force_login(user)
            self.every(403)

    def test_the_administration_of_another_church_gets_404_by_guessed_numbers(self):
        self.client.force_login(self.admin_b)
        before = self.unchanged()
        gets, posts = self.admin_urls()
        for url in gets[1:]:
            self.assertEqual(self.client.get(url).status_code, 404, url)
        for url in [gets[2], *posts]:
            self.assertEqual(self.client.post(url, self.form()).status_code, 404, url)
        self.assertEqual(self.unchanged(), before)
        self.assertEqual(self.client.get(self.member_url()).status_code, 404)

    def test_a_date_of_another_series_is_not_reached_through_ones_own(self):
        self.client.force_login(self.admin_b)
        foreign = self.rows[1]
        for name in ("series_call_off", "series_restore"):
            self.assertEqual(self.client.post(reverse(f"agenda:{name}", args=[self.item_b.pk, foreign.pk])).status_code, 404)
        self.assertEqual(SeriesOccurrence.objects.get(pk=foreign.pk).status, "activa")

    def test_with_the_module_off_everything_is_404(self):
        flags.set_enabled(self.church, "community", False)
        for user in (self.admin, self.member_user):
            self.client.force_login(user)
            self.every(404)
            self.assertEqual(self.client.get(self.member_url()).status_code, 404)

    def test_the_writes_are_posts_with_the_token(self):
        self.client.force_login(self.admin)
        _gets, posts = self.admin_urls()
        for url in posts:
            self.assertEqual(self.client.get(url).status_code, 405, url)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        before = self.unchanged()
        for url in [reverse("agenda:series_create"), reverse("agenda:series_edit", args=[self.item.pk]), *posts]:
            self.assertEqual(strict.post(url, self.form()).status_code, 403, url)
        self.assertEqual(self.unchanged(), before)


class AdministrationScreenTests(ScreenFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_create_writes_the_series_and_its_dates(self):
        page = self.client.get(reverse("agenda:series_create"))
        self.assertContains(page, "<h1", count=1)
        self.assertContains(page, "Qué días de la semana")
        response = self.client.post(reverse("agenda:series_create"), self.form(), follow=True)
        created = EventSeries.objects.get(name="Reunión de jóvenes")
        self.assertRedirects(response, reverse("agenda:series_admin", args=[created.pk]))
        self.assertEqual((created.church, created.weekdays, created.published, created.public, created.registration),
                         (self.church, "4", False, False, "cerrada"))
        self.assertIn(SeriesOccurrence.objects.filter(series=created).count(), (8, 9))
        self.assertContains(response, "Cada viernes, a las 19:30, 120 minutos")
        # Each date, and the sentence that says what the series itself does.
        self.assertContains(response, "Sin publicar", count=SeriesOccurrence.objects.filter(series=created).count() + 1)

    def test_the_form_refuses_what_makes_no_series(self):
        bad = self.form(weekdays=[], starts_at="25:00", duration_minutes="5", ends_on=(self.today - timedelta(days=1)).isoformat(),
                        capacity="0", time_zone="Marte/Olimpo")
        response = self.client.post(reverse("agenda:series_create"), bad)
        self.assertEqual(response.status_code, 200)
        for text in ("Elige al menos un día de la semana.", "Escribe la hora así: 10:00.", "entre 15 y 720",
                     "El último día no puede ser antes del primero.", "Escribe cuántos lugares hay"):
            self.assertContains(response, text)
        self.assertEqual(EventSeries.objects.filter(church=self.church).count(), 1)

    def test_edit_says_which_dates_it_left_alone(self):
        services.save_page(self.rows[0].event, self.admin, registration="abierta")
        SeriesOccurrence.objects.filter(pk=self.rows[0].pk).update(edited_at=None)
        services.register(self.rows[0].event, self.member, self.member_user)
        page = self.client.get(reverse("agenda:series_edit", args=[self.item.pk]))
        self.assertContains(page, 'id="id_weekdays_6" checked')
        self.assertNotContains(page, 'id="id_weekdays_5" checked')
        response = self.client.post(reverse("agenda:series_edit", args=[self.item.pk]),
                                    self.form(name="Servicio dominical", weekdays=["6"], location="Auditorio", published="on"), follow=True)
        self.assertContains(response, "No cambiamos las fechas que ya tienen personas inscritas")
        self.assertContains(response, "Fechas que no cambiamos")
        self.assertEqual(Event.objects.get(pk=self.rows[0].event_id).location, "Templo central")
        self.assertEqual(Event.objects.get(pk=self.rows[1].event_id).location, "Auditorio")

    def test_the_list_groups_the_dates_under_their_series(self):
        page = self.client.get(reverse("agenda:admin_list"))
        self.assertContains(page, "Crear una serie")
        self.assertContains(page, reverse("agenda:series_admin", args=[self.item.pk]))
        self.assertContains(page, "Cada domingo, a las 10:00, en Templo central.")
        self.assertContains(page, "Retiro de jóvenes")  # an event on its own keeps its row
        for row in self.rows:
            self.assertNotContains(page, reverse("agenda:page_edit", args=[row.event.slug]))
        self.assertNotContains(page, "Culto de la iglesia B")

    def test_calling_a_day_off_and_back_from_the_screen(self):
        row = self.rows[1]
        services.save_page(row.event, self.admin, registration="abierta")
        services.register(row.event, self.member, self.member_user)
        response = self.client.post(reverse("agenda:series_call_off", args=[self.item.pk, row.pk]), follow=True)
        self.assertContains(response, "Hay personas inscritas: avísales tú, Emmaus no les envía nada.")
        self.assertContains(response, "Sí hay")
        self.assertEqual(SeriesOccurrence.objects.get(pk=row.pk).status, "cancelada")
        # The answer acts in place (CRAFT §3), where the message is only announced: the row itself keeps saying what
        # calling the day off did and that who signed up must be told, with the message long gone.
        screen = self.client.get(reverse("agenda:series_admin", args=[self.item.pk]))
        self.assertContains(screen, "Hay personas inscritas: avísales tú, Emmaus no les envía nada.", count=1)
        self.assertContains(screen, "Ya no aparece en Eventos ni en el calendario público.", count=1)
        response = self.client.post(reverse("agenda:series_restore", args=[self.item.pk, row.pk]), follow=True)
        self.assertContains(response, "Listo: ese día sí hay.")
        self.assertEqual(SeriesOccurrence.objects.get(pk=row.pk).status, "activa")

    def test_ending_and_resuming_from_the_screen(self):
        response = self.client.post(reverse("agenda:series_end", args=[self.item.pk]), follow=True)
        self.assertContains(response, "Esta serie terminó.")
        self.assertFalse(EventSeries.objects.get(pk=self.item.pk).active)
        self.assertEqual(SeriesOccurrence.objects.filter(series=self.item).count(), len(self.rows))
        self.client.post(reverse("agenda:series_resume", args=[self.item.pk]))
        self.assertTrue(EventSeries.objects.get(pk=self.item.pk).active)

    def test_what_a_person_typed_is_escaped_everywhere(self):
        evil = '<script>alert("x")</script>'
        series.update_series(self.item, self.admin, name=evil, location=evil, description=evil)
        self.client.force_login(self.admin)
        pages = [reverse("agenda:admin_list"), reverse("agenda:series_admin", args=[self.item.pk]),
                 reverse("agenda:series_edit", args=[self.item.pk]), reverse("agenda:list"), self.member_url(),
                 reverse("agenda:detail", args=[self.rows[0].event.slug]), reverse("agenda:page_edit", args=[self.rows[0].event.slug]),
                 reverse("community:home")]
        for url in pages:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertNotContains(response, "<script>alert")
        self.assertContains(self.client.get(self.member_url()), "&lt;script&gt;alert")

    def test_a_date_opened_by_itself_says_it_stops_following_the_series(self):
        slug = self.rows[2].event.slug
        for name in ("page_edit", "event_edit"):
            self.assertContains(self.client.get(reverse(f"agenda:{name}", args=[slug])), "deja de seguir los cambios que le hagas a la serie")
        self.assertNotContains(self.client.get(self.url("page_edit")), "deja de seguir")
        self.client.post(reverse("agenda:event_edit", args=[slug]), {"name": "Servicio especial", "kind": "otro", "location": "Parque",
                                                                     "starts_on": self.rows[2].day.isoformat(), "ends_on": "", "slug": slug})
        self.assertIsNotNone(SeriesOccurrence.objects.get(pk=self.rows[2].pk).edited_at)


class MemberScreenTests(ScreenFixture):
    def setUp(self):
        self.client.force_login(self.member_user)

    def links(self, response) -> int:
        html = response.content.decode()
        return sum(html.count(f'href="{reverse("agenda:detail", args=[row.event.slug])}"') for row in self.rows)

    def test_the_list_shows_the_next_date_of_a_series_and_the_way_to_the_rest(self):
        page = self.client.get(reverse("agenda:list"))
        self.assertEqual(self.links(page), 1)
        self.assertContains(page, reverse("agenda:detail", args=[self.rows[0].event.slug]))
        self.assertContains(page, "Se repite cada domingo.")
        self.assertContains(page, f'href="{self.member_url()}"')
        self.assertContains(page, "Retiro de jóvenes")
        self.assertNotContains(page, "Culto de la iglesia B")
        # "Mi iglesia" leads to an event with the way back to itself (?desde=inicio)
        home = self.client.get(reverse("community:home")).content.decode()
        self.assertEqual(sum(home.count(f'href="{reverse("agenda:detail", args=[row.event.slug])}?desde=inicio"') for row in self.rows), 1)
        rest = self.client.get(self.member_url())
        self.assertContains(rest, "<h1", count=1)
        self.assertContains(rest, "Se repite cada domingo")
        self.assertEqual(self.links(rest), len(self.rows))

    def test_a_date_the_person_signed_up_for_keeps_its_own_row(self):
        services.save_page(self.rows[2].event, self.admin, registration="abierta")
        services.register(self.rows[2].event, self.member, self.member_user)
        page = self.client.get(reverse("agenda:list"))
        self.assertContains(page, "Tus inscripciones")
        self.assertEqual(self.links(page), 2)

    def test_a_series_that_is_not_published_does_not_exist_for_a_member(self):
        series.update_series(self.item, self.admin, published=False)
        self.assertEqual(self.client.get(self.member_url()).status_code, 404)
        self.assertEqual(self.links(self.client.get(reverse("agenda:list"))), 0)
        self.assertEqual(self.client.get(self.member_url(self.item_b)).status_code, 404)
        self.assertEqual(self.client.get(reverse("agenda:series", args=[999999])).status_code, 404)

    def test_a_day_called_off_leaves_the_list_and_tells_who_had_signed_up(self):
        first = self.rows[0]
        services.save_page(first.event, self.admin, registration="abierta")
        services.register(first.event, self.member, self.member_user)
        series.call_off(first, self.admin)
        series.call_off(self.rows[1], self.admin)
        page = self.client.get(reverse("agenda:list"))
        self.assertContains(page, "Este día no hay.", count=1)  # the one the person had signed up for
        self.assertNotContains(page, reverse("agenda:detail", args=[self.rows[1].event.slug]))
        self.assertContains(page, reverse("agenda:detail", args=[self.rows[2].event.slug]))  # the next one that is on
        self.assertContains(self.client.get(reverse("agenda:detail", args=[first.event.slug])), "Tu iglesia avisó que esta fecha no se hace.")
        self.assertEqual(self.client.get(reverse("agenda:detail", args=[self.rows[1].event.slug])).status_code, 404)
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.get(reverse("agenda:detail", args=[first.event.slug])).status_code, 404)

    def test_a_date_says_that_it_repeats(self):
        page = self.client.get(reverse("agenda:detail", args=[self.rows[0].event.slug]))
        self.assertContains(page, "Se repite cada domingo.")
        self.assertNotContains(self.client.get(self.url()), "Se repite")

    def test_the_list_costs_the_same_with_one_series_or_three(self):
        def cost() -> int:
            with CaptureQueriesContext(connection) as queries:
                self.client.get(reverse("agenda:list"))
            return len(queries)

        cost()  # the first request fills the caches every later one uses
        before = cost()
        for name in ("Oración", "Jóvenes"):
            series.create_series(self.church, self.admin, name=name, weekdays="2", starts_at=time(19, 0), starts_on=self.today, published=True)
        self.assertEqual(cost(), before)


@override_settings(EMMAUS_SCHEDULED_COMMUNITY_JOBS=True)
class ScheduledJobTests(SeriesFixture):
    """The job ``series_eventos`` of the scheduler's pass, as ``notices/tests/test_scheduled_delivery.py`` does for the e-mails."""

    def run_pass(self) -> str:
        out, self.code = StringIO(), 0
        try:
            call_command("run_scheduled_jobs", stdout=out)
        except CommandError as error:
            self.code = error.returncode
        return out.getvalue()

    def setUp(self):
        self.item = EventSeries.objects.create(church=self.church, name="Servicio dominical", weekdays="6", starts_at=time(10, 0), starts_on=self.today)
        self.item_b = EventSeries.objects.create(church=self.church_b, name="Culto B", weekdays="6", starts_at=time(9, 0), starts_on=self.today)

    def count(self, item) -> int:
        return SeriesOccurrence.objects.filter(series=item).count()

    def test_a_pass_writes_the_dates_once_and_the_next_pass_writes_none(self):
        self.assertIn(scheduler.SERIES, scheduler.JOBS)
        first = self.run_pass()
        written = self.count(self.item)
        self.assertIn(written, (8, 9))
        self.assertIn(f"series: {written + self.count(self.item_b)} fechas nuevas", first)
        self.assertIn("series: 0 fechas nuevas", self.run_pass())
        self.assertEqual((self.code, self.count(self.item)), (0, written))
        beat = SchedulerHeartbeat.objects.get(job=scheduler.SERIES)
        self.assertEqual(beat.last_error, "")
        self.assertIsNotNone(beat.last_ok_at)
        # A pass with nothing to write asks one question.
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(series.generate_due(), {"series": 0, "created": 0, "failed": 0})
        self.assertEqual(len(queries), 1)

    def test_a_church_without_the_module_waits_and_catches_up_when_it_is_back(self):
        flags.set_enabled(self.church, "community", False)
        self.run_pass()
        self.assertEqual((self.count(self.item), self.count(self.item_b) > 0), (0, True))
        self.assertEqual(series.generate(self.item), 0)  # nor when asked directly
        flags.set_enabled(self.church, "community", True)
        self.run_pass()
        self.assertGreater(self.count(self.item), 0)

    def test_the_switches_of_the_community_jobs_stop_it(self):
        for off in ({"EMMAUS_SCHEDULED_COMMUNITY_JOBS": False}, {"EMMAUS_V2_MODULES": False}):
            with self.settings(**off):
                self.assertNotIn("series:", self.run_pass())
        self.assertEqual(self.count(self.item), 0)
        self.assertEqual(scheduler.skipped(scheduler.SERIES), "")

    def test_a_series_that_fails_stops_neither_the_others_nor_the_other_jobs(self):
        real = series.generate

        def failing(item, *args, **kwargs):
            if item.pk == self.item.pk:
                raise RuntimeError("Servicio dominical secreto")
            return real(item, *args, **kwargs)

        with mock.patch("agenda.series.generate", side_effect=failing), self.assertLogs("emmaus.scheduler") as lines, \
                self.assertLogs("agenda.series", "ERROR") as plain:
            printed = self.run_pass()
        self.assertEqual(self.code, 1)
        self.assertIn("lista de espera: 0 lugares liberados, 0 ofrecidos", printed)
        self.assertIn("1 series con error", printed)
        self.assertEqual((self.count(self.item), self.count(self.item_b) > 0), (0, True))
        logged = printed + "".join(plain.output) + "".join(record.getMessage() for record in lines.records)
        self.assertIn("RuntimeError", logged)
        self.assertNotIn("secreto", logged)
        self.assertNotIn("Servicio dominical", logged)
        self.assertEqual(SchedulerHeartbeat.objects.get(job=scheduler.SERIES).last_error, "SeriesFailed")
        self.assertIsNotNone(SchedulerHeartbeat.objects.get(job=scheduler.WAITING_OFFERS).last_ok_at)
        self.assertIn("series:", self.run_pass())
        self.assertEqual((self.code, SchedulerHeartbeat.objects.get(job=scheduler.SERIES).last_error), (0, ""))
        self.assertGreater(self.count(self.item), 0)


class SeedTests(SeriesFixture):
    def test_the_demonstration_has_one_weekly_series_however_many_times_it_runs(self):
        with self.settings(DEBUG=True):
            call_command("seed_camp_demo", password="x", stdout=StringIO())
            for _run in range(2):
                call_command("seed_community_demo", "--eventos", password="x", stdout=StringIO())
        item = EventSeries.objects.get(name="Reunión general")
        self.assertEqual((item.weekdays, item.published, item.public, item.registration), ("6", True, False, "cerrada"))
        self.assertIn(SeriesOccurrence.objects.filter(series=item).count(), (8, 9))
        self.assertEqual(EventSeries.objects.filter(church=item.church).count(), 1)


class ReviewFixesTests(ScreenFixture):
    """What an independent review of this slice found (wave 9): each test is one of its findings."""

    def page_of(self, row) -> EventPage:
        return EventPage.objects.select_related("event").get(event_id=row.event_id)

    def test_a_day_that_is_on_again_takes_what_the_series_says_now(self):
        """Defect: called off, the series changed (not public, another hour and place), "Sí hay" put it back public as it was."""
        series.update_series(self.item, self.admin, public=True)
        untouched, signed, by_hand = self.rows[1], self.rows[2], self.rows[3]
        services.save_page(signed.event, self.admin, registration="abierta")
        SeriesOccurrence.objects.filter(pk=signed.pk).update(edited_at=None)
        services.register(signed.event, self.member, self.member_user)
        services.save_page(by_hand.event, self.admin, description="Con bautizos.")
        for row in (untouched, signed, by_hand):
            series.call_off(row, self.admin)
        series.update_series(self.item, self.admin, public=False, starts_at=time(12, 30), location="Auditorio")
        for row in (untouched, signed, by_hand):
            series.restore(row, self.admin)
        page = self.page_of(untouched)
        self.assertEqual((page.published, page.public, page.starts_at, page.event.location), (True, False, time(12, 30), "Auditorio"))
        # Somebody is signed up, or somebody changed it: nothing is rewritten, and it does NOT come back published.
        for row in (signed, by_hand):
            page = self.page_of(row)
            self.assertEqual((page.published, page.starts_at, page.event.location), (False, time(10, 0), "Templo central"))
        fresh = EventSeries.objects.get(pk=self.item.pk)
        self.assertEqual({row.pk: row.why for row in series.to_review(fresh)}, {signed.pk: "sin_publicar", by_hand.pk: "sin_publicar"})
        for row in (untouched, signed, by_hand):
            self.assertEqual(self.client.get(reverse("evento:detail", args=[row.event.slug])).status_code, 404)
            self.assertEqual(self.client.get(reverse("evento:calendar", args=[row.event.slug])).status_code, 404)
        self.client.force_login(self.admin)
        screen = self.client.get(reverse("agenda:series_admin", args=[self.item.pk]))
        self.assertContains(screen, "No está publicada: revísala", count=2)

    def test_a_day_that_is_on_again_stays_hidden_if_the_series_is_no_longer_published_or_left_its_day(self):
        series.call_off(self.rows[1], self.admin)
        series.update_series(self.item, self.admin, published=False)
        series.restore(self.rows[1], self.admin)
        self.assertFalse(self.page_of(self.rows[1]).published)
        series.call_off(self.rows[2], self.admin)
        other_day = (self.rows[2].day.weekday() + 1) % 7
        series.update_series(self.item, self.admin, published=True, weekdays=str(other_day))
        series.restore(self.rows[2], self.admin)
        row = SeriesOccurrence.objects.get(pk=self.rows[2].pk)
        self.assertEqual((row.status, self.page_of(row).published), ("fuera", False))

    def test_a_member_never_reads_the_template_of_a_series_that_is_not_published(self):
        """Defect: a date with a sign-up stays published by design, and the series page showed the rewritten template."""
        kept = self.rows[2]
        services.save_page(kept.event, self.admin, registration="abierta")
        SeriesOccurrence.objects.filter(pk=kept.pk).update(edited_at=None)
        services.register(kept.event, self.member, self.member_user)
        series.update_series(self.item, self.admin, published=False, name="Borrador interno", description="Nota solo para el equipo.",
                             weekdays="2")
        self.assertTrue(self.page_of(kept).published)
        self.client.force_login(self.member_user)
        urls = [self.member_url(), reverse("agenda:list"), reverse("agenda:detail", args=[kept.event.slug]), reverse("community:home")]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            for secret in ("Borrador interno", "Nota solo para el equipo.", "Se repite"):
                self.assertNotContains(response, secret)
        own = self.client.get(self.member_url())
        self.assertContains(own, "Servicio dominical")  # what the published date itself says
        self.assertContains(own, 'class="heading">Fechas<')
        # The one date that stays published is all anybody sees of the series.
        html = own.content.decode()
        self.assertEqual(sum(html.count(reverse("agenda:detail", args=[row.event.slug])) for row in self.rows), 1)

    def test_a_church_that_is_not_active_gets_no_dates_even_when_asked_directly(self):
        from core.models import Church

        Church.objects.filter(pk=self.church.pk).update(active=False)
        before = SeriesOccurrence.objects.filter(series=self.item).count()
        later = self.today + timedelta(weeks=4)
        self.assertEqual(series.generate(EventSeries.objects.get(pk=self.item.pk), today=later), 0)
        self.assertEqual(series.generate_due(today=later)["series"], 1)  # church B only
        self.assertEqual(SeriesOccurrence.objects.filter(series=self.item).count(), before)

    def test_a_series_that_ended_is_not_changed_by_its_address(self):
        series.set_active(self.item, self.admin, False)
        self.client.force_login(self.admin)
        url = reverse("agenda:series_edit", args=[self.item.pk])
        shown = self.client.get(url, follow=True)
        self.assertRedirects(shown, reverse("agenda:series_admin", args=[self.item.pk]))
        self.assertContains(shown, "Esta serie terminó y no cambiamos nada.")
        posted = self.client.post(url, self.form(name="Otra cosa", weekdays=["2"], location="Auditorio", published="on"), follow=True)
        self.assertContains(posted, "primero reanúdala")
        self.assertEqual(EventSeries.objects.get(pk=self.item.pk).name, "Servicio dominical")
        self.assertEqual(set(Event.objects.filter(series_occurrence__series=self.item).values_list("location", flat=True)), {"Templo central"})
        self.assertEqual(SeriesOccurrence.objects.filter(series=self.item, status="activa").count(), len(self.rows))
        with self.assertRaises(series.SeriesEnded):
            series.update_series(self.item, self.admin, location="Auditorio")

    def test_the_dates_a_change_of_day_left_behind_are_listed_on_the_series_screen(self):
        other_day = (self.rows[0].day.weekday() + 1) % 7
        self.client.force_login(self.admin)
        screen = self.client.get(reverse("agenda:series_admin", args=[self.item.pk]))
        self.assertNotContains(screen, "Fechas que la serie ya no incluye")
        series.update_series(self.item, self.admin, weekdays=str(other_day))
        screen = self.client.get(reverse("agenda:series_admin", args=[self.item.pk]))
        self.assertContains(screen, "Fechas que la serie ya no incluye")
        for row in self.rows:
            self.assertContains(screen, reverse("agenda:page_edit", args=[row.event.slug]), count=1)
        self.assertEqual(SeriesOccurrence.objects.filter(series=self.item, status="fuera").count(), len(self.rows))


class ClockTests(SeriesFixture):
    """A date whose hour already passed today is not "coming" (the browser journey, wave 9): Wednesday 18 November
    2026, 21:00 in Mexico City, and a series of Wednesdays at 07:00."""

    NIGHT = datetime(2026, 11, 19, 3, 0, tzinfo=UTC)  # 21:00 on the 18th in Mexico City
    DAWN = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)  # 06:00 the same day
    WEDNESDAY = date(2026, 11, 18)

    def at(self, moment):
        return mock.patch("django.utils.timezone.now", return_value=moment)

    def create(self, **fields):
        values = {"name": "Oración de la mañana", "weekdays": "2", "starts_at": time(7, 0), "starts_on": self.WEDNESDAY,
                  "published": True, **fields}
        return series.create_series(self.church, self.admin, **values)

    def test_created_at_night_the_first_date_is_next_week_and_there_are_exactly_eight(self):
        with self.at(self.NIGHT):
            item = self.create()
            self.assertEqual(self.days(item), [date(2026, 11, 25) + timedelta(weeks=n) for n in range(8)])
            self.assertEqual(series.generate(item), 0)
            self.assertEqual(series.generate_due(), {"series": 0, "created": 0, "failed": 0})  # nor does the scheduler write today
            series.update_series(item, self.admin, location="Capilla")  # nor a change of the series
            self.assertEqual(len(self.days(item)), 8)
            late = self.create(name="Vigilia", starts_at=time(23, 0))  # its hour has not passed: today counts
            self.assertEqual(self.days(late), [self.WEDNESDAY + timedelta(weeks=n) for n in range(8)])

    def test_the_hour_is_the_one_of_the_series_zone(self):
        """21:00 in Mexico City is 19:00 in Tijuana: a series there at 20:00 still has today's date."""
        with self.at(self.NIGHT):
            self.assertEqual(self.days(self.create(starts_at=time(20, 0), time_zone="America/Tijuana"))[0], self.WEDNESDAY)
            self.assertEqual(self.days(self.create(name="Otra", starts_at=time(20, 0)))[0], date(2026, 11, 25))

    def test_a_date_that_began_this_morning_is_not_the_next_one_tonight(self):
        with self.at(self.DAWN):
            item = self.create()
            today, following = (self.date_of(item, day) for day in self.days(item)[:2])
            self.assertEqual(today.day, self.WEDNESDAY)
            self.assertFalse(series.has_started(today.event))
        admin_list, screen, member = reverse("agenda:admin_list"), reverse("agenda:series_admin", args=[item.pk]), reverse("agenda:series", args=[item.pk])
        today_url, following_url = (reverse("agenda:detail", args=[row.event.slug]) for row in (today, following))
        with self.at(self.DAWN):
            self.client.force_login(self.admin)
            self.assertContains(self.client.get(admin_list), "La que sigue: 18 de noviembre de 2026.")
            self.client.force_login(self.member_user)
            self.assertContains(self.client.get(reverse("agenda:list")), today_url)
        with self.at(self.NIGHT):
            self.assertTrue(series.has_started(today.event))
            self.client.force_login(self.admin)
            self.assertContains(self.client.get(admin_list), "La que sigue: 25 de noviembre de 2026.")
            html = self.client.get(screen).content.decode()
            coming, passed = html.split("Las últimas que pasaron")
            self.assertNotIn("Miércoles 18 de noviembre de 2026", coming)
            self.assertIn("Miércoles 18 de noviembre de 2026, 07:00", passed)
            self.assertIn("Miércoles 25 de noviembre de 2026, 07:00", coming)
            self.client.force_login(self.member_user)
            for url in (reverse("agenda:list"), member, reverse("community:home")):
                page = self.client.get(url)
                self.assertNotContains(page, today_url)
                self.assertContains(page, following_url)
            # As for any event of today, the date itself still opens, and a sign-up keeps its row.
            self.assertEqual(self.client.get(today_url).status_code, 200)
            EventPage.objects.filter(event=today.event).update(registration="abierta")
            services.register(today.event, self.member, self.member_user)
            self.assertContains(self.client.get(reverse("agenda:list")), today_url)
            self.assertContains(self.client.get(member), today_url)


class PolishedScreenTests(ScreenFixture):
    def test_a_row_of_the_series_screen_is_when_one_way_in_and_one_action(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("agenda:series_admin", args=[self.item.pk])).content.decode()
        first = self.rows[0]
        self.assertIn(f"Domingo {first.day.day} de", html)
        self.assertIn(", 10:00</a></p>", html)
        # The way in is the date itself (a row that opens: no «Abrir» button), once per date.
        self.assertNotIn(">Abrir<", html)
        for row in self.rows:
            self.assertEqual(html.count(f'<a href="{reverse("agenda:page_edit", args=[row.event.slug])}">'), 1)
        self.assertEqual(html.count("Este día no hay<span"), len(self.rows))
        # The one action answers in place, on its own row (CRAFT §3), and stays an ordinary POST form without JavaScript.
        self.assertEqual(html.count(f'data-inline="fecha-{first.pk}"'), 1)
        self.assertIn(f'<li class="row row--lead row--stack" id="fecha-{first.pk}">', html)
        self.assertEqual(html.count('<span class="date-block"'), len(self.rows))
        for word in (">Página<", ">Datos<", ">Inscritos<"):
            self.assertNotIn(word, html)
        self.assertLess(html.index('id="fechas"'), html.index('id="terminar"'))  # ending it is by itself, at the end
        # The rarer doors are on the date's own page.
        page = self.client.get(reverse("agenda:page_edit", args=[first.event.slug]))
        self.assertContains(page, reverse("agenda:registrations", args=[first.event.slug]))
        self.assertContains(page, reverse("agenda:event_edit", args=[first.event.slug]))

    def test_the_members_page_says_the_name_and_the_place_once_and_each_date_once(self):
        Event.objects.filter(pk=self.rows[3].event_id).update(location="Parque central")
        self.client.force_login(self.member_user)
        html = self.client.get(self.member_url()).content.decode()
        main = html[html.index("<main"):]
        self.assertEqual(main.count("Servicio dominical"), 1)
        self.assertEqual(main.count("Templo central"), 1)
        self.assertEqual(main.count("En Parque central."), 1)
        for row in self.rows:
            self.assertEqual(main.count(reverse("agenda:detail", args=[row.event.slug])), 1)
        self.assertEqual(main.count(", 10:00</a>"), len(self.rows))
        self.assertIn(f"Domingo {self.rows[0].day.day} de", main)
