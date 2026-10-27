"""Several questionnaires per event, in the numbers: outcomes keyed per questionnaire, old cutoffs, the timeline
and the pace in camperos, and the headings on the cutoff page and the PDF.

Each test names the defect it would catch.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest import mock

from django.utils import timezone

from events.access import resolve_access
from events.models import Answer, EventParticipant, Interview, ReportSnapshot
from events.services import pdf as pdf_module
from events.services.event_questionnaires import links_for, move, use_version
from events.services import interviews as svc
from events.services.metrics import (
    build_payload,
    event_scope,
    keyed_outcomes,
    local_tz,
    outcome_counts,
    present_outcomes,
    throughput,
    timeline,
)
from events.services.questionnaire import create_questionnaire_from_definition, options_by_code
from events.services.reports import generate_snapshot, visible_payload

from .base import CampFixture
from .test_multi_questionnaires import IsolatedCacheMixin, ServiceHelpers, TwoQuestionnairesFixture, definition


def shared_code_definition(name, title):
    """Both questionnaires ask a question coded "comun": the codes repeat, the questionnaires do not."""
    return {
        "name": name,
        "title": title,
        "sections": [
            {
                "title": f"Sección de {name}",
                "questions": [
                    {
                        "code": "comun",
                        "text": f"¿Pregunta común de {name}?",
                        "kind": "unica",
                        "sensitivity": "sensible",
                        "options": [{"code": "si", "label": "Sí"}, {"code": "no", "label": "No"}],
                    }
                ],
            }
        ],
    }


class SharedCodesFixture(IsolatedCacheMixin, CampFixture):
    """Event A uses "Primero" then "Segundo"; each has one choice question coded "comun"."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.first = create_questionnaire_from_definition(cls.church, shared_code_definition("Primero", "Primer cuestionario"))
        cls.second = create_questionnaire_from_definition(cls.church, shared_code_definition("Segundo", "Segundo cuestionario"))
        cls.event.questionnaire_links.all().delete()
        use_version(cls.event, cls.first)
        use_version(cls.event, cls.second)
        cls.q1, cls.q2 = cls.first.questionnaire, cls.second.questionnaire

    def camperos(self, n):
        start = EventParticipant.objects.filter(event=self.event).count() + 1
        return [
            EventParticipant.objects.create(event=self.event, number=start + i, full_name=f"Campero {start + i}", age=20, room="9")
            for i in range(n)
        ]

    def answered(self, participant, version, code, *, minutes_ago=10):
        """A completed interview of ``version`` whose "comun" answer is ``code``."""
        now = timezone.now()
        interview = Interview.objects.create(
            event=self.event, participant=participant, version=version, status=Interview.Status.COMPLETED,
            started_at=now - timedelta(minutes=minutes_ago + 5), completed_at=now - timedelta(minutes=minutes_ago),
        )
        question = version.sections.get().questions.get(code="comun")
        answer = Answer.objects.create(interview=interview, question=question)
        answer.options.set([options_by_code(question)[code]])
        return interview


# ---- 1. outcome_counts --------------------------------------------------------------------------------------


class OutcomeCountsTests(SharedCodesFixture):
    def test_counts_are_keyed_by_questionnaire_and_a_shared_code_does_not_collide(self):
        """Defect: one dict keyed by question code, where the second questionnaire's "comun" overwrites the first's
        (or the first's answers are counted under the second's question)."""
        people = self.camperos(3)
        for person in people:
            self.answered(person, self.first, "si")
        self.answered(people[0], self.second, "no")
        counts = outcome_counts(self.event)
        self.assertEqual(
            counts,
            {
                str(self.q1.id): {"comun": {"respondents": 3, "options": {"si": 3, "no": 0}}},
                str(self.q2.id): {"comun": {"respondents": 1, "options": {"si": 0, "no": 1}}},
            },
        )

    def test_no_questionnaire_no_counts(self):
        """Defect: an event without questionnaires failing or inventing keys."""
        self.event.questionnaire_links.all().delete()
        self.assertEqual(outcome_counts(self.event), {})


# ---- 2. present_outcomes ---------------------------------------------------------------------------------------


class PresentOutcomesTests(SharedCodesFixture):
    FLAT = {"comun": {"respondents": 12, "options": {"si": 10, "no": 2}}}

    def test_an_old_cutoff_is_read_as_the_first_questionnaire(self):
        """Defect: cutoffs saved before several questionnaires (flat, by code) showing no answers at all, or their
        counts shown under the second questionnaire's question of the same code."""
        rows = present_outcomes(self.event, self.FLAT, k=5)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["code"], row["title"]), ("comun", "¿Pregunta común de Primero?"))
        self.assertEqual((row["questionnaire_id"], row["questionnaire"], row["show_questionnaire"]), (self.q1.id, "Primer cuestionario", True))
        self.assertEqual([(r["code"], r["display"]) for r in row["rows"]], [("si", "unas 10"), ("no", "Menos de 5")])

    def test_keyed_counts_give_one_flat_list_in_the_events_order(self):
        """Defect: the rows of the second questionnaire missing, out of order, or not saying which they belong to."""
        released = {str(self.q2.id): {"comun": {"respondents": 10, "options": {"si": 0, "no": 10}}}, **{str(self.q1.id): self.FLAT}}
        rows = present_outcomes(self.event, released, k=5)
        self.assertEqual([(r["questionnaire"], r["title"]) for r in rows], [
            ("Primer cuestionario", "¿Pregunta común de Primero?"),
            ("Segundo cuestionario", "¿Pregunta común de Segundo?"),
        ])
        self.assertEqual([r["questionnaire_id"] for r in rows], [self.q1.id, self.q2.id])
        self.assertEqual([r["rows"][1]["display"] for r in rows], ["Menos de 5", "unas 10"])

    def test_one_questionnaire_shows_no_heading(self):
        """Defect: a one-questionnaire event's summary gaining a questionnaire heading."""
        self.event.questionnaire_links.filter(questionnaire=self.q2).delete()
        rows = present_outcomes(self.event, self.FLAT, k=5)
        self.assertEqual([r["show_questionnaire"] for r in rows], [False])


# ---- 3. The first cutoff after the change keeps the "moved by at least k" rule ----------------------------------------


class ReleaseAcrossShapesTests(SharedCodesFixture):
    def test_a_flat_previous_cutoff_still_holds_cells_that_moved_less_than_k(self):
        """Defect: the old cutoff is flat and the new counts are keyed, so the release finds no previous cell and
        publishes the raw counts: two cutoffs could then be compared to isolate fewer than k people."""
        scope = event_scope(self.event)
        payload = build_payload(scope)
        previous = {**payload, "released": {
            "follow_ups": {"requested_total": 0, "open_total": 0, "done": 0, "to_evaluate": 0},
            "outcomes": {"comun": {"respondents": 10, "options": {"si": 10, "no": 0}}},
        }}
        previous.pop("outcome_counts", None)
        ReportSnapshot.objects.create(
            event=self.event, scope_key=scope.key, scope_label=scope.label, trigger="automatico", status="listo",
            cutoff_at=timezone.now() - timedelta(hours=1), interval_minutes=60, metrics_version="1", data=previous,
        )
        for person in self.camperos(12):  # 12 now: moved by 2, less than k = 5
            self.answered(person, self.first, "si")
        snapshot = generate_snapshot(self.event, scope, trigger="automatico", cutoff_at=timezone.now())
        released = snapshot.data["released"]["outcomes"]
        self.assertEqual(released[str(self.q1.id)]["comun"], {"respondents": 10, "options": {"si": 10, "no": 0}})
        self.assertEqual(released[str(self.q2.id)]["comun"], {"respondents": 0, "options": {"si": 0, "no": 0}})

    def flat_cutoff(self, respondents):
        """A cutoff saved before several questionnaires: unkeyed counts of the event's only questionnaire."""
        scope = event_scope(self.event)
        previous = {**build_payload(scope), "released": {
            "follow_ups": {"requested_total": 0, "open_total": 0, "done": 0, "to_evaluate": 0},
            "outcomes": {"comun": {"respondents": respondents, "options": {"si": respondents, "no": 0}}},
        }}
        previous.pop("outcome_counts", None)
        ReportSnapshot.objects.create(
            event=self.event, scope_key=scope.key, scope_label=scope.label, trigger="automatico", status="listo",
            cutoff_at=timezone.now() - timedelta(hours=1), interval_minutes=60, metrics_version="1", data=previous,
        )
        return scope

    def test_a_flat_cutoff_stays_with_its_questionnaire_after_another_one_is_moved_above_it(self):
        """Defect: the unkeyed counts read as the event's FIRST questionnaire. After "Subir" puts another one first,
        the old count of "Primero" is compared with "Segundo"'s cell and "Primero"'s cell (12, moved by 2 < k)
        is published raw: two cutoffs could then be compared to isolate fewer than k people."""
        self.event.questionnaire_links.filter(questionnaire=self.q2).delete()
        scope = self.flat_cutoff(10)  # saved while "Primero" was the only questionnaire
        use_version(self.event, self.second)
        move(self.event, self.q2, "subir")
        self.assertEqual([link.questionnaire_id for link in links_for(self.event)], [self.q2.id, self.q1.id])
        for person in self.camperos(12):
            self.answered(person, self.first, "si")
        snapshot = generate_snapshot(self.event, scope, trigger="automatico", cutoff_at=timezone.now())
        released = snapshot.data["released"]["outcomes"]
        self.assertEqual(released[str(self.q1.id)]["comun"], {"respondents": 10, "options": {"si": 10, "no": 0}})
        self.assertEqual(released[str(self.q2.id)]["comun"], {"respondents": 0, "options": {"si": 0, "no": 0}})

    def test_a_flat_cutoff_goes_to_the_questionnaire_that_has_its_questions(self):
        """Defect: the unkeyed counts attributed by position (or to the oldest link) to a questionnaire that never
        asked those questions, while the one that did is used further down."""
        other = create_questionnaire_from_definition(self.church, definition("Tercero", "Tercer cuestionario", "otra"))
        self.event.questionnaire_links.all().delete()
        use_version(self.event, other)  # the oldest link now: its only question is "otra"
        use_version(self.event, self.first)
        links = links_for(self.event)
        flat = {"comun": {"respondents": 10, "options": {"si": 10, "no": 0}}}
        self.assertEqual(keyed_outcomes(flat, links), {str(self.q1.id): flat})
        move(self.event, self.q1, "subir")
        self.assertEqual(keyed_outcomes(flat, links_for(self.event)), {str(self.q1.id): flat})


# ---- 4. Timeline and pace count camperos, not interview rows ------------------------------------------------------


class CamperoCompletionTests(TwoQuestionnairesFixture):
    def test_a_campero_who_answered_both_counts_once(self):
        """Defect: the timeline adding one point per questionnaire (2 for one campero), climbing above the goal line
        of camperos, and the pace ("Ritmo") twice the real one."""
        participants = EventParticipant.objects.filter(event=self.event, is_active=True)
        self.complete(self.begin(questionnaire=self.q1))
        later = timezone.now() + timedelta(minutes=1)
        self.assertEqual(timeline(self.event, participants, now=later)["points"], [])  # halfway is not done
        self.assertEqual(throughput(participants, now=later)["last_hour"], 0)
        self.complete(self.begin(questionnaire=self.q2))
        points = timeline(self.event, participants, now=later)["points"]
        self.assertEqual(points[-1]["cumulative"], 1)
        self.assertEqual(sum(point["count"] for point in points), 1)
        self.assertEqual(throughput(participants, now=later)["last_hour"], 1)
        payload = build_payload(event_scope(self.event), now=later)
        self.assertEqual((payload["progress"]["completed"], payload["timeline"]["points"][-1]["cumulative"]), (1, 1))


class SingleQTimelineEquivalenceTests(IsolatedCacheMixin, ServiceHelpers, CampFixture):
    """One questionnaire: the timeline and the pace say what they said before several questionnaires, one point per
    completed interview at its completion time. The expectation is written by hand from the data."""

    def setUp(self):
        super().setUp()
        self.session = {}

    def test_start_discard_restart_complete_is_one_completion_at_its_time(self):
        """Defect: counting by campero changing a one-questionnaire event's numbers: the discarded attempt counted, the
        completion placed at another time, or the campero counted twice."""
        self.assertEqual(self.event.questionnaire_count, 1)
        first = self.begin()
        svc.discard(first, actor=self.coord1, reason="repetir", session=self.session)
        second = self.begin()
        self.complete(second)
        self.assertEqual(Interview.objects.filter(participant=self.p2).count(), 2)
        tz = local_tz(self.event)
        done_at = datetime(2026, 10, 25, 10, 20, tzinfo=tz)
        Interview.objects.filter(pk=second.pk).update(completed_at=done_at)
        # A stray time on the discarded row, after the real completion and in the same hour: must be ignored (counted,
        # it would add a second completion; taken as the campero's time, it would move the completion to 10:40).
        Interview.objects.filter(pk=first.pk).update(completed_at=done_at + timedelta(minutes=20))
        participants = EventParticipant.objects.filter(event=self.event, is_active=True)

        one_point = {"bucket_minutes": 60, "points": [
            {"t": datetime(2026, 10, 25, 10, 0, tzinfo=tz).isoformat(), "label": "10:00", "day": "", "count": 1, "cumulative": 1},
        ]}
        later = done_at + timedelta(minutes=1)
        self.assertEqual(timeline(self.event, participants, now=later), one_point)
        self.assertEqual(throughput(participants, now=later), {"last_hour": 1, "per_hour": 0.5})
        self.assertEqual(timeline(self.event, participants, now=done_at + timedelta(minutes=30)), one_point)
        self.assertEqual(throughput(participants, now=done_at + timedelta(minutes=30)), {"last_hour": 1, "per_hour": 0.5})
        self.assertEqual(throughput(participants, now=done_at + timedelta(minutes=90)), {"last_hour": 0, "per_hour": 0.5})
        self.assertEqual(throughput(participants, now=done_at + timedelta(hours=3)), {"last_hour": 0, "per_hour": 0.0})
        self.assertEqual(timeline(self.event, participants, now=done_at - timedelta(minutes=1)), {"bucket_minutes": 60, "points": []})
        payload = build_payload(event_scope(self.event), now=later)
        self.assertEqual((payload["progress"]["completed"], payload["progress"]["in_progress"]), (1, 0))


# ---- 5. The cutoff page and the PDF put each questionnaire's title over its questions --------------------------------


class ReportHeadingTests(SharedCodesFixture):
    def snapshot(self):
        for person in self.camperos(10):
            self.answered(person, self.first, "si")
            self.answered(person, self.second, "no")
        return generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=timezone.now())

    def test_the_cutoff_page_has_one_heading_per_questionnaire(self):
        """Defect: two questions titled alike ("comun" of each) under one list, with nothing saying which is which."""
        snapshot = self.snapshot()
        self.login(self.admin)
        response = self.client.get(self.url("report_detail", snapshot.public_id))
        html = response.content.decode()
        self.assertEqual(html.count('<h3 class="heading">Primer cuestionario</h3>'), 1)
        self.assertEqual(html.count('<h3 class="heading">Segundo cuestionario</h3>'), 1)
        self.assertLess(html.index("Primer cuestionario</h3>"), html.index("¿Pregunta común de Primero?"))
        self.assertLess(html.index("¿Pregunta común de Primero?"), html.index("Segundo cuestionario</h3>"))
        self.assertLess(html.index("Segundo cuestionario</h3>"), html.index("¿Pregunta común de Segundo?"))

    def test_the_pdf_has_one_heading_per_questionnaire_and_still_says_camperos(self):
        """Defect: the PDF listing both questionnaires' questions with no title between them."""
        snapshot = self.snapshot()
        payload = visible_payload(snapshot.data, resolve_access(self.admin, self.event))
        self.assertEqual(len(payload["outcomes"]), 2)
        with mock.patch.object(pdf_module, "Paragraph", wraps=pdf_module.Paragraph) as paragraph:
            data = pdf_module.build_snapshot_pdf(snapshot, payload)
        self.assertTrue(data.startswith(b"%PDF"))
        texts = [str(call.args[0]) for call in paragraph.call_args_list]
        self.assertEqual(texts.count("<b>Primer cuestionario</b>"), 1)
        self.assertEqual(texts.count("<b>Segundo cuestionario</b>"), 1)
        self.assertLess(texts.index("<b>Segundo cuestionario</b>"), next(i for i, t in enumerate(texts) if "Pregunta común de Segundo" in t))
        self.assertFalse([t for t in texts if "participantes" in t.lower()])

    def test_one_questionnaire_prints_no_heading(self):
        """Defect: a one-questionnaire cutoff gaining a heading on the page or in the PDF."""
        self.event.questionnaire_links.filter(questionnaire=self.q2).delete()
        snapshot = self.snapshot()
        self.login(self.admin)
        html = self.client.get(self.url("report_detail", snapshot.public_id)).content.decode()
        self.assertIn("¿Pregunta común de Primero?", html)
        self.assertNotIn('<h3 class="heading">Primer cuestionario</h3>', html)
        payload = visible_payload(snapshot.data, resolve_access(self.admin, self.event))
        with mock.patch.object(pdf_module, "Paragraph", wraps=pdf_module.Paragraph) as paragraph:
            pdf_module.build_snapshot_pdf(snapshot, payload)
        self.assertNotIn("<b>Primer cuestionario</b>", [str(call.args[0]) for call in paragraph.call_args_list])
