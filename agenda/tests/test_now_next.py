""""Ahora y después" on the camp's homes (agenda/now_next.py, templates/events/_now_next.html).

The programme of an event can exist for a camp too. When today, in the event's time zone, has programme items, the
homes of the coordinator, the tutor, the pastor and the administration say what is happening and what follows, in
the page and in its live fragment. It is the public programme: read-only, the same for every role, nothing about a
campero. With the community module off those homes do not change.
"""

import re
from datetime import date, datetime, time, timezone as dt_timezone
from unittest import mock
from zoneinfo import ZoneInfo

from django.urls import reverse

from agenda.models import EventScheduleItem
from agenda.now_next import NowNext, now_and_next
from events.models import Event
from events.tests.base import CampFixture
from people import flags
from people.tests.test_screens_unchanged import masked

DAY = date(2026, 7, 14)
MEXICO = ZoneInfo("America/Mexico_City")
STRIP = re.compile(r'<section class="notice notice--quiet now-next" aria-labelledby="ahora-y-despues">(.*?)</section>', re.DOTALL)
HOMES = ("coord_home", "tutor_home", "pastor_home", "admin_home")


def at(hour: int, minute: int = 0, day: date = DAY, tz=MEXICO) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


def lines(html: str) -> list[str]:
    """The sentences of the strip as a person reads them, or [] without a strip."""
    found = STRIP.findall(html)
    assert len(found) <= 1, "one strip at most"
    if not found:
        return []
    return [re.sub(r"<[^>]+>", "", line).strip() for line in re.findall(r'<p class="now-next__line">(.*?)</p>', found[0], re.DOTALL)]


class ProgrammeFixture(CampFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        flags.set_enabled(cls.church, "community", True)
        make = EventScheduleItem.objects.create
        cls.breakfast = make(event=cls.event, day=DAY, starts_at=time(8, 0), ends_at=time(9, 0), title="Desayuno", place="Comedor")
        cls.talk = make(event=cls.event, day=DAY, starts_at=time(10, 0), ends_at=time(11, 30), title="Plática", place="Auditorio")
        cls.games = make(event=cls.event, day=DAY, starts_at=time(16, 0), title="Juegos")  # no end, no place
        cls.fire = make(event=cls.event, day=DAY, starts_at=time(21, 0), ends_at=time(22, 0), title="Fogata", place="Explanada")
        # Another event of the same church has its own programme at the same hours: never read here
        make(event=cls.other_event, day=DAY, starts_at=time(8, 0), ends_at=time(23, 0), title="Programa de otro evento")


class NowAndNextTests(ProgrammeFixture):
    def test_before_the_first_item_only_what_follows(self):
        self.assertEqual(now_and_next(self.event, at(7, 30)), NowNext(next=self.breakfast))

    def test_during_an_item_with_its_end_and_what_follows(self):
        self.assertEqual(now_and_next(self.event, at(8, 0)), NowNext(now=self.breakfast, now_until=time(9, 0), next=self.talk))
        self.assertEqual(now_and_next(self.event, at(10, 45)), NowNext(now=self.talk, now_until=time(11, 30), next=self.games))

    def test_between_two_items_only_what_follows(self):
        self.assertEqual(now_and_next(self.event, at(9, 0)), NowNext(next=self.talk))  # the end is not part of the item
        self.assertEqual(now_and_next(self.event, at(12, 0)), NowNext(next=self.games))

    def test_an_item_without_an_end_runs_until_the_next_one_starts(self):
        self.assertEqual(now_and_next(self.event, at(18, 0)), NowNext(now=self.games, now_until=None, next=self.fire))
        self.assertEqual(now_and_next(self.event, at(21, 0)), NowNext(now=self.fire, now_until=time(22, 0)))

    def test_the_last_item_without_an_end_runs_until_the_day_ends(self):
        self.fire.delete()
        self.assertEqual(now_and_next(self.event, at(23, 59)), NowNext(now=self.games))

    def test_after_the_last_item_the_programme_is_over(self):
        self.assertEqual(now_and_next(self.event, at(22, 0)), NowNext(over=True))
        self.assertEqual(now_and_next(self.event, at(23, 30)), NowNext(over=True))

    def test_another_day_says_nothing(self):
        self.assertIsNone(now_and_next(self.event, at(10, 30, day=date(2026, 7, 13))))
        self.assertIsNone(now_and_next(self.event, at(10, 30, day=date(2026, 7, 15))))

    def test_of_two_at_once_the_one_that_started_last(self):
        workshop = EventScheduleItem.objects.create(event=self.event, day=DAY, starts_at=time(10, 30), ends_at=time(11, 0), title="Taller")
        self.assertEqual(now_and_next(self.event, at(10, 45)), NowNext(now=workshop, now_until=time(11, 0), next=self.games))
        self.assertEqual(now_and_next(self.event, at(11, 15)), NowNext(now=self.talk, now_until=time(11, 30), next=self.games))

    def test_the_module_off_says_nothing(self):
        flags.set_enabled(self.church, "community", False)
        self.assertIsNone(now_and_next(self.event, at(10, 45)))
        flags.set_enabled(self.church, "community", True)
        with self.settings(EMMAUS_V2_MODULES=False):  # the kill switch
            self.assertIsNone(now_and_next(self.event, at(10, 45)))

    def test_the_hour_and_the_day_are_the_events_own(self):
        """21:30 in Tijuana is 22:30 in Mexico City: the same instant is "Fogata" there and "ya terminó" here."""
        instant = datetime(2026, 7, 15, 4, 30, tzinfo=dt_timezone.utc)
        self.assertEqual(now_and_next(self.event, instant), NowNext(over=True))
        Event.objects.filter(pk=self.event.pk).update(time_zone="America/Tijuana")
        self.event.refresh_from_db()
        self.assertEqual(now_and_next(self.event, instant), NowNext(now=self.fire, now_until=time(22, 0)))
        # 23:30 of the 14th in Tijuana is already the 15th in Mexico City and in UTC: the day is Tijuana's
        late = datetime(2026, 7, 15, 6, 30, tzinfo=dt_timezone.utc)
        self.assertEqual(now_and_next(self.event, late), NowNext(over=True))
        Event.objects.filter(pk=self.event.pk).update(time_zone="America/Mexico_City")
        self.event.refresh_from_db()
        self.assertIsNone(now_and_next(self.event, late))

    def test_it_costs_two_queries_and_one_with_the_module_off(self):
        with self.assertNumQueries(2):
            now_and_next(self.event, at(10, 45))
        flags.set_enabled(self.church, "community", False)
        with self.assertNumQueries(1):
            now_and_next(self.event, at(10, 45))


class StripOnTheHomesTests(ProgrammeFixture):
    def users(self):
        return {"coord_home": self.coord1, "tutor_home": self.tutor1, "pastor_home": self.pastor, "admin_home": self.admin}

    def get(self, name: str, when: datetime, *, fragment: bool = False) -> str:
        self.client.force_login(self.users()[name])
        extra = {"HTTP_X_REQUESTED_WITH": "fetch"} if fragment else {}
        with mock.patch("agenda.now_next.timezone.now", return_value=when):
            response = self.client.get(reverse(f"events:{name}", args=[self.event.slug]), **extra)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_every_home_and_its_live_fragment_say_the_same_two_sentences(self):
        expected = ["Ahora: Plática, en Auditorio (hasta las 11:30)", "Después: Juegos, a las 16:00"]
        for name in HOMES:
            with self.subTest(home=name):
                page = self.get(name, at(10, 45))
                self.assertEqual(lines(page), expected)
                fragment = self.get(name, at(10, 45), fragment=True)
                self.assertEqual(lines(fragment), expected)
                # The first thing of the fragment, so the page's own refresh moves it on; never a live region of its own
                self.assertTrue(fragment.startswith('<section class="notice notice--quiet now-next"'), fragment[:80])
                self.assertNotIn("aria-live", STRIP.search(fragment).group(0))
                self.assertIn('<h2 id="ahora-y-despues" class="visually-hidden">Programa de hoy</h2>', fragment)

    def test_each_moment_of_the_day_in_words(self):
        for when, expected in (
            (at(7, 30), ["Después: Desayuno, a las 08:00"]),
            (at(8, 30), ["Ahora: Desayuno, en Comedor (hasta las 09:00)", "Después: Plática, a las 10:00"]),
            (at(9, 30), ["Después: Plática, a las 10:00"]),
            (at(18, 0), ["Ahora: Juegos", "Después: Fogata, a las 21:00"]),  # no place, no end: neither is made up
            (at(21, 30), ["Ahora: Fogata, en Explanada (hasta las 22:00)"]),
            (at(22, 30), ["Hoy ya terminó el programa."]),
        ):
            with self.subTest(when=when.time()):
                self.assertEqual(lines(self.get("coord_home", when, fragment=True)), expected)

    def test_a_day_without_programme_draws_nothing(self):
        for name in HOMES:
            self.assertEqual(lines(self.get(name, at(10, 45, day=date(2026, 7, 15)))), [])
            self.assertNotIn("now-next", self.get(name, at(10, 45, day=date(2026, 7, 15)), fragment=True))

    def test_the_events_time_zone_decides_what_the_home_says(self):
        Event.objects.filter(pk=self.event.pk).update(time_zone="America/Tijuana")
        instant = datetime(2026, 7, 15, 4, 30, tzinfo=dt_timezone.utc)  # 21:30 of the 14th in Tijuana
        self.assertEqual(lines(self.get("tutor_home", instant)), ["Ahora: Fogata, en Explanada (hasta las 22:00)"])

    def test_with_the_module_off_the_homes_are_byte_for_byte_what_they_are_without_a_programme(self):
        flags.set_enabled(self.church, "community", False)
        for name in HOMES:
            for fragment in (False, True):
                with self.subTest(home=name, fragment=fragment):
                    self.get(name, at(10, 45), fragment=fragment)  # first sign-in settles "Acceso del equipo"
                    with_programme = self.get(name, at(10, 45), fragment=fragment)
                    self.assertNotIn("now-next", with_programme)
                    self.assertNotIn("Plática", with_programme)
        reference = {(name, fragment): masked_html(self.get(name, at(10, 45), fragment=fragment)) for name in HOMES for fragment in (False, True)}
        EventScheduleItem.objects.all().delete()
        for (name, fragment), html in reference.items():
            self.assertEqual(masked_html(self.get(name, at(10, 45), fragment=fragment)), html, (name, fragment))

    def test_the_strip_names_no_campero_and_another_events_programme_never_shows(self):
        for name in HOMES:
            strip = STRIP.search(self.get(name, at(10, 45))).group(0)
            for participant in (self.p1, self.p2, self.p3, self.p4):
                self.assertNotIn(participant.full_name, strip)
            self.assertNotIn("Programa de otro evento", strip)
            self.assertNotIn("<a ", strip)  # read-only: it leads nowhere
            self.assertNotIn("<form", strip)

    def test_a_title_is_escaped(self):
        self.talk.title = "<b>Plática</b> & café"
        self.talk.save()
        page = self.get("coord_home", at(10, 45), fragment=True)
        self.assertIn("&lt;b&gt;Plática&lt;/b&gt; &amp; café", page)
        self.assertNotIn("<b>Plática</b>", page)


def masked_html(html: str) -> str:
    class _Response:
        content = html.encode()

    return masked(_Response())
