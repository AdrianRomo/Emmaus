"""The administration's Camperos: assigning a coordinator to several camperos at once (admin.participants_assign),
the minors without a registered guardian authorization on the administration's list (?autorizacion=falta), a bare
number found as a room too, and the redesigned screens (ui_v2) that show both facts apart.

Who may do what does not change here: only the administration assigns, only camperos it sees taking part in this
event, only active coordinators of this event, and every change keeps its own audit row.
"""

from __future__ import annotations

import re

from django.urls import reverse

from events.models import AuditEvent, EventMembership, EventParticipant, FollowUp, InterviewStatus, MembershipRole
from people import flags
from people.models import Person

from .base import CampFixture
from .test_ux_coordinator import give_interview


def said(response) -> str:
    """The message the action left (the newest one: an earlier one, not shown yet, waits in the same storage)."""
    return [str(m) for m in response.wsgi_request._messages][-1]


class AssignCoordinatorTests(CampFixture):
    def post(self, data, user=None):
        self.client.force_login(user or self.admin)
        return self.client.post(self.url("participants_assign"), data)

    def test_the_administration_assigns_the_marked_camperos_and_each_change_is_audited(self):
        list_url = self.url("participants") + "?coordinador=ninguno&orden=edad"
        response = self.post({"campero": [str(self.p1.public_id), str(self.p2.public_id)], "coordinador": self.m_coord2.pk, "siguiente": list_url})
        self.assertRedirects(response, list_url, fetch_redirect_response=False)
        for p in (self.p1, self.p2):
            p.refresh_from_db()
            self.assertEqual(p.coordinator, self.m_coord2)
        rows = AuditEvent.objects.filter(event=self.event, action="campero_reasignado", actor=self.admin)
        self.assertEqual(rows.count(), 2)
        self.assertEqual({r.object_id for r in rows}, {str(self.p1.public_id), str(self.p2.public_id)})
        self.assertEqual(said(response), "Asignaste 2 camperos a César Dos.")

    def test_ids_of_another_event_withdrawn_or_practice_camperos_are_ignored(self):
        withdrawn = EventParticipant.objects.create(
            event=self.event, number=40, full_name="Ya No Participa", age=15, room="4", church_group="X", is_active=False
        )
        practice = EventParticipant.objects.create(event=self.event, number=41, full_name="Campero de práctica", age=15, room="-", church_group="", is_practice=True)
        response = self.post({
            "campero": [str(self.p_other_event.public_id), str(withdrawn.public_id), str(practice.public_id), "no-es-un-id", str(self.p3.public_id)],
            "coordinador": self.m_coord1.pk,
        })
        self.assertRedirects(response, self.url("participants"), fetch_redirect_response=False)
        self.p_other_event.refresh_from_db()
        withdrawn.refresh_from_db()
        practice.refresh_from_db()
        self.p3.refresh_from_db()
        self.assertEqual(self.p_other_event.coordinator, self.m_other)
        self.assertIsNone(withdrawn.coordinator)
        self.assertIsNone(practice.coordinator)
        self.assertEqual(self.p3.coordinator, self.m_coord1)  # the one that is this event's moves
        self.assertEqual(AuditEvent.objects.filter(action="campero_reasignado").count(), 1)
        self.assertFalse(AuditEvent.objects.filter(event=self.other_event, action="campero_reasignado").exists())

    def test_only_active_coordinators_of_this_event(self):
        self.m_coord3.is_active = False
        self.m_coord3.save()
        tutor_membership = self.m_tutor1
        for membership in (self.m_coord3, self.m_other, tutor_membership):
            with self.subTest(membership=membership.pk):
                response = self.post({"campero": [str(self.p1.public_id)], "coordinador": membership.pk})
                self.p1.refresh_from_db()
                self.assertEqual(self.p1.coordinator, self.m_coord1)
                self.assertEqual(said(response), "Elige un coordinador.")
        self.assertFalse(AuditEvent.objects.filter(action="campero_reasignado").exists())

    def test_only_the_administration(self):
        data = {"campero": [str(self.p1.public_id)], "coordinador": self.m_coord2.pk}
        for user in (self.coord1, self.tutor1, self.pastor, self.care, self.outsider, self.other_coord):
            with self.subTest(user=user.username):
                self.assertEqual(self.post(data, user).status_code, 403)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.coordinator, self.m_coord1)
        self.client.logout()
        self.assertEqual(self.client.post(self.url("participants_assign"), data).status_code, 302)  # to sign in
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url("participants_assign")).status_code, 405)
        # The administration of another event of another church does not reach this one by its ids either
        self.assertFalse(AuditEvent.objects.filter(action="campero_reasignado").exists())

    def test_the_home_assigns_row_by_row(self):
        home = self.url("admin_home")
        response = self.post({
            f"coordinador-{self.p1.public_id}": self.m_coord2.pk,
            f"coordinador-{self.p3.public_id}": self.m_coord3.pk,
            f"coordinador-{self.p4.public_id}": "",  # left "Sin asignar": untouched
            f"coordinador-{self.p_other_event.public_id}": self.m_coord2.pk,
            "siguiente": home,
        })
        self.assertRedirects(response, home, fetch_redirect_response=False)
        for p, membership in ((self.p1, self.m_coord2), (self.p3, self.m_coord3), (self.p4, self.m_coord3), (self.p_other_event, self.m_other)):
            p.refresh_from_db()
            self.assertEqual(p.coordinator, membership)
        self.assertEqual(said(response), "Asignaste 2 camperos.")

    def test_nothing_marked_or_nothing_chosen_says_so(self):
        cases = (
            ({"coordinador": self.m_coord2.pk}, "Marca al menos un campero."),
            ({f"coordinador-{self.p1.public_id}": ""}, "Elige el coordinador de al menos un campero."),
            ({"campero": [str(self.p1.public_id)], "coordinador": ""}, "Elige un coordinador."),
            ({"campero": [str(self.p1.public_id)], "coordinador": self.m_coord1.pk}, "Nada cambió: ya tenían ese coordinador."),
        )
        for data, words in cases:
            with self.subTest(words=words):
                response = self.post(data)
                self.assertEqual(said(response), words)
        self.assertFalse(AuditEvent.objects.filter(action="campero_reasignado").exists())

    def test_the_way_back_is_this_site_only(self):
        response = self.post({"campero": [str(self.p1.public_id)], "coordinador": self.m_coord2.pk, "siguiente": "https://otro.example/camperos/"})
        self.assertRedirects(response, self.url("participants"), fetch_redirect_response=False)


class AdminListFiltersTests(CampFixture):
    def names(self, query="", user=None):
        self.client.force_login(user or self.admin)
        response = self.client.get(self.url("participants") + query)
        self.assertEqual(response.status_code, 200)
        return {p.full_name for p in response.context["page"].object_list}

    def test_minors_without_authorization(self):
        self.assertEqual(self.names("?autorizacion=falta"), {"Lucía Torres Gómez"})
        self.assertEqual(self.names("?autorizacion=falta", self.tutor1), {"Lucía Torres Gómez"})  # her coordinator is in the team
        self.assertEqual(self.names("?autorizacion=falta", self.tutor2), set())  # not in this tutor's team: scoping holds
        # An interviewed minor without authorization is still a minor without authorization: two separate facts
        give_interview(self.p_minor_no_auth, InterviewStatus.COMPLETED)
        self.assertEqual(self.names("?autorizacion=falta"), {"Lucía Torres Gómez"})
        # A withdrawn minor is not waiting for anything
        self.p_minor_no_auth.is_active = False
        self.p_minor_no_auth.save()
        self.assertEqual(self.names("?autorizacion=falta&participa=todos"), set())

    def test_minors_filter_only_when_the_event_asks_for_it(self):
        self.event.requires_guardian_authorization = False
        self.event.save()
        self.assertEqual(len(self.names("?autorizacion=falta")), 5)

    def test_a_bare_number_is_a_room_too(self):
        cabin = EventParticipant.objects.create(event=self.event, number=30, full_name="Rosa Cabaña Cinco", age=15, room="Cabaña 5", church_group="X")
        EventParticipant.objects.create(event=self.event, number=31, full_name="Raúl Cabaña Quince", age=15, room="Cabaña 15", church_group="X")
        self.assertEqual(self.names("?q=5"), {"Lucía Torres Gómez", cabin.full_name})  # #0005, and the room "Cabaña 5"
        self.assertEqual(self.names("?q=%235"), {"Lucía Torres Gómez"})  # "#5" is the number only
        self.assertEqual(self.names("?q=05"), {"Lucía Torres Gómez", cabin.full_name})


def main_of(response) -> str:
    return response.content.decode().split("<main", 1)[1]


class RedesignedCamperosTests(CampFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        flags.set_enabled(cls.church, "community", True)
        for user in (cls.admin, cls.tutor1):
            Person.objects.create(church=cls.church, user=user, first_name=user.first_name, last_name=user.last_name)

    def test_the_list_shows_authorization_apart_from_the_interview(self):
        give_interview(self.p_minor_no_auth, InterviewStatus.COMPLETED)
        self.client.force_login(self.admin)
        page = main_of(self.client.get(self.url("participants")))
        self.assertIn('<th scope="col"><span class="camperos__th">Autorización</span></th>', page)
        row = re.search(r"<tr>.*?Lucía Torres Gómez.*?</tr>", page, re.DOTALL).group(0)
        self.assertIn("Completada", row)
        self.assertIn('<span class="chip chip--wait">Falta', row)
        adult = re.search(r"<tr>.*?Diego Hernández Cruz.*?</tr>", page, re.DOTALL).group(0)
        self.assertIn('<td class="camperos__autorizacion"></td>', adult)
        authorized = re.search(r"<tr>.*?Mariana López Ruiz.*?</tr>", page, re.DOTALL).group(0)
        self.assertIn("Autorizada", authorized)
        self.assertIn('href="?autorizacion=falta"', page)
        self.assertIn("Menores sin autorización <span class=\"count\">1</span>", page)

    def test_only_the_administration_marks_rows(self):
        self.client.force_login(self.admin)
        page = main_of(self.client.get(self.url("participants")))
        self.assertEqual(page.count("data-seleccion-fila"), 5)
        self.assertIn(f'action="{self.url("participants_assign")}"', page)
        self.assertIn("César Dos · 1", page)  # each coordinator with the camperos it has now
        self.client.force_login(self.tutor1)
        page = main_of(self.client.get(self.url("participants")))
        self.assertNotIn("data-seleccion", page)
        self.assertNotIn(self.url("participants_assign"), page)
        self.assertNotIn("Sin coordinador <span", page)  # a tutor's list never has a campero without one

    def test_nobody_matches(self):
        self.client.force_login(self.admin)
        page = main_of(self.client.get(self.url("participants") + "?q=zzzz&orden=edad"))
        self.assertIn("Nadie coincide", page)
        self.assertIn('href="?orden=edad&amp;dir=asc">Quitar filtros</a>', page)
        self.assertNotIn("Importar", page.split("Nadie coincide", 1)[1].split("</section>", 1)[0])

    def test_a_queue_emptied_by_work_says_it_is_done(self):
        """Every campero has a coordinator: «Sin coordinador» is a finished queue, not a failed search."""
        self.client.force_login(self.admin)
        page = main_of(self.client.get(self.url("participants") + "?coordinador=ninguno"))
        self.assertIn("Todos tienen coordinador", page)
        self.assertNotIn("Nadie coincide", page)
        self.assertNotIn("Quitar filtros", page)
        searched = main_of(self.client.get(self.url("participants") + "?coordinador=ninguno&q=zzzz"))
        self.assertIn("Nadie coincide", searched)  # a search is on: there is something to take away
        self.assertIn("Quitar filtros", searched)

    def test_the_whole_row_opens_the_record_and_the_table_names_its_own_region(self):
        self.client.force_login(self.admin)
        page = main_of(self.client.get(self.url("participants")))
        self.assertIn('<th scope="row" class="camperos__nombre"><a href="' + self.url("participant_detail", self.p1.public_id) + '">', page)
        self.assertIn('role="region" aria-label="Tabla de camperos"', page)
        self.assertEqual(page.count('aria-labelledby="resultados"'), 1)  # the section only: no second landmark of the same name
        self.assertNotIn("table--stack", page)  # the list decides its own columns (santuario.css, Camperos)

    def test_the_home_puts_risk_first_then_minors_then_the_assignment(self):
        FollowUp.objects.create(event=self.event, participant=self.p2, kind=FollowUp.Kind.SAFETY, reason=FollowUp.Reason.TALK_TO_LEADER, created_by=self.coord1)
        unassigned = EventParticipant.objects.create(event=self.event, number=20, full_name="Nadia Sin Coordinador", age=20, room="7", church_group="X")
        self.client.force_login(self.admin)
        page = main_of(self.client.get(self.url("admin_home")))
        risk = page.index("Situación de riesgo abierta")
        minors = page.index("Menor espera la autorización")
        sign = page.index("Asigna coordinador a 1 campero")
        self.assertLess(risk, minors)
        self.assertLess(minors, sign)
        self.assertIn(f'href="{self.url("participants")}?autorizacion=falta&amp;estado=pendiente"', page)
        self.assertIn(f'name="coordinador-{unassigned.public_id}"', page)
        self.assertNotIn("figures__alert", page)  # the risk is a decision, not a tile
        fragment = self.client.get(self.url("admin_home"), HTTP_X_REQUESTED_WITH="fetch").content.decode()
        self.assertIn(f'name="coordinador-{unassigned.public_id}"', fragment)
        self.assertNotIn("<html", fragment)
        self.assertNotIn("Ver los", page)

    def test_minors_without_authorization_are_two_rows_each_counting_its_list(self):
        """Waiting for the authorization (not interviewed yet, vela) and interviewed without a current one (in progress
        or completed, chile) are two facts; each number is the length of the Camperos list its row opens, and a minor
        already interviewed is never counted as one who «no se le puede entrevistar»."""
        waiting_too = EventParticipant.objects.create(event=self.event, number=31, full_name="Ana Espera Firma", age=12, room="2", church_group="X", coordinator=self.m_coord2)
        done = EventParticipant.objects.create(event=self.event, number=32, full_name="Beto Ya Entrevistado", age=14, room="3", church_group="X", coordinator=self.m_coord2)
        live = EventParticipant.objects.create(event=self.event, number=33, full_name="Caro En Curso", age=15, room="4", church_group="X", coordinator=self.m_coord3)
        give_interview(done, InterviewStatus.COMPLETED)
        give_interview(live, InterviewStatus.IN_PROGRESS)
        self.client.force_login(self.admin)
        page = main_of(self.client.get(self.url("admin_home")))
        waiting = re.search(r'estado=pendiente">\s*<span class="decision__cifra"[^>]*>(\d+)</span>.*?</a>', page, re.DOTALL)
        interviewed = re.search(r'estado=iniciada">\s*<span class="decision__cifra"[^>]*>(\d+)</span>.*?</a>', page, re.DOTALL)
        self.assertEqual(waiting.group(1), "2")  # Lucía and Ana: nobody interviewed them yet
        self.assertIn("Menores esperan la autorización", waiting.group(0))
        self.assertIn("padre, madre o tutor legal no se les puede entrevistar", waiting.group(0))
        self.assertEqual(interviewed.group(1), "2")  # Beto (completed) and Caro (in progress)
        self.assertIn("Entrevistados sin autorización vigente", interviewed.group(0))
        self.assertIn("decision--riesgo", page[page.rindex("<a", 0, interviewed.start()):interviewed.start()])
        for status, names in (("pendiente", {"Lucía Torres Gómez", "Ana Espera Firma"}), ("iniciada", {"Beto Ya Entrevistado", "Caro En Curso"})):
            listed = self.client.get(self.url("participants") + f"?autorizacion=falta&estado={status}").context["page"]
            with self.subTest(status=status):
                self.assertEqual(listed.paginator.count, len(names))
                self.assertEqual({p.full_name for p in listed.object_list}, names)
        self.assertNotIn(waiting_too.full_name, {p.full_name for p in self.client.get(self.url("participants") + "?autorizacion=falta&estado=iniciada").context["page"].object_list})

    def test_no_minor_row_when_nobody_is_missing_an_authorization(self):
        from events.models import ConsentRecord
        ConsentRecord.objects.create(participant=self.p_minor_no_auth, kind=ConsentRecord.Kind.GUARDIAN, status=ConsentRecord.Status.GRANTED)
        self.client.force_login(self.admin)
        page = main_of(self.client.get(self.url("admin_home")))
        self.assertNotIn("autorizacion=falta", page)

    def test_the_home_lists_ten_and_leads_to_the_rest(self):
        for n in range(11):
            EventParticipant.objects.create(event=self.event, number=60 + n, full_name=f"Persona {n:02d} Sin Coordinador", age=20, room=str(n), church_group="X")
        self.client.force_login(self.admin)
        page = main_of(self.client.get(self.url("admin_home")))
        self.assertEqual(len(re.findall(r'name="coordinador-[0-9a-f-]{36}"', page)), 10)
        self.assertIn(f'href="{self.url("participants")}?coordinador=ninguno">Ver los 11</a>', page)

    def test_the_record_says_the_authorization_and_asks_before_erasing(self):
        give_interview(self.p_minor_no_auth, InterviewStatus.COMPLETED)
        self.client.force_login(self.admin)
        page = main_of(self.client.get(self.url("participant_detail", self.p_minor_no_auth.public_id)))
        self.assertIn("Entrevistado sin autorización vigente", page)
        self.assertIn(f'href="{self.url("guardian_authorization", self.p_minor_no_auth.public_id)}">Marcar como recibida</a>', page)
        self.assertEqual(page.count("Marcar como recibida"), 1)
        self.assertIn(f'<a class="btn btn--danger-outline" href="{self.url("participant_delete", self.p_minor_no_auth.public_id)}">Eliminar sus datos</a>', page)
        page = main_of(self.client.get(self.url("participant_detail", self.p1.public_id)))
        self.assertIn("Autorizada", page)
        self.assertNotIn("sin autorización vigente", page)

    def test_with_the_module_off_none_of_it_is_served(self):
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.admin)
        page = main_of(self.client.get(self.url("participants")))
        self.assertNotIn("data-seleccion", page)
        self.assertNotIn("camperos__", page)
        self.assertIn('<form method="get" class="filter-bar admin-people__filters"', page)
        record = main_of(self.client.get(self.url("participant_detail", self.p_minor_no_auth.public_id)))
        self.assertIn('<p class="more"><a href="' + self.url("participant_delete", self.p_minor_no_auth.public_id) + '">Eliminar sus datos</a></p>', record)
        self.assertNotIn("ficha__", record)
        home = main_of(self.client.get(self.url("admin_home")))
        self.assertNotIn("admin-esperan", home)


class CoordinatorLoadTests(CampFixture):
    def test_load_counts_camperos_taking_part_and_never_the_practice_one(self):
        from events.access import resolve_access
        from events.services.participants import assignable_coordinators

        EventParticipant.objects.create(event=self.event, number=50, full_name="Práctica", age=15, room="-", church_group="", coordinator=self.m_coord2, is_practice=True)
        EventParticipant.objects.create(event=self.event, number=51, full_name="Se fue", age=15, room="1", church_group="", coordinator=self.m_coord2, is_active=False)
        EventMembership.objects.create(event=self.event, user=self.outsider, role=MembershipRole.COORDINATOR, is_active=False)
        loads = {m.pk: m.load for m in assignable_coordinators(resolve_access(self.admin, self.event))}
        self.assertEqual(loads, {self.m_coord1.pk: 3, self.m_coord2.pk: 1, self.m_coord3.pk: 1})
        self.assertIsNotNone(reverse("events:participants_assign", args=[self.event.slug]))
