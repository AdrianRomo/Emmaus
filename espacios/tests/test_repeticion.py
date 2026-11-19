"""Bookings that repeat (``espacios.repeticion``): a rule plus ordinary bookings written ahead, each
under the clash rule of every booking; decided once as a series; never around another booking."""

from datetime import UTC, date, datetime, time, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from agenda import series as event_series
from agenda.models import SeriesOccurrence
from espacios import repeticion, services
from espacios.models import Booking, BookingSeries, BookingSeriesDay
from people import flags
from people.models import ChurchAuditEvent

from .test_espacios import LIST, QUEUE, SpacesFixture, url

Status = BookingSeries.Status
Outcome = BookingSeriesDay.Outcome
SECRET = "Consejería de la familia Robles"
WEEKS = repeticion.WINDOW_WEEKS


class SeriesFixture(SpacesFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.today = timezone.now().astimezone(cls.tz).date()
        cls.first = cls.today + timedelta(days=1)

    def ask(self, *, person=None, actor=None, space=None, title="Ensayo", weeks=20, **extra) -> BookingSeries:
        person = person or self.leader
        fields = {"starts_on": self.first, "ends_on": self.first + timedelta(weeks=weeks), "starts_at": time(18), "ends_at": time(20)}
        fields.update(extra)
        return repeticion.request_series(space or self.room, person, actor or person.user, title=title, **fields)

    def approved(self, **extra) -> BookingSeries:
        return self.ask(person=self.admin_person, actor=self.admin, approve=True, **extra)

    def bookings(self, series):
        return Booking.objects.filter(series_day__series=series).order_by("starts_at")

    def local(self, moment):
        return moment.astimezone(self.tz)

    def one_off(self, weeks: int, start=19, end=21, **extra) -> Booking:
        day = self.first + timedelta(weeks=weeks)
        return services.request_booking(
            self.room, self.coordinator, self.coordinator_user, title="Otra cosa",
            starts_at=datetime.combine(day, time(start), tzinfo=self.tz), ends_at=datetime.combine(day, time(end), tzinfo=self.tz), **extra,
        )


class GenerationTests(SeriesFixture):
    def test_an_approved_series_writes_ordinary_bookings_for_the_window_and_no_further(self):
        series = self.approved()
        rows = list(self.bookings(series))
        self.assertEqual(len(rows), WEEKS)
        for number, booking in enumerate(rows):
            self.assertEqual(self.local(booking.starts_at), datetime.combine(self.first + timedelta(weeks=number), time(18), tzinfo=self.tz))
            self.assertEqual(self.local(booking.ends_at).time(), time(20))
            self.assertEqual((booking.status, booking.requested_by, booking.decided_by, booking.title),
                             (Booking.Status.APPROVED, self.admin_person, self.admin, "Ensayo"))
        self.assertEqual(set(BookingSeriesDay.objects.filter(series=series).values_list("outcome", flat=True)), {Outcome.BOOKED})

    def test_running_twice_writes_nothing_more_and_a_day_cannot_be_written_twice(self):
        series = self.approved()
        self.assertEqual(repeticion.generate(series), 0)
        self.assertEqual(repeticion.generate_due()["created"], 0)
        self.assertEqual(self.bookings(series).count(), WEEKS)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BookingSeriesDay.objects.create(series=series, day=self.first, outcome=Outcome.TAKEN)

    def test_the_window_rolls_one_week_at_a_time(self):
        series = self.approved()
        later = timezone.now() + timedelta(weeks=1)
        self.assertEqual(repeticion.generate_due(now=later), {"series": 1, "created": 1, "failed": 0})
        self.assertEqual(repeticion.generate_due(now=later)["created"], 0)
        self.assertEqual(self.bookings(series).count(), WEEKS + 1)

    def test_every_n_weeks_and_never_past_the_last_day(self):
        every_two = self.approved(interval_weeks=2)
        self.assertEqual([self.local(row.starts_at).date() for row in self.bookings(every_two)],
                         [self.first + timedelta(weeks=2 * n) for n in range(WEEKS // 2)])
        short = self.approved(space=self.projector, weeks=2)
        self.assertEqual(self.bookings(short).count(), 3)
        self.assertEqual(repeticion.generate(short, now=timezone.now() + timedelta(weeks=30)), 0)

    def test_a_series_that_waits_holds_nothing_and_is_decided_once(self):
        series = self.ask()
        self.assertEqual((series.status, Booking.objects.count(), BookingSeriesDay.objects.count()), (Status.REQUESTED, 0, 0))
        self.assertEqual(repeticion.generate(series), 0)
        self.one_off(1)  # somebody else may take a day meanwhile: nothing held it
        repeticion.approve_series(series, self.admin)
        self.assertEqual(self.bookings(series).count(), WEEKS - 1)
        self.assertEqual(BookingSeriesDay.objects.get(series=series, outcome=Outcome.TAKEN).day, self.first + timedelta(weeks=1))
        with self.assertRaisesMessage(services.SpaceError, "ya no está por decidir"):
            repeticion.approve_series(series, self.admin)
        with self.assertRaisesMessage(services.SpaceError, "ya no está por decidir"):
            repeticion.reject_series(series, self.admin, "No")

    def test_a_rejected_series_writes_nothing_and_keeps_its_reason_out_of_the_log(self):
        series = self.ask(title=SECRET)
        with self.assertRaisesMessage(services.SpaceError, "Escribe el motivo"):
            repeticion.reject_series(series, self.admin, "  ")
        repeticion.reject_series(series, self.admin, "Motivo muy privado")
        self.assertEqual((BookingSeries.objects.get(pk=series.pk).status, Booking.objects.count()), (Status.REJECTED, 0))
        self.assertEqual(repeticion.generate(series), 0)
        logged = " ".join(f"{row.summary} {row.metadata}" for row in ChurchAuditEvent.objects.all())
        self.assertNotIn(SECRET, logged)
        self.assertNotIn("privado", logged)
        self.assertEqual(
            list(ChurchAuditEvent.objects.filter(action__startswith="espacio_serie").order_by("pk").values_list("action", flat=True)),
            ["espacio_serie_solicitada", "espacio_serie_rechazada"],
        )

    def test_a_space_without_approval_and_the_administration_approve_at_once(self):
        direct = self.ask(space=self.projector)
        self.assertEqual((direct.status, direct.decided_by, self.bookings(direct).count()), (Status.APPROVED, None, WEEKS))
        self.assertEqual(ChurchAuditEvent.objects.get(action="espacio_serie_aprobada").metadata, {"space_id": self.projector.pk, "source": "directa"})
        own = self.approved()
        self.assertEqual((own.status, own.decided_by), (Status.APPROVED, self.admin))

    def test_a_church_that_is_not_active_or_has_the_module_off_generates_nothing(self):
        series = self.approved()
        later = timezone.now() + timedelta(weeks=2)
        flags.set_enabled(self.church, "community", False)
        self.assertEqual(repeticion.generate(series, now=later), 0)
        self.assertEqual(repeticion.generate_due(now=later), {"series": 0, "created": 0, "failed": 0})
        flags.set_enabled(self.church, "community", True)
        type(self.church).objects.filter(pk=self.church.pk).update(active=False)
        self.assertEqual(repeticion.generate(series, now=later), 0)
        self.assertEqual(repeticion.generate_due(now=later)["series"], 0)
        type(self.church).objects.filter(pk=self.church.pk).update(active=True)
        with override_settings(EMMAUS_V2_MODULES=False):
            self.assertEqual(repeticion.generate(series, now=later), 0)
        self.room.active = False
        self.room.save()
        self.assertEqual(repeticion.generate(series, now=later), 0)
        self.assertEqual(self.bookings(series).count(), WEEKS)
        self.room.active = True
        self.room.save()
        self.assertEqual(repeticion.generate_due(now=later)["created"], 2)  # it catches up

    def test_one_series_that_fails_does_not_stop_the_next(self):
        self.approved()
        second = self.approved(space=self.projector)
        later = timezone.now() + timedelta(weeks=1)
        real = repeticion.generate

        def failing(series, **kwargs):
            if series.pk != second.pk:
                raise RuntimeError(SECRET)
            return real(series, **kwargs)

        with mock.patch("espacios.repeticion.generate", side_effect=failing), self.assertLogs("espacios.repeticion", "ERROR") as logs:
            self.assertEqual(repeticion.generate_due(now=later), {"series": 2, "created": 1, "failed": 1})
        self.assertNotIn(SECRET, " ".join(logs.output))
        self.assertIn("RuntimeError", logs.output[0])


class ClashTests(SeriesFixture):
    def test_a_date_that_clashes_is_listed_not_created_and_never_tried_again(self):
        taken = self.one_off(2)
        series = self.approved()
        self.assertEqual(self.bookings(series).count(), WEEKS - 1)
        row = BookingSeriesDay.objects.get(series=series, outcome=Outcome.TAKEN)
        self.assertEqual((row.day, row.booking), (self.first + timedelta(weeks=2), None))
        taken.refresh_from_db()
        self.assertEqual(taken.status, Booking.Status.REQUESTED)
        # Nothing of the series overlaps anything: the rule of every booking held.
        for booking in self.bookings(series):
            self.assertFalse(services.overlapping(self.room.pk, booking.blocked_from, booking.blocked_until, exclude=booking.pk).exists())
        services.cancel(taken, self.coordinator_user, by_admin=False)
        self.assertEqual(repeticion.generate(series), 0)  # the day was decided: the administration books it, or not
        self.assertEqual(self.bookings(series).count(), WEEKS - 1)

    def test_buffers_count_for_a_series_too(self):
        self.one_off(1, 20, 21)   # touches 18:00-20:00, but the series puts away until 20:30
        series = self.approved(teardown_minutes=30)
        self.assertEqual(BookingSeriesDay.objects.get(series=series, outcome=Outcome.TAKEN).day, self.first + timedelta(weeks=1))

    def test_a_one_off_request_for_a_day_beyond_the_window_is_accepted_and_the_series_lists_it_later(self):
        series = self.approved()
        beyond = self.one_off(WEEKS + 1)
        self.assertEqual(beyond.status, Booking.Status.REQUESTED)
        repeticion.generate(series, now=timezone.now() + timedelta(weeks=2))
        self.assertEqual(BookingSeriesDay.objects.get(series=series, outcome=Outcome.TAKEN).day, self.first + timedelta(weeks=WEEKS + 1))

    def test_the_administration_books_a_listed_day_only_naming_what_it_saw(self):
        taken = self.one_off(2)
        series = self.approved()
        row = BookingSeriesDay.objects.get(series=series, outcome=Outcome.TAKEN)
        with self.assertRaises(services.Clash):
            repeticion.book_day(row, self.admin)
        with self.assertRaises(services.Clash):
            repeticion.book_day(row, self.admin, seen=[taken.pk + 999])
        other = self.one_off(2, 17, 19)  # another one arrives after the list was read
        with self.assertRaises(services.Clash) as refused:
            repeticion.book_day(row, self.admin, seen=[taken.pk])
        self.assertEqual({found.pk for found in refused.exception.bookings}, {taken.pk, other.pk})
        self.assertEqual(self.bookings(series).count(), WEEKS - 1)
        booking = repeticion.book_day(row, self.admin, seen=[taken.pk, other.pk])
        row.refresh_from_db()
        self.assertEqual((booking.overrode_clash, booking.status, row.outcome, row.booking), (True, Booking.Status.APPROVED, Outcome.BOOKED, booking))
        audit = ChurchAuditEvent.objects.get(action="espacio_reserva_sobre_choque")
        self.assertEqual((audit.object_id, audit.metadata), (str(booking.pk), {"space_id": self.room.pk, "override": "empalme"}))
        with self.assertRaisesMessage(services.SpaceError, "ya no está pendiente"):
            repeticion.book_day(row, self.admin, seen=[taken.pk, other.pk])

    def test_a_listed_day_whose_hours_are_free_again_is_simply_booked(self):
        taken = self.one_off(2)
        series = self.approved()
        services.cancel(taken, self.coordinator_user, by_admin=False)
        booking = repeticion.book_day(BookingSeriesDay.objects.get(series=series, outcome=Outcome.TAKEN), self.admin)
        self.assertFalse(booking.overrode_clash)
        self.assertFalse(ChurchAuditEvent.objects.filter(action="espacio_reserva_sobre_choque").exists())

    def test_a_day_is_not_booked_for_a_series_that_ended(self):
        self.one_off(2)
        series = self.approved()
        row = BookingSeriesDay.objects.get(series=series, outcome=Outcome.TAKEN)
        repeticion.end_series(series, self.admin, by_admin=True)
        with self.assertRaisesMessage(services.SpaceError, "ya no aparta fechas"):
            repeticion.book_day(row, self.admin, seen=[1])


class EndingTests(SeriesFixture):
    def test_este_dia_no_keeps_the_row_so_the_day_is_not_booked_again(self):
        series = self.approved()
        third = self.bookings(series)[2]
        services.cancel(third, self.admin, by_admin=False)
        self.assertEqual(repeticion.generate(series), 0)
        self.assertEqual(repeticion.generate(series, now=timezone.now() + timedelta(weeks=1)), 1)
        self.assertEqual(self.bookings(series).filter(status=Booking.Status.APPROVED).count(), WEEKS)
        row = BookingSeriesDay.objects.get(series=series, day=self.first + timedelta(weeks=2))
        self.assertEqual((row.booking.status, row.outcome), (Booking.Status.CANCELLED, Outcome.BOOKED))
        # The hours of that one day are free for somebody else; the other dates still hold theirs.
        self.assertEqual(self.one_off(2, 18, 20).status, Booking.Status.REQUESTED)
        with self.assertRaises(services.Clash):
            self.one_off(3, 18, 20)

    def test_ending_stops_new_dates_and_leaves_the_ones_that_exist(self):
        series = self.approved()
        repeticion.end_series(series, self.admin, by_admin=True)
        later = timezone.now() + timedelta(weeks=3)
        self.assertEqual(repeticion.generate(series, now=later), 0)
        self.assertEqual(repeticion.generate_due(now=later)["series"], 0)
        self.assertEqual(self.bookings(series).filter(status=Booking.Status.APPROVED).count(), WEEKS)
        ended = BookingSeries.objects.get(pk=series.pk)
        self.assertEqual((ended.status, ended.ended_by), (Status.ENDED, self.admin))
        with self.assertRaisesMessage(services.SpaceError, "ya había terminado"):
            repeticion.end_series(series, self.admin, by_admin=True)
        self.assertEqual(ChurchAuditEvent.objects.get(action="espacio_serie_terminada").metadata, {"space_id": self.room.pk, "source": "administracion"})

    def test_a_stale_copy_never_brings_a_decision_back(self):
        series = self.ask()
        stale = BookingSeries.objects.get(pk=series.pk)
        repeticion.end_series(series, self.leader_user, by_admin=False)   # who asked withdrew it
        with self.assertRaisesMessage(services.SpaceError, "ya no está por decidir"):
            repeticion.approve_series(stale, self.admin)
        self.assertEqual((BookingSeries.objects.get(pk=series.pk).status, Booking.objects.count()), (Status.ENDED, 0))
        # Without the re-read under the lock the stale copy would have been approved: the guard is what refuses.
        self.assertEqual(stale.status, Status.REQUESTED)


class BoundsTests(SeriesFixture):
    def test_unbounded_input_is_refused(self):
        far = self.today + timedelta(days=repeticion.MAX_AHEAD_DAYS + 1)
        for message, fields in (
            ("cada semana o cada 2, 3 o 4", {"interval_weeks": 0}),
            ("cada semana o cada 2, 3 o 4", {"interval_weeks": 5}),
            ("cada semana o cada 2, 3 o 4", {"interval_weeks": "2"}),
            ("a lo más seis meses", {"ends_on": far}),
            ("a lo más seis meses", {"ends_on": self.today + timedelta(days=365 * 30)}),
            ("igual o posterior al primero", {"ends_on": self.first - timedelta(days=1)}),
            ("Ese día ya pasó", {"starts_on": self.today - timedelta(days=1)}),
            ("después de la hora en que empieza", {"ends_at": time(18)}),
            ("240 minutos a lo más", {"setup_minutes": 241}),
            ("Escribe para qué es", {"title": "   "}),
        ):
            with self.assertRaisesMessage(services.SpaceError, message, msg=fields):
                self.ask(**fields)
        self.assertEqual(self.ask(ends_on=self.today + timedelta(days=repeticion.MAX_AHEAD_DAYS)).status, Status.REQUESTED)
        self.assertEqual(BookingSeries.objects.count(), 1)

    def test_the_database_refuses_an_interval_out_of_range_too(self):
        series = self.ask()
        for interval in (0, 5):
            with self.assertRaises(IntegrityError), transaction.atomic():
                BookingSeries.objects.filter(pk=series.pk).update(interval_weeks=interval)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BookingSeries.objects.filter(pk=series.pk).update(ends_on=series.starts_on - timedelta(days=1))

    def test_a_space_takes_a_bounded_number_of_series(self):
        with mock.patch.object(repeticion, "MAX_OPEN_PER_SPACE", 2):
            self.ask()
            self.ask()
            with self.assertRaisesMessage(services.SpaceError, "muchas reservas que se repiten"):
                self.ask()

    def test_nothing_of_another_church_and_nobody_merged_asks(self):
        with self.assertRaisesMessage(services.SpaceError, "no es de esta iglesia"):
            self.ask(person=self.leader_b, actor=self.leader_b_user)
        type(self.leader).objects.filter(pk=self.leader.pk).update(merged_into=self.coordinator)
        with self.assertRaisesMessage(services.SpaceError, "no es de esta iglesia"):
            self.ask()
        self.room.active = False
        self.room.save()
        with self.assertRaisesMessage(services.SpaceError, "ya no se puede reservar"):
            self.ask(person=self.coordinator)
        self.assertEqual(BookingSeries.objects.count(), 0)


class ClockChangeTests(SeriesFixture):
    """A zone whose clock changes: America/Tijuana, 14 March 2027 (02:00 jumps to 03:00)."""

    TIJUANA = ZoneInfo("America/Tijuana")
    NOW = datetime(2027, 2, 22, 20, 0, tzinfo=UTC)

    def setUp(self):
        type(self.church).objects.filter(pk=self.church.pk).update(time_zone="America/Tijuana")
        self.room.refresh_from_db()

    def series(self, start, end):
        with mock.patch("django.utils.timezone.now", return_value=self.NOW):
            return repeticion.request_series(
                self.room, self.admin_person, self.admin, title="Oración", starts_on=date(2027, 2, 28), ends_on=date(2027, 4, 4),
                starts_at=start, ends_at=end, approve=True, now=self.NOW,
            )

    def test_ten_oclock_stays_ten_oclock_across_the_change(self):
        series = self.series(time(10), time(11, 30))
        rows = list(self.bookings(series))
        self.assertEqual([row.starts_at.astimezone(self.TIJUANA).strftime("%m-%d %H:%M") for row in rows],
                         ["02-28 10:00", "03-07 10:00", "03-14 10:00", "03-21 10:00", "03-28 10:00", "04-04 10:00"])
        # The same hour on the wall is a different moment after the change: 18:00 UTC, then 17:00 UTC.
        self.assertEqual([row.starts_at.astimezone(UTC).hour for row in rows], [18, 18, 17, 17, 17, 17])
        self.assertEqual({row.ends_at - row.starts_at for row in rows}, {timedelta(minutes=90)})

    def test_a_day_whose_hour_does_not_exist_is_skipped_and_listed(self):
        series = self.series(time(2, 30), time(3, 30))
        skipped = BookingSeriesDay.objects.get(series=series, outcome=Outcome.NO_HOUR)
        self.assertEqual((skipped.day, skipped.booking), (date(2027, 3, 14), None))
        self.assertEqual(self.bookings(series).count(), 5)
        self.assertNotIn(date(2027, 3, 14), [row.starts_at.astimezone(self.TIJUANA).date() for row in self.bookings(series)])
        with mock.patch("django.utils.timezone.now", return_value=self.NOW):
            self.client.force_login(self.admin)
            page = self.client.get(url("series", series))
        self.assertContains(page, "esa hora no existe ese día, porque se adelanta el reloj")
        with self.assertRaisesMessage(services.SpaceError, "ya no está pendiente"):
            repeticion.book_day(skipped, self.admin, now=self.NOW)


class EventSeriesLinkTests(SeriesFixture):
    def event_series(self, church=None, **fields):
        weekday = self.first.weekday()
        values = {"name": "Reunión de jóvenes", "weekdays": str(weekday), "starts_at": time(19, 30), "starts_on": self.first,
                  "time_zone": str(self.tz), **fields}
        return event_series.create_series(church or self.church, self.admin, **values)

    def test_the_dates_follow_the_event_series_with_its_hour_and_the_bookings_own_duration(self):
        followed = self.event_series()
        series = self.approved(event_series=followed, setup_minutes=30)
        rows = list(self.bookings(series))
        self.assertEqual(len(rows), WEEKS)
        for booking, occurrence in zip(rows, SeriesOccurrence.objects.filter(series=followed).order_by("day")):
            self.assertEqual(self.local(booking.starts_at), datetime.combine(occurrence.day, time(19, 30), tzinfo=self.tz))
            self.assertEqual(booking.ends_at - booking.starts_at, timedelta(hours=2))
            self.assertEqual((booking.event, booking.setup_minutes), (occurrence.event, 30))

    def test_a_date_the_event_series_called_off_is_skipped_and_comes_back_with_it(self):
        followed = self.event_series()
        second = SeriesOccurrence.objects.filter(series=followed).order_by("day")[1]
        event_series.call_off(second, self.admin)
        series = self.approved(event_series=followed)
        self.assertEqual(self.bookings(series).count(), WEEKS - 1)
        self.assertFalse(BookingSeriesDay.objects.filter(series=series, day=second.day).exists())
        event_series.restore(second, self.admin)
        self.assertEqual(repeticion.generate(series), 1)
        self.assertTrue(BookingSeriesDay.objects.filter(series=series, day=second.day, outcome=Outcome.BOOKED).exists())

    def test_a_date_called_off_after_its_booking_keeps_the_room_and_the_page_says_to_look(self):
        followed = self.event_series()
        series = self.approved(event_series=followed)
        event_series.call_off(SeriesOccurrence.objects.filter(series=followed).order_by("day")[1], self.admin)
        self.assertEqual(self.bookings(series).filter(status=Booking.Status.APPROVED).count(), WEEKS)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url("series", series)), "el espacio sigue apartado", count=1)

    def test_only_the_dates_until_the_last_day(self):
        followed = self.event_series()
        series = self.approved(event_series=followed, weeks=2)
        self.assertEqual(self.bookings(series).count(), 3)

    def test_a_series_of_another_church_is_refused_and_only_the_administration_links(self):
        foreign = self.event_series(church=self.church_b)
        with self.assertRaisesMessage(services.SpaceError, "no es de esta iglesia"):
            self.approved(event_series=foreign)
        own = self.event_series(name="Otra")
        with self.assertRaisesMessage(services.SpaceError, "Solo la administración"):
            self.ask(event_series=own)
        ended = self.event_series(name="Terminó")
        event_series.set_active(ended, self.admin, False)
        with self.assertRaisesMessage(services.SpaceError, "ya terminó"):
            self.approved(event_series=ended)
        self.assertEqual(BookingSeries.objects.count(), 0)

    def test_a_link_that_ends_up_pointing_at_another_church_generates_nothing(self):
        series = self.approved(event_series=self.event_series(), weeks=2)
        before = self.bookings(series).count()
        foreign = self.event_series(church=self.church_b, name="Ajena")
        BookingSeries.objects.filter(pk=series.pk).update(event_series=foreign, ends_on=self.first + timedelta(weeks=20))
        self.assertEqual(repeticion.generate(series), 0)
        self.assertEqual(self.bookings(series).count(), before)
        self.assertFalse(Booking.objects.filter(space=self.room, event__church=self.church_b).exists())


class SeriesViewTests(SeriesFixture):
    def form(self, **changes) -> dict:
        return {"title": "Ensayo", "day": self.first.isoformat(), "starts": "18:00", "ends": "20:00", "setup_minutes": "0",
                "teardown_minutes": "0", "repeat": "1", "until": (self.first + timedelta(weeks=12)).isoformat(), **changes}

    def every_request(self, series, row):
        return [
            ("get", url("series", series), {}), ("get", url("series_end", series), {}), ("post", url("series_end", series), {}),
            ("post", url("series_approve", series), {}),
            ("get", url("series_reject", series), {}), ("post", url("series_reject", series), {"reason": "No"}),
            ("get", reverse("espacios:series_day_book", args=[series.pk, row.pk]), {}),
            ("post", reverse("espacios:series_day_book", args=[series.pk, row.pk]), {}),
        ]

    def waiting_with_a_listed_day(self):
        """A series of the guide that still waits, plus a day row to aim the day's address at."""
        series = self.ask()
        row = BookingSeriesDay.objects.create(series=series, day=self.first, outcome=Outcome.TAKEN)
        return series, row

    def state(self, series):
        return BookingSeries.objects.get(pk=series.pk).status, Booking.objects.count()

    # ---- denials --------------------------------------------------------------------------------
    def test_without_an_account_everything_asks_to_sign_in(self):
        series, row = self.waiting_with_a_listed_day()
        for method, address, data in self.every_request(series, row):
            response = getattr(self.client, method)(address, data)
            self.assertEqual(response.status_code, 302, address)
            self.assertTrue(response.url.startswith(reverse("login")), address)
        self.assertEqual(self.state(series), (Status.REQUESTED, 0))

    def test_with_the_module_off_nothing_exists_for_anybody(self):
        series, row = self.waiting_with_a_listed_day()
        flags.set_enabled(self.church, "community", False)
        for user in (self.admin, self.leader_user, self.member_user):
            self.client.force_login(user)
            for method, address, data in self.every_request(series, row):
                self.assertEqual(getattr(self.client, method)(address, data).status_code, 404, f"{user.email} {address}")
        self.assertEqual(self.state(series), (Status.REQUESTED, 0))

    def test_a_series_exists_only_for_who_asked_and_for_the_administration(self):
        series, row = self.waiting_with_a_listed_day()
        for user in (self.member_user, self.pastor_user, self.coordinator_user):
            self.client.force_login(user)
            for method, address, data in self.every_request(series, row):
                staff_only = any(word in address for word in ("aprobar", "rechazar", "apartar"))
                self.assertEqual(getattr(self.client, method)(address, data).status_code, 403 if staff_only else 404,
                                 f"{user.email} {method} {address}")
        self.assertEqual(self.state(series), (Status.REQUESTED, 0))
        self.client.force_login(self.leader_user)   # who asked reads it, and does not decide it
        self.assertEqual(self.client.get(url("series", series)).status_code, 200)
        self.assertEqual(self.client.post(url("series_approve", series)).status_code, 403)
        self.assertEqual(self.client.get(url("series_reject", series)).status_code, 403)
        self.assertEqual(self.state(series), (Status.REQUESTED, 0))

    def test_another_church_gets_404_for_every_id(self):
        series, row = self.waiting_with_a_listed_day()
        for user in (self.admin_b, self.leader_b_user, self.member_b_user):
            self.client.force_login(user)
            for method, address, data in self.every_request(series, row):
                staff_only = user != self.admin_b and any(word in address for word in ("aprobar", "rechazar", "apartar"))
                self.assertEqual(getattr(self.client, method)(address, data).status_code, 403 if staff_only else 404,
                                 f"{user.email} {method} {address}")
        self.client.force_login(self.admin_b)
        for address in (LIST, QUEUE):
            self.assertNotContains(self.client.get(address), "Ensayo")
        self.assertEqual(self.state(series), (Status.REQUESTED, 0))

    def test_a_day_of_another_series_is_a_404(self):
        self.one_off(2)
        series = self.approved()
        other = self.approved(space=self.projector)
        row = BookingSeriesDay.objects.get(series=series, outcome=Outcome.TAKEN)
        self.client.force_login(self.admin)
        address = reverse("espacios:series_day_book", args=[other.pk, row.pk])
        self.assertEqual(self.client.get(address).status_code, 404)
        self.assertEqual(self.client.post(address, {"sobre": "1"}).status_code, 404)
        self.assertIsNone(BookingSeriesDay.objects.get(pk=row.pk).booking)

    def test_posts_need_the_csrf_token_and_approving_is_a_post(self):
        series, row = self.waiting_with_a_listed_day()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        for method, address, data in self.every_request(series, row):
            if method == "post":
                self.assertEqual(client.post(address, data).status_code, 403, address)
        self.assertEqual(client.post(url("request", self.room), self.form()).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url("series_approve", series)).status_code, 405)
        self.assertEqual((self.state(series), BookingSeries.objects.count()), ((Status.REQUESTED, 0), 1))

    def test_a_plain_member_cannot_ask_for_a_series(self):
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.post(url("request", self.room), self.form()).status_code, 403)
        self.assertEqual(BookingSeries.objects.count(), 0)

    # ---- the journey ----------------------------------------------------------------------------
    def test_a_guide_asks_the_administration_approves_once_and_each_day_is_cancelled_alone(self):
        self.client.force_login(self.leader_user)
        response = self.client.post(url("request", self.room), self.form(), follow=True)
        series = BookingSeries.objects.get()
        self.assertRedirects(response, url("series", series))
        self.assertEqual((series.status, series.requested_by, series.interval_weeks, Booking.objects.count()),
                         (Status.REQUESTED, self.leader, 1, 0))
        self.assertContains(response, "mientras tanto no están apartadas")
        self.assertContains(response, "Mientras tanto no hay ninguna fecha apartada")
        self.assertNotContains(response, "Aprobar la serie")
        self.assertContains(self.client.get(LIST), "Se repite cada")

        self.client.force_login(self.admin)
        self.assertEqual(services.pending_count(self.church), 1)
        queue = self.client.get(QUEUE)
        self.assertContains(queue, "No aparta nada mientras decides")
        self.assertContains(queue, url("series_approve", series))
        approved = self.client.post(url("series_approve", series), follow=True)
        self.assertRedirects(approved, url("series", series))
        self.assertContains(approved, f"{WEEKS} fechas apartadas por ahora")
        self.assertContains(approved, f"Las fechas se apartan con {WEEKS} semanas de anticipación")
        self.assertEqual((services.pending_count(self.church), self.bookings(series).count()), (0, WEEKS))

        self.client.force_login(self.leader_user)
        page = self.client.get(url("series", series))
        third = self.bookings(series)[2]
        self.assertContains(page, "Este día no", count=WEEKS)
        ask = self.client.get(url("cancel", third))
        self.assertContains(ask, "la serie no vuelve a apartarlo")
        self.assertContains(ask, "Sí, este día no")
        self.assertRedirects(self.client.post(url("cancel", third)), url("series", series))
        page = self.client.get(url("series", series))
        self.assertContains(page, "Días que no")
        self.assertContains(page, "Este día no", count=WEEKS - 1)
        self.assertEqual(repeticion.generate(series), 0)

        self.assertContains(self.client.get(url("series_end", series)), "No se apartan más fechas")
        self.assertRedirects(self.client.post(url("series_end", series)), url("series", series))
        self.assertEqual(BookingSeries.objects.get(pk=series.pk).status, Status.ENDED)
        self.assertEqual(self.bookings(series).filter(status=Booking.Status.APPROVED).count(), WEEKS - 1)
        self.assertEqual(self.client.get(url("series_end", series)).status_code, 404)

    def test_the_rule_is_for_staff_and_for_who_asked_everybody_else_reads_ocupado(self):
        series = self.ask(title=SECRET)
        repeticion.approve_series(series, self.admin)
        week = url("week", self.room) + f"?semana={self.first.isoformat()}"
        for user in (self.admin, self.leader_user):
            self.client.force_login(user)
            page = self.client.get(week)
            self.assertContains(page, SECRET)
            self.assertContains(page, "Se repite cada")
            self.assertContains(page, url("series", series))
        for user in (self.member_user, self.pastor_user, self.coordinator_user):
            self.client.force_login(user)
            page = self.client.get(week)
            self.assertContains(page, "Ocupado")
            for hidden in (SECRET, "Se repite", "Suele ocuparse", url("series", series)):
                self.assertNotContains(page, hidden, msg_prefix=user.email)
            self.assertNotContains(self.client.get(LIST), SECRET)

    def test_beyond_the_window_only_the_administration_reads_suele_ocuparse(self):
        series = self.ask(title=SECRET)
        repeticion.approve_series(series, self.admin)
        beyond = self.first + timedelta(weeks=WEEKS + 2)
        week = url("week", self.room) + f"?semana={beyond.isoformat()}"
        self.client.force_login(self.admin)
        page = self.client.get(week)
        self.assertContains(page, "Suele ocuparse de 18:00 a 20:00", count=1)
        self.assertContains(page, "Todavía no está apartado")
        self.assertContains(page, "Libre todo el día", count=7)  # nothing holds it: the day still reads as free
        for user in (self.leader_user, self.member_user, self.pastor_user):
            self.client.force_login(user)
            page = self.client.get(week)
            self.assertNotContains(page, "Suele ocuparse", msg_prefix=user.email)
            self.assertNotContains(page, SECRET, msg_prefix=user.email)
        # Past the last day of the series, and for a series that ended, nothing is said.
        self.client.force_login(self.admin)
        after = url("week", self.room) + f"?semana={(series.ends_on + timedelta(weeks=2)).isoformat()}"
        self.assertNotContains(self.client.get(after), "Suele ocuparse")
        repeticion.end_series(series, self.admin, by_admin=True)
        self.assertNotContains(self.client.get(week), "Suele ocuparse")

    def test_the_administration_decides_a_listed_day_on_a_page_that_lists_the_clash(self):
        taken = self.one_off(2)
        series = self.ask()
        self.client.force_login(self.admin)
        approved = self.client.post(url("series_approve", series), follow=True)
        self.assertContains(approved, "1 no se pudo apartar porque ya estaba ocupado")
        self.assertContains(approved, "No se pudo apartar: ya está ocupado.")
        row = BookingSeriesDay.objects.get(series=series, outcome=Outcome.TAKEN)
        address = reverse("espacios:series_day_book", args=[series.pk, row.pk])
        self.assertContains(approved, address)
        page = self.client.get(address)
        self.assertContains(page, "Otra cosa")
        self.assertContains(page, f'name="visto" value="{taken.pk}"')
        self.assertContains(page, "Apartar aunque se empalme")
        # Pressing without the list, or with an old one, books nothing and shows the list of now.
        again = self.client.post(address, {})
        self.assertContains(again, "No apartamos nada todavía")
        self.assertIsNone(BookingSeriesDay.objects.get(pk=row.pk).booking)
        done = self.client.post(address, {"sobre": "1", "visto": [str(taken.pk)]}, follow=True)
        self.assertRedirects(done, url("series", series))
        self.assertContains(done, "quedó registrado que tú lo decidiste")
        self.assertTrue(BookingSeriesDay.objects.get(pk=row.pk).booking.overrode_clash)
        self.assertEqual(self.client.get(address).status_code, 404)
        # Who asked reads the day and cannot decide it.
        self.client.force_login(self.leader_user)
        self.assertNotContains(self.client.get(url("series", series)), "Decidir")

    def test_who_asked_reads_the_listed_day_and_the_way_to_the_administration(self):
        self.one_off(2)
        series = self.ask()
        repeticion.approve_series(series, self.admin)
        self.client.force_login(self.leader_user)
        page = self.client.get(url("series", series))
        self.assertContains(page, "No se pudo apartar: ya está ocupado.")
        self.assertContains(page, "La administración de tu iglesia decide")
        self.assertNotContains(page, "Otra cosa")   # what holds the day is somebody else's booking
        row = BookingSeriesDay.objects.get(series=series, outcome=Outcome.TAKEN)
        self.assertEqual(self.client.get(reverse("espacios:series_day_book", args=[series.pk, row.pk])).status_code, 403)

    def test_what_a_person_typed_is_escaped(self):
        risky = '<script>alert("x")</script>'
        series = self.ask(title=risky)
        self.client.force_login(self.admin)
        for address in (url("series", series), QUEUE, url("series_reject", series), url("series_end", series)):
            page = self.client.get(address)
            self.assertNotContains(page, "<script>alert", msg_prefix=address)
            self.assertContains(page, "&lt;script&gt;", msg_prefix=address)
        repeticion.approve_series(series, self.admin)
        for address in (url("week", self.room) + f"?semana={self.first.isoformat()}",
                        url("week", self.room) + f"?semana={(self.first + timedelta(weeks=WEEKS + 2)).isoformat()}"):
            page = self.client.get(address)
            self.assertNotContains(page, "<script>alert", msg_prefix=address)
            self.assertContains(page, "&lt;script&gt;", msg_prefix=address)

    def test_the_form_refuses_what_makes_no_sense(self):
        self.client.force_login(self.leader_user)
        far = (self.today + timedelta(days=repeticion.MAX_AHEAD_DAYS + 1)).isoformat()
        for message, changes in (
            ("Escribe hasta qué día se repite.", {"until": ""}),
            ("a lo más seis meses", {"until": far}),
            ("a lo más seis meses", {"until": "2099-01-01"}),
            ("Elige un día igual o posterior al primero.", {"until": (self.first - timedelta(days=1)).isoformat()}),
            ("Elige una opción de la lista.", {"repeat": "5"}),
            ("Elige una opción de la lista.", {"repeat": "-1"}),
            ("di cada cuánto se repite", {"repeat": "0"}),
        ):
            response = self.client.post(url("request", self.room), self.form(**changes))
            self.assertEqual(response.status_code, 200, changes)
            self.assertContains(response, message, msg_prefix=str(changes))
        self.assertEqual((BookingSeries.objects.count(), Booking.objects.count()), (0, 0))
        # Without "se repite" the form is the booking of one day it always was.
        self.client.post(url("request", self.room), self.form(repeat="0", until=""))
        self.assertEqual((BookingSeries.objects.count(), Booking.objects.count()), (0, 1))

    def test_only_the_administration_is_offered_and_obeyed_on_a_series_of_events(self):
        followed = event_series.create_series(self.church, self.admin, name="Reunión de jóvenes", weekdays=str(self.first.weekday()),
                                              starts_at=time(19, 30), starts_on=self.first, time_zone=str(self.tz))
        foreign = event_series.create_series(self.church_b, self.admin_b, name="Serie ajena", weekdays="0", starts_at=time(9),
                                             starts_on=self.first, time_zone=str(self.tz))
        self.client.force_login(self.leader_user)
        self.assertNotContains(self.client.get(url("request", self.room)), "Reunión de jóvenes")
        self.client.post(url("request", self.room), self.form(event_series=str(followed.pk)))
        self.assertIsNone(BookingSeries.objects.get().event_series)
        BookingSeries.objects.all().delete()
        self.client.force_login(self.admin)
        form = self.client.get(url("request", self.room))
        self.assertContains(form, "Reunión de jóvenes")
        self.assertNotContains(form, "Serie ajena")
        refused = self.client.post(url("request", self.room), self.form(event_series=str(foreign.pk)))
        self.assertContains(refused, "Elige una opción de la lista.")
        self.assertEqual(BookingSeries.objects.count(), 0)
        done = self.client.post(url("request", self.room), self.form(repeat="0", event_series=str(followed.pk)), follow=True)
        series = BookingSeries.objects.get()
        self.assertEqual((series.event_series, series.status), (followed, Status.APPROVED))
        self.assertContains(done, "Sigue las fechas de la serie de eventos Reunión de jóvenes")
        self.assertEqual(self.local(self.bookings(series)[0].starts_at).time(), time(19, 30))

    def test_rejecting_has_a_reason_only_the_administration_reads(self):
        series = self.ask()
        self.client.force_login(self.admin)
        self.assertContains(self.client.post(url("series_reject", series), {"reason": ""}), "Escribe el motivo")
        self.assertRedirects(self.client.post(url("series_reject", series), {"reason": "Motivo muy privado"}), QUEUE)
        self.assertContains(self.client.get(url("series", series)), "Motivo muy privado")
        self.assertEqual(self.client.get(url("series_reject", series)).status_code, 404)
        self.client.force_login(self.leader_user)
        page = self.client.get(url("series", series))
        self.assertContains(page, "La administración no aprobó esta serie")
        self.assertNotContains(page, "privado")
