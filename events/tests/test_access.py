"""Authorization: role scope, cross-event isolation, sensitive fields, reports."""

from django.urls import reverse

from events.access import resolve_access
from events.models import FollowUp, Interview, MembershipRole, ReportSnapshot
from events.services.reports import generate_snapshot, scopes_for_event
from django.utils import timezone

from .base import CampFixture


class RoleResolutionTests(CampFixture):
    def test_church_admin_is_event_admin_everywhere_in_the_church(self):
        self.assertEqual(resolve_access(self.admin, self.event).role, MembershipRole.ADMIN)
        self.assertEqual(resolve_access(self.admin, self.other_event).role, MembershipRole.ADMIN)

    def test_roles_are_event_scoped(self):
        self.assertIsNone(resolve_access(self.outsider, self.event))
        self.assertEqual(resolve_access(self.outsider, self.other_event).role, MembershipRole.TUTOR)
        self.assertIsNone(resolve_access(self.coord1, self.other_event))

    def test_inactive_membership_has_no_access(self):
        self.m_coord3.is_active = False
        self.m_coord3.save()
        self.assertIsNone(resolve_access(self.coord3, self.event))

    def test_pastor_status_does_not_grant_sensitive_access(self):
        self.assertFalse(resolve_access(self.pastor, self.event).can_view_sensitive)
        self.assertFalse(resolve_access(self.admin, self.event).can_view_sensitive)
        self.assertTrue(resolve_access(self.care, self.event).can_view_sensitive)


class ScopeTests(CampFixture):
    def test_admin_and_pastor_see_whole_event(self):
        for user in (self.admin, self.pastor):
            ids = set(resolve_access(user, self.event).participants().values_list("id", flat=True))
            self.assertEqual(ids, {self.p1.id, self.p2.id, self.p3.id, self.p4.id, self.p_minor_no_auth.id})

    def test_tutor_sees_only_assigned_coordinators_and_their_campers(self):
        access = resolve_access(self.tutor1, self.event)
        self.assertEqual(set(access.coordinators().values_list("id", flat=True)), {self.m_coord1.id, self.m_coord2.id})
        self.assertEqual(
            set(access.participants().values_list("id", flat=True)), {self.p1.id, self.p2.id, self.p3.id, self.p_minor_no_auth.id}
        )
        self.assertNotIn(self.p4.id, access.participants().values_list("id", flat=True))

    def test_coordinator_sees_only_own_campers(self):
        access = resolve_access(self.coord2, self.event)
        self.assertEqual(list(access.participants().values_list("id", flat=True)), [self.p3.id])

    def test_tutor_cannot_open_unassigned_coordinator_by_url(self):
        self.login(self.tutor1)
        self.assertEqual(self.client.get(self.url("coordinator_detail", self.m_coord3.id)).status_code, 404)
        self.assertEqual(self.client.get(self.url("coordinator_detail", self.m_coord1.id)).status_code, 200)

    def test_tutor_cannot_open_unassigned_participant_by_url(self):
        self.login(self.tutor1)
        self.assertEqual(self.client.get(self.url("participant_detail", self.p4.public_id)).status_code, 404)

    def test_tutor_filter_cannot_widen_scope(self):
        self.login(self.tutor1)
        response = self.client.get(self.url("interviews") + f"?coordinador={self.m_coord3.id}")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.p4.full_name)

    def test_coordinator_cannot_open_other_coordinators_camper(self):
        self.login(self.coord2)
        self.assertEqual(self.client.get(self.url("participant_detail", self.p1.public_id)).status_code, 404)
        self.assertEqual(self.client.get(self.url("interview_start", self.p1.public_id)).status_code, 404)

    def test_cross_event_object_is_not_reachable_through_another_event_url(self):
        self.login(self.admin)
        response = self.client.get(self.url("participant_detail", self.p_other_event.public_id))
        self.assertEqual(response.status_code, 404)

    def test_outsider_gets_403_for_event(self):
        self.login(self.outsider)
        self.assertEqual(self.client.get(self.url("home")).status_code, 403)

    def test_role_pages_are_closed_to_other_roles(self):
        self.login(self.coord1)
        for name in ("admin_home", "pastor_home", "tutor_home", "participants", "settings", "audit", "team"):
            self.assertEqual(self.client.get(self.url(name)).status_code, 403, name)
        self.login(self.tutor1)
        for name in ("admin_home", "pastor_home", "coord_home", "settings", "participant_export"):
            self.assertEqual(self.client.get(self.url(name)).status_code, 403, name)
        self.login(self.pastor)
        for name in ("admin_home", "settings", "participant_export", "participant_import", "member_add"):
            self.assertEqual(self.client.get(self.url(name)).status_code, 403, name)

    def test_navigation_shows_only_role_items(self):
        self.login(self.coord1)
        response = self.client.get(self.url("coord_home"))
        self.assertContains(response, "Mis camperos")
        self.assertNotContains(response, "Configuración")
        self.assertNotContains(response, "Reportes")


class SensitiveFieldTests(CampFixture):
    def _completed_interview(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p1)
        interview = Interview.objects.get(participant=self.p1)
        self.answer(interview, "dudas", "si", extra={f"q_{self.question('dudas_detalle').id}": "TEXTO-CONFIDENCIAL-123"})
        self.answer(interview, "salvo", "si")
        interview.refresh_from_db()
        self.client.post(self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "no_requerido"})
        interview.refresh_from_db()
        self.assertEqual(interview.status, Interview.Status.COMPLETED)
        return interview

    def test_confidential_text_visible_only_with_explicit_authorization(self):
        interview = self._completed_interview()
        detail = self.url("interview_detail", interview.public_id)
        for user, visible in [(self.care, True), (self.pastor, False), (self.admin, False), (self.tutor1, False), (self.coord1, False)]:
            self.login(user)
            response = self.client.get(detail)
            self.assertEqual(response.status_code, 200, user)
            if visible:
                self.assertContains(response, "TEXTO-CONFIDENCIAL-123")
            else:
                self.assertNotContains(response, "TEXTO-CONFIDENCIAL-123")

    def test_spiritual_answers_hidden_from_tutor_but_visible_to_assigned_coordinator(self):
        interview = self._completed_interview()
        access_tutor = resolve_access(self.tutor1, self.event)
        access_coord = resolve_access(self.coord1, self.event)
        salvo = self.question("salvo")
        self.assertFalse(access_tutor.can_view_answer(interview, salvo))
        self.assertTrue(access_coord.can_view_answer(interview, salvo))

    def test_viewing_confidential_answers_is_audited(self):
        interview = self._completed_interview()
        self.login(self.care)
        self.client.get(self.url("interview_detail", interview.public_id))
        self.assertTrue(self.event.audit_events.filter(action="consulta_confidencial", actor=self.care).exists())

    def test_safety_reports_hidden_from_pastor_without_authorization(self):
        follow_up = FollowUp.objects.create(
            event=self.event, participant=self.p1, kind=FollowUp.Kind.SAFETY, reason=FollowUp.Reason.SAFETY,
            details="DETALLE-RIESGO", created_by=self.coord1,
        )
        self.login(self.pastor)
        self.assertEqual(self.client.get(self.url("follow_up_detail", follow_up.id)).status_code, 404)
        self.login(self.care)
        self.assertContains(self.client.get(self.url("follow_up_detail", follow_up.id)), "DETALLE-RIESGO")
        self.login(self.admin)
        response = self.client.get(self.url("follow_up_detail", follow_up.id))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "DETALLE-RIESGO")


class ReportAccessTests(CampFixture):
    def setUp(self):
        now = timezone.now().replace(second=0, microsecond=0)
        for scope in scopes_for_event(self.event):
            generate_snapshot(self.event, scope, trigger="manual", cutoff_at=now)
        self.event_snapshot = ReportSnapshot.objects.get(event=self.event, scope_key="evento")
        self.team2_snapshot = ReportSnapshot.objects.get(event=self.event, scope_key=f"equipo:{self.m_tutor2.id}")

    def test_tutor_cannot_read_event_or_other_team_snapshots(self):
        self.login(self.tutor1)
        for snap in (self.event_snapshot, self.team2_snapshot):
            for name in ("report_detail", "report_pdf", "report_presentation"):
                self.assertEqual(self.client.get(self.url(name, snap.public_id)).status_code, 404, name)

    def test_saved_report_does_not_outlive_access(self):
        own = ReportSnapshot.objects.get(event=self.event, scope_key=f"equipo:{self.m_tutor1.id}")
        self.login(self.tutor1)
        self.assertEqual(self.client.get(self.url("report_detail", own.public_id)).status_code, 200)
        self.m_tutor1.is_active = False
        self.m_tutor1.save()
        self.assertEqual(self.client.get(self.url("report_detail", own.public_id)).status_code, 403)

    def test_pdf_download_for_pastor_is_a_pdf_and_audited(self):
        self.login(self.pastor)
        response = self.client.get(self.url("report_pdf", self.event_snapshot.public_id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertTrue(self.event.audit_events.filter(action="exportacion", actor=self.pastor).exists())

    def test_tutor_cannot_generate_event_scope_cutoff(self):
        self.login(self.tutor1)
        response = self.client.post(self.url("report_generate"), {"scope": "evento"})
        self.assertIn(response.status_code, (302,))
        self.assertFalse(ReportSnapshot.objects.filter(event=self.event, scope_key="evento", created_by=self.tutor1).exists())

    def test_admin_export_is_audited_and_has_no_answers(self):
        self.login(self.admin)
        response = self.client.get(self.url("participant_export"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Mariana López Ruiz", body)
        self.assertNotIn("salvo", body.lower())
        self.assertTrue(self.event.audit_events.filter(action="exportacion").exists())

    def test_individual_data_export_requires_sensitive_access(self):
        self.login(self.admin)
        self.assertEqual(self.client.get(self.url("participant_data_export", self.p1.public_id)).status_code, 403)

    def test_events_list_only_shows_accessible_events(self):
        self.login(self.coord1)
        response = self.client.get(reverse("events:list") + "?todos=1")
        self.assertContains(response, "Campamento A")
        self.assertNotContains(response, "Campamento B")
