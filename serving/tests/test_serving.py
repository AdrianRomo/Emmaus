"""Servir: teams, their dates, who is asked and what they answer; who may see and do what."""

from datetime import time, timedelta

from django.urls import reverse
from django.utils import timezone

from access.models import RoleAssignment
from core.models import UserChurchRole
from events.models import EventMembership
from groups.models import GroupMembership
from groups.tests.base import CommunityFixture
from people import flags
from people.models import ChurchAuditEvent
from serving import services
from serving.models import Assignment, ServiceDate, Team, TeamMembership, Unavailable


class ServingFixture(CommunityFixture):
    """Church A: the team "Bienvenida" (Gala coordinates; Diego and Pía serve) with one date in a
    week, and the team "Alabanza" with nobody. Church B: its own team, date and assignment."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.today = timezone.localdate()
        cls.week = cls.today + timedelta(days=7)
        cls.team = services.save_team(cls.church, cls.admin, name="Bienvenida", description="Recibimos a quien llega.")
        cls.other_team = services.save_team(cls.church, cls.admin, name="Alabanza")
        services.set_member(cls.team, cls.leader, cls.admin, role="coordina")
        cls.membership = services.set_member(cls.team, cls.member, cls.admin)
        services.set_member(cls.team, cls.asked, cls.admin)
        cls.day = services.save_date(
            cls.team, cls.leader_user, date=cls.week, starts_at=time(9, 30), what="Bienvenida del domingo", place="Templo"
        )
        cls.team_b = services.save_team(cls.church_b, cls.admin_b, name="Equipo secreto B")
        services.set_member(cls.team_b, cls.member_b, cls.admin_b, role="coordina")
        cls.day_b = services.save_date(cls.team_b, cls.admin_b, date=cls.week, what="Servicio de la iglesia B")
        cls.assignment_b = services.assign(cls.day_b, cls.member_b, cls.admin_b)
        cls.busy_b = services.set_unavailable(cls.member_b, cls.week + timedelta(days=1), cls.member_b_user)

    def url(self, name, *args):
        return reverse(f"serving:{name}", args=args)

    def assign(self, person=None, day=None) -> Assignment:
        return services.assign(day or self.day, person or self.member, self.leader_user)

    def move(self, day, days: int):
        ServiceDate.objects.filter(pk=day.pk).update(date=self.today + timedelta(days=days))
        day.refresh_from_db()

    def date_post(self, *people, **changes):
        data = {"date": self.week.isoformat(), "starts_at": "9:30", "what": "Bienvenida del domingo", "place": "Templo",
                "sirve": [person.pk for person in people]}
        data.update(changes)
        return data


class JourneyTests(ServingFixture):
    def test_the_full_journey(self):
        # The administration creates a team and says who coordinates and who serves.
        self.client.force_login(self.admin)
        page = self.client.get(self.url("team_admin"))
        self.assertContains(page, "<h1", count=1)
        self.assertContains(page, "Equipos de la iglesia")
        self.assertContains(page, "Crear un equipo")
        response = self.client.post(self.url("team_create"), {"name": "Niños", "description": "Cuidamos a los niños."})
        team = Team.objects.get(name="Niños")
        self.assertRedirects(response, self.url("team", team.pk))
        self.assertEqual(team.church, self.church)
        found = self.client.get(self.url("team", team.pk), {"q": "gala"})
        self.assertContains(found, "Gala Guía")
        self.client.post(self.url("member_add", team.pk), {"persona": self.leader.pk, "rol": "coordina"})
        self.client.post(self.url("member_add", team.pk), {"persona": self.member.pk, "rol": "sirve"})
        roles = dict(TeamMembership.objects.filter(team=team).values_list("person_id", "role"))
        self.assertEqual(roles, {self.leader.pk: "coordina", self.member.pk: "sirve"})

        # Who coordinates adds a date and asks the member.
        self.client.force_login(self.leader_user)
        page = self.client.get(self.url("team", team.pk))
        self.assertContains(page, "<h1", count=1)
        self.assertContains(page, "Agregar una fecha")
        self.assertContains(page, "tel:5511112222")
        self.assertNotContains(page, "Agregar a alguien de la iglesia")
        self.assertContains(self.client.get(self.url("date_create", team.pk)), "Guardar la fecha")
        response = self.client.post(
            self.url("date_create", team.pk), self.date_post(self.member, what="Salón de niños", place="Salón 3")
        )
        self.assertRedirects(response, self.url("team", team.pk))
        day = ServiceDate.objects.get(team=team)
        self.assertEqual((day.date, day.starts_at, day.place), (self.week, time(9, 30), "Salón 3"))
        page = self.client.get(self.url("team", team.pk))
        self.assertContains(page, "0 de 1 confirmaron.")
        self.assertContains(page, "Por confirmar")

        # The member reads the date and confirms.
        self.client.force_login(self.member_user)
        page = self.client.get(self.url("home"))
        self.assertContains(page, "<h1", count=1)
        self.assertContains(page, "Tus fechas")
        self.assertContains(page, "Salón de niños")
        self.assertContains(page, "a las 9:30, en Salón 3.")
        self.assertContains(page, "row--attention")
        self.assertContains(page, "Coordina el equipo: Gala Guía.")
        assignment = Assignment.objects.get(service_date=day, person=self.member)
        response = self.client.post(self.url("respond", assignment.pk), {"respuesta": "aceptar"})
        self.assertRedirects(response, self.url("home"))
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, "aceptada")
        self.assertIsNotNone(assignment.responded_at)
        page = self.client.get(self.url("home"))
        self.assertContains(page, "Confirmaste")
        self.assertContains(page, "Cambiar")
        self.assertNotContains(page, "row--attention")

        # Who coordinates sees the answer.
        self.client.force_login(self.leader_user)
        page = self.client.get(self.url("team", team.pk))
        self.assertContains(page, "1 de 1 confirmaron.")
        self.assertContains(page, "Confirmó")

    def test_someone_in_no_team_reads_how_to_join(self):
        self.client.force_login(self.coord1)
        page = self.client.get(self.url("home"))
        self.assertContains(page, "Todavía no estás en ningún equipo.")
        self.assertContains(page, "la administración de tu iglesia")
        self.assertNotContains(page, "Tus fechas")
        self.assertNotContains(page, "Equipos de la iglesia")

    def test_the_administration_edits_and_archives_a_team(self):
        self.client.force_login(self.admin)
        self.client.post(self.url("team_edit", self.team.pk), {"name": "Bienvenida y café", "description": ""})
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, "Bienvenida y café")
        self.assertContains(self.client.get(self.url("team_create")), "Crear equipo")
        self.assertContains(self.client.get(self.url("team_edit", self.team.pk)), "Archivar el equipo")
        self.assertContains(self.client.get(self.url("team_archive", self.team.pk)), "¿Archivar el equipo Bienvenida y café?")
        self.assertRedirects(self.client.post(self.url("team_archive", self.team.pk)), self.url("team_admin"))
        self.team.refresh_from_db()
        self.assertFalse(self.team.is_active)
        self.assertEqual(self.client.get(self.url("team_edit", self.team.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url("team", self.team.pk)).status_code, 200)

    def test_the_administration_changes_who_coordinates_and_takes_people_out(self):
        self.assign()
        self.client.force_login(self.admin)
        self.client.post(self.url("member_add", self.team.pk), {"persona": self.member.pk, "rol": "coordina"})
        self.membership.refresh_from_db()
        self.assertTrue(self.membership.is_coordinator)
        self.assertContains(self.client.get(self.url("member_remove", self.team.pk, self.membership.pk)), "¿Quitar a Diego")
        self.client.post(self.url("member_remove", self.team.pk, self.membership.pk))
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "inactiva")
        self.assertFalse(Assignment.objects.filter(person=self.member).exists())
        self.assertFalse(services.has_teams(self.member))
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(self.url("team", self.team.pk)).status_code, 403)


class AnswerTests(ServingFixture):
    def setUp(self):
        self.assignment = self.assign()
        self.client.force_login(self.member_user)

    def respond(self, answer):
        return self.client.post(self.url("respond", self.assignment.pk), {"respuesta": answer}, follow=True)

    def status(self):
        self.assignment.refresh_from_db()
        return self.assignment.status

    def test_decline_and_change_the_answer(self):
        self.assertContains(self.respond("declinar"), "Dijiste que no puedes")
        self.assertEqual(self.status(), "declinada")
        page = self.client.get(self.url("home"), {"cambiar": self.assignment.pk})
        self.assertContains(page, 'value="aceptar"')
        self.assertContains(page, "No puedo")
        self.respond("aceptar")
        self.assertEqual(self.status(), "aceptada")
        self.assertEqual(self.client.post(self.url("respond", self.assignment.pk), {"respuesta": "otra"}).status_code, 404)
        self.assertEqual(self.client.get(self.url("respond", self.assignment.pk)).status_code, 405)

    def test_no_change_on_the_day_itself(self):
        self.respond("aceptar")
        self.move(self.day, 0)
        page = self.respond("declinar")
        self.assertContains(page, "hasta un día antes")
        self.assertEqual(self.status(), "aceptada")
        page = self.client.get(self.url("home"), {"cambiar": self.assignment.pk})
        self.assertContains(page, "Confirmaste")
        self.assertNotContains(page, 'value="declinar"')
        self.assertNotContains(page, "cambiar=")

    def test_no_answer_after_the_day(self):
        self.move(self.day, -1)
        self.respond("aceptar")
        self.assertEqual(self.status(), "pendiente")
        self.assertNotContains(self.client.get(self.url("home")), "Bienvenida del domingo")
        with self.assertRaises(services.ServingError):
            services.respond(self.assignment, False, self.member_user)

    def test_a_first_answer_is_taken_on_the_day_itself(self):
        self.move(self.day, 0)
        self.respond("aceptar")
        self.assertEqual(self.status(), "aceptada")

    def test_nobody_answers_for_somebody_else(self):
        self.client.force_login(self.asked_user)
        self.assertEqual(self.client.post(self.url("respond", self.assignment.pk), {"respuesta": "aceptar"}).status_code, 404)
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.post(self.url("respond", self.assignment.pk), {"respuesta": "aceptar"}).status_code, 404)
        self.assertEqual(self.status(), "pendiente")


class UnavailableTests(ServingFixture):
    def test_an_unavailable_day_blocks_the_assignment_with_the_reason(self):
        self.client.force_login(self.member_user)
        page = self.client.post(self.url("unavailable_add"), {"dia": self.week.isoformat()}, follow=True)
        self.assertContains(page, "ese día no te pedirán servir")
        self.assertContains(page, "Días que no puedes")
        self.assertTrue(Unavailable.objects.filter(person=self.member, date=self.week).exists())
        with self.assertRaisesMessage(services.ServingError, "Diego marcó que ese día no puede."):
            services.assign(self.day, self.member, self.leader_user)

        self.client.force_login(self.leader_user)
        page = self.client.get(self.url("date_detail", self.day.pk))
        self.assertContains(page, "(ese día no puede)")
        self.assertContains(page, f'value="{self.member.pk}" disabled')
        page = self.client.post(self.url("date_detail", self.day.pk), self.date_post(self.member, self.asked))
        self.assertContains(page, "Diego marcó que ese día no puede.")
        self.assertFalse(Assignment.objects.filter(service_date=self.day).exists())  # nothing saved by halves

    def test_marking_a_day_already_assigned_keeps_the_assignment(self):
        assignment = self.assign()
        self.client.force_login(self.member_user)
        page = self.client.post(self.url("unavailable_add"), {"dia": self.week.isoformat()}, follow=True)
        self.assertContains(page, "sigue en pie")
        self.assertContains(page, "«No puedo»")
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, "pendiente")
        # The coordinator saves the date again: the person stays until taken out.
        self.client.force_login(self.leader_user)
        self.assertContains(self.client.get(self.url("date_detail", self.day.pk)), "Después marcó que ese día no puede.")
        self.client.post(self.url("date_detail", self.day.pk), self.date_post(self.member))
        self.assertTrue(Assignment.objects.filter(pk=assignment.pk).exists())

    def test_unmark_a_day_and_refuse_the_past(self):
        self.client.force_login(self.member_user)
        self.client.post(self.url("unavailable_add"), {"dia": self.week.isoformat()})
        day = Unavailable.objects.get(person=self.member)
        self.assertEqual(self.client.get(self.url("unavailable_remove", day.pk)).status_code, 405)
        self.client.force_login(self.asked_user)
        self.assertEqual(self.client.post(self.url("unavailable_remove", day.pk)).status_code, 404)
        self.client.force_login(self.member_user)
        self.client.post(self.url("unavailable_remove", day.pk))
        self.assertFalse(Unavailable.objects.filter(person=self.member).exists())
        page = self.client.post(self.url("unavailable_add"), {"dia": (self.today - timedelta(days=1)).isoformat()}, follow=True)
        self.assertContains(page, "Elige un día de hoy en adelante.")
        self.assertContains(self.client.post(self.url("unavailable_add"), {"dia": "mañana"}, follow=True), "Elige un día del calendario.")
        self.assertFalse(Unavailable.objects.filter(person=self.member).exists())

    def test_moving_a_date_asks_again_and_respects_unavailable_days(self):
        assignment = self.assign()
        services.respond(assignment, True, self.member_user)
        later = self.week + timedelta(days=3)
        services.set_unavailable(self.member, later, self.member_user)
        self.client.force_login(self.leader_user)
        page = self.client.post(self.url("date_detail", self.day.pk), self.date_post(self.member, date=later.isoformat()))
        self.assertContains(page, "Diego marcó que ese día no puede.")
        self.day.refresh_from_db()
        self.assertEqual(self.day.date, self.week)
        other = self.week + timedelta(days=4)
        self.client.post(self.url("date_detail", self.day.pk), self.date_post(self.member, date=other.isoformat()))
        assignment.refresh_from_db()
        self.assertEqual((assignment.status, assignment.responded_at), ("pendiente", None))


class DateTests(ServingFixture):
    def test_moving_a_date_does_not_ask_again_who_said_no_and_cannot_the_new_day(self):
        new_day = self.week + timedelta(days=7)
        said_no = services.assign(self.day, self.member, self.leader_user)
        services.respond(said_no, False, self.member_user)
        services.set_unavailable(self.member, new_day, self.member_user)
        asked_again = services.assign(self.day, self.asked, self.leader_user)
        services.respond(asked_again, True, self.asked_user)
        services.save_date(self.team, self.leader_user, service_date=self.day, date=new_day, what="Bienvenida del domingo")
        said_no.refresh_from_db()
        asked_again.refresh_from_db()
        self.assertEqual(said_no.status, "declinada")
        self.assertEqual(asked_again.status, "pendiente")  # an answer was for the day it had
        self.assertEqual(services.pending_count(self.member), 0)

    def test_a_person_that_is_not_a_number_is_not_a_server_error(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("serving:member_add", args=[self.team.pk]), {"persona": "abc", "rol": "sirve"})
        self.assertEqual(response.status_code, 404)

    def setUp(self):
        self.client.force_login(self.leader_user)

    def test_the_coordinator_assigns_unassigns_and_deletes(self):
        self.client.post(self.url("date_detail", self.day.pk), self.date_post(self.member, self.asked))
        self.assertEqual(Assignment.objects.filter(service_date=self.day).count(), 2)
        self.client.post(self.url("date_detail", self.day.pk), self.date_post(self.asked, what="Café"))
        self.assertEqual(list(Assignment.objects.filter(service_date=self.day).values_list("person_id", flat=True)), [self.asked.pk])
        self.day.refresh_from_db()
        self.assertEqual(self.day.what, "Café")
        self.assertContains(self.client.get(self.url("date_delete", self.day.pk)), "¿Borrar la fecha del")
        self.assertRedirects(self.client.post(self.url("date_delete", self.day.pk)), self.url("team", self.team.pk))
        self.assertFalse(ServiceDate.objects.filter(pk=self.day.pk).exists())

    def test_only_dates_from_today_on(self):
        yesterday = (self.today - timedelta(days=1)).isoformat()
        page = self.client.post(self.url("date_create", self.team.pk), self.date_post(date=yesterday))
        self.assertContains(page, "Elige un día de hoy en adelante.")
        self.assertEqual(ServiceDate.objects.filter(team=self.team).count(), 1)
        self.move(self.day, -1)
        self.assertEqual(self.client.get(self.url("date_detail", self.day.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url("date_detail", self.day.pk), self.date_post()).status_code, 404)
        self.assertEqual(self.client.post(self.url("date_delete", self.day.pk)).status_code, 404)
        for write in (services.delete_date, lambda day, actor: services.assign(day, self.member, actor)):
            with self.assertRaises(services.ServingError):
                write(self.day, self.leader_user)
        self.assertTrue(ServiceDate.objects.filter(pk=self.day.pk).exists())

    def test_only_active_members_of_the_team_are_assigned(self):
        with self.assertRaises(services.ServingError):
            services.assign(self.day, self.coord_person, self.leader_user)
        self.client.post(self.url("date_detail", self.day.pk), self.date_post(self.coord_person, self.no_account))
        self.assertFalse(Assignment.objects.filter(service_date=self.day).exists())


class PermissionTests(ServingFixture):
    def test_a_member_cannot_open_the_work_of_the_team_or_the_administration(self):
        self.assign()
        self.client.force_login(self.member_user)
        gets = [
            self.url("team", self.team.pk), self.url("date_create", self.team.pk), self.url("date_detail", self.day.pk),
            self.url("date_delete", self.day.pk), self.url("team_admin"), self.url("team_create"),
            self.url("team_edit", self.team.pk), self.url("team_archive", self.team.pk),
            self.url("member_remove", self.team.pk, self.membership.pk),
        ]
        for url in gets:
            self.assertEqual(self.client.get(url).status_code, 403, url)
            self.assertEqual(self.client.post(url, self.date_post(self.member)).status_code, 403, url)
        response = self.client.post(self.url("member_add", self.team.pk), {"persona": self.member.pk, "rol": "coordina"})
        self.assertEqual(response.status_code, 403)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.role, "sirve")
        self.assertEqual(Team.objects.filter(church=self.church).count(), 2)
        self.assertTrue(self.team.is_active)

    def test_a_member_never_sees_other_members_answers_or_contact_data(self):
        self.assign()
        services.respond(self.assign(self.asked), False, self.asked_user)
        services.set_unavailable(self.asked, self.week + timedelta(days=2), self.asked_user)
        type(self.leader).objects.filter(pk=self.leader.pk).update(phone="55 9999 0000")
        self.client.force_login(self.asked_user)
        page = self.client.get(self.url("home"))
        self.assertContains(page, "Coordina el equipo: Gala Guía.")
        self.assertContains(page, "Dijiste que no puedes")
        for hidden in ("Diego", "55 1111 2222", "diego@prueba.mx", "guia@prueba.mx", "55 9999 0000", "tel:", "mailto:",
                       "Por confirmar", "No puede", "confirmaron", self.url("team", self.team.pk)):
            self.assertNotContains(page, hidden)
        self.client.force_login(self.member_user)
        page = self.client.get(self.url("home"))
        for hidden in ("Pía", "pidio@prueba.mx", "Dijiste que no puedes", "No puede", "tel:", "mailto:"):
            self.assertNotContains(page, hidden)
        self.assertEqual(page.context["days"].count(), 0)

    def test_a_coordinator_cannot_add_people_or_touch_another_team(self):
        other_day = services.save_date(self.other_team, self.admin, date=self.week, what="Ensayo")
        self.client.force_login(self.leader_user)
        self.assertContains(self.client.get(self.url("home")), self.url("team", self.team.pk))
        response = self.client.post(self.url("member_add", self.team.pk), {"persona": self.coord_person.pk, "rol": "sirve"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post(self.url("member_remove", self.team.pk, self.membership.pk)).status_code, 403)
        self.assertEqual(TeamMembership.objects.filter(team=self.team, status="activa").count(), 3)
        page = self.client.get(self.url("team", self.team.pk), {"q": "nora"})
        self.assertNotContains(page, "Nora")
        self.assertNotContains(page, "Agregar a alguien de la iglesia")
        self.assertNotContains(page, "Quitar")
        urls = [
            self.url("team", self.other_team.pk), self.url("date_create", self.other_team.pk),
            self.url("date_detail", other_day.pk), self.url("date_delete", other_day.pk),
            self.url("team_edit", self.team.pk), self.url("team_edit", self.other_team.pk),
            self.url("team_archive", self.team.pk), self.url("team_admin"), self.url("team_create"),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)
            self.assertEqual(self.client.post(url, self.date_post()).status_code, 403, url)
        self.assertTrue(ServiceDate.objects.filter(pk=other_day.pk).exists())
        self.assertFalse(ServiceDate.objects.filter(team=self.other_team).exclude(pk=other_day.pk).exists())

    def test_the_administration_does_what_a_coordinator_does(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url("team", self.team.pk)).status_code, 200)
        self.client.post(self.url("date_detail", self.day.pk), self.date_post(self.member))
        self.assertTrue(Assignment.objects.filter(service_date=self.day, person=self.member).exists())
        self.assertContains(self.client.get(self.url("home")), "Equipos de la iglesia")

    def test_another_church_is_a_404_everywhere(self):
        busy_a = services.set_unavailable(self.member, self.week, self.member_user)
        membership_b = TeamMembership.objects.get(team=self.team_b)
        anyone = [
            self.url("team", self.team_b.pk), self.url("date_create", self.team_b.pk), self.url("date_detail", self.day_b.pk),
            self.url("date_delete", self.day_b.pk), self.url("respond", self.assignment_b.pk),
            self.url("unavailable_remove", self.busy_b.pk),
        ]
        # The administration's own pages answer 403 to anybody else before looking at the id.
        admin_only = [
            self.url("team_edit", self.team_b.pk), self.url("team_archive", self.team_b.pk),
            self.url("member_add", self.team_b.pk), self.url("member_remove", self.team_b.pk, membership_b.pk),
        ]
        data = {**self.date_post(), "respuesta": "aceptar", "persona": self.member_b.pk, "name": "Tomado"}
        for user in (self.admin, self.leader_user, self.member_user):
            self.client.force_login(user)
            for url in anyone + admin_only:
                expected = 403 if url in admin_only and user != self.admin else 404
                self.assertEqual(self.client.post(url, data).status_code, expected, url)
                if "responder" not in url and "no-puedo" not in url and "agregar" not in url:
                    self.assertEqual(self.client.get(url).status_code, expected, url)
            page = self.client.get(self.url("home"))
            self.assertNotContains(page, "Equipo secreto B")
            self.assertNotContains(page, "Servicio de la iglesia B")
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(self.url("team_admin")), "Equipo secreto B")
        # A person of church B cannot be put in a team of church A, nor found by its administration.
        self.client.force_login(self.admin)
        response = self.client.post(self.url("member_add", self.team.pk), {"persona": self.member_b.pk, "rol": "sirve"})
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(self.client.get(self.url("team", self.team.pk), {"q": "bruno"}), "Bruno")
        with self.assertRaises(services.ServingError):
            services.set_member(self.team, self.member_b, self.admin)
        # Nothing of church B moved, and church B cannot reach church A either.
        self.team_b.refresh_from_db()
        self.assignment_b.refresh_from_db()
        self.assertEqual((self.team_b.name, self.team_b.status, self.assignment_b.status), ("Equipo secreto B", "activo", "pendiente"))
        self.assertTrue(ServiceDate.objects.filter(pk=self.day_b.pk).exists())
        self.assertTrue(Unavailable.objects.filter(pk=self.busy_b.pk).exists())
        self.assertEqual(TeamMembership.objects.filter(team=self.team_b).count(), 1)
        self.client.force_login(self.admin_b)
        for url in (self.url("team", self.team.pk), self.url("date_detail", self.day.pk), self.url("team_archive", self.team.pk),
                    self.url("unavailable_remove", busy_a.pk)):
            self.assertEqual(self.client.post(url, data).status_code, 404, url)

    def test_module_off_is_a_404(self):
        assignment = self.assign()
        flags.set_enabled(self.church, "community", False)
        urls = [
            self.url("home"), self.url("team_admin"), self.url("team_create"), self.url("team", self.team.pk),
            self.url("team_edit", self.team.pk), self.url("team_archive", self.team.pk), self.url("member_add", self.team.pk),
            self.url("member_remove", self.team.pk, self.membership.pk), self.url("date_create", self.team.pk),
            self.url("date_detail", self.day.pk), self.url("date_delete", self.day.pk), self.url("respond", assignment.pk),
            self.url("unavailable_add"), self.url("unavailable_remove", 1),
        ]
        for user in (self.admin, self.leader_user, self.member_user):
            self.client.force_login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 404, url)
                self.assertEqual(self.client.post(url, {"respuesta": "aceptar", "dia": self.week.isoformat()}).status_code, 404, url)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, "pendiente")

    def test_signed_out_goes_to_sign_in(self):
        self.assertEqual(self.client.get(self.url("home")).status_code, 302)
        self.assertEqual(self.client.post(self.url("unavailable_add"), {"dia": self.week.isoformat()}).status_code, 302)
        self.assertFalse(Unavailable.objects.filter(date=self.week).exists())


class NothingElseTests(ServingFixture):
    def test_a_membership_grants_no_role_anywhere(self):
        before = (
            RoleAssignment.objects.count(), UserChurchRole.objects.count(), GroupMembership.objects.count(),
            EventMembership.objects.count(),
        )
        team = services.save_team(self.church, self.admin, name="Estacionamiento")
        services.set_member(team, self.member, self.admin, role="coordina")
        services.set_member(team, self.coord_person, self.admin)
        day = services.save_date(team, self.member_user, date=self.week, what="Acomodar coches")
        services.respond(services.assign(day, self.coord_person, self.member_user), True, self.coord1)
        after = (
            RoleAssignment.objects.count(), UserChurchRole.objects.count(), GroupMembership.objects.count(),
            EventMembership.objects.count(),
        )
        self.assertEqual(before, after)
        self.assertFalse(RoleAssignment.objects.filter(subject_user=self.member_user).exists())
        self.assertFalse(UserChurchRole.objects.filter(user=self.member_user).exists())
        self.assertFalse(GroupMembership.objects.filter(person=self.member).exists())
        # Coordinating a team opens nothing of the church's administration or of a group.
        self.client.force_login(self.member_user)
        for url in (reverse("community:group_admin_list"), reverse("community:group_people", args=[self.group.pk]),
                    reverse("agenda:admin_list"), self.url("team_admin")):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_audit_rows_carry_ids_and_fixed_words_only(self):
        type(self.leader).objects.filter(pk=self.leader.pk).update(phone="55 9999 0000")
        assignment = self.assign()
        services.respond(assignment, False, self.member_user)
        services.respond(assignment, True, self.member_user)
        busy = services.set_unavailable(self.member, self.week + timedelta(days=1), self.member_user)
        services.clear_unavailable(busy, self.member_user)
        services.save_date(self.team, self.leader_user, service_date=self.day, date=self.week, what="Café", place="Atrio")
        services.unassign(self.day, self.member, self.leader_user)
        services.remove_member(self.team, self.member, self.admin)
        services.delete_date(self.day, self.leader_user)
        services.save_team(self.church, self.admin, team=self.team, name="Bienvenida", description="Otra cosa.")
        services.archive_team(self.team, self.admin)
        rows = ChurchAuditEvent.objects.filter(church=self.church, action__startswith="equipo_")
        self.assertEqual(
            set(rows.values_list("action", flat=True)),
            {"equipo_creado", "equipo_modificado", "equipo_archivado", "equipo_integrante", "equipo_fecha",
             "equipo_asignacion", "equipo_respuesta"},
        )
        for row in rows:
            self.assertLessEqual(set(row.metadata), {"person_id", "role"})
            text = f"{row.summary} {row.metadata}"
            for secret in ("55 1111 2222", "55 9999 0000", "@", "Templo", "Atrio", "Otra cosa"):
                self.assertNotIn(secret, text)


class ServiceFunctionTests(ServingFixture):
    def test_my_dates_and_pending_count(self):
        days = [self.day] + [
            services.save_date(self.team, self.leader_user, date=self.today + timedelta(days=offset), what=f"Fecha {offset}")
            for offset in (0, 2, 3, 10, 12, 14)
        ]
        assignments = {day.pk: self.assign(day=day) for day in days}
        self.assertEqual(services.pending_count(self.member), 7)
        mine = services.my_dates(self.member)
        self.assertEqual(len(mine), 5)
        self.assertEqual([row.service_date.date for row in mine], sorted(day.date for day in days)[:5])
        with self.assertNumQueries(0):
            self.assertEqual({row.service_date.team.name for row in mine}, {"Bienvenida"})
        self.assertEqual(len(services.my_dates(self.member, limit=2)), 2)

        services.respond(assignments[days[1].pk], True, self.member_user)   # today: accepted stays, not pending
        services.respond(assignments[days[2].pk], False, self.member_user)  # declined leaves both
        self.assertEqual(services.pending_count(self.member), 5)
        mine = services.my_dates(self.member, limit=10)
        self.assertEqual(len(mine), 6)
        self.assertNotIn(days[2].pk, [row.service_date_id for row in mine])
        self.assertEqual(mine[0].service_date_id, days[1].pk)
        # "today" moves: what already passed is gone.
        later = self.today + timedelta(days=11)
        self.assertEqual([row.service_date.date for row in services.my_dates(self.member, today=later)],
                         [self.today + timedelta(days=12), self.today + timedelta(days=14)])
        self.assertEqual(services.pending_count(self.member, today=later), 2)
        self.assertEqual(services.my_dates(self.asked), [])
        self.assertEqual(services.pending_count(self.asked), 0)

    def test_an_archived_team_hides_its_dates(self):
        assignment = self.assign()
        services.archive_team(self.team, self.admin)
        self.assertEqual(services.my_dates(self.member), [])
        self.assertEqual(services.pending_count(self.member), 0)
        self.assertFalse(services.has_teams(self.member))
        self.assertEqual(services.coordinated_teams(self.leader), [])
        self.client.force_login(self.member_user)
        page = self.client.get(self.url("home"))
        self.assertNotContains(page, "Bienvenida del domingo")
        self.assertContains(page, "Todavía no estás en ningún equipo.")
        self.assertEqual(self.client.post(self.url("respond", assignment.pk), {"respuesta": "aceptar"}).status_code, 404)
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.get(self.url("team", self.team.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url("date_detail", self.day.pk)).status_code, 404)
        with self.assertRaises(services.ServingError):
            services.set_member(self.team, self.coord_person, self.admin)

    def test_coordinated_teams_and_has_teams(self):
        self.assertEqual(services.coordinated_teams(self.leader), [self.team])
        self.assertEqual(services.coordinated_teams(self.member), [])
        self.assertEqual(services.coordinated_teams(self.admin_person), [])
        self.assertTrue(services.has_teams(self.leader))
        self.assertTrue(services.has_teams(self.member))
        self.assertFalse(services.has_teams(self.coord_person))
        services.set_member(self.other_team, self.leader, self.admin, role="coordina")
        self.assertEqual(services.coordinated_teams(self.leader), [self.other_team, self.team])
        services.remove_member(self.team, self.leader, self.admin)
        self.assertEqual(services.coordinated_teams(self.leader), [self.other_team])
        services.set_member(self.other_team, self.leader, self.admin, role="sirve")
        self.assertEqual(services.coordinated_teams(self.leader), [])
        self.assertTrue(services.has_teams(self.leader))
        self.assertEqual(services.coordinated_teams(self.member_b), [self.team_b])
