"""Panorama: who opens it, that it counts and never names, and that it costs the same with two groups or twelve."""

import re
from datetime import time, timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from agenda import services as agenda
from agenda.models import EventRegistration
from care import services as care
from core.models import Role
from core.services import assign_role
from events.models import Event
from events.tests.base import make_user
from groups import services as groups
from groups.models import GroupAttendance, GroupMeeting, GroupMembership
from groups.tests.base import CommunityFixture
from overview import services
from overview.access import can_see_overview
from people import flags
from people.models import Person
from serving import services as serving

URL = reverse("overview:panorama")


class PanoramaFixture(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.today = timezone.localdate()
        cls.church_pastor = make_user("pastora@prueba.mx", "Paula", "Pastora")
        assign_role(cls.church_pastor, cls.church, Role.PASTOR)
        cls.mentor = make_user("mentor@prueba.mx", "Mario", "Mentor")
        assign_role(cls.mentor, cls.church, Role.MENTOR)
        cls._person(cls.church, cls.mentor)
        cls.pastor_b = make_user("pastor-b@prueba.mx", "Benito", "Pastor Be")
        assign_role(cls.pastor_b, cls.church_b, Role.PASTOR)
        # The pastor of an event (an event role) who is also a person of the church: still not the church's pastor.
        cls._person(cls.church, cls.pastor)

        groups.set_membership(cls.group, cls.member, cls.admin)
        groups.set_membership(cls.group, cls.no_account, cls.admin)
        for weeks, present in ((3, [cls.leader, cls.member, cls.no_account]), (2, [cls.leader]), (1, [cls.leader, cls.member])):
            groups.record_meeting(cls.group, cls.today - timedelta(weeks=weeks), [p.pk for p in present], cls.leader_user)

    def get(self, user):
        self.client.force_login(user)
        return self.client.get(URL)


class AccessTests(PanoramaFixture):
    def test_the_pastor_and_the_administration_open_it(self):
        for user in (self.church_pastor, self.admin):
            response = self.get(user)
            self.assertEqual(response.status_code, 200, user.email)
            self.assertContains(response, "no un registro de cada persona")

    def test_nobody_else_does(self):
        for user in (
            self.mentor,         # a church role that does not look after the whole church
            self.leader_user,    # guides a group
            self.member_user,    # a person of the church
            self.pastor,         # "Pastor" of an event: an event role grants no church role
            self.coord1,
        ):
            self.assertEqual(self.get(user).status_code, 404, user.email)
            self.assertFalse(can_see_overview(user, self.church), user.email)
        self.client.logout()
        self.assertEqual(self.client.get(URL).status_code, 302)

    def test_another_church_s_pastor_never_reaches_this_church(self):
        self.assertFalse(can_see_overview(self.pastor_b, self.church))
        # The page has no church in its address: it is always of the reader's own church. A forged current church
        # is dropped (core.authz.current_church), so what answers is still church B, never A.
        self.client.force_login(self.pastor_b)
        session = self.client.session
        session["current_church"] = self.church.pk
        session.save()
        response = self.client.get(URL)
        self.assertContains(response, "Grupo de la iglesia B")
        self.assertNotContains(response, "Jóvenes Roca")
        # With nothing of their own to open, the address answers 404: church A's panorama does not exist for them.
        flags.set_enabled(self.church_b, "community", False)
        session = self.client.session
        session["current_church"] = self.church.pk
        session.save()
        self.assertEqual(self.client.get(URL).status_code, 404)

    def test_the_module_off_hides_it_from_everybody(self):
        flags.set_enabled(self.church, "community", False)
        for user in (self.church_pastor, self.admin):
            self.assertEqual(self.get(user).status_code, 404)


class ContentTests(PanoramaFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        day = cls.today + timedelta(days=10)
        cls.coming = Event.objects.create(church=cls.church, name="Noche de alabanza", slug="noche-a", starts_on=day, ends_on=day)
        agenda.save_page(cls.coming, cls.admin, published=True, registration="abierta", capacity=40, starts_at=time(19, 0), description="")
        agenda.register(cls.coming, cls.member, cls.member_user)
        agenda.register(cls.coming, cls.asked, cls.asked_user)
        past = cls.today - timedelta(days=20)
        cls.done = Event.objects.create(church=cls.church, name="Retiro de matrimonios", slug="retiro-m", starts_on=past, ends_on=past)
        agenda.save_page(cls.done, cls.admin, published=True, registration="abierta", capacity=None, starts_at=None, description="")
        for person, attended in ((cls.member, True), (cls.asked, False), (cls.leader, True)):
            EventRegistration.objects.create(event=cls.done, person=person, attended=attended)
        cls.hidden = Event.objects.create(church=cls.church, name="Evento sin publicar", slug="oculto", starts_on=day, ends_on=day)

        cls.team = serving.save_team(cls.church, cls.admin, name="Bienvenida", description="")
        for person in (cls.leader, cls.member, cls.asked):
            serving.set_member(cls.team, person, cls.admin)
        service = serving.save_date(cls.team, cls.admin, date=cls.today + timedelta(days=5), what="Puerta", starts_at=None, place="")
        answers = {cls.leader: True, cls.member: False}
        for person in (cls.leader, cls.member, cls.asked):
            assignment = serving.assign(service, person, cls.admin)
            if person in answers:
                serving.respond(assignment, answers[person], person.user)

        care.create(cls.church, cls.admin, person=cls.asked, reason="grupo", owner=cls.leader, due_on=cls.today + timedelta(days=3))
        late = care.create(cls.church, cls.admin, person=cls.member, reason="visita", owner=None, due_on=cls.today + timedelta(days=1))
        type(late).objects.filter(pk=late.pk).update(due_on=cls.today - timedelta(days=2))

    def test_groups_in_totals(self):
        data = services.build(self.church, None, self.today)
        self.assertEqual([group.name for group in data["groups"]], ["Estudio de Romanos", "Jóvenes Roca"])
        roca = data["groups"][1]
        self.assertEqual((roca.member_count, roca.waiting, roca.trend, roca.stale), (3, 1, [3, 1, 2], False))
        self.assertEqual((roca.last["present"], roca.last["expected"], roca.last["date"]), (2, 3, self.today - timedelta(weeks=1)))
        self.assertEqual((data["group_people"], data["group_joined"]), (3, 3))
        response = self.get(self.church_pastor)
        self.assertContains(response, "2 de 3 personas asistieron")
        self.assertContains(response, ">3, 1, 2<")
        self.assertContains(response, "1 solicitud espera respuesta")
        self.assertContains(response, "Todavía no tiene")  # the group without any meeting, created today: not pointed out
        self.assertNotContains(response, "Sin ninguna reunión registrada")

    def test_a_group_that_stopped_recording_is_pointed_out(self):
        GroupMeeting.objects.filter(group=self.group, date__gt=self.today - timedelta(weeks=3)).delete()
        last = self.today - timedelta(weeks=3)
        GroupMeeting.objects.filter(group=self.group).update(date=last - timedelta(days=1))
        type(self.other_group).objects.filter(pk=self.other_group.pk).update(created_at=timezone.now() - timedelta(days=40))
        data = services.build(self.church, None, self.today)
        self.assertEqual([group.stale for group in data["groups"]], [True, True])
        response = self.get(self.church_pastor)
        self.assertContains(response, "Sin reunión registrada desde el ")
        self.assertContains(response, "Sin ninguna reunión registrada")
        self.assertContains(response, "2 sin reunión registrada en tres semanas")

    def test_people_who_left_or_joined_long_ago_are_not_counted_as_new(self):
        GroupMembership.objects.filter(person=self.member).update(start_at=timezone.now() - timedelta(days=45))
        groups.end_membership(GroupMembership.objects.get(group=self.group, person=self.no_account), self.admin)
        data = services.build(self.church, None, self.today)
        self.assertEqual((data["group_people"], data["group_joined"]), (2, 1))

    def test_events_serving_and_care_in_totals(self):
        response = self.get(self.church_pastor)
        self.assertContains(response, "2 inscritos de 40 lugares")
        self.assertContains(response, "Quedan 38 lugares")
        self.assertNotContains(response, "Evento sin publicar")
        html = response.content.decode()
        done = html[html.index("Retiro de matrimonios"):]
        self.assertRegex(done, r'(?s)^.*?data-label="Inscritos">3</td>\s*<td[^>]*data-label="Asistieron">2</td>')
        team = html[html.index(">Bienvenida</th>"):]
        self.assertRegex(team, r'(?s)^.*?data-label="Confirmaron">1</td>.*?data-label="Por confirmar">1</td>.*?data-label="No pueden">1</td>')
        data = services.build(self.church, None, self.today)
        self.assertEqual((data["care_open"], data["care_late"], data["care_unowned"]), (2, 1, 1))

    def test_a_follow_up_about_the_reader_is_never_counted(self):
        reader = Person.objects.get(church=self.church, user=self.admin)
        care.create(self.church, self.admin, person=reader, reason="otro", owner=None, due_on=None)
        self.assertEqual(services.build(self.church, reader, self.today)["care_open"], 2)
        self.assertEqual(services.build(self.church, None, self.today)["care_open"], 3)

    def test_an_empty_church_is_told_what_to_do(self):
        type(self.group_b).objects.filter(pk=self.group_b.pk).update(status="archivado")
        response = self.get(self.admin_b)
        for words in ("Todavía no hay grupos activos", "No hay eventos publicados por venir", "Ningún equipo tiene fechas por venir", "No hay seguimientos abiertos"):
            self.assertContains(response, words)
        self.assertContains(response, reverse("community:group_create"))
        self.assertNotContains(response, "<table")

    def test_nothing_of_another_church_is_counted(self):
        groups.set_membership(self.group_b, self.member_b, self.admin_b)
        groups.record_meeting(self.group_b, self.today, [self.member_b.pk], self.admin_b)
        care.create(self.church_b, self.admin_b, person=self.member_b, reason="otro", owner=None, due_on=None)
        data = services.build(self.church, None, self.today)
        self.assertEqual((len(data["groups"]), data["group_people"], data["care_open"]), (2, 3, 2))
        self.assertNotContains(self.get(self.church_pastor), "Grupo de la iglesia B")


    def test_a_meeting_that_kept_only_its_totals_still_counts(self):
        # prune_attendance deleted who came and kept how many: the page reads the total that was kept.
        meeting = GroupMeeting.objects.get(group=self.group, date=self.today - timedelta(weeks=1))
        meeting.attendances.all().delete()
        GroupMeeting.objects.filter(pk=meeting.pk).update(present_kept=2)
        data = services.build(self.church, None, self.today)
        self.assertEqual(data["groups"][1].trend, [3, 1, 2])
        self.assertContains(self.get(self.church_pastor), "2 de 3 personas asistieron")


class PrivacyTests(ContentTests):
    NAMES = ("Diego", "Hernández", "Gala Guía", "Pía", "Pidió", "Nora", "Sin Cuenta", "Mario", "Coordinador", "diego@", "guia@", "55 1111")

    def test_no_person_is_named(self):
        for user in (self.church_pastor, self.admin):
            html = self.get(user).content.decode()
            page = html[html.index('<div class="panorama">'):html.index("</main>")]
            for name in self.NAMES:
                self.assertNotIn(name, page, (user.email, name))
        # For the pastor the whole document is clean, navigation included: only her own name is on it.
        html = self.get(self.church_pastor).content.decode()
        for name in self.NAMES:
            self.assertNotIn(name, html, name)

    def test_the_numbers_carry_no_person(self):
        data = services.build(self.church, None, self.today)
        text = repr({key: value for key, value in data.items() if key != "groups"}) + repr([vars(group) for group in data["groups"]])
        self.assertNotIn("person", text.replace("group_people", ""))
        self.assertEqual(set(data["groups"][1].last), {"id", "group_id", "date", "expected", "present_kept", "present"})

    def test_every_link_of_the_pastor_s_page_opens_and_none_leads_to_who_came(self):
        html = self.get(self.church_pastor).content.decode()
        links = set(re.findall(r'href="(/[^"#]*)"', html))
        self.assertIn(reverse("community:group_detail", args=[self.group.pk]), links)
        self.assertIn(reverse("agenda:detail", args=[self.coming.slug]), links)
        for link in sorted(links):
            if link.startswith("/static/"):
                continue
            for private in ("/reuniones/", "/personas/", "/inscritos/", "/acompanar/administrar", "/equipos/"):
                self.assertNotIn(private, link)
            self.assertIn(self.client.get(link).status_code, (200, 302), link)
        # The group's page says nothing of who came to somebody who does not guide it, and the lists stay closed.
        detail = self.client.get(reverse("community:group_detail", args=[self.group.pk]))
        self.assertNotContains(detail, "Tu asistencia")
        self.assertNotContains(detail, "reuniones/")
        meeting = GroupMeeting.objects.filter(group=self.group).first()
        for url in (
            reverse("community:group_meetings", args=[self.group.pk]),
            reverse("community:meeting_record", args=[self.group.pk, meeting.date.isoformat()]),
            reverse("community:group_people", args=[self.group.pk]),
            reverse("agenda:registrations", args=[self.done.slug]),
            reverse("care:admin_list"),
        ):
            self.assertEqual(self.client.get(url).status_code, 403, url)


class CostTests(PanoramaFixture):
    def queries(self) -> int:
        self.client.force_login(self.church_pastor)
        self.client.get(URL)  # the first visit creates the pastor's own person
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(self.client.get(URL).status_code, 200)
        return len(captured)

    def test_the_numbers_take_a_fixed_number_of_queries(self):
        with self.assertNumQueries(10):
            services.build(self.church, None, self.today)

    def test_more_groups_cost_no_more_queries(self):
        before = self.queries()
        for number in range(10):
            group = self._group(self.church, f"Grupo {number}", meeting_day=2)
            groups.set_membership(group, self.member, self.admin)
            groups.set_membership(group, self.asked, self.admin)
            for weeks in range(1, 11):
                meeting = GroupMeeting.objects.create(group=group, date=self.today - timedelta(weeks=weeks), expected=2)
                GroupAttendance.objects.create(meeting=meeting, person=self.member)
        with self.assertNumQueries(10):
            data = services.build(self.church, None, self.today)
        self.assertEqual(len(data["groups"]), 12)
        self.assertEqual(data["groups"][2].trend, [1] * services.TREND_MEETINGS)
        self.assertEqual(self.queries(), before)
