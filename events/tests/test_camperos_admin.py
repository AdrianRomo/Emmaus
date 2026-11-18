"""The administration's Camperos: assigning a coordinator to several camperos at once (admin.participants_assign),
the minors without a registered guardian authorization on the administration's list (?autorizacion=falta), a bare
number found as a room too, and the redesigned screens (ui_v2) that show both facts apart.

Who may do what does not change here: only the administration assigns, only camperos it sees taking part in this
event, only active coordinators of this event, and every change keeps its own audit row.
"""

from __future__ import annotations

from django.urls import reverse

from events.models import AuditEvent, EventMembership, EventParticipant, InterviewStatus, MembershipRole

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
