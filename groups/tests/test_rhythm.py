"""Each group's rhythm: every week, every two weeks, once a month or without a fixed day. What the app says is the
next meeting, which meeting waits for its list, and what the form asks for each of them."""

from datetime import date, time, timedelta

from django.urls import reverse
from django.utils import timezone

from groups import services
from groups.forms import GroupForm
from groups.models import Group, GroupMembership, nth_weekday
from groups.templatetags.community import rhythm
from groups.tests.base import CommunityFixture

TUESDAY = date(2026, 11, 17)


def group(**fields) -> Group:
    fields.setdefault("meeting_day", 3)  # Thursday
    return Group(name="Ritmo", **fields)


class RhythmTests(CommunityFixture):
    def test_every_week(self):
        weekly = group()
        self.assertEqual((weekly.last_meeting_day(TUESDAY), weekly.next_meeting(TUESDAY)), (date(2026, 11, 12), date(2026, 11, 19)))
        thursday = date(2026, 11, 19)
        self.assertEqual((weekly.last_meeting_day(thursday), weekly.next_meeting(thursday)), (thursday, thursday))
        self.assertEqual(weekly.schedule, "Los jueves")

    def test_every_two_weeks_from_a_date_they_do_meet(self):
        # The anchor may be any day of a week they meet, past or to come: 5 November, then 19 November, 3 December.
        for anchor in (date(2026, 11, 5), date(2026, 11, 3), date(2026, 12, 3), date(2026, 9, 24)):
            biweekly = group(meeting_frequency="quincenal", meeting_anchor=anchor, meeting_time=time(19, 30))
            self.assertEqual((biweekly.last_meeting_day(TUESDAY), biweekly.next_meeting(TUESDAY)), (date(2026, 11, 5), date(2026, 11, 19)), anchor)
            self.assertEqual(biweekly.next_meeting(date(2026, 11, 20)), date(2026, 12, 3))
            self.assertEqual(biweekly.last_meeting_day(date(2026, 11, 19)), date(2026, 11, 19))
        self.assertEqual(biweekly.schedule, "Los jueves, cada dos semanas, a las 19:30")
        self.assertEqual(group(meeting_frequency="quincenal", meeting_anchor=TUESDAY).schedule, "Los jueves, cada dos semanas")

    def test_once_a_month(self):
        second = group(meeting_frequency="mensual", meeting_week=2)  # second Thursday: 12 November, 10 December
        self.assertEqual((second.last_meeting_day(TUESDAY), second.next_meeting(TUESDAY)), (date(2026, 11, 12), date(2026, 12, 10)))
        self.assertEqual(second.last_meeting_day(date(2026, 11, 11)), date(2026, 10, 8))
        self.assertEqual(second.schedule, "El segundo jueves de cada mes")
        last = group(meeting_frequency="mensual", meeting_week=5, meeting_day=6, meeting_time=time(13, 0))  # last Sunday
        self.assertEqual((last.last_meeting_day(TUESDAY), last.next_meeting(TUESDAY)), (date(2026, 10, 25), date(2026, 11, 29)))
        self.assertEqual(last.next_meeting(date(2026, 12, 28)), date(2027, 1, 31))  # across the year
        self.assertEqual(last.last_meeting_day(date(2027, 1, 5)), date(2026, 12, 27))
        self.assertEqual(last.schedule, "El último domingo de cada mes a las 13:00")
        self.assertEqual(nth_weekday(2026, 2, 6, 4), nth_weekday(2026, 2, 6, 5))  # February 2026 has four Sundays

    def test_without_a_fixed_day_the_app_does_not_guess(self):
        for unknown in (
            group(meeting_frequency="variable"), group(meeting_day=None),
            group(meeting_frequency="quincenal"),  # no date they do meet
            group(meeting_frequency="mensual"),    # no week of the month
        ):
            self.assertEqual((unknown.has_fixed_day, unknown.next_meeting(TUESDAY), unknown.last_meeting_day(TUESDAY)), (False, None, None))
        self.assertEqual(group(meeting_frequency="variable").schedule, "Sin día fijo")
        self.assertEqual(group(meeting_frequency="mensual").schedule, "Los jueves, una vez al mes")
        self.assertEqual(group(meeting_day=None).schedule, "")

    def test_the_day_and_the_hour_are_two_facts_with_the_schedule_s_words(self):
        for fields, days, hour in (
            ({"meeting_time": time(19, 30)}, "Los jueves", time(19, 30)),
            ({}, "Los jueves", None),
            ({"meeting_frequency": "quincenal", "meeting_anchor": TUESDAY, "meeting_time": time(19, 30)}, "Los jueves, cada dos semanas", time(19, 30)),
            ({"meeting_frequency": "mensual", "meeting_week": 2, "meeting_time": time(8, 0)}, "El segundo jueves de cada mes", time(8, 0)),
            ({"meeting_frequency": "variable", "meeting_time": time(19, 30)}, "Sin día fijo", None),  # the schedule says no hour
            ({"meeting_day": None, "meeting_time": time(19, 30)}, "", None),
        ):
            one = group(**fields)
            self.assertEqual(rhythm(one), {"days": days, "time": hour}, fields)
            self.assertEqual(one.meeting_time, fields.get("meeting_time"))  # the group itself is not touched

    def test_an_archived_group_has_no_next_meeting(self):
        self.assertIsNone(group(status="archivado").next_meeting(TUESDAY))


class RhythmOnThePagesTests(CommunityFixture):
    def rhythm(self, **changes):
        Group.objects.filter(pk=self.group.pk).update(created_at=timezone.now() - timedelta(days=90), **changes)
        self.group.refresh_from_db()
        GroupMembership.objects.filter(group=self.group).exclude(person=self.leader).delete()
        type(self.pending).objects.filter(pk=self.pending.pk).delete()
        return self.group

    def test_the_guide_is_asked_for_the_list_of_the_meeting_that_was_due_by_the_rhythm(self):
        today = timezone.localdate()
        # They meet every two weeks and last met nine days ago: the day of this week is not theirs.
        last = today - timedelta(days=9)
        group = self.rhythm(meeting_frequency="quincenal", meeting_day=last.weekday(), meeting_anchor=last, meeting_time=None)
        self.assertEqual(services.meeting_due(group, today), last)
        self.assertEqual(group.next_meeting(today), last + timedelta(days=14))
        self.client.force_login(self.leader_user)
        home = self.client.get(reverse("community:home"))
        self.assertContains(home, "¿Quién vino a Jóvenes Roca?")
        self.assertContains(home, reverse("community:meeting_record", args=[group.pk, last.isoformat()]))
        services.record_meeting(group, last, [], self.leader_user)
        home = self.client.get(reverse("community:home"))
        self.assertNotContains(home, "¿Quién vino a")  # the week in between asks for nothing
        self.assertNotContains(home, "Sin día de reunión")  # the row says the next meeting's day
        detail = self.client.get(reverse("community:group_detail", args=[group.pk]))
        self.assertContains(detail, "Los jueves, cada dos semanas" if last.weekday() == 3 else ", cada dos semanas")
        self.assertContains(detail, '<h2 id="reunion" class="meeting__title">Próxima reunión</h2>')

    def test_a_group_without_a_fixed_day_is_never_asked(self):
        group = self.rhythm(meeting_frequency="variable", meeting_day=None)
        self.assertIsNone(services.meeting_due(group))
        self.client.force_login(self.leader_user)
        home = self.client.get(reverse("community:home"))
        self.assertNotContains(home, "¿Quién vino a")
        self.assertContains(home, "Sin día fijo")
        detail = self.client.get(reverse("community:group_detail", args=[group.pk]))
        self.assertContains(detail, '<h2 id="reunion" class="meeting__title">Sin día fijo</h2>')
        self.assertContains(detail, "Quien guía el grupo te avisará")
        meetings = self.client.get(reverse("community:group_meetings", args=[group.pk]))
        # No day to ask about: choosing the day is the way to pass the list, and the section says just that.
        self.assertContains(meetings, '<h2 id="otra-fecha" class="heading">Pasar lista</h2>')
        self.assertContains(meetings, "</svg>Sin día fijo</li>")


class RhythmFormTests(CommunityFixture):
    BASE = {"name": "Matrimonios", "kind": "pequeno", "description": "", "audience": "", "meeting_time": "", "meeting_place": ""}

    def form(self, **data):
        return GroupForm({**self.BASE, **data}, church=self.church)

    def test_every_week_needs_nothing_more_and_keeps_nothing_of_another_rhythm(self):
        form = self.form(meeting_frequency="semanal", meeting_day="3", meeting_week="2", meeting_anchor="2026-11-12")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual((form.cleaned_data["meeting_week"], form.cleaned_data["meeting_anchor"]), (None, None))
        self.assertTrue(self.form(meeting_frequency="semanal", meeting_day="").is_valid())

    def test_every_two_weeks_needs_a_date_on_the_day_they_meet(self):
        form = self.form(meeting_frequency="quincenal", meeting_day="3")
        self.assertEqual(form.errors["meeting_anchor"], ["Elige una fecha en que sí se reúnen, para saber qué semanas les toca."])
        form = self.form(meeting_frequency="quincenal", meeting_day="3", meeting_anchor="2026-11-13")
        self.assertIn("Esa fecha cae en viernes y el grupo se reúne los jueves.", form.errors["meeting_anchor"][0])
        form = self.form(meeting_frequency="quincenal", meeting_day="3", meeting_anchor="2026-11-12", meeting_week="1")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["meeting_week"])
        # With the date and no day, the day is the date's.
        form = self.form(meeting_frequency="quincenal", meeting_day="", meeting_anchor="2026-11-13")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["meeting_day"], 4)
        self.assertTrue(self.form(meeting_frequency="quincenal", meeting_day="").is_valid())  # nothing known yet
        self.assertEqual(self.form(meeting_frequency="quincenal", meeting_day="3", meeting_anchor="mañana").errors["meeting_anchor"],
                         ["Escribe la fecha con el calendario o así: 2026-11-12."])

    def test_once_a_month_needs_which_one(self):
        form = self.form(meeting_frequency="mensual", meeting_day="3")
        self.assertEqual(form.errors["meeting_week"], ["Elige cuál del mes: por ejemplo, el segundo."])
        form = self.form(meeting_frequency="mensual", meeting_day="3", meeting_week="5", meeting_anchor="2026-11-12")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual((form.cleaned_data["meeting_week"], form.cleaned_data["meeting_anchor"]), (5, None))

    def test_without_a_fixed_day_keeps_no_day(self):
        form = self.form(meeting_frequency="variable", meeting_day="3", meeting_week="2", meeting_anchor="2026-11-12")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual([form.cleaned_data[name] for name in ("meeting_day", "meeting_week", "meeting_anchor")], [None, None, None])

    def test_the_administration_saves_a_rhythm_and_the_group_page_says_it(self):
        self.client.force_login(self.admin)
        url = reverse("community:group_edit", args=[self.group.pk])
        page = self.client.get(url)
        for words in ("Cada cuándo", "Cada dos semanas", "Una vez al mes", "Sin día fijo", "Si es cada dos semanas, una fecha en que sí se reúnen", "Si es una vez al mes, cuál"):
            self.assertContains(page, words)
        data = {**self.BASE, "name": self.group.name, "meeting_frequency": "mensual", "meeting_day": "3", "meeting_week": ""}
        response = self.client.post(url, data)
        self.assertContains(response, "Elige cuál del mes")
        self.assertContains(response, "Error: ")
        self.client.post(url, {**data, "meeting_week": "2", "meeting_time": "19:30"})
        self.group.refresh_from_db()
        self.assertEqual((self.group.meeting_frequency, self.group.meeting_week), ("mensual", 2))
        # The day and the hour are two facts, each with its mark, on the group's page and in the list.
        for name, args in (("community:group_detail", [self.group.pk]), ("community:groups", [])):
            page = self.client.get(reverse(name, args=args))
            self.assertContains(page, "</svg>El segundo jueves de cada mes</li>")
            self.assertContains(page, "</svg>19:30</li>")
