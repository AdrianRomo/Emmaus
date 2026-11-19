"""Servir, wave 12: a team whose dates follow a series of events, and templates of the order
(``serving.follow``). A date written from a series is an ordinary date: the rules of every date are
run over it here, unchanged."""

from datetime import date, time, timedelta
from unittest import mock

from django.core import mail
from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from agenda import series as event_series
from agenda.models import EventSeries, SeriesOccurrence
from core import scheduler
from core.management.commands import run_scheduled_jobs
from core.models import SchedulerHeartbeat
from people import flags
from people.models import ChurchAuditEvent, Person
from serving import follow, reminders, rotas, services
from serving.models import Assignment, PlanItem, PlanTemplate, PlanTemplateItem, RoleNeed, ServiceDate, Team, TeamRole, Unavailable

from .test_rotas import RotasFixture

WEEKS = event_series.WINDOW_WEEKS
SECRET = "Reunión reservada del consejo"


class FollowFixture(RotasFixture):
    """The fixture of the rotation, plus a published weekly series of events of church A whose first date
    is tomorrow at 11:00, and one of church B."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tomorrow = cls.today + timedelta(days=1)
        cls.series = cls.make_series(cls.church, cls.admin, "Reunión general", published=True)
        cls.series_b = cls.make_series(cls.church_b, cls.admin_b, "Reunión de la iglesia B", published=True)

    @classmethod
    def make_series(cls, church, admin, name, **fields) -> EventSeries:
        values = {"weekdays": str(cls.tomorrow.weekday()), "starts_at": time(11), "starts_on": cls.tomorrow, "location": "Templo principal",
                  **fields}
        return event_series.create_series(church, admin, name=name, **values)

    def follow(self, series=None, **extra) -> int:
        return follow.follow(self.team, self.leader_user, series or self.series, is_church_admin=False, **extra)

    def generated(self, team=None):
        return ServiceDate.objects.filter(team=team or self.team, occurrence__isnull=False).order_by("date")

    def occurrences(self, series=None):
        return SeriesOccurrence.objects.filter(series=series or self.series).order_by("day")


class GenerationTests(FollowFixture):
    def test_following_writes_one_ordinary_date_per_date_of_the_series(self):
        self.assertEqual(self.follow(), WEEKS)
        rows = list(self.generated())
        self.assertEqual([row.date for row in rows], [self.tomorrow + timedelta(weeks=n) for n in range(WEEKS)])
        for row, occurrence in zip(rows, self.occurrences()):
            self.assertEqual((row.occurrence, row.starts_at, row.what, row.place, row.called_off),
                             (occurrence, time(11), "Reunión general", "Templo principal", False))
        self.assertFalse(Assignment.objects.filter(service_date__in=rows).exists())   # nobody is asked by a series
        audit = ChurchAuditEvent.objects.get(action="equipo_serie_fechas")
        self.assertEqual((audit.metadata, "Reunión" in audit.summary), ({"count": WEEKS}, False))

    def test_twice_writes_nothing_more_and_one_date_per_team_and_occurrence(self):
        self.follow()
        self.assertEqual(follow.sync_team(self.team), 0)
        self.assertEqual(follow.sync_due(), {"teams": 1, "created": 0, "failed": 0})
        self.assertEqual(self.generated().count(), WEEKS)
        first = self.generated()[0]
        with self.assertRaises(IntegrityError), transaction.atomic():
            ServiceDate.objects.create(team=self.team, date=first.date, what="Otra vez", occurrence=first.occurrence)
        # Another team may follow the same series: the rule is per team.
        services.set_member(self.other_team, self.leader, self.admin, role="coordina")
        self.assertEqual(follow.follow(self.other_team, self.leader_user, self.series, is_church_admin=False), WEEKS)

    def test_a_new_date_of_the_series_gets_its_date_on_the_next_pass(self):
        self.follow()
        later = timezone.now() + timedelta(weeks=1)
        event_series.generate(self.series, now=later)
        self.assertEqual(follow.sync_due(now=later)["created"], 1)
        self.assertEqual(follow.sync_due(now=later)["created"], 0)

    def test_new_dates_take_what_each_role_asks_and_the_default_template(self):
        rotas.save_role(self.team, self.leader_user, role=self.door, name="Puerta", default_count=2)
        rotas.save_plan_item(self.day, self.leader_user, title="Bienvenida", minutes=10, responsible=self.member)
        rotas.save_plan_item(self.day, self.leader_user, title="Anuncios", minutes=5)
        template = follow.save_template(self.day, self.leader_user, "Servicio dominical")
        self.follow(default_template=template)
        for row in self.generated():
            self.assertEqual(dict(RoleNeed.objects.filter(service_date=row).values_list("role_id", "count")), {self.door.pk: 2})
            self.assertEqual(list(PlanItem.objects.filter(service_date=row).values_list("position", "title", "minutes", "responsible")),
                             [(1, "Bienvenida", 10, None), (2, "Anuncios", 5, None)])
        # What existed is not rewritten: the date made by hand keeps asking for nobody.
        self.assertFalse(RoleNeed.objects.filter(service_date=self.day).exists())

    def test_dates_made_by_hand_are_untouched_and_their_day_gets_no_second_date(self):
        mine = services.save_date(self.team, self.leader_user, date=self.tomorrow + timedelta(weeks=2), starts_at=time(8), what="Lo que ya tenía")
        services.assign(mine, self.member, self.leader_user)
        before = list(ServiceDate.objects.filter(pk__in=[mine.pk, self.day.pk]).values("pk", "date", "starts_at", "what", "place", "occurrence"))
        self.assertEqual(self.follow(), WEEKS - 1)
        self.assertEqual(list(ServiceDate.objects.filter(pk__in=[mine.pk, self.day.pk]).values("pk", "date", "starts_at", "what", "place", "occurrence")), before)
        self.assertEqual(ServiceDate.objects.filter(team=self.team, date=mine.date).count(), 1)
        self.assertTrue(Assignment.objects.filter(service_date=mine, person=self.member).exists())

    def test_nothing_for_a_church_that_is_off_a_team_that_stopped_or_one_that_follows_nothing(self):
        self.assertEqual(follow.sync_team(self.team), 0)   # follows nothing
        Team.objects.filter(pk=self.team.pk).update(follows_series=self.series)
        flags.set_enabled(self.church, "community", False)
        self.assertEqual(follow.sync_team(self.team), 0)
        self.assertEqual(follow.sync_due(), {"teams": 0, "created": 0, "failed": 0})
        flags.set_enabled(self.church, "community", True)
        with override_settings(EMMAUS_V2_MODULES=False):
            self.assertEqual(follow.sync_team(self.team), 0)
        type(self.church).objects.filter(pk=self.church.pk).update(active=False)
        self.assertEqual(follow.sync_team(self.team), 0)
        self.assertEqual(follow.sync_due()["teams"], 0)
        type(self.church).objects.filter(pk=self.church.pk).update(active=True)
        Team.objects.filter(pk=self.team.pk).update(status=Team.Status.ARCHIVED)
        self.assertEqual(follow.sync_team(self.team), 0)
        self.assertEqual(follow.sync_due()["teams"], 0)
        Team.objects.filter(pk=self.team.pk).update(status=Team.Status.ACTIVE)
        self.assertFalse(self.generated().exists())
        self.assertEqual(follow.sync_due(), {"teams": 1, "created": WEEKS, "failed": 0})   # it catches up

    def test_a_series_of_another_church_is_refused_and_a_forced_link_writes_nothing(self):
        for is_admin in (False, True):
            with self.assertRaisesMessage(services.ServingError, "de esta iglesia"):
                follow.follow(self.team, self.admin, self.series_b, is_church_admin=is_admin)
        self.assertIsNone(Team.objects.get(pk=self.team.pk).follows_series)
        Team.objects.filter(pk=self.team.pk).update(follows_series=self.series_b)
        self.assertEqual(follow.sync_team(self.team), 0)
        self.assertFalse(self.generated().exists())

    def test_who_coordinates_chooses_among_what_the_church_already_sees(self):
        hidden = self.make_series(self.church, self.admin, SECRET)   # in preparation: not published
        ended = self.make_series(self.church, self.admin, "Terminó", published=True)
        event_series.set_active(ended, self.admin, False)
        self.assertEqual(list(follow.series_choices(self.team, is_church_admin=False)), [self.series])
        self.assertEqual(set(follow.series_choices(self.team, is_church_admin=True)), {self.series, hidden})
        for series in (hidden, ended):
            with self.assertRaisesMessage(services.ServingError, "de esta iglesia"):
                self.follow(series)
        self.assertEqual(follow.follow(self.team, self.admin, hidden, is_church_admin=True), WEEKS)
        # Who coordinates may keep what the administration set while changing something else, and may drop it.
        rotas.save_plan_item(self.day, self.leader_user, title="Bienvenida", minutes=10)
        template = follow.save_template(self.day, self.leader_user, "Dominical")
        self.follow(hidden, default_template=template)
        self.assertEqual(Team.objects.get(pk=self.team.pk).default_template, template)
        follow.follow(self.team, self.leader_user, None, is_church_admin=False)
        self.assertEqual((Team.objects.get(pk=self.team.pk).follows_series, self.generated().count()), (None, WEEKS))

    def test_one_team_that_fails_does_not_stop_the_next(self):
        services.set_member(self.other_team, self.leader, self.admin, role="coordina")
        Team.objects.filter(pk__in=[self.team.pk, self.other_team.pk]).update(follows_series=self.series)
        real = follow.sync_team

        def failing(team, **kwargs):
            if team.pk == self.team.pk:
                raise RuntimeError(SECRET)
            return real(team, **kwargs)

        with mock.patch("serving.follow.sync_team", side_effect=failing), self.assertLogs("serving.follow", "ERROR") as logs:
            self.assertEqual(follow.sync_due(), {"teams": 2, "created": WEEKS, "failed": 1})
        self.assertNotIn(SECRET, " ".join(logs.output))

    def test_a_date_written_for_the_series_is_not_deleted_while_the_team_follows_it(self):
        self.follow()
        first = self.generated()[0]
        with self.assertRaisesMessage(services.ServingError, "la vuelve a escribir"):
            services.delete_date(first, self.leader_user)
        self.client.force_login(self.leader_user)
        page = self.client.get(self.url("date_detail", first.pk))
        self.assertNotContains(page, "Borrar la fecha")
        self.assertContains(page, "no se borra")
        self.assertContains(self.client.get(self.url("date_detail", self.day.pk)), "Borrar la fecha")
        follow.follow(self.team, self.leader_user, None, is_church_admin=False)
        services.delete_date(first, self.leader_user)
        self.assertEqual(self.generated().count(), WEEKS - 1)


class CalledOffTests(FollowFixture):
    def setUp(self):
        self.follow()
        self.first, self.second = self.generated()[0], self.generated()[1]
        rotas.set_needs(self.second, self.leader_user, {self.door.pk: 2})
        self.yes = services.assign(self.second, self.member, self.leader_user, role=self.door)
        services.respond(self.yes, True, self.member_user)
        self.waits = services.assign(self.second, self.asked, self.leader_user)
        self.off = self.occurrences()[1]
        event_series.call_off(self.off, self.admin)
        self.second = ServiceDate.objects.get(pk=self.second.pk)

    def test_the_date_is_cancelled_and_its_people_stay_listed_for_who_coordinates(self):
        self.assertTrue(self.second.called_off)
        self.assertEqual(Assignment.objects.filter(service_date=self.second).count(), 2)   # nothing was deleted
        self.client.force_login(self.leader_user)
        page = self.client.get(self.url("team", self.team.pk))
        self.assertContains(page, "Este día no hay")
        self.assertContains(page, "Emmaus no avisa: avísales tú.")
        self.assertContains(page, "Había confirmado")
        self.assertContains(page, "No había respondido")
        self.assertContains(page, "Diego Hernández Cruz (Puerta)")
        self.assertEqual([row.pk for row in page.context["called_off"]], [self.second.pk])
        self.assertNotIn(self.second.pk, [row.pk for row in page.context["dates"]])
        self.assertNotContains(page, self.url("date_detail", self.second.pk))
        self.assertEqual(self.client.get(self.url("date_detail", self.second.pk)).status_code, 404)
        self.assertEqual(mail.outbox, [])   # nobody is told, as the screen says

    def test_who_was_asked_reads_it_and_has_nothing_to_answer(self):
        self.assertEqual(services.pending_count(self.asked), 0)
        self.assertEqual(services.my_dates(self.member), [])
        self.assertEqual([row.pk for row in services.all_my_dates(self.asked)], [self.waits.pk])
        self.client.force_login(self.asked_user)
        page = self.client.get(self.url("home"))
        self.assertContains(page, "Ese día no hay: la fecha se canceló")
        self.assertNotContains(page, "Sí, puedo")
        self.assertNotContains(page, self.url("respond", self.waits.pk))
        with self.assertRaisesMessage(services.ServingError, "Ese día no hay"):
            services.respond(self.waits, True, self.asked_user)
        response = self.client.post(self.url("respond", self.waits.pk), {"respuesta": "aceptar"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Assignment.objects.get(pk=self.waits.pk).status, "pendiente")

    def test_nothing_about_it_changes_and_nothing_counts_on_it(self):
        with self.assertRaises(services.ServingError) as refused:
            services.assign(self.second, self.carlos, self.leader_user)
        self.assertEqual(refused.exception.code, "called_off")
        for write in (lambda: services.unassign(self.second, self.member, self.leader_user),
                      lambda: services.save_date(self.team, self.leader_user, service_date=self.second, date=self.second.date, what="Otro"),
                      lambda: rotas.set_needs(self.second, self.leader_user, {self.door.pk: 1}),
                      lambda: rotas.save_plan_item(self.second, self.leader_user, title="Bienvenida", minutes=5),
                      lambda: services.delete_date(self.second, self.leader_user)):
            with self.assertRaises(services.ServingError) as refused:
                write()
            self.assertEqual(refused.exception.code, "called_off")
        # Who was asked that day is free for another date of the same day, and the rotation proposes nobody for it.
        self.assertFalse(services.assigned_on(self.member, self.second.date))
        self.assertEqual(rotas.busy_elsewhere([self.member.pk, self.asked.pk], self.second.date), set())
        self.assertFalse(rotas.is_coming(self.second))
        rotas.save_rotation(self.team, self.leader_user, every_weeks=1, orders={self.door.pk: [self.carlos.pk, self.leader.pk]})
        self.assertNotIn(self.second.pk, [entry.service_date.pk for entry in rotas.build_proposal(self.team).dates])
        self.assertFalse(rotas.may_ask_swap(Assignment.objects.select_related("service_date", "person").get(pk=self.yes.pk)))
        self.assertEqual(services.uncovered_dates(self.leader), [])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMMAUS_PUBLIC_BASE_URL="https://emmaus.example.org")
    def test_no_reminder_goes_out_for_it(self):
        Team.objects.filter(pk=self.team.pk).update(reminder_days=5)
        near = timezone.now() + timedelta(days=(self.second.date - self.today).days - 2)
        self.assertEqual(reminders.due(near), [])
        event_series.restore(self.off, self.admin)
        self.assertEqual({row.pk for row in reminders.due(near)}, {self.yes.pk, self.waits.pk})

    def test_when_the_day_is_on_again_so_is_the_date_with_its_people(self):
        event_series.restore(self.off, self.admin)
        self.second = ServiceDate.objects.get(pk=self.second.pk)
        self.assertFalse(self.second.called_off)
        self.assertEqual(services.pending_count(self.asked), 1)
        self.assertEqual(Assignment.objects.get(pk=self.yes.pk).status, "aceptada")
        self.assertEqual(follow.sync_team(self.team), 0)   # and it is not written a second time
        services.assign(self.second, self.carlos, self.leader_user)

    def test_a_day_called_off_before_the_team_follows_gets_no_date_until_it_is_on(self):
        services.set_member(self.other_team, self.leader, self.admin, role="coordina")
        self.assertEqual(follow.follow(self.other_team, self.leader_user, self.series, is_church_admin=False), WEEKS - 1)
        self.assertFalse(ServiceDate.objects.filter(team=self.other_team, occurrence=self.off).exists())
        event_series.restore(self.off, self.admin)
        self.assertEqual(follow.sync_due()["created"], 1)


class SameRulesTests(FollowFixture):
    """The rules of every date, run over a date a series wrote: nothing is different for it."""

    def setUp(self):
        rotas.save_role(self.team, self.leader_user, role=self.door, name="Puerta", default_count=1)
        rotas.save_role(self.team, self.leader_user, role=self.kids, name="Niños", adults_only=True, default_count=1)
        self.follow()
        self.dates = list(self.generated())
        self.first = self.dates[0]

    def test_adults_only_and_ese_dia_no_puedo_hold(self):
        with self.assertRaises(services.ServingError) as refused:   # an age the church has not verified
            services.assign(self.first, self.member, self.leader_user, role=self.kids)
        self.assertEqual(refused.exception.code, "adults_only")
        self.adult(self.member)
        self.member.refresh_from_db()
        services.assign(self.first, self.member, self.leader_user, role=self.kids)
        Unavailable.objects.create(person=self.asked, date=self.first.date)
        with self.assertRaises(services.ServingError) as refused:
            services.assign(self.first, self.asked, self.leader_user)
        self.assertEqual(refused.exception.code, "unavailable")
        with self.assertRaises(services.ServingError) as refused:   # not of the team
            services.assign(self.first, self.plain, self.leader_user)
        self.assertEqual(refused.exception.code, "not_member")

    def test_eligibility_is_read_when_the_page_is_read(self):
        self.adult(self.member)
        self.member.refresh_from_db()
        row = services.assign(self.first, self.member, self.leader_user, role=self.kids)
        self.assertEqual([need.missing for need in rotas.missing_by_date([self.first])[self.first.pk]], [0, 1])
        Person.objects.filter(pk=self.member.pk).update(birth_date=date(self.today.year - 10, 1, 1))   # corrected later
        row = Assignment.objects.select_related("person", "role", "service_date").get(pk=row.pk)
        self.assertTrue(rotas.unfit(row))
        self.assertEqual(sorted(need.missing for need in rotas.missing_by_date([self.first])[self.first.pk]), [1, 1])
        self.client.force_login(self.leader_user)
        self.assertContains(self.client.get(self.url("team", self.team.pk)), "Ya no puede cubrir este rol")

    def test_the_rotation_has_dates_to_work_on_without_anybody_typing_them(self):
        self.adult(self.carlos)
        rotas.save_rotation(self.team, self.leader_user, every_weeks=1,
                            orders={self.door.pk: [self.member.pk, self.asked.pk], self.kids.pk: [self.carlos.pk, self.member.pk]})
        proposal = rotas.build_proposal(self.team)
        generated = {row.pk for row in self.dates}
        proposed = [entry for entry in proposal.dates if entry.service_date.pk in generated]
        self.assertEqual(len(proposed), WEEKS)
        for entry in proposed:
            by_role = {slot.role.pk: slot.proposed for slot in entry.slots}
            # The door rotates; the room for adults only ever gets who the church verified as adult.
            self.assertIn(by_role[self.door.pk], (self.member.pk, self.asked.pk))
            self.assertEqual(by_role[self.kids.pk], self.carlos.pk)
        choices = {slot.key: slot.proposed for entry in proposal.dates for slot in entry.slots}
        done = rotas.confirm_proposal(self.team, self.leader_user, token=proposal.token, choices=choices)
        self.assertEqual(done, 2 * WEEKS)
        self.assertEqual(Assignment.objects.filter(service_date__in=self.dates, status="pendiente").count(), 2 * WEEKS)
        self.assertEqual(rotas.build_proposal(self.team).dates, [])   # every place is taken: nothing more to propose
        with self.assertRaises(rotas.StaleProposal):
            rotas.confirm_proposal(self.team, self.leader_user, token=proposal.token, choices=choices)

    def test_a_swap_moves_the_date_only_when_the_teammate_accepts(self):
        mine = services.assign(self.first, self.member, self.leader_user, role=self.door)
        services.respond(mine, True, self.member_user)
        self.assertIn(self.asked.pk, {person.pk for person in rotas.swap_candidates(mine)})
        swap = rotas.request_swap(mine, self.asked, self.member_user)
        self.assertEqual(Assignment.objects.get(pk=mine.pk).person, self.member)
        rotas.answer_swap(swap, True, self.asked_user)
        moved = Assignment.objects.get(pk=mine.pk)
        self.assertEqual((moved.person, moved.status, moved.role), (self.asked, "aceptada", self.door))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMMAUS_PUBLIC_BASE_URL="https://emmaus.example.org")
    def test_one_content_free_reminder_per_assignment(self):
        Team.objects.filter(pk=self.team.pk).update(reminder_days=2)
        services.assign(self.first, self.member, self.leader_user)
        self.assertEqual(reminders.send_due("https://emmaus.example.org")["sent"], 1)
        self.assertEqual(reminders.send_due("https://emmaus.example.org")["sent"], 0)
        message = mail.outbox[0]
        for hidden in ("Reunión general", "Bienvenida", "Templo", "Diego"):
            self.assertNotIn(hidden, message.subject + message.body)

    def test_the_person_answers_and_changes_their_answer_as_on_any_date(self):
        mine = services.assign(self.dates[2], self.member, self.leader_user)
        self.assertEqual(services.pending_count(self.member), 1)
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(self.url("home")), "Reunión general")
        self.client.post(self.url("respond", mine.pk), {"respuesta": "aceptar"})
        self.assertEqual(Assignment.objects.get(pk=mine.pk).status, "aceptada")
        self.client.post(self.url("respond", mine.pk), {"respuesta": "declinar"})
        self.assertEqual(Assignment.objects.get(pk=mine.pk).status, "declinada")

    def test_a_date_whose_event_moved_is_flagged_and_never_moved_by_itself(self):
        occurrence = self.first.occurrence
        type(occurrence.event).objects.filter(pk=occurrence.event_id).update(starts_on=self.first.date + timedelta(days=1))
        self.assertEqual(follow.sync_team(self.team), 0)
        self.assertEqual(ServiceDate.objects.get(pk=self.first.pk).date, self.first.date)
        self.client.force_login(self.leader_user)
        self.assertContains(self.client.get(self.url("team", self.team.pk)), "Esta fecha no cambia sola", count=1)


class RoleDefaultTests(FollowFixture):
    def test_a_role_says_how_many_people_a_new_date_asks_for(self):
        self.assertEqual(TeamRole.objects.get(pk=self.door.pk).default_count, 0)
        self.client.force_login(self.leader_user)
        self.client.post(self.url("role_save", self.team.pk), {"rol": self.door.pk, "nombre": "Puerta", "por_fecha": "3"})
        self.assertEqual(TeamRole.objects.get(pk=self.door.pk).default_count, 3)
        self.client.post(self.url("role_save", self.team.pk), {"rol": self.door.pk, "nombre": "Puerta"})   # a page from before
        self.assertEqual(TeamRole.objects.get(pk=self.door.pk).default_count, 3)
        for wrong in ("21", "9999", "-1", "muchas", "1.5"):
            response = self.client.post(self.url("role_save", self.team.pk), {"rol": self.door.pk, "nombre": "Puerta", "por_fecha": wrong},
                                        follow=True)
            self.assertContains(response, "Escribe un número entre 0 y 20", msg_prefix=wrong)
        self.assertEqual(TeamRole.objects.get(pk=self.door.pk).default_count, 3)
        self.client.post(self.url("role_save", self.team.pk), {"nombre": "Sonido", "por_fecha": ""})
        self.assertEqual(TeamRole.objects.get(team=self.team, name="Sonido").default_count, 0)
        # The form of a new date starts with it; a date that exists says what it has.
        self.assertContains(self.client.get(self.url("date_create", self.team.pk)), f'name="necesita_{self.door.pk}" min="0" max="20" inputmode="numeric" value="3"')
        self.assertContains(self.client.get(self.url("date_detail", self.day.pk)), f'name="necesita_{self.door.pk}" min="0" max="20" inputmode="numeric" value="0"')
        self.assertContains(self.client.get(self.url("organize", self.team.pk)), 'name="por_fecha" value="3"')
        self.assertEqual(TeamRole._meta.get_field("default_count").db_default, 0)


class TemplateTests(FollowFixture):
    def setUp(self):
        rotas.save_plan_item(self.day, self.leader_user, title="Bienvenida", minutes=10, responsible=self.member)
        rotas.save_plan_item(self.day, self.leader_user, title="Mensaje", minutes=35, responsible=self.leader)
        self.later = self.new_day(14)

    def test_a_template_holds_titles_and_minutes_and_no_people(self):
        template = follow.save_template(self.day, self.leader_user, "  Servicio   dominical ")
        self.assertEqual(template.name, "Servicio dominical")
        self.assertEqual(list(PlanTemplateItem.objects.filter(template=template).values_list("position", "title", "minutes")),
                         [(1, "Bienvenida", 10), (2, "Mensaje", 35)])
        self.assertFalse(any(field.related_model is Person for field in PlanTemplateItem._meta.get_fields() if field.is_relation))
        self.assertFalse(any(field.related_model is Person for field in PlanTemplate._meta.get_fields() if field.is_relation))
        self.assertEqual(follow.use_template(self.later, template, self.leader_user), 2)
        self.assertEqual(list(PlanItem.objects.filter(service_date=self.later).values_list("title", "minutes", "responsible")),
                         [("Bienvenida", 10, None), ("Mensaje", 35, None)])
        # The date it came from keeps who was in charge; later changes to it do not reach the template.
        self.assertEqual(PlanItem.objects.filter(service_date=self.day, responsible__isnull=False).count(), 2)
        PlanItem.objects.filter(service_date=self.day).delete()
        self.assertEqual(PlanTemplateItem.objects.filter(template=template).count(), 2)
        logged = " ".join(f"{row.summary} {row.metadata}" for row in ChurchAuditEvent.objects.filter(action="equipo_plantilla"))
        self.assertNotIn("dominical", logged)
        self.assertNotIn("Mensaje", logged)

    def test_only_into_an_empty_order_and_copying_the_previous_one_still_works(self):
        template = follow.save_template(self.day, self.leader_user, "Dominical")
        self.assertEqual(rotas.copy_previous_plan(self.later, self.leader_user), 2)
        with self.assertRaises(services.ServingError) as refused:
            follow.use_template(self.later, template, self.leader_user)
        self.assertEqual(refused.exception.code, "not_empty")
        self.assertEqual(PlanItem.objects.filter(service_date=self.later).count(), 2)

    def test_what_a_template_must_be(self):
        empty = self.new_day(21)
        for code, day, name in (("empty", empty, "Vacía"), ("empty", self.day, "   "), ("lines", self.day, "Uno\nDos"),
                                ("too_long", self.day, "x" * 61), ("link", self.day, "Mira https://ejemplo.invalid")):
            with self.assertRaises(services.ServingError, msg=name) as refused:
                follow.save_template(day, self.leader_user, name)
            self.assertEqual(refused.exception.code, code, name)
        follow.save_template(self.day, self.leader_user, "Dominical")
        with self.assertRaises(services.ServingError) as refused:
            follow.save_template(self.day, self.leader_user, "DOMINICAL")
        self.assertEqual(refused.exception.code, "duplicate")
        self.assertEqual(PlanTemplate.objects.count(), 1)

    def test_ten_templates_per_team_and_no_more(self):
        for number in range(follow.MAX_TEMPLATES):
            follow.save_template(self.day, self.leader_user, f"Plantilla {number}")
        with self.assertRaises(services.ServingError) as refused:
            follow.save_template(self.day, self.leader_user, "Una más")
        self.assertEqual(refused.exception.code, "too_many")
        self.assertEqual(PlanTemplate.objects.filter(team=self.team).count(), 10)
        follow.delete_template(PlanTemplate.objects.first(), self.leader_user)
        follow.save_template(self.day, self.leader_user, "Una más")
        # The limit is each team's own.
        services.set_member(self.other_team, self.leader, self.admin, role="coordina")
        other_day = self.new_day(7, team=self.other_team)
        rotas.save_plan_item(other_day, self.leader_user, title="Ensayo", minutes=30)
        follow.save_template(other_day, self.leader_user, "Ensayo general")

    def test_a_template_of_another_team_is_never_used_or_made_the_default(self):
        services.set_member(self.other_team, self.leader, self.admin, role="coordina")
        other_day = self.new_day(7, team=self.other_team)
        rotas.save_plan_item(other_day, self.leader_user, title="Ensayo", minutes=30)
        foreign = follow.save_template(other_day, self.leader_user, "Ensayo general")
        with self.assertRaises(services.ServingError) as refused:
            follow.use_template(self.later, foreign, self.leader_user)
        self.assertEqual(refused.exception.code, "template")
        with self.assertRaises(services.ServingError) as refused:
            self.follow(default_template=foreign)
        self.assertEqual(refused.exception.code, "template")
        self.assertFalse(PlanItem.objects.filter(service_date=self.later).exists())
        # A default forced onto the team from another one gives a new date no order at all.
        Team.objects.filter(pk=self.team.pk).update(follows_series=self.series, default_template=foreign)
        self.assertEqual(follow.sync_team(self.team), WEEKS)
        self.assertFalse(PlanItem.objects.filter(service_date__in=self.generated()).exists())

    def test_removing_a_template_leaves_the_orders_and_the_team_without_a_default(self):
        template = follow.save_template(self.day, self.leader_user, "Dominical")
        self.follow(default_template=template)
        follow.delete_template(template, self.leader_user)
        team = Team.objects.get(pk=self.team.pk)
        self.assertEqual((team.default_template, team.follows_series, PlanTemplate.objects.count()), (None, self.series, 0))
        self.assertEqual(PlanItem.objects.filter(service_date__in=self.generated()).count(), 2 * WEEKS)
        follow.delete_template(template, self.leader_user)   # twice: nothing

    def test_the_coordinator_saves_and_uses_a_template_from_the_order_and_it_is_escaped(self):
        risky = '<script>alert("x")</script>'
        self.client.force_login(self.leader_user)
        page = self.client.get(self.url("plan", self.day.pk))
        self.assertContains(page, "Guardar como plantilla")
        self.assertContains(page, "No lleva canciones, letras, acordes, archivos ni enlaces")
        response = self.client.post(self.url("template_save", self.day.pk), {"nombre": risky}, follow=True)
        self.assertContains(response, "sin responsables")
        self.assertNotContains(response, "<script>alert")
        template = PlanTemplate.objects.get()
        empty = self.client.get(self.url("plan", self.later.pk))
        self.assertContains(empty, "Empezar con una plantilla")
        self.assertContains(empty, "Copiar del anterior")
        self.assertNotContains(empty, "<script>alert")
        self.assertContains(empty, "&lt;script&gt;")
        used = self.client.post(self.url("template_use", self.later.pk), {"plantilla": template.pk}, follow=True)
        self.assertContains(used, "Elige quién queda a cargo de cada uno")
        self.assertEqual(PlanItem.objects.filter(service_date=self.later, responsible__isnull=True).count(), 2)
        organize = self.client.get(self.url("organize", self.team.pk))
        self.assertContains(organize, "2 momentos, 45 minutos")
        self.assertNotContains(organize, "<script>alert")
        self.assertContains(organize, "No lleva canciones, letras, acordes, archivos ni enlaces")
        ask = self.client.get(self.url("template_delete", self.team.pk, template.pk))
        self.assertContains(ask, "Los órdenes que empezaron con ella se quedan como están")
        self.assertNotContains(ask, "<script>alert")
        self.client.post(self.url("template_delete", self.team.pk, template.pk))
        self.assertFalse(PlanTemplate.objects.exists())
        # Who only serves reads the order and is offered none of it.
        services.assign(self.later, self.member, self.leader_user)
        reader_client = Client()   # a browser of its own: no message of the coordinator's waits in it
        reader_client.force_login(self.member_user)
        reader = reader_client.get(self.url("plan", self.later.pk))
        self.assertContains(reader, "Bienvenida")
        self.assertNotContains(reader, "plantilla")


class ScreenTests(FollowFixture):
    def test_who_coordinates_says_the_team_serves_on_every_date_of_a_series(self):
        hidden = self.make_series(self.church, self.admin, SECRET)
        self.client.force_login(self.leader_user)
        page = self.client.get(self.url("organize", self.team.pk))
        self.assertContains(page, "Fechas que siguen una serie")
        self.assertContains(page, "Reunión general (cada ")
        self.assertContains(page, "Emmaus no avisa: avísales tú.")
        for other in (SECRET, "Reunión de la iglesia B"):
            self.assertNotContains(page, other)
        for refused in (hidden, self.series_b):
            response = self.client.post(self.url("follow_save", self.team.pk), {"serie": refused.pk}, follow=True)
            self.assertContains(response, "Elige una serie de eventos de esta iglesia.")
        self.assertIsNone(Team.objects.get(pk=self.team.pk).follows_series)
        response = self.client.post(self.url("follow_save", self.team.pk), {"serie": self.series.pk}, follow=True)
        self.assertRedirects(response, self.url("team", self.team.pk))
        self.assertContains(response, f"Escribimos {WEEKS} fechas del equipo")
        self.assertContains(response, "El equipo sirve en cada fecha de la serie Reunión general")
        self.assertContains(response, "Falta elegir quién sirve", count=WEEKS + 1)
        again = self.client.post(self.url("follow_save", self.team.pk), {"serie": self.series.pk}, follow=True)
        self.assertContains(again, "No había fechas nuevas que escribir")
        stopped = self.client.post(self.url("follow_save", self.team.pk), {"serie": ""}, follow=True)
        self.assertContains(stopped, "Las fechas que ya tenía se quedan como están")
        self.assertEqual(self.generated().count(), WEEKS)
        self.assertEqual(list(ChurchAuditEvent.objects.filter(action="equipo_serie").order_by("pk").values_list("metadata", flat=True)),
                         [{"value": "sigue"}, {"value": "no_sigue"}])
        # The administration is offered the series still in preparation; the coordinator then reads what the team follows.
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url("organize", self.team.pk)), SECRET)
        self.client.post(self.url("follow_save", self.team.pk), {"serie": hidden.pk})
        self.client.force_login(self.leader_user)
        self.assertContains(self.client.get(self.url("organize", self.team.pk)), SECRET)

    def test_a_template_of_another_team_or_church_is_not_in_the_list(self):
        day_b = services.save_date(self.team_b, self.admin_b, date=self.week, what="B")
        rotas.save_plan_item(day_b, self.admin_b, title="Cosa de B", minutes=5)
        foreign = follow.save_template(day_b, self.admin_b, "Plantilla de B")
        self.client.force_login(self.leader_user)
        response = self.client.post(self.url("follow_save", self.team.pk), {"serie": self.series.pk, "plantilla": foreign.pk}, follow=True)
        self.assertContains(response, "Elige una plantilla de este equipo.")
        self.assertIsNone(Team.objects.get(pk=self.team.pk).follows_series)
        self.assertEqual(self.client.post(self.url("template_use", self.day.pk), {"plantilla": foreign.pk}).status_code, 404)
        self.assertEqual(self.client.get(self.url("template_delete", self.team.pk, foreign.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url("template_delete", self.team.pk, foreign.pk)).status_code, 404)
        self.assertNotContains(self.client.get(self.url("organize", self.team.pk)), "Plantilla de B")
        self.assertTrue(PlanTemplate.objects.filter(pk=foreign.pk).exists())


class AccessTests(FollowFixture):
    """Every new address, for everybody who must not get in."""

    def setUp(self):
        rotas.save_plan_item(self.day, self.leader_user, title="Bienvenida", minutes=5)
        self.template = follow.save_template(self.day, self.leader_user, "Dominical")
        self.empty = self.new_day(14)
        team = self.team.pk
        self.requests = [
            ("follow_save", (team,), {"serie": self.series.pk}), ("template_save", (self.day.pk,), {"nombre": "Otra"}),
            ("template_use", (self.empty.pk,), {"plantilla": self.template.pk}),
            ("template_delete", (team, self.template.pk), None), ("template_delete", (team, self.template.pk), {}),
        ]

    def hit(self, name, args, data, client=None):
        client = client or self.client
        return client.get(self.url(name, *args)) if data is None else client.post(self.url(name, *args), data)

    def unchanged(self):
        team = Team.objects.get(pk=self.team.pk)
        self.assertEqual((team.follows_series, team.default_template), (None, None))
        self.assertEqual(list(PlanTemplate.objects.values_list("name", flat=True)), ["Dominical"])
        self.assertFalse(PlanItem.objects.filter(service_date=self.empty).exists())
        self.assertFalse(ServiceDate.objects.filter(occurrence__isnull=False).exists())

    def test_signed_out_goes_to_sign_in(self):
        for name, args, data in self.requests:
            response = self.hit(name, args, data)
            self.assertEqual(response.status_code, 302, name)
            self.assertTrue(response["Location"].startswith(reverse("login")), name)
        self.unchanged()

    def test_who_only_serves_a_plain_member_and_the_coordinator_of_another_team_get_403(self):
        services.set_member(self.other_team, self.plain, self.admin, role="coordina")
        for user in (self.member_user, self.asked_user, self.coord1, self.plain_user):
            self.client.force_login(user)
            for name, args, data in self.requests:
                self.assertEqual(self.hit(name, args, data).status_code, 403, (user.email, name))
        self.unchanged()

    def test_another_church_is_a_404_everywhere(self):
        for user in (self.member_b_user, self.admin_b):
            self.client.force_login(user)
            for name, args, data in self.requests:
                self.assertEqual(self.hit(name, args, data).status_code, 404, (user.email, name))
        for user in (self.leader_user, self.admin):   # and the other way
            self.client.force_login(user)
            for name, args, data in (("follow_save", (self.team_b.pk,), {"serie": self.series_b.pk}),
                                     ("template_save", (self.day_b.pk,), {"nombre": "X"}),
                                     ("template_use", (self.day_b.pk,), {"plantilla": self.template.pk}),
                                     ("template_delete", (self.team_b.pk, self.template.pk), {})):
                self.assertEqual(self.hit(name, args, data).status_code, 404, (user.email, name))
        self.assertIsNone(Team.objects.get(pk=self.team_b.pk).follows_series)
        self.unchanged()

    def test_with_the_module_off_everything_is_a_404(self):
        flags.set_enabled(self.church, "community", False)
        for user in (self.leader_user, self.admin, self.member_user):
            self.client.force_login(user)
            for name, args, data in self.requests:
                self.assertEqual(self.hit(name, args, data).status_code, 404, (user.email, name))
        self.unchanged()

    def test_an_archived_team_is_closed(self):
        services.archive_team(self.team, self.admin)
        for user in (self.leader_user, self.admin):
            self.client.force_login(user)
            for name, args, data in self.requests:
                self.assertIn(self.hit(name, args, data).status_code, (403, 404), (user.email, name))
        self.unchanged()

    def test_every_post_needs_its_csrf_token_and_get_changes_nothing(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.leader_user)
        for name, args, data in self.requests:
            if data is not None:
                self.assertEqual(self.hit(name, args, data, client).status_code, 403, name)
        self.client.force_login(self.leader_user)
        for name, args in (("follow_save", (self.team.pk,)), ("template_save", (self.day.pk,)), ("template_use", (self.empty.pk,))):
            self.assertEqual(self.client.get(self.url(name, *args)).status_code, 405, name)
        self.unchanged()


class SchedulerTests(FollowFixture):
    def test_the_job_is_registered_as_one_that_acts_and_waits_for_the_switches(self):
        self.assertIn(scheduler.REPETITION, scheduler.JOBS)
        self.assertNotIn(scheduler.REPETITION, scheduler.DELETING_JOBS)
        self.assertEqual(scheduler.skipped(scheduler.REPETITION), "")
        for off in ({"EMMAUS_SCHEDULED_COMMUNITY_JOBS": False}, {"EMMAUS_V2_MODULES": False}):
            with self.settings(**off):
                self.assertEqual(scheduler.skipped(scheduler.REPETITION), scheduler.OFF)

    def test_one_pass_writes_the_bookings_and_the_dates_and_a_second_one_nothing(self):
        from espacios import repeticion
        from espacios.models import Booking, Space

        room = Space.objects.create(church=self.church, name="Salón 2")
        Team.objects.filter(pk=self.team.pk).update(follows_series=self.series)
        with mock.patch("espacios.repeticion._generate", return_value=0):   # approved, and nothing written yet
            booked = repeticion.request_series(room, self.leader, self.admin, title="Ensayo", starts_on=self.tomorrow,
                                               ends_on=self.tomorrow + timedelta(weeks=20), starts_at=time(18), ends_at=time(20), approve=True)
        self.assertEqual(run_scheduled_jobs.write_what_repeats(), {"bookings": WEEKS, "dates": WEEKS, "failed": 0})
        self.assertEqual(run_scheduled_jobs.write_what_repeats(), {"bookings": 0, "dates": 0, "failed": 0})
        self.assertEqual((Booking.objects.filter(series_day__series=booked).count(), self.generated().count()), (WEEKS, WEEKS))
        # A church with the module off: nothing is written for it, and the job still ends well.
        flags.set_enabled(self.church, "community", False)
        later = timezone.now() + timedelta(weeks=2)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.assertEqual(run_scheduled_jobs.write_what_repeats(), {"bookings": 0, "dates": 0, "failed": 0})

    def test_one_half_failing_never_stops_the_other_and_fails_the_pass_without_its_message(self):
        Team.objects.filter(pk=self.team.pk).update(follows_series=self.series)
        with mock.patch("espacios.repeticion.generate_due", side_effect=RuntimeError(SECRET)), \
                self.assertLogs("core.management.commands.run_scheduled_jobs", "ERROR") as plain:
            done = run_scheduled_jobs.write_what_repeats()
        self.assertEqual(done, {"bookings": 0, "dates": WEEKS, "failed": 1})
        self.assertEqual(run_scheduled_jobs.repetition_verdict(done), "RepetitionFailed")
        self.assertNotIn(SECRET, " ".join(plain.output))
        with mock.patch("serving.follow.sync_due", return_value={"teams": 1, "created": 0, "failed": 1}), \
                self.assertLogs("emmaus.scheduler", "ERROR") as lines:
            run = scheduler.run_job(scheduler.REPETITION, run_scheduled_jobs.write_what_repeats,
                                    verdict=run_scheduled_jobs.repetition_verdict)
        self.assertFalse(run.ok)
        self.assertIn('"exception": "RepetitionFailed"', lines.output[0])
        self.assertEqual(SchedulerHeartbeat.objects.get(job=scheduler.REPETITION).last_error, "RepetitionFailed")
