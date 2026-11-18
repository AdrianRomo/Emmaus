"""Panorama: who opens it, that it counts and never names, that a total over very few people is never shown, that
a list is missing only when the group's own rhythm had a meeting, and that it costs the same with two groups or twelve.

The page draws its totals (rows, tiles, a bar, marks) instead of writing them in sentences, so these tests read it
one piece at a time: ``section`` (from a heading to the next), ``row_of`` (one row or one tile) and ``totals``
(the named totals inside it). A number is a number; words the service gave instead of a number are on a neutral
pill (``pill``), and so is a total it kept back."""

import re
from datetime import date, datetime, time, timedelta

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
from groups.models import Group, GroupAttendance, GroupMeeting, GroupMembership
from groups.tests.base import CommunityFixture
from overview import services
from overview.access import can_see_overview
from people import flags
from people.models import Person
from serving import services as serving

URL = reverse("overview:panorama")


def pill(words: str) -> str:
    """A total told in words, as the page draws it (``overview.templatetags.panorama.told``)."""
    return f'<span class="chip chip--plain">{words}</span>'


def section(html: str, heading: str) -> str:
    """One section of the page: from the id of its heading to the next section."""
    start = html.index(f'id="{heading}"')
    end = html.find("<section", start)
    return html[start:end if end != -1 else html.index("</main>")]


def row_of(html: str, heading: str, name: str) -> str:
    """The whole row (or tile) of ``name`` in one section: its ``<li>``, from the tag to its end. A row of this
    page holds no list of its own, so the first ``</li>`` after the name is its end; this fails if that stops
    being true, instead of reading a row cut short."""
    part = section(html, heading)
    at = part.index(name)
    row = part[part.rindex("<li", 0, at):part.index("</li>", at)]
    assert row.count("<li") == 1, "a row holds a list: row_of would cut it short"
    return row


def totals(html: str) -> dict:
    """The named totals of a row, a tile or a section, each as the page draws it:
    ``{"Inscritos": "6", "Asistieron": pill("La mitad o más")}``."""
    return dict(re.findall(r"<dt>([^<]+)</dt><dd[^>]*>(.*?)</dd>", html))


def numbers(row: str) -> list[str]:
    """Every number a person reads in a row (markup and its attributes left out)."""
    return re.findall(r"\d+", re.sub(r"<[^>]+>", " ", row))


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

        # Seven people in the group: over the minimum reporting size, so its totals are shown.
        cls.extras = [Person.objects.create(church=cls.church, first_name=f"Extra{n}", last_name="Relleno") for n in range(4)]
        for person in (cls.member, cls.no_account, *cls.extras):
            groups.set_membership(cls.group, person, cls.admin)
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
        for person, attended in ((cls.member, True), (cls.asked, False), (cls.leader, True), *((extra, True) for extra in cls.extras[:3])):
            EventRegistration.objects.create(event=cls.done, person=person, attended=attended)
        cls.hidden = Event.objects.create(church=cls.church, name="Evento sin publicar", slug="oculto", starts_on=day, ends_on=day)

        cls.team = serving.save_team(cls.church, cls.admin, name="Bienvenida", description="")
        for person in (cls.leader, cls.member, cls.asked, *cls.extras[:2]):
            serving.set_member(cls.team, person, cls.admin)
        service = serving.save_date(cls.team, cls.admin, date=cls.today + timedelta(days=5), what="Puerta", starts_at=None, place="")
        cls.service = service
        answers = {cls.leader: True, cls.member: False}
        for person in (cls.leader, cls.member, cls.asked, *cls.extras[:2]):
            assignment = serving.assign(service, person, cls.admin)
            if person in answers:
                serving.respond(assignment, answers[person], person.user)

        care.create(cls.church, cls.admin, person=cls.asked, reason="grupo", owner=cls.leader, due_on=cls.today + timedelta(days=3))
        late = care.create(cls.church, cls.admin, person=cls.member, reason="visita", owner=None, due_on=cls.today + timedelta(days=1))
        type(late).objects.filter(pk=late.pk).update(due_on=cls.today - timedelta(days=2))

    def test_a_group_is_an_arch_and_nothing_on_the_page_is_a_person_s_circle(self):
        # «Ventanas»: a group is a gathering (an arch); the page names no person, so no circle is drawn at all. The
        # follow-ups are their icon, never initials in a circle.
        html = self.get(self.church_pastor).content.decode()
        page = html[html.index('<div class="panorama">'):html.index("</main>")]
        drawn = re.findall(r'<span class="(mono[^"]*)" aria-hidden="true">', page)
        self.assertTrue(drawn)
        self.assertEqual(set(drawn), {"mono mono--grupo"})
        for heading, name in (("p-atender", ">Jóvenes Roca<"), ("p-grupos", ">Jóvenes Roca</a>")):
            self.assertIn('class="mono mono--grupo"', row_of(html, heading, name))
        self.assertIn('<span class="hoy__icono" aria-hidden="true">', row_of(html, "p-atender", "Seguimientos"))

    def test_groups_in_totals(self):
        data = services.build(self.church, None, self.today)
        self.assertEqual([group.name for group in data["groups"]], ["Estudio de Romanos", "Jóvenes Roca"])
        roca = data["groups"][1]
        self.assertEqual((roca.member_count, roca.waiting, roca.trend, roca.missing), (7, 1, ["menos de la mitad"] * 3, 0))
        self.assertEqual((roca.last["share"]["level"], roca.last["share"]["value"], roca.last["expected"], roca.last["date"]),
                         ("under_half", None, 7, self.today - timedelta(weeks=1)))
        self.assertEqual((data["group_people"], data["group_joined"]), (7, "La mitad o más"))
        response = self.get(self.church_pastor)
        html = response.content.decode()
        row = row_of(html, "p-grupos", ">Jóvenes Roca</a>")
        self.assertIn(pill("Menos de la mitad") + " de 7 personas", row)
        self.assertIn(">menos de la mitad · menos de la mitad · menos de la mitad<", row)
        self.assertIn("<i></i><i></i><i></i>", row)  # three meetings, three short marks: no mark says more than its band
        self.assertNotIn('class="on"', row)
        self.assertEqual(numbers(row)[0], "7")  # "7 personas": the group meets the minimum
        for told in ("2 de 7", "3, 1, 2", ">7</dd><dd class=\"figures__note\">de las personas"):
            self.assertNotContains(response, told)
        # The request that waits is first on the page, and for the pastor it leads to the group, not to its people.
        waiting = row_of(html, "p-atender", ">Jóvenes Roca<")
        self.assertIn('<span class="status status--review"><span class="status__mark" aria-hidden="true"></span>1 solicitud</span>', waiting)
        self.assertIn(f'href="{reverse("community:group_detail", args=[self.group.pk])}"', waiting)
        # The group without any meeting, created today, is not pointed out.
        self.assertIn("Sin listas todavía", row_of(html, "p-grupos", ">Estudio de Romanos</a>"))
        for words in ("Falta 1 lista", "Faltan "):
            self.assertNotContains(response, words)

    def test_people_who_left_or_joined_long_ago_are_not_counted_as_new(self):
        GroupMembership.objects.filter(person=self.member).update(start_at=timezone.now() - timedelta(days=45))
        groups.end_membership(GroupMembership.objects.get(group=self.group, person=self.no_account), self.admin)
        data = services.build(self.church, None, self.today)
        self.assertEqual((data["group_people"], data["group_joined"]), (6, "La mitad o más"))

    def test_events_serving_and_care_in_totals(self):
        response = self.get(self.church_pastor)
        html = response.content.decode()
        coming = row_of(html, "p-eventos", ">Noche de alabanza</a>")
        self.assertIn(pill("Menos de 5") + " inscritos de 40 lugares", coming)
        self.assertIn("Hay lugares", coming)
        self.assertNotIn("meter", coming)  # no bar without a number: a bar would be a guess
        self.assertNotContains(response, "2 inscritos")
        self.assertNotContains(response, "Quedan 38")
        self.assertNotContains(response, "Evento sin publicar")
        self.assertEqual(totals(row_of(html, "p-terminaron", "Retiro de matrimonios")), {"Inscritos": "6", "Asistieron": pill("La mitad o más")})
        self.assertEqual(
            totals(row_of(html, "p-servir", ">Bienvenida</h3>")),
            {"Confirmaron": pill("Menos de la mitad"), "Por confirmar": pill("La mitad o más"), "No pueden": pill("Menos de la mitad")},
        )
        data = services.build(self.church, None, self.today)
        self.assertEqual((data["care_open"], data["care_late"], data["care_unowned"]), (2, 1, 1))
        self.assertEqual(totals(section(html, "p-acompanar")), {"Seguimientos abiertos": "2", "Con la fecha vencida": "1", "Sin responsable": "1"})

    def test_what_waits_is_first_and_leads_to_where_it_is_solved(self):
        # Follow-ups without an owner or past their day, as two counts and no name. The pastor cannot open the
        # administration's list, so for her the row is not a link; for the administration it is.
        for user, linked in ((self.church_pastor, False), (self.admin, True)):
            html = self.get(user).content.decode()
            self.assertLess(html.index('id="p-atender"'), html.index('id="p-grupos"'))
            care_row = row_of(html, "p-atender", "Seguimientos")
            self.assertIn('<span class="chip chip--wait">1 con la fecha vencida</span>', care_row)  # vela, as on «Seguimiento»
            self.assertIn('<span class="chip chip--wait">1 sin responsable</span>', care_row)
            self.assertEqual(f'href="{reverse("care:admin_list")}"' in care_row, linked, user.email)
        # The administration (the last page read) decides a request where the group's people are; nothing here
        # leads to who came.
        waiting = row_of(html, "p-atender", ">Jóvenes Roca<")
        self.assertIn(f'href="{reverse("community:group_people", args=[self.group.pk])}"', waiting)
        for link in set(re.findall(r'href="(/[^"#]*)"', html[html.index('<div class="panorama">'):html.index("</main>")])):
            for private in ("/reuniones/", "/inscritos/"):
                self.assertNotIn(private, link)
            self.assertIn(self.client.get(link).status_code, (200, 302), link)

    def test_a_follow_up_about_the_reader_is_never_counted(self):
        reader = Person.objects.get(church=self.church, user=self.admin)
        care.create(self.church, self.admin, person=reader, reason="otro", owner=None, due_on=None)
        self.assertEqual(services.build(self.church, reader, self.today)["care_open"], 2)
        self.assertEqual(services.build(self.church, None, self.today)["care_open"], 3)

    def test_an_empty_church_is_told_what_to_do(self):
        type(self.group_b).objects.filter(pk=self.group_b.pk).update(status="archivado")
        response = self.get(self.admin_b)
        for words in ("Nada por atender", "Sin grupos activos todavía", "Sin eventos publicados por venir", "Ningún equipo tiene fechas por venir", "Sin seguimientos abiertos"):
            self.assertContains(response, words)
        self.assertContains(response, reverse("community:group_create"))
        for drawn in ('class="rows', 'class="figures"', 'class="grid"', "chip"):  # no list, no tile and no pill about nothing
            self.assertNotContains(response, drawn)

    def test_nothing_of_another_church_is_counted(self):
        groups.set_membership(self.group_b, self.member_b, self.admin_b)
        groups.record_meeting(self.group_b, self.today, [self.member_b.pk], self.admin_b)
        care.create(self.church_b, self.admin_b, person=self.member_b, reason="otro", owner=None, due_on=None)
        data = services.build(self.church, None, self.today)
        self.assertEqual((len(data["groups"]), data["group_people"], data["care_open"]), (2, 7, 2))
        self.assertNotContains(self.get(self.church_pastor), "Grupo de la iglesia B")


    def test_a_meeting_that_kept_only_its_totals_still_counts(self):
        # prune_attendance deleted who came and kept how many: the page reads the total that was kept.
        meeting = GroupMeeting.objects.get(group=self.group, date=self.today - timedelta(weeks=1))
        meeting.attendances.all().delete()
        GroupMeeting.objects.filter(pk=meeting.pk).update(present_kept=6)
        data = services.build(self.church, None, self.today)
        self.assertEqual(data["groups"][1].trend, ["menos de la mitad", "menos de la mitad", "la mitad o más"])
        row = row_of(self.get(self.church_pastor).content.decode(), "p-grupos", ">Jóvenes Roca</a>")
        self.assertIn(pill("La mitad o más") + " de 7 personas", row)
        self.assertIn('<i></i><i></i><i class="on"></i>', row)


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
        self.assertEqual(set(data["groups"][1].last), {"date", "expected", "share"})

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
        with self.assertNumQueries(11):
            services.build(self.church, None, self.today)

    def test_more_groups_cost_no_more_queries(self):
        before = self.queries()
        for number in range(10):
            group = self._group(self.church, f"Grupo {number}", meeting_day=2)
            for person in (self.member, self.asked, *self.extras[:3]):
                groups.set_membership(group, person, self.admin)
            for weeks in range(1, 11):
                meeting = GroupMeeting.objects.create(group=group, date=self.today - timedelta(weeks=weeks), expected=5)
                GroupAttendance.objects.create(meeting=meeting, person=self.member)
        with self.assertNumQueries(11):
            data = services.build(self.church, None, self.today)
        self.assertEqual(len(data["groups"]), 12)
        self.assertEqual(data["groups"][2].trend, ["menos de la mitad"] * services.TREND_MEETINGS)
        self.assertEqual(self.queries(), before)


class MinimumReportingSizeTests(PanoramaFixture):
    """Decision 0015: under five people a total says who came, so the page shows none (``overview.services``)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.duo = cls._group(cls.church, "Dúo de lectura", meeting_day=1)
        for person in (cls.asked, cls.coord_person):
            groups.set_membership(cls.duo, person, cls.admin)
        groups.record_meeting(cls.duo, cls.today - timedelta(days=9), [cls.asked.pk, cls.coord_person.pk], cls.admin)
        groups.record_meeting(cls.duo, cls.today - timedelta(days=2), [cls.asked.pk], cls.admin)

    def test_a_group_of_two_shows_no_attendance_and_no_exact_count(self):
        data = services.build(self.church, None, self.today)
        duo = next(group for group in data["groups"] if group.name == "Dúo de lectura")
        self.assertEqual((duo.small, duo.member_count, duo.trend), (True, None, []))
        self.assertEqual(duo.last, {"date": self.today - timedelta(days=2), "expected": None, "share": None})
        for user in (self.church_pastor, self.admin):
            html = self.get(user).content.decode()
            page = html[html.index('<div class="panorama">'):html.index("</main>")]
            row = row_of(page, "p-grupos", ">Dúo de lectura</a>")
            self.assertIn(pill("Menos de 5") + '<span class="visually-hidden"> personas</span>', row)
            self.assertIn(pill("No se muestra"), row)
            self.assertNotIn("level", row)           # no mark of any meeting
            self.assertNotIn("panorama__last", row)  # and nothing about its last list but the day
            self.assertNotRegex(row, r">\s*[0-9]")
            # Not one number in the row but the minimum itself ("Menos de 5") and two days of the calendar: the day of
            # its last list and the day of its next meeting. Nothing that counts people.
            day = self.today - timedelta(days=2)
            calendar = [str(day.day)] + ([str(day.year)] if day.year != self.today.year else []) + [str(duo.next_day.day)]
            self.assertEqual(numbers(row), ["5", *calendar])
            for told in ("1 de 2", "2 de 2", "de 2 personas", ">2<", ">2, 1<", ">1, 2<", "2 personas"):
                self.assertNotIn(told, page, told)

    def test_the_church_s_figures_cannot_be_subtracted_down_to_a_small_group(self):
        # Nine people are in a group, two of them in the group of two. The figure counts the seven of the group that
        # meets the minimum and says that small groups were left out: 9 - 7 = 2 is not on the page to be computed.
        data = services.build(self.church, None, self.today)
        self.assertEqual((data["group_people"], data["group_joined"], data["groups_small"]), (7, "La mitad o más", 2))
        html = self.get(self.church_pastor).content.decode()
        self.assertIn("Sin contar 2 grupos de menos de 5 personas", html)
        self.assertNotIn('figures__value">9<', html)
        # A figure that is itself under the minimum is not shown either.
        GroupMembership.objects.filter(group=self.group).update(start_at=timezone.now() - timedelta(days=60))
        groups.set_membership(self.group, self.admin_person, self.admin)
        data = services.build(self.church, None, self.today)
        # "Se unieron" is a part of "Personas en grupos": one of eight is told in words.
        self.assertEqual((data["group_people"], data["group_joined"]), (8, "Menos de la mitad"))
        self.assertContains(self.get(self.church_pastor), f'<dd class="figures__value">{pill("Menos de la mitad")}</dd>', count=1)

    def test_a_church_of_small_groups_only_sees_no_number_of_people(self):
        groups.set_membership(self.group_b, self.member_b, self.admin_b)
        groups.record_meeting(self.group_b, self.today, [self.member_b.pk], self.admin_b)
        data = services.build(self.church_b, None, self.today)
        self.assertEqual((data["group_people"], data["group_joined"], data["groups_small"]), (None, None, 1))
        html = self.get(self.admin_b).content.decode()
        self.assertEqual(html.count("Menos de 5"), 3)
        self.assertNotIn("1 de 1", html)

    def test_the_trend_keeps_only_the_meetings_that_met_the_minimum(self):
        # The group was three people once: those lists stay out, whatever its size today.
        old = GroupMeeting.objects.get(group=self.group, date=self.today - timedelta(weeks=2))
        GroupMeeting.objects.filter(pk=old.pk).update(expected=3)
        roca = services.build(self.church, None, self.today)["groups"][-1]
        self.assertEqual((roca.name, roca.trend, roca.last["share"]["level"]), ("Jóvenes Roca", ["menos de la mitad"] * 2, "under_half"))
        # When the last list is one of them, its day is told and its total is not.
        GroupMeeting.objects.filter(group=self.group, date=self.today - timedelta(weeks=1)).update(expected=4)
        roca = services.build(self.church, None, self.today)["groups"][-1]
        self.assertEqual((roca.trend, roca.last["share"], roca.last["expected"], roca.member_count), ([], None, None, 7))
        html = self.get(self.church_pastor).content.decode()
        self.assertNotIn("de 4 personas", html)
        self.assertNotIn("de 3 personas", html)
        row = row_of(html, "p-grupos", ">Jóvenes Roca</a>")
        self.assertIn(pill("Sin desglose por privacidad"), row)
        self.assertNotIn("level", row)

    def test_a_group_that_shrank_under_the_minimum_shows_nothing_of_its_past(self):
        for membership in GroupMembership.objects.filter(group=self.group, person__in=self.extras):
            groups.end_membership(membership, self.admin)
        roca = services.build(self.church, None, self.today)["groups"][-1]
        self.assertEqual((roca.small, roca.member_count, roca.trend, roca.last["share"]), (True, None, [], None))
        # Not one group of the page says anything of who came: no mark, no band, no "de N personas".
        html = self.get(self.church_pastor).content.decode()
        for name in ("Dúo de lectura", "Estudio de Romanos", "Jóvenes Roca"):
            row = row_of(html, "p-grupos", f">{name}</a>")
            for told in ("level", "panorama__last", "La mitad", "Menos de la mitad", "Casi todas"):
                self.assertNotIn(told, row, (name, told))
            self.assertNotRegex(row, r"de \d+ personas")
        self.assertIn(pill("No se muestra"), row_of(html, "p-grupos", ">Jóvenes Roca</a>"))

    def test_small_events_and_small_serving_dates(self):
        past = self.today - timedelta(days=8)
        few = Event.objects.create(church=self.church, name="Desayuno de varones", slug="desayuno-v", starts_on=past, ends_on=past)
        agenda.save_page(few, self.admin, published=True, registration="abierta", capacity=None, starts_at=None, description="")
        for person in (self.member, self.asked):
            EventRegistration.objects.create(event=few, person=person, attended=person == self.member)
        many = Event.objects.create(church=self.church, name="Vigilia de jóvenes", slug="vigilia-j", starts_on=past, ends_on=past)
        agenda.save_page(many, self.admin, published=True, registration="abierta", capacity=None, starts_at=None, description="")
        for person in (self.member, self.asked, self.leader, *self.extras):
            EventRegistration.objects.create(event=many, person=person, attended=person in (self.member, self.asked))
        team = serving.save_team(self.church, self.admin, name="Sonido", description="")
        serving.set_member(team, self.leader, self.admin)
        serving.set_member(team, self.member, self.admin)
        service = serving.save_date(team, self.admin, date=self.today + timedelta(days=4), what="Consola", starts_at=None, place="")
        serving.respond(serving.assign(service, self.leader, self.admin), True, self.leader.user)
        serving.assign(service, self.member, self.admin)

        data = services.build(self.church, None, self.today)
        by_name = {event.name: (event.taken, event.came) for event in data["finished"]}
        self.assertEqual(by_name, {"Desayuno de varones": (None, None), "Vigilia de jóvenes": (7, "Menos de la mitad")})
        date_row = data["serving_dates"][0]
        self.assertEqual((date_row.few, date_row.confirmed, date_row.waiting, date_row.declined), (True, None, None, None))
        html = self.get(self.church_pastor).content.decode()
        self.assertEqual(totals(row_of(html, "p-terminaron", "Desayuno de varones")), {"Inscritos": pill("Menos de 5"), "Asistieron": pill("Menos de 5")})
        self.assertEqual(totals(row_of(html, "p-terminaron", "Vigilia de jóvenes")), {"Inscritos": "7", "Asistieron": pill("Menos de la mitad")})
        sound = row_of(html, "p-servir", ">Sonido</h3>")
        self.assertIn("Se le pidió a menos de 5 personas: sin desglose por privacidad", sound)
        self.assertNotIn("Confirmaron", sound)
        self.assertEqual(totals(sound), {})


class PartOfAWholeTests(PanoramaFixture):
    """A part is exact only when it and its complement are both at least the minimum (``services.share``): the
    four ways the page used to tell a small number, by complement or between two days."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.crowd = [Person.objects.create(church=cls.church, first_name=f"Gente{n}", last_name="Relleno") for n in range(12)]
        cls.big = cls._group(cls.church, "Asamblea")
        for person in cls.crowd:
            groups.set_membership(cls.big, person, cls.admin)

    def meeting(self, days_ago, present, expected=12):
        meeting = GroupMeeting.objects.create(group=self.big, date=self.today - timedelta(days=days_ago), expected=expected)
        GroupAttendance.objects.bulk_create(GroupAttendance(meeting=meeting, person=person) for person in self.crowd[:present])

    def published(self, name, days, signed, attended=None, capacity=None, pending=0):
        day = self.today + timedelta(days=days)
        event = Event.objects.create(church=self.church, name=name, slug=name.lower().replace(" ", "-"), starts_on=day, ends_on=day)
        agenda.save_page(event, self.admin, published=True, registration="abierta", capacity=capacity, starts_at=None, description="")
        for index, person in enumerate(self.crowd[:signed]):
            EventRegistration.objects.create(event=event, person=person, attended=None if attended is None else index < attended)
        for person in self.crowd[signed:signed + pending]:
            EventRegistration.objects.create(event=event, person=person, status=EventRegistration.Status.PENDING)
        return event

    def test_the_rule(self):
        told = {(part, whole): (services.share(part, whole, 5) or {}).get("label") for part, whole in (
            (2, 4), (0, 5), (2, 5), (3, 5), (5, 5), (2, 6), (5, 6), (4, 8), (0, 9), (4, 9), (5, 9), (9, 9),
            (4, 10), (5, 10), (6, 10), (10, 10), (2, 12), (5, 12), (7, 12), (8, 12), (11, 12), (12, 12),
        )}
        self.assertEqual(told, {
            (2, 4): None,
            (0, 5): "Menos de la mitad", (2, 5): "Menos de la mitad", (3, 5): "La mitad o más", (5, 5): "La mitad o más",
            (2, 6): "Menos de la mitad", (5, 6): "La mitad o más", (4, 8): "La mitad o más",
            (0, 9): "Menos de 5", (4, 9): "Menos de 5", (5, 9): "Casi todas", (9, 9): "Casi todas",
            (4, 10): "Menos de 5", (5, 10): "5", (6, 10): "Casi todas", (10, 10): "Casi todas",
            (2, 12): "Menos de 5", (5, 12): "5", (7, 12): "7", (8, 12): "Casi todas", (11, 12): "Casi todas", (12, 12): "Casi todas",
        })
        # No word ever stands for one value only, and a number is never within four of either end.
        for whole in range(5, 40):
            words: dict[str, int] = {}
            for part in range(whole + 1):
                one = services.share(part, whole, 5)
                if one["level"] == "exact":
                    self.assertTrue(part >= 5 and whole - part >= 5, (part, whole))
                    self.assertEqual(one["value"], part)
                else:
                    self.assertIsNone(one["value"])
                    words[one["label"]] = words.get(one["label"], 0) + 1
            self.assertEqual(len(words), 2, whole)
            self.assertGreaterEqual(min(words.values()), 3, whole)
        # Parts that add up: one in words and the exact ones are not printed, or the third would follow by subtraction.
        self.assertEqual(services.shares([5, 5, 4], 5), ["5 o más", "5 o más", "Menos de 5"])
        self.assertEqual(services.shares([5, 5, 5], 5), ["5", "5", "5"])
        self.assertEqual(services.shares([12, 0, 0], 5), ["Casi todas", "Menos de 5", "Menos de 5"])
        self.assertIsNone(services.shares([2, 1, 1], 5))

    def test_finding_1_attendance_under_the_minimum_or_with_few_absent(self):
        for days_ago, present in ((28, 2), (21, 11), (14, 7), (7, 1)):
            self.meeting(days_ago, present)
        self.meeting(2, 5, expected=6)  # "5 de 6": one was absent
        html = self.get(self.church_pastor).content.decode()
        row = row_of(html, "p-grupos", ">Asamblea</a>")
        self.assertIn(pill("La mitad o más") + " de 6 personas", row)
        self.assertIn(">menos de 5 · casi todas · 7 · menos de 5 · la mitad o más<", row)
        # The same five meetings as marks: short, tall, the number the service gave, short, tall.
        self.assertIn('<i></i><i class="on"></i><b>7</b><i></i><i class="on"></i>', row)
        for told in ("5 de 6", "2 de 12", "11 de 12", "1 de 12", "2, 11", "1, 2", "11,"):
            self.assertNotIn(told, html, told)
        # With few present and with few absent, in words; with enough of both, the number.
        for present, sentence in ((2, pill("Menos de 5") + " de 12 personas"), (11, pill("Casi todas") + " de 12 personas"), (7, ">7 de 12 personas<")):
            GroupMeeting.objects.filter(group=self.big, date=self.today - timedelta(days=2)).delete()
            self.meeting(2, present)
            html = self.get(self.church_pastor).content.decode()
            self.assertIn(sentence, html)
            for told in ("2 de 12", "11 de 12"):
                self.assertNotIn(told, html, told)

    def test_finding_2_a_finished_event_where_almost_everybody_came(self):
        self.published("Cena de gala", -6, signed=10, attended=9)
        self.published("Tarde de campo", -5, signed=12, attended=6)
        html = self.get(self.church_pastor).content.decode()
        # Nine of ten came: "Casi todas", never the 9 that would count the one who did not.
        self.assertEqual(totals(row_of(html, "p-terminaron", "Cena de gala")), {"Inscritos": "10", "Asistieron": pill("Casi todas")})
        self.assertEqual(totals(row_of(html, "p-terminaron", "Tarde de campo")), {"Inscritos": "12", "Asistieron": "6"})

    def test_finding_3_an_event_reads_the_same_before_and_after_its_last_day(self):
        event = self.published("Taller de padres", 3, signed=3, capacity=20)
        html = self.get(self.church_pastor).content.decode()
        self.assertIn(pill("Menos de 5") + " inscritos de 20 lugares", html)
        self.assertIn("Hay lugares", html)
        self.assertNotIn("meter", html)
        for told in ("3 inscritos", "Quedan 17", "17 lugares"):
            self.assertNotIn(told, html, told)
        # The day after: the finished table says the same, so two days side by side tell nothing more.
        past = self.today - timedelta(days=1)
        Event.objects.filter(pk=event.pk).update(starts_on=past, ends_on=past)
        html = self.get(self.church_pastor).content.decode()
        self.assertEqual(totals(row_of(html, "p-terminaron", "Taller de padres")), {"Inscritos": pill("Menos de 5"), "Asistieron": pill("Menos de 5")})
        # A full small event says it is full, as its public page does, and nothing else.
        self.published("Mesa chica", 4, signed=3, capacity=3)
        self.assertContains(self.get(self.church_pastor), pill("Menos de 5") + " inscritos de 3 lugares")
        # With enough sign-ups the numbers are exact, unless the free places would count a few minors who wait.
        self.published("Congreso", 5, signed=8, capacity=20)
        self.published("Campamento chico", 6, signed=8, capacity=20, pending=2)
        html = self.get(self.church_pastor).content.decode()
        self.assertIn("8 inscritos de 20 lugares", html)
        self.assertEqual(html.count("Quedan 12 lugares"), 1)
        self.assertNotIn("Quedan 10 lugares", html)
        # The bar is the same number, drawn: 8 of 20, for the two events that show it and for no other.
        self.assertEqual(html.count('class="meter__fill" style="width:40%"'), 2)
        self.assertEqual(html.count("meter__fill"), 2)
        self.assertNotIn("meter", row_of(html, "p-eventos", ">Mesa chica</a>"))

    def test_finding_4_each_answer_of_a_serving_date_is_a_part_of_who_was_asked(self):
        team = serving.save_team(self.church, self.admin, name="Estacionamiento", description="")
        service = serving.save_date(team, self.admin, date=self.today + timedelta(days=6), what="Entrada", starts_at=None, place="")
        for index, person in enumerate(self.crowd):
            serving.set_member(team, person, self.admin)
            assignment = serving.assign(service, person, self.admin)
            if index < 6:
                type(assignment).objects.filter(pk=assignment.pk).update(status="aceptada")
            elif index == 6:
                type(assignment).objects.filter(pk=assignment.pk).update(status="declinada")
        html = self.get(self.church_pastor).content.decode()
        row = row_of(html, "p-servir", ">Estacionamiento</h3>")
        # 6 confirmed, 5 waiting, 1 cannot: the one is in words, and so are the others, or 12 - 6 - 5 would give it.
        self.assertEqual(totals(row), {"Confirmaron": pill("5 o más"), "Por confirmar": pill("5 o más"), "No pueden": pill("Menos de 5")})
        for exact in ("<dd>1<", "<dd>5<", "<dd>6<"):
            self.assertNotIn(exact, row)


class DrawingTests(PanoramaFixture):
    """The two helpers that draw (``overview.templatetags.panorama``) add nothing to what ``services`` decided."""

    def test_a_total_is_drawn_as_it_was_told(self):
        from overview.templatetags.panorama import told

        self.assertEqual(told(12, 5), "12")
        self.assertEqual(told("7", 5), "7")
        self.assertEqual(told(None, 5), pill("Menos de 5"))  # kept back: said so, never a blank
        self.assertEqual(told(None, 8), pill("Menos de 8"))  # the church's own minimum
        for words in ("Casi todas", "La mitad o más", "Menos de la mitad", "Menos de 5", "5 o más"):
            self.assertEqual(told(words, 5), pill(words))
        self.assertEqual(told("<b>", 5), pill("&lt;b&gt;"))

    def test_a_mark_never_says_more_than_its_band(self):
        from overview.templatetags.panorama import trend_mark

        for minimum in (5, 8):
            for whole in range(minimum, 45):
                for part in range(whole + 1):
                    one = services.share(part, whole, minimum)
                    mark = trend_mark(one["label"].lower())  # as ``group.trend`` carries it
                    if one["level"] == "exact":
                        self.assertEqual((mark, one["label"]), ("exact", str(part)))
                    else:
                        # Tall only when half or more came, short only when fewer: true of every value in the band.
                        self.assertEqual(mark, "high" if one["level"] in ("half_up", "most") else "low", (part, whole))
                        self.assertEqual(mark == "high", part * 2 >= whole, (part, whole, one["label"]))


TUESDAY = date(2026, 11, 17)


def rhythm_group(created=date(2026, 9, 1), **fields) -> Group:
    """A group that is never saved: ``missing_lists`` reads only its rhythm and the day it was created."""
    fields.setdefault("meeting_day", 3)  # Thursday: 12 November, 5 November, 29 October 2026...
    return Group(name="Ritmo", created_at=timezone.make_aware(datetime.combine(created, time(12, 0))), **fields)


class MissingListsTests(PanoramaFixture):
    """A list is missing when the group's own rhythm had a meeting and nobody said who came."""

    def test_a_weekly_group(self):
        group = rhythm_group()
        self.assertEqual(services.missing_lists(group, date(2026, 11, 12), TUESDAY), (0, None))             # up to date
        self.assertEqual(services.missing_lists(group, date(2026, 11, 5), TUESDAY), (1, date(2026, 11, 12)))
        self.assertEqual(services.missing_lists(group, date(2026, 10, 29), TUESDAY), (2, date(2026, 11, 12)))
        self.assertEqual(services.missing_lists(group, date(2026, 10, 1), TUESDAY), (3, date(2026, 11, 12)))  # capped
        self.assertEqual(services.missing_lists(group, None, TUESDAY), (3, date(2026, 11, 12)))

    def test_two_days_of_grace(self):
        group = rhythm_group()
        self.assertEqual(services.missing_lists(group, date(2026, 11, 5), date(2026, 11, 12)), (0, None))  # the day itself
        self.assertEqual(services.missing_lists(group, date(2026, 11, 5), date(2026, 11, 14)), (0, None))  # two days after
        self.assertEqual(services.missing_lists(group, date(2026, 11, 5), date(2026, 11, 15)), (1, date(2026, 11, 12)))

    def test_a_list_taken_a_little_early_or_on_another_day_counts(self):
        group = rhythm_group()
        self.assertEqual(services.missing_lists(group, date(2026, 11, 10), TUESDAY), (0, None))  # two days early
        self.assertEqual(services.missing_lists(group, date(2026, 11, 13), TUESDAY), (0, None))  # they met on Friday

    def test_a_group_that_meets_every_two_weeks(self):
        group = rhythm_group(meeting_frequency="quincenal", meeting_anchor=date(2026, 10, 1))  # 29 October, 12 November
        self.assertEqual(services.missing_lists(group, date(2026, 11, 12), TUESDAY), (0, None))
        self.assertEqual(services.missing_lists(group, date(2026, 10, 29), TUESDAY), (1, date(2026, 11, 12)))
        # The week in between has no meeting: nine days after the last list nothing is missing.
        self.assertEqual(services.missing_lists(group, date(2026, 10, 29), date(2026, 11, 7)), (0, None))

    def test_a_monthly_group_three_weeks_after_it_met(self):
        group = rhythm_group(meeting_frequency="mensual", meeting_week=1, meeting_day=5)  # first Saturday: 7 November
        self.assertEqual(services.missing_lists(group, date(2026, 11, 7), date(2026, 11, 28)), (0, None))
        self.assertEqual(group.next_meeting(date(2026, 11, 28)), date(2026, 12, 5))
        self.assertEqual(services.missing_lists(group, date(2026, 10, 3), date(2026, 11, 28)), (1, date(2026, 11, 7)))

    def test_a_meeting_of_today_that_has_its_list_is_not_the_next_one(self):
        Group.objects.filter(pk=self.group.pk).update(meeting_day=self.today.weekday())
        self.assertEqual(services.build(self.church, None, self.today)["groups"][-1].next_day, self.today)
        GroupMeeting.objects.create(group=self.group, date=self.today, expected=7)
        self.assertEqual(services.build(self.church, None, self.today)["groups"][-1].next_day, self.today + timedelta(weeks=1))

    def test_no_fixed_day_is_never_missing(self):
        for fields in ({"meeting_frequency": "variable"}, {"meeting_day": None}, {"meeting_frequency": "quincenal"}, {"meeting_frequency": "mensual"}):
            self.assertEqual(services.missing_lists(rhythm_group(**fields), None, TUESDAY), (0, None), fields)

    def test_a_new_group_is_not_asked_for_what_came_before_it(self):
        self.assertEqual(services.missing_lists(rhythm_group(created=date(2026, 11, 13)), None, TUESDAY), (0, None))
        self.assertEqual(services.missing_lists(rhythm_group(created=date(2026, 11, 12)), None, TUESDAY), (1, date(2026, 11, 12)))
        self.assertEqual(services.missing_lists(rhythm_group(created=date(2026, 11, 1)), None, TUESDAY), (2, date(2026, 11, 12)))

    def test_the_page_says_it_in_neutral_words(self):
        day = self.today - timedelta(days=3)
        long_ago = timezone.now() - timedelta(days=60)
        Group.objects.filter(pk=self.group.pk).update(meeting_day=day.weekday(), created_at=long_ago)
        GroupMeeting.objects.filter(group=self.group, date=self.today - timedelta(weeks=1)).update(date=day - timedelta(weeks=1))
        Group.objects.filter(pk=self.other_group.pk).update(meeting_day=day.weekday(), created_at=long_ago)
        monthly = self._group(self.church, "Oración mensual", meeting_frequency="mensual", meeting_day=day.weekday(), meeting_week=1)
        variable = self._group(self.church, "Sin calendario", meeting_frequency="variable")
        Group.objects.filter(pk__in=[monthly.pk, variable.pk]).update(created_at=long_ago)
        last_monthly = monthly.last_meeting_day(self.today)
        GroupMeeting.objects.create(group=monthly, date=last_monthly, expected=0)
        GroupMeeting.objects.create(group=variable, date=self.today - timedelta(days=50), expected=0)

        data = services.build(self.church, None, self.today)
        state = {group.name: (group.missing, group.missing_day, group.next_day) for group in data["groups"]}
        self.assertEqual(state["Jóvenes Roca"], (1, day, None))
        self.assertEqual(state["Estudio de Romanos"], (3, day, None))
        self.assertEqual(state["Oración mensual"], (0, None, monthly.next_meeting(self.today)))
        self.assertEqual(state["Sin calendario"], (0, None, None))
        self.assertEqual(data["groups_missing"], 2)
        html = self.get(self.church_pastor).content.decode()
        # The two groups, by name, in the list of what waits; each row leads to the group, never to its lists.
        waits = section(html, "p-atender")
        missing = dict(re.findall(r'>([^<>]+)<span class="visually-hidden">: listas</span>.*?<span class="chip chip--wait">([^<]+)</span>', waits, flags=re.S))
        self.assertEqual(missing, {"Jóvenes Roca": "Falta 1 lista", "Estudio de Romanos": "Faltan 3 o más listas"})
        self.assertIn("La última, ", waits)
        self.assertNotIn("/reuniones/", waits)
        self.assertIn("Próxima: ", row_of(html, "p-grupos", ">Oración mensual</a>"))
        self.assertIn("Sin día fijo", row_of(html, "p-grupos", ">Sin calendario</a>"))
        for blame in ("tres semanas", "Sin reunión registrada", "olvid", "atrasad", "No hubo reunión"):
            self.assertNotIn(blame, html, blame)
