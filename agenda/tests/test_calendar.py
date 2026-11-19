"""The calendar files (issue #93) on synthetic data: the church's public feed and one event's file say what the
public page of each event says about when and where, and never anything about people. Everything that is not
such a calendar is the same 404."""

from datetime import date, time, timedelta
from unittest import mock

from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from agenda import calendar, public, series, services
from agenda.models import EventPage, EventRegistration, SeriesOccurrence
from agenda.tests.test_public_page import PublicFixture, public_url
from core.models import Church
from events.models import Event
from people import flags


def feed_url(church) -> str:
    return reverse("calendario:feed", args=[church.slug])


def file_url(event) -> str:
    return reverse("evento:calendar", args=[event.slug])


def unfold(body: str) -> list[str]:
    return body.replace("\r\n ", "").split("\r\n")


class TextTests(SimpleTestCase):
    def test_text_is_escaped_as_the_standard_says(self):
        self.assertEqual(calendar.escape("Salón 2; puerta B, planta \\ alta\nsegundo piso"),
                         "Salón 2\\; puerta B\\, planta \\\\ alta\\nsegundo piso")
        # A line break somebody typed can never start a property of its own; control characters go.
        self.assertEqual(calendar.escape("Culto\r\nATTENDEE:mailto:a@prueba.mx\x00\x07\x7f"), "Culto\\nATTENDEE:mailto:a@prueba.mx")

    def test_no_kind_of_line_break_or_control_character_survives(self):
        """Unicode's own line breaks and the C1 controls: some readers take them for the end of a line."""
        for mark in ("\u2028", "\u2029", "\x85", "\x0b", "\x0c"):
            self.assertEqual(calendar.escape(f"Culto{mark}ATTENDEE:x"), "Culto" + chr(92) + "nATTENDEE:x", repr(mark))
        self.assertEqual(calendar.escape("a\x00b\x1bc\x7fd\x9be"), "abcde")
        folded = calendar.fold("SUMMARY:" + calendar.escape("Culto\u2028de oración\u2029" * 12))
        self.assertEqual(folded.splitlines(), folded.split("\r\n"))  # no break but the standard's own

    def test_lines_fold_at_75_octets_without_cutting_a_character(self):
        line = "SUMMARY:" + "Reunión de oración y alabanza ñ " * 9
        folded = calendar.fold(line)
        parts = folded.split("\r\n")
        self.assertGreater(len(parts), 3)
        for part in parts:
            self.assertLessEqual(len(part.encode()), 75)  # each piece encodes by itself: no character was cut
        self.assertTrue(all(part.startswith(" ") for part in parts[1:]))
        self.assertEqual(folded.replace("\r\n ", ""), line)
        self.assertEqual(calendar.fold("SUMMARY:corto"), "SUMMARY:corto")


class FeedTests(PublicFixture):
    def feed(self, church=None):
        return self.client.get(feed_url(church or self.church))

    def test_anybody_gets_the_public_events_with_when_and_where(self):
        response = self.feed()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/calendar; charset=utf-8")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(dict(response.cookies), {})
        body = response.content.decode()
        self.assertTrue(body.endswith("END:VCALENDAR\r\n"))
        self.assertNotIn("\n", body.replace("\r\n", ""))  # every line ends as the standard says
        lines = unfold(body)
        self.assertEqual(lines[:2], ["BEGIN:VCALENDAR", "VERSION:2.0"])
        self.assertIn(f"X-WR-CALNAME:{self.church.name}", lines)
        self.assertEqual(lines.count("BEGIN:VEVENT"), 1)
        last = self.retiro.ends_on + timedelta(days=1)
        for line in ("UID:retiro-jovenes@testserver", "SUMMARY:Retiro de jóvenes", "LOCATION:Rancho El Olivo",
                     f"DTSTART;VALUE=DATE:{self.retiro.starts_on:%Y%m%d}", f"DTEND;VALUE=DATE:{last:%Y%m%d}",
                     "URL:http://testserver/evento/retiro-jovenes/"):
            self.assertIn(line, lines)
        self.assertTrue(any(line.startswith("DTSTAMP:") and line.endswith("Z") for line in lines))

    def test_nothing_about_people_and_no_description_is_in_the_bytes(self):
        content = self.feed().content + self.client.get(file_url(self.retiro)).content
        for word in [*self.private(), b"Tres d", b"descansar", "¿Qué llevo?".encode(), b"Bienvenida", b"ATTENDEE", b"ORGANIZER"]:
            self.assertNotIn(word, content, word.decode())
        self.assertNotIn(f"@{self.retiro.pk}".encode(), content)

    def test_it_never_asks_a_table_of_people(self):
        for url, most in ((feed_url(self.church), 3), (file_url(self.retiro), 3)):
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get(url).status_code, 200)
            sql = " ".join(query["sql"] for query in queries.captured_queries)
            for table in ("agenda_eventregistration", "agenda_guardianconsent", "people_person", "events_eventparticipant",
                          "auth_user", "care_followup", "django_session"):
                self.assertNotIn(table, sql)
            self.assertLessEqual(len(queries), most, url)

    def test_who_is_signed_in_is_not_treated_differently_and_it_only_reads(self):
        for url in (feed_url(self.church), file_url(self.retiro)):
            anonymous = self.client.get(url).content
            for user in (self.member_user, self.admin, self.admin_b):
                self.client.force_login(user)
                self.assertEqual(self.client.get(url).content, anonymous)
            self.client.logout()
            for method in (self.client.post, self.client.put, self.client.delete):
                self.assertEqual(method(url).status_code, 405)

    def test_the_feed_is_exactly_the_events_whose_public_page_answers(self):
        """Every way an event stops being public takes it out of the feed and of its own file, and back."""
        coming = self._future(self.church, "Vigilia de oración", "vigilia-oracion", 3, 3, location="Templo")
        services.save_page(coming, self.admin, published=True, public=True, starts_at=time(21, 0))
        EventPage.objects.filter(event=self.hidden).update(public=True)  # public but not published

        def check(why):
            response = self.feed()
            body = response.content.decode() if response.status_code == 200 else ""
            for event in Event.objects.filter(church=self.church):
                served = public.public_page(event.slug) is not None
                self.assertEqual(f"UID:{event.slug}@" in body, served, f"{why}: {event.slug}")
                self.assertEqual(self.client.get(file_url(event)).status_code, 200 if served else 404, f"{why}: {event.slug}")
                self.assertEqual(self.client.get(public_url(event)).status_code, 200 if served else 404)
            return body

        self.assertEqual(check("start").count("BEGIN:VEVENT"), 2)
        for status in (Event.Status.DRAFT, Event.Status.CLOSED):
            Event.objects.filter(pk=coming.pk).update(status=status)
            self.assertEqual(check(status).count("BEGIN:VEVENT"), 1)
        Event.objects.filter(pk=coming.pk).update(status=Event.Status.ACTIVE)
        self.move(coming, -3, -3)
        self.assertEqual(check("over").count("BEGIN:VEVENT"), 1)
        self.move(coming, 3, 3)
        for off in ({"public": False}, {"published": False}):
            EventPage.objects.filter(event=coming).update(**off)
            self.assertEqual(check(str(off)).count("BEGIN:VEVENT"), 1)
            EventPage.objects.filter(event=coming).update(public=True, published=True)
        body = check("back")
        # One day with an hour is said as an instant; this church is in Mexico City, six hours behind.
        self.assertIn(f"DTSTART:{coming.starts_on + timedelta(days=1):%Y%m%d}T030000Z", body)
        self.assertLess(body.index("UID:retiro-jovenes@"), body.index("UID:vigilia-oracion@"))  # by first day

    def test_what_somebody_typed_cannot_add_a_line_to_the_calendar(self):
        Event.objects.filter(pk=self.retiro.pk).update(name="Retiro; uno, dos\r\nATTENDEE:mailto:a@prueba.mx",
                                                       location="Rancho\nBEGIN:VEVENT")
        lines = unfold(self.feed().content.decode())
        self.assertIn("SUMMARY:Retiro\\; uno\\, dos\\nATTENDEE:mailto:a@prueba.mx", lines)
        self.assertIn("LOCATION:Rancho\\nBEGIN:VEVENT", lines)
        self.assertEqual(lines.count("BEGIN:VEVENT"), 1)
        self.assertFalse(any(line.startswith("ATTENDEE") for line in lines))

    @override_settings(EMMAUS_PUBLIC_BASE_URL="https://emmaus.prueba.mx")
    def test_the_links_use_the_public_address_when_there_is_one(self):
        lines = unfold(self.feed().content.decode())
        self.assertIn("URL:https://emmaus.prueba.mx/evento/retiro-jovenes/", lines)
        self.assertIn("UID:retiro-jovenes@emmaus.prueba.mx", lines)

    def test_what_is_over_is_left_out_by_the_database(self):
        """Defect: every public page the church ever had was read and thrown away in Python on each request."""
        for number in range(6):
            old = self._future(self.church, f"Culto pasado {number}", f"culto-pasado-{number}", -30 - number, -30 - number)
            services.save_page(old, self.admin, published=True, public=True)
        with mock.patch("agenda.calendar.services.has_ended", wraps=services.has_ended) as asked:
            body = self.feed().content.decode()
        self.assertEqual(asked.call_count, 1)  # the one event that is not over
        self.assertEqual(body.count("BEGIN:VEVENT"), 1)
        self.assertNotIn("culto-pasado", body)

    def test_a_calendar_may_be_kept_for_a_few_minutes_and_a_refusal_never(self):
        for url in (feed_url(self.church), file_url(self.retiro)):
            response = self.client.get(url)
            self.assertEqual(response["Cache-Control"], "public, max-age=300")
            self.assertNotIn("Pragma", response)
            self.assertEqual(dict(response.cookies), {})
        for url in (feed_url(self.church_b), file_url(self.hidden)):
            self.assertEqual(self.client.get(url)["Cache-Control"], "no-store, max-age=0")
        # Signed in, the bytes are the same; if the session left a cookie on the answer, it is not kept by anybody.
        self.client.force_login(self.member_user)
        response = self.client.get(feed_url(self.church))
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0" if response.cookies else "public, max-age=300")

    def test_an_event_without_a_first_day_is_the_same_404_and_not_an_error(self):
        Event.objects.filter(pk=self.retiro.pk).update(starts_on=None)
        self.assertEqual(self.client.get(public_url(self.retiro)).status_code, 200)  # its page answers: it has a last day
        absent = self.client.get(reverse("evento:calendar", args=["no-existe"]))
        response = self.client.get(file_url(self.retiro))
        self.assertEqual((response.status_code, response.content), (404, absent.content))
        self.assertEqual(self.client.get(feed_url(self.church)).status_code, 404)

    def test_one_events_file_downloads_with_the_same_facts(self):
        response = self.client.get(file_url(self.retiro))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="retiro-jovenes.ics"')
        self.assertEqual(dict(response.cookies), {})
        lines = unfold(response.content.decode())
        self.assertEqual(lines.count("BEGIN:VEVENT"), 1)
        self.assertIn("SUMMARY:Retiro de jóvenes", lines)
        page = self.client.get(public_url(self.retiro))
        self.assertContains(page, f'<a class="btn btn--link" href="{file_url(self.retiro)}">Agregar a mi calendario</a>', count=1)

    def test_the_administration_reads_the_address_only_while_it_answers(self):
        self.client.force_login(self.admin)
        address = "http://testserver" + feed_url(self.church)
        self.assertContains(self.client.get(reverse("agenda:admin_list")), f'value="{address}"')
        self.set_page(public=False)
        self.assertNotContains(self.client.get(reverse("agenda:admin_list")), "calendario.ics")


class NoOracleTests(PublicFixture):
    def test_everything_that_is_not_a_calendar_is_the_same_404(self):
        missing = self.client.get(reverse("calendario:feed", args=["no-existe"]))
        self.assertEqual(missing.status_code, 404)

        def gone(why, church=None):
            # Asked at the same moment: the 404 page itself wears whatever the installation wears right now.
            missing = self.client.get(reverse("calendario:feed", args=["no-existe"]))
            response = self.client.get(feed_url(church or self.church))
            self.assertEqual(response.status_code, 404, why)
            self.assertEqual(response.content, missing.content, why)

        gone("a church with nothing public", self.church_b)
        flags.set_enabled(self.church, "community", False)
        gone("module off")
        flags.set_enabled(self.church, "community", True)
        with self.settings(EMMAUS_V2_MODULES=False):
            gone("kill switch")
        Church.objects.filter(pk=self.church.pk).update(active=False)
        gone("church not active")
        Church.objects.filter(pk=self.church.pk).update(active=True)
        self.set_page(public=False)
        gone("nothing public any more")
        # Signed in, even its administration gets the refusal (the 404 page then carries the person's own shell).
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(feed_url(self.church)).status_code, 404)
        self.client.logout()
        self.set_page(public=True)
        self.assertEqual(self.client.get(feed_url(self.church)).status_code, 200)
        # One event's file: the same answer as an address that never existed.
        absent = self.client.get(reverse("evento:calendar", args=["no-existe"]))
        for event in (self.hidden, self.no_page, self.retiro_b):
            response = self.client.get(file_url(event))
            self.assertEqual((response.status_code, response.content), (404, absent.content))

    def test_a_church_never_serves_another_churchs_events(self):
        services.save_page(self.retiro_b, self.admin_b, public=True)
        services.register(self.retiro_b, self.member_b, self.member_b_user)
        ours, theirs = self.client.get(feed_url(self.church)).content, self.client.get(feed_url(self.church_b)).content
        self.assertIn(b"UID:vigilia-b@", theirs)
        self.assertNotIn(b"retiro-jovenes", theirs)
        self.assertNotIn(b"vigilia-b", ours)
        self.assertNotIn(b"Bruno", theirs)


class SeriesInTheCalendarTests(PublicFixture):
    """A weekly service at 10:00 where the clocks change (Baja California follows the United States' change)."""

    TODAY = date(2026, 10, 19)

    def setUp(self):
        patcher = mock.patch("django.utils.timezone.localdate", return_value=self.TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.set_page(public=False)
        self.item = series.create_series(
            self.church, self.admin, today=self.TODAY, name="Servicio dominical", weekdays="6", starts_at=time(10, 0),
            duration_minutes=90, location="Templo de Tijuana", time_zone="America/Tijuana", starts_on=self.TODAY,
            published=True, public=True, registration="abierta",
        )

    def lines(self) -> list[str]:
        return unfold(self.client.get(feed_url(self.church)).content.decode())

    def test_ten_oclock_stays_ten_oclock_across_the_change_of_clock(self):
        hours = set(EventPage.objects.filter(event__series_occurrence__series=self.item).values_list("starts_at", flat=True))
        self.assertEqual(hours, {time(10, 0)})
        lines = self.lines()
        self.assertEqual(lines.count("BEGIN:VEVENT"), 8)
        # 25 October is summer time (UTC-7); the clocks go back on 1 November, before the service (UTC-8).
        for start, end in (("20261025T170000Z", "20261025T183000Z"), ("20261101T180000Z", "20261101T193000Z"),
                           ("20261108T180000Z", "20261108T193000Z")):
            self.assertIn(f"DTSTART:{start}", lines)
            self.assertIn(f"DTEND:{end}", lines)
        self.assertEqual(lines.count("SUMMARY:Servicio dominical"), 8)
        self.assertEqual(lines.count("LOCATION:Templo de Tijuana"), 8)

    def test_people_who_signed_up_for_a_date_are_not_in_it_and_a_day_called_off_leaves_it(self):
        first, second = SeriesOccurrence.objects.filter(series=self.item).order_by("day")[:2]
        services.register(first.event, self.member, self.member_user)
        with CaptureQueriesContext(connection) as queries:
            content = self.client.get(feed_url(self.church)).content
        self.assertLessEqual(len(queries), 3)
        self.assertNotIn("agenda_eventregistration", " ".join(query["sql"] for query in queries.captured_queries))
        for word in self.private():
            self.assertNotIn(word, content)
        self.assertTrue(EventRegistration.objects.filter(event=first.event).exists())
        series.call_off(second, self.admin)
        lines = self.lines()
        self.assertEqual(lines.count("BEGIN:VEVENT"), 7)
        self.assertNotIn(f"UID:{second.event.slug}@testserver", lines)
        self.assertEqual(self.client.get(public_url(second.event)).status_code, 404)
        series.set_active(self.item, self.admin, False)  # ended: what exists stays
        self.assertEqual(self.lines().count("BEGIN:VEVENT"), 7)
