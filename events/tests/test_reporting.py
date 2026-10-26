"""Metrics, cutoffs, presentation privacy."""

import re
from datetime import datetime, time, timedelta
from urllib.parse import quote
from unittest import mock

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from events.access import resolve_access
from events.models import (
    Answer,
    EventParticipant,
    FollowUp,
    Interview,
    InterviewStatus,
    ReportSnapshot,
)
from events.services.metrics import (
    build_payload,
    compare,
    event_scope,
    local_tz,
    progress_for,
    scope_for_access,
    suppress,
    team_scope,
)
from events.services.presentation import build_deck
from events.services.questionnaire import options_by_code
from events.services.reports import generate_snapshot, latest_due_slot, next_slot, run_due_cutoffs

from .base import CampFixture


def complete(event, participant, answers=None, when=None, decision="no_requerido", questions=None):
    when = when or timezone.now()
    interview = Interview.objects.create(
        event=event, participant=participant, version=event.questionnaire_version, status=Interview.Status.COMPLETED,
        completed_at=when, started_at=when - timedelta(minutes=10), follow_up_decision=decision,
    )
    for code, values in (answers or {}).items():
        question = questions[code]
        answer = Answer.objects.create(interview=interview, question=question)
        opts = options_by_code(question)
        answer.options.set([opts[v] for v in values])
    participant.interview_status = InterviewStatus.COMPLETED
    participant.save(update_fields=["interview_status"])
    return interview


class MetricTests(CampFixture):
    def test_zero_denominator(self):
        progress = progress_for(EventParticipant.objects.none())
        self.assertIsNone(progress.percent)
        self.assertEqual(progress.pending, 0)

    def test_counts_and_percent(self):
        complete(self.event, self.p1)
        complete(self.event, self.p3)
        self.p2.interview_status = InterviewStatus.IN_PROGRESS
        self.p2.save()
        progress = build_payload(event_scope(self.event))["progress"]
        self.assertEqual(progress["eligible"], 5)
        self.assertEqual(progress["completed"], 2)
        self.assertEqual(progress["in_progress"], 1)
        self.assertEqual(progress["pending"], 3)
        self.assertEqual(progress["not_started"], 2)
        self.assertEqual(progress["percent"], 40)

    def test_withdrawn_campers_are_not_eligible(self):
        self.p4.is_active = False
        self.p4.save()
        self.assertEqual(build_payload(event_scope(self.event))["progress"]["eligible"], 4)

    def test_corrections_do_not_double_count(self):
        interview = complete(self.event, self.p1)
        interview.correction_count = 3
        interview.save()
        self.assertEqual(build_payload(event_scope(self.event))["progress"]["completed"], 1)

    def test_scopes_by_role(self):
        complete(self.event, self.p4)  # team 2
        tutor1 = build_payload(scope_for_access(resolve_access(self.tutor1, self.event)))
        tutor2 = build_payload(scope_for_access(resolve_access(self.tutor2, self.event)))
        coord3 = build_payload(scope_for_access(resolve_access(self.coord3, self.event)))
        self.assertEqual((tutor1["progress"]["eligible"], tutor1["progress"]["completed"]), (4, 0))
        self.assertEqual((tutor2["progress"]["eligible"], tutor2["progress"]["completed"]), (1, 1))
        self.assertEqual(coord3["progress"]["percent"], 100)
        self.assertEqual([t["label"] for t in tutor1["teams"]], [])  # tutors see coordinators, not other teams

    def test_team_breakdown_sums_to_event(self):
        complete(self.event, self.p1)
        payload = build_payload(event_scope(self.event))
        self.assertEqual(sum(t["eligible"] for t in payload["teams"]), payload["progress"]["eligible"])
        self.assertEqual(sum(t["completed"] for t in payload["teams"]), payload["progress"]["completed"])

    def test_follow_up_counts(self):
        interview = complete(self.event, self.p1, decision="requerido")
        FollowUp.objects.create(event=self.event, participant=self.p1, interview=interview)
        complete(self.event, self.p2, decision="por_evaluar")
        FollowUp.objects.create(event=self.event, participant=self.p3, kind=FollowUp.Kind.SAFETY)
        fu = build_payload(event_scope(self.event), include_safety=True)["follow_ups"]
        self.assertEqual((fu["pending"], fu["to_evaluate"], fu["open_total"], fu["safety_open"]), (1, 1, 1, 1))
        self.assertIsNone(build_payload(event_scope(self.event))["follow_ups"]["safety_open"])

    def test_recommendations_are_deterministic_and_cite_evidence(self):
        a = build_payload(event_scope(self.event))["recommendations"]
        b = build_payload(event_scope(self.event))["recommendations"]
        self.assertEqual(a, b)
        self.assertTrue(all(item["evidence"] for item in a))
        self.assertTrue(any("Faltan 5 entrevistas" in item["text"] for item in a))


class SuppressionTests(CampFixture):
    def test_suppress_rules(self):
        self.assertEqual(suppress(0, 5)["display"], "0")
        self.assertEqual(suppress(3, 5)["display"], "Menos de 5")
        self.assertIsNone(suppress(4, 5)["value"])
        self.assertEqual(suppress(12, 5)["value"], 10)
        self.assertEqual(suppress(13, 5)["value"], 15)

    def test_outcomes_withheld_for_small_groups(self):
        from events.services.reports import visible_payload

        questions = {q.code: q for step in self.steps for q in step.questions}
        complete(self.event, self.p1, {"salvo": ["si"]}, questions=questions)
        snap = generate_snapshot(self.event, event_scope(self.event), trigger="manual", cutoff_at=timezone.now())
        outcomes = visible_payload(snap.data, resolve_access(self.pastor, self.event))["outcomes"]
        outcome = next(o for o in outcomes if o["code"] == "salvo")
        self.assertTrue(outcome["withheld"])
        self.assertEqual(outcome["rows"], [])
        self.assertEqual(outcome["respondents"]["display"], "Menos de 5")

    def test_zero_is_hidden_like_small_counts_in_outcomes(self):
        self.assertEqual(suppress(0, 5, hide_zero=True)["display"], "Menos de 5")

    def test_release_rule_moves_cells_only_in_steps_of_k(self):
        from events.services.reports import release_cells

        self.assertEqual(release_cells({"a": 7, "b": 2}, None, 5), {"a": 7, "b": 2})
        self.assertEqual(release_cells({"a": 8, "b": 2}, {"a": 7, "b": 2}, 5), {"a": 7, "b": 2})
        self.assertEqual(release_cells({"a": 12, "b": 3}, {"a": 7, "b": 2}, 5), {"a": 12, "b": 2})

    def test_tutor_and_pastor_without_sensitive_access_get_no_outcomes(self):
        from events.services.reports import visible_payload

        snap = generate_snapshot(self.event, event_scope(self.event), trigger="manual", cutoff_at=timezone.now())
        self.assertEqual(visible_payload(snap.data, resolve_access(self.tutor1, self.event))["outcomes"], [])
        self.assertNotIn("released", visible_payload(snap.data, resolve_access(self.pastor, self.event)))


class SnapshotTests(CampFixture):
    def test_scheduled_generation_is_idempotent(self):
        self.event.report_window_start = time(0, 0)
        self.event.report_window_end = time(23, 59)
        self.event.save()
        tz = local_tz(self.event)
        now = datetime.combine(timezone.localdate(), time(14, 5), tzinfo=tz)
        with mock.patch("events.services.reports.timezone.now", return_value=now):
            first = [s for s in run_due_cutoffs(now) if s.event_id == self.event.id]
            second = [s for s in run_due_cutoffs(now) if s.event_id == self.event.id]
        self.assertEqual(len(first), 3)  # event + two teams
        self.assertEqual({s.pk for s in first}, {s.pk for s in second})
        self.assertEqual(ReportSnapshot.objects.filter(event=self.event).count(), 3)
        self.assertEqual(first[0].cutoff_at.astimezone(tz).hour, 14)

    def test_missed_slot_is_not_backfilled(self):
        tz = local_tz(self.event)
        late = datetime.combine(timezone.localdate(), time(15, 50), tzinfo=tz)
        self.assertIsNone(latest_due_slot(self.event, late))

    def test_next_slot(self):
        tz = local_tz(self.event)
        now = datetime.combine(timezone.localdate(), time(14, 5), tzinfo=tz)
        self.assertEqual(next_slot(self.event, now).astimezone(tz).hour, 16)

    def test_snapshot_is_immutable(self):
        snap = generate_snapshot(self.event, event_scope(self.event), trigger="manual", cutoff_at=timezone.now())
        snap.data = {"progress": {}}
        from events.models import ImmutableRecordError

        with self.assertRaises(ImmutableRecordError):
            snap.save()

    def test_comparison_with_previous(self):
        t0 = timezone.now() - timedelta(hours=2)
        first = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=t0)
        complete(self.event, self.p1)
        complete(self.event, self.p2)
        second = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=timezone.now())
        delta = compare(second.data, first.data)
        self.assertEqual(delta["completed"], 2)
        self.assertEqual(delta["percent"], 40)

    def test_manual_cutoff_double_click_returns_same_snapshot(self):  # see also the minute-boundary test
        self.login(self.pastor)
        self.client.post(self.url("report_generate"), {"scope": "evento"})
        self.client.post(self.url("report_generate"), {"scope": "evento"})
        self.assertEqual(ReportSnapshot.objects.filter(event=self.event, trigger="manual").count(), 1)

    def test_management_command_runs(self):
        call_command("run_report_cutoffs", verbosity=0)


class PresentationPrivacyTests(CampFixture):
    def test_deck_contains_no_names_or_answers(self):
        questions = {q.code: q for step in self.steps for q in step.questions}
        interview = complete(self.event, self.p1, {"apoyo": ["sexualidad"], "decision": ["hablar_lider"]}, questions=questions, decision="requerido")
        FollowUp.objects.create(event=self.event, participant=self.p1, interview=interview)
        self.login(self.pastor)
        response = self.client.get(self.url("presentation_live"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for forbidden in [self.p1.full_name, "Mariana", self.coord1.get_full_name(), self.tutor1.get_full_name(), "Sexualidad", "hablar con un líder", "Teresa"]:
            self.assertNotIn(forbidden, html, forbidden)
        self.assertIn("Así va nuestro campamento", html)
        self.assertIn("Equipo Norte", html)  # explicit team names are allowed

    def test_small_follow_up_counts_are_suppressed_in_deck(self):
        deck = build_deck(
            build_payload(event_scope(self.event)), event_name="X", kind_label="Campamento", k=5,
            released_follow_ups={"requested_total": 1, "open_total": 1, "done": 0, "to_evaluate": 0},
        )
        self.assertEqual(deck["follow_ups"]["open"]["display"], "Menos de 5")
        self.assertIsNone(deck["follow_ups"]["open"]["value"])
        without_cutoff = build_deck(build_payload(event_scope(self.event)), event_name="X", kind_label="Campamento", k=5)
        self.assertEqual(without_cutoff["follow_ups"]["requested"]["display"], "—")

    def test_team_numbering_is_stable(self):
        complete(self.event, self.p4)  # changes the sort order of teams
        payload = build_payload(event_scope(self.event))
        for team in payload["teams"]:
            team["label"] = "Equipo de Alguien"
        deck = build_deck(payload, event_name="X", kind_label="Campamento", k=5, team_names={})
        labels = {row["label"] for row in deck["teams"]}
        self.assertEqual(labels, {"Equipo 1", "Equipo 2"})
        self.assertNotIn("Alguien", str(deck))

    def test_snapshot_presentation_for_tutor_is_team_scoped(self):
        snap = generate_snapshot(self.event, team_scope(self.event, self.m_tutor1), trigger="manual", cutoff_at=timezone.now())
        self.login(self.tutor1)
        response = self.client.get(self.url("report_presentation", snap.public_id))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.coord1.get_full_name())


@override_settings(DEBUG=False)
class SeedSafetyTests(CampFixture):
    def test_demo_seed_refuses_without_debug(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("seed_camp_demo", verbosity=0)


class PresentationExitTests(CampFixture):
    """"Salir" returns to where the presentation was opened from: Reportes by default, a saved cutoff's deck to
    that cutoff, never the role's home (which is not where the person was) and never another site."""

    def salir(self, url: str) -> str:
        html = self.client.get(url).content.decode()
        return re.search(r'<a class="btn" href="([^"]+)">Salir</a>', html).group(1)

    def test_the_live_deck_returns_to_reports_by_default(self):
        self.login(self.pastor)
        self.assertEqual(self.salir(self.url("presentation_live")), self.url("reports"))
        self.login(self.admin)
        self.assertEqual(self.salir(self.url("presentation_live")), self.url("reports"))

    def test_the_live_deck_returns_to_the_page_that_opened_it(self):
        self.login(self.pastor)
        home = self.url("pastor_home")
        self.assertEqual(self.salir(self.url("presentation_live") + "?desde=" + quote(home)), home)
        scoped = self.url("reports") + "?alcance=evento"
        self.assertEqual(self.salir(self.url("presentation_live") + "?desde=" + quote(scoped)), scoped)
        for elsewhere in ("https://evil.example/", "//evil.example/", "/\\evil.example"):
            with self.subTest(desde=elsewhere):
                self.assertEqual(self.salir(self.url("presentation_live") + "?desde=" + quote(elsewhere, safe="")), self.url("reports"))

    def test_the_pages_that_open_it_say_where_they_are(self):
        self.login(self.pastor)
        live = self.url("presentation_live")
        reports = self.client.get(self.url("reports"))
        self.assertContains(reports, f'href="{live}?desde={quote(self.url("reports"))}">Mostrar presentación</a>')
        home = self.client.get(self.url("pastor_home"))
        self.assertContains(home, f'href="{live}?desde={quote(self.url("pastor_home"))}">Mostrar presentación</a>')

    def test_a_saved_cutoffs_deck_returns_to_that_cutoff(self):
        from events.services.reports import generate_snapshot

        snap = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=timezone.now())
        self.login(self.pastor)
        self.assertEqual(self.salir(self.url("report_presentation", snap.public_id)), self.url("report_detail", snap.public_id))


class BackupListWayBackTests(CampFixture):
    def test_volver_returns_to_mis_camperos_for_a_coordinator(self):
        self.login(self.coord1)
        self.choose_station()
        response = self.client.get(self.url("backup_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{self.url("my_participants")}">Volver</a>')

    def test_and_to_the_role_home_for_everyone_else(self):
        self.login(self.admin)
        response = self.client.get(self.url("backup_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{self.url("home")}">Volver</a>')
