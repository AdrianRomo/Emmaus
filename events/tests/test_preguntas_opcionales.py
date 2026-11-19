"""No interview question requires an answer (the owner's decision, 2026-11-18).

Every question offers «Prefiero no responder» (a choice question without its own decline option and a text question
too), a campero or a coordinador finishes with blanks, and a decline and a blank stay distinct wherever they are
stored, shown or counted; neither counts as a substantive answer. Only "Antes de empezar" requires anything: the
authorizations that apply. Both with the community module off (the camp as it runs) and on.
"""

from __future__ import annotations

import csv
import io
import json
from unittest import mock

from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from events.access import resolve_access
from events.models import Answer, AnswerRevision, EventMembership, EventParticipant, Interview, MembershipRole, Question
from events.services import interviews as svc
from events.services import questionnaire
from events.services.metrics import event_scope, outcome_counts, present_outcomes
from events.services.reports import generate_snapshot
from events.services.questionnaire import (
    DECLINE_LABEL,
    DECLINED_LABEL,
    DECLINED_VALUE,
    TEXT_DECLINE_ERROR,
    AnswerValue,
    decline_field,
    describe_answer,
    duplicate_version,
    load_answers,
    options_by_code,
    parse_question,
)
from people import flags

from .base import PASSWORD, CampFixture


def posted(**fields) -> QueryDict:
    data = QueryDict(mutable=True)
    for name, value in fields.items():
        data.setlist(name, value if isinstance(value, list) else [value])
    return data


class ParseTests(CampFixture):
    """The value of one question: a decline is stored apart from a blank, and a blank is never an error."""

    def test_a_blank_is_never_an_error_even_with_an_old_required_flag(self):
        for code in ("salvo", "apoyo", "impedimento"):
            question = self.question(code)
            question.required = True  # in memory only: what an older published version could hold
            value, error = parse_question(question, posted())
            self.assertIsNone(error, code)
            self.assertTrue(value.is_empty, code)
            self.assertFalse(value.declined, code)

    def test_the_questions_own_decline_option_is_a_decline(self):
        question = self.question("salvo")
        value, error = parse_question(question, posted(**{f"q_{question.id}": "prefiero_no"}))
        self.assertIsNone(error)
        self.assertEqual((value.codes, value.declined, value.is_empty, value.is_substantive), (["prefiero_no"], True, False, False))
        self.assertEqual(describe_answer(question, value), "Prefiero no responder")  # the campero's words
        self.assertEqual(describe_answer(question, value, staff=True), "Prefirió no responder")  # as staff read it

    def test_a_choice_question_without_one_gets_it(self):
        question = self.question("orientacion")  # its options have no «Prefiero no responder»
        self.assertFalse(any(option.is_decline for option in question.options.all()))
        value, error = parse_question(question, posted(**{f"q_{question.id}": DECLINED_VALUE}))
        self.assertIsNone(error)
        self.assertEqual((value.codes, value.declined, value.is_empty), ([], True, False))
        self.assertEqual(describe_answer(question, value), DECLINE_LABEL)
        self.assertEqual(describe_answer(question, value, staff=True), DECLINED_LABEL)

    def test_the_added_decline_is_not_valid_where_the_question_has_its_own(self):
        question = self.question("salvo")
        value, error = parse_question(question, posted(**{f"q_{question.id}": DECLINED_VALUE}))
        self.assertEqual(error, "Elige una de las opciones que aparecen en pantalla.")

    def test_a_text_question_has_its_decline_box(self):
        question = self.question("impedimento")
        value, error = parse_question(question, posted(**{decline_field(question): "1"}))
        self.assertIsNone(error)
        self.assertEqual((value.text, value.declined, value.is_empty, value.is_substantive), ("", True, False, False))
        self.assertEqual(describe_answer(question, value), DECLINE_LABEL)
        self.assertEqual(describe_answer(question, value, staff=True), DECLINED_LABEL)
        self.assertEqual(describe_answer(question, None, staff=True), "")  # a blank is no answer: «Sin responder» on screens

    def test_a_decline_and_an_answer_at_once_are_refused(self):
        text = self.question("impedimento")
        _, error = parse_question(text, posted(**{f"q_{text.id}": "Algo", decline_field(text): "1"}))
        self.assertEqual(error, TEXT_DECLINE_ERROR)
        self.assertEqual(error, "Si eliges “Prefiero no responder”, borra lo que escribiste.")
        multi = self.question("apoyo")
        _, error = parse_question(multi, posted(**{f"q_{multi.id}": ["familia", "prefiero_no"]}))
        self.assertEqual(error, "Si eliges “Prefiero no responder”, no marques otras opciones.")

    def test_an_added_decline_with_another_option_is_refused(self):
        with mock.patch.dict(questionnaire._structure_cache, clear=True):
            draft = duplicate_version(self.version, self.admin)
            apoyo = Question.objects.get(section__version=draft, code="apoyo")
            apoyo.options.filter(is_decline=True).delete()  # a question made in the editor without one
            _, error = parse_question(apoyo, posted(**{f"q_{apoyo.id}": ["familia", DECLINED_VALUE]}))
        self.assertEqual(error, "Si eliges “Prefiero no responder”, no marques otras opciones.")

    def test_a_decline_stored_before_the_flag_is_still_a_decline(self):
        interview = Interview.objects.create(event=self.event, participant=self.p2, version=self.version)
        question = self.question("salvo")
        answer = Answer.objects.create(interview=interview, question=question)  # declined=False: an older row
        answer.options.set([options_by_code(question)["prefiero_no"]])
        value = load_answers(interview)[question.id]
        self.assertTrue(value.declined)
        self.assertFalse(value.is_substantive)


class AssistedInterviewTests(CampFixture):
    """A coordinador's interview with blanks and declines finalizes, and every screen tells them apart."""

    def setUp(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)  # an adult: the consent is the only authorization
        self.interview = Interview.objects.get(participant=self.p2)

    def go(self, code, values=None, **extra):
        response = self.answer(self.interview, code, values if values is not None else "", extra=extra)
        self.assertEqual(response.status_code, 302, response.content.decode()[:500] if response.status_code == 200 else "")
        return response

    def interview_with_blanks_and_declines(self):
        orientacion, detalle = self.question("orientacion"), self.question("apoyo_detalle")
        self.go("salvo", "prefiero_no")  # its own decline option
        self.go("dudas")  # left blank
        self.go("apoyo", ["familia"], **{decline_field(detalle): "1"})  # a confidential text declined
        self.go("decision", ["recibir_cristo"], **{f"q_{orientacion.id}": DECLINED_VALUE})  # the added decline
        self.go("impedimento")  # left blank
        self.interview.refresh_from_db()
        return self.client.post(
            self.url("interview_review", self.interview.public_id), {"revision": self.interview.revision, "follow_up_decision": "no_requerido"}
        )

    def test_it_finalizes_and_stores_a_decline_apart_from_a_blank(self):
        response = self.interview_with_blanks_and_declines()
        self.assertRedirects(response, self.url("interview_done", self.interview.public_id), fetch_redirect_response=False)
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.COMPLETED)
        rows = {a.question.code: a for a in self.interview.answers.select_related("question").prefetch_related("options")}
        self.assertNotIn("dudas", rows)  # a blank has no row
        self.assertNotIn("impedimento", rows)
        self.assertTrue(rows["salvo"].declined)
        self.assertEqual([o.code for o in rows["salvo"].options.all()], ["prefiero_no"])
        self.assertTrue(rows["apoyo_detalle"].declined)
        self.assertEqual(rows["apoyo_detalle"].text_value, "")
        self.assertTrue(rows["orientacion"].declined)
        self.assertEqual(list(rows["orientacion"].options.all()), [])
        self.assertFalse(rows["apoyo"].declined)
        self.assertFalse(rows["decision"].declined)

    def test_the_record_shows_a_decline_and_a_blank_apart_in_both_modes(self):
        self.interview_with_blanks_and_declines()
        self.login(self.care)  # can_view_sensitive: reads every answer
        for module_on in (False, True):
            flags.set_enabled(self.church, "community", module_on)
            html = self.client.get(self.url("interview_detail", self.interview.public_id)).content.decode()
            self.assertEqual(html.count(DECLINED_LABEL), 3, module_on)  # salvo, apoyo_detalle, orientacion
            self.assertNotIn(DECLINE_LABEL, html)  # the campero's words are for the campero's screens
            self.assertEqual(html.count('<span class="muted">Sin responder</span>'), 2, module_on)  # dudas, impedimento

    def test_the_coordinadors_review_says_it_as_staff_read_it_in_both_modes(self):
        orientacion = self.question("orientacion")
        self.go("salvo", "prefiero_no")
        self.go("dudas")
        self.go("apoyo", ["ninguna"])
        self.go("decision", ["recibir_cristo"], **{f"q_{orientacion.id}": DECLINED_VALUE})
        self.go("impedimento")
        for module_on in (False, True):
            flags.set_enabled(self.church, "community", module_on)
            html = self.client.get(self.url("interview_review", self.interview.public_id)).content.decode()
            main = html.split("<main", 1)[1]
            self.assertEqual(main.count(DECLINED_LABEL), 2, module_on)  # salvo, orientacion
            self.assertNotIn(DECLINE_LABEL, main)
            self.assertEqual(main.count('<span class="muted">Sin responder</span>'), 2, module_on)  # dudas, impedimento

    def test_the_persons_export_says_it_as_staff_read_it(self):
        self.interview_with_blanks_and_declines()
        EventMembership.objects.create(event=self.event, user=self.admin, role=MembershipRole.ADMIN, can_view_sensitive=True)
        self.login(self.admin)
        response = self.client.get(self.url("participant_data_export", self.p2.public_id))
        data = json.loads(response.content.decode())
        answers = {a["pregunta"]: a["respuesta"] for a in data["entrevista"]["respuestas"]}
        for code in ("salvo", "apoyo_detalle", "orientacion"):
            self.assertEqual(answers[self.question(code).text], DECLINED_LABEL, code)
        self.assertNotIn(self.question("dudas").text, answers)  # a blank is not an answer

    def test_a_coordinador_does_not_learn_a_confidential_decline(self):
        self.interview_with_blanks_and_declines()
        rows = svc.review_rows(resolve_access(self.coord1, self.event), self.interview)
        # "apoyo" is confidential: its row shows only its level, and its follow-up is not even listed.
        hidden = [r for r in rows if not r["visible"]]
        self.assertEqual([r["question"].code for r in hidden], ["apoyo", "impedimento"])
        self.assertTrue(all((r["answered"], r["declined"], r["display"]) == (None, None, "") for r in hidden))
        self.assertNotIn("apoyo_detalle", [r["question"].code for r in rows])
        html = self.client.get(self.url("interview_detail", self.interview.public_id)).content.decode()
        self.assertEqual(html.count(DECLINED_LABEL), 2)  # salvo and orientacion only

    def test_the_answers_csv_writes_a_decline_and_leaves_a_blank_empty(self):
        self.interview_with_blanks_and_declines()
        EventMembership.objects.create(event=self.event, user=self.admin, role=MembershipRole.ADMIN, can_view_sensitive=True)
        self.login(self.admin)
        content = self.client.get(self.url("answers_export")).content.decode("utf-8-sig")
        header, *rows = list(csv.reader(io.StringIO(content)))
        row = next(r for r in rows if r[1] == self.p2.full_name)
        cell = {h.split(": ", 1)[1]: row[i] for i, h in enumerate(header) if ": " in h}
        self.assertEqual(cell[self.question("salvo").text], DECLINED_LABEL)
        self.assertEqual(cell[self.question("apoyo_detalle").text], DECLINED_LABEL)
        self.assertEqual(cell[self.question("orientacion").text], DECLINED_LABEL)
        self.assertEqual(cell[self.question("dudas").text], "")
        self.assertEqual(cell[self.question("impedimento").text], "")

    def test_the_step_offers_a_decline_on_every_question_in_both_modes(self):
        orientacion, impedimento = self.question("orientacion"), self.question("impedimento")
        for module_on in (False, True):
            flags.set_enabled(self.church, "community", module_on)
            decision = self.client.get(self.url("interview_step", self.interview.public_id, self.step("decision").number)).content.decode()
            self.assertIn(f'name="q_{orientacion.id}" value="{DECLINED_VALUE}"', decision)
            text = self.client.get(self.url("interview_step", self.interview.public_id, self.step("impedimento").number)).content.decode()
            self.assertIn(f'type="checkbox" id="q{impedimento.id}--prefiero-no" name="{decline_field(impedimento)}" value="1"', text)
            self.assertIn(f'<span>Prefiero no responder<span class="visually-hidden"> a “{impedimento.text}”</span></span>', text)

    def test_a_correction_from_a_decline_to_a_blank_is_recorded(self):
        self.interview_with_blanks_and_declines()
        step = self.step("salvo")
        self.interview.refresh_from_db()
        access = resolve_access(self.coord1, self.event)
        _, errors = svc.correct_step(access, self.interview, step, posted(revision=str(self.interview.revision)), reason="Lo dijo después")
        self.assertEqual(errors, {})
        revision = AnswerRevision.objects.get(interview=self.interview, question=step.question)
        self.assertEqual((revision.previous_option_codes, revision.previous_declined), (["prefiero_no"], True))
        self.assertNotIn(step.question.id, load_answers(self.interview))


class KioskTests(CampFixture):
    """A campero alone finishes with blanks; the coordinador finalizes; the campero's review tells them apart."""

    def setUp(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p1, mode="supervisada")  # a minor with a guardian's authorization
        self.interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", self.interview.public_id))

    def test_the_campero_finishes_with_blanks_and_a_decline_and_the_coordinador_finalizes(self):
        impedimento = self.question("impedimento")
        self.assertEqual(self.answer(self.interview, "salvo", "si", url_name="kiosk").status_code, 302)
        self.assertEqual(
            self.answer(self.interview, "impedimento", "", url_name="kiosk", extra={decline_field(impedimento): "1"}).status_code, 302
        )
        review = self.client.get(reverse("kiosk:review")).content.decode()
        self.assertIn(DECLINE_LABEL + "</dd>", review)  # impedimento, in the campero's own words
        self.assertNotIn(DECLINED_LABEL, review)
        self.assertIn('<span class="muted">Sin responder</span>', review)  # dudas, apoyo, decision
        self.assertIn("Te faltan 3 preguntas.", review)  # the blanks; a decline is an answer given
        self.client.post(reverse("kiosk:finish"))
        self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        self.interview.refresh_from_db()
        response = self.client.post(
            self.url("interview_review", self.interview.public_id), {"revision": self.interview.revision, "follow_up_decision": "no_requerido"}
        )
        self.assertRedirects(response, self.url("interview_done", self.interview.public_id), fetch_redirect_response=False)
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.COMPLETED)
        answers = load_answers(self.interview)
        self.assertEqual(answers[impedimento.id], AnswerValue(declined=True))
        self.assertEqual(set(answers), {self.question("salvo").id, impedimento.id})


class MetricsTests(CampFixture):
    """Neither a blank nor a decline counts as a respondent; the declines are their own bucket."""

    def complete(self, participant, values: dict[str, AnswerValue]):
        interview = Interview.objects.create(
            event=self.event, participant=participant, version=self.version, status=Interview.Status.COMPLETED
        )
        cleaned = {self.question(code).id: value for code, value in values.items()}
        questionnaire.write_answers(interview, cleaned, actor=None, by_participant=False)
        return interview

    def test_counts_and_presentation(self):
        self.complete(self.p1, {"salvo": AnswerValue(codes=["si"]), "decision": AnswerValue(codes=["recibir_cristo"]),
                                "orientacion": AnswerValue(declined=True)})
        self.complete(self.p2, {"salvo": AnswerValue(codes=["prefiero_no"], declined=True)})
        self.complete(self.p3, {})  # every question left blank
        self.complete(self.p_minor_no_auth, {"salvo": AnswerValue(codes=["no"])})
        legacy = self.complete(self.p4, {})
        old = Answer.objects.create(interview=legacy, question=self.question("salvo"))  # a decline saved before the flag
        old.options.set([options_by_code(self.question("salvo"))["prefiero_no"]])
        counts = outcome_counts(self.event)[str(self.version.questionnaire_id)]
        self.assertEqual((counts["salvo"]["respondents"], counts["salvo"]["declined"]), (2, 2))
        self.assertEqual(counts["salvo"]["options"]["prefiero_no"], 2)
        self.assertEqual((counts["orientacion"]["respondents"], counts["orientacion"]["declined"]), (0, 1))
        self.assertEqual((counts["dudas"]["respondents"], counts["dudas"]["declined"]), (0, 0))

        released = {str(self.version.questionnaire_id): counts}
        salvo = next(o for o in present_outcomes(self.event, released, k=1) if o["code"] == "salvo")
        self.assertEqual(salvo["respondents"]["value"], 2)
        self.assertNotIn(DECLINE_LABEL, [row["label"] for row in salvo["rows"]])  # not a bar over the respondents
        self.assertEqual(salvo["declined"]["value"], 2)

    def test_a_cutoff_saved_before_reads_its_decline_option_apart(self):
        flat = {"salvo": {"respondents": 20, "options": {"si": 12, "no": 2, "no_seguro": 1, "prefiero_no": 5}}}
        salvo = next(o for o in present_outcomes(self.event, flat, k=5) if o["code"] == "salvo")
        self.assertEqual(salvo["respondents"]["value"], 20)  # as it was saved
        self.assertEqual([row["code"] for row in salvo["rows"]], ["si", "no", "no_seguro"])
        self.assertEqual(salvo["declined"]["display"], "unas 5")

    def test_the_report_says_the_declines_apart_in_both_modes(self):
        self.event.min_group_size = 3
        self.event.save(update_fields=["min_group_size"])
        people = [EventParticipant.objects.create(event=self.event, number=100 + n, full_name=f"Campero {n}", age=20, room="9") for n in range(9)]
        for participant in people[:6]:
            self.complete(participant, {"salvo": AnswerValue(codes=["si"])})
        for participant in people[6:]:
            self.complete(participant, {"salvo": AnswerValue(codes=["prefiero_no"], declined=True)})
        snapshot = generate_snapshot(self.event, event_scope(self.event), trigger="manual", cutoff_at=timezone.now())
        EventMembership.objects.create(event=self.event, user=self.admin, role=MembershipRole.ADMIN, can_view_sensitive=True)
        self.login(self.admin)
        for module_on in (False, True):
            flags.set_enabled(self.church, "community", module_on)
            html = self.client.get(self.url("report_detail", snapshot.public_id)).content.decode()
            self.assertIn("Personas. Respondieron unas 6. Unas 3 prefirieron no responder.", html, module_on)
            self.assertNotIn('<span class="outcome__label">Prefiero no responder</span>', html)


class StartTests(CampFixture):
    """"Antes de empezar" keeps what it requires: the authorizations that apply, checked on the server."""

    def setUp(self):
        self.login(self.coord1)
        self.choose_station()

    def test_a_minor_needs_the_assent_and_a_missing_guardian_authorization(self):
        url = self.url("interview_start", self.p_minor_no_auth.public_id)
        for module_on in (False, True):
            flags.set_enabled(self.church, "community", module_on)
            page = self.client.get(url).content.decode()
            self.assertIn("¿Es la persona correcta?", page)
            self.assertIn("Tengo la autorización firmada de su padre, madre o tutor legal", page)
            self.assertIn(f"Le expliqué el aviso de privacidad a {self.p_minor_no_auth.first_name} y está de acuerdo en conversar", page)
            refused = self.client.post(url, {"identity": "on", "mode": "asistida", "consent_assent": "on"})
            self.assertContains(refused, "Para comenzar, confirma esta autorización.")
            self.assertFalse(Interview.objects.filter(participant=self.p_minor_no_auth).exists())
        response = self.client.post(url, {"identity": "on", "mode": "asistida", "consent_assent": "on", "consent_guardian": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Interview.objects.filter(participant=self.p_minor_no_auth).exists())

    def test_an_adult_needs_the_consent(self):
        url = self.url("interview_start", self.p2.public_id)
        self.assertContains(self.client.post(url, {"identity": "on", "mode": "asistida"}), "Para comenzar, confirma esta autorización.")
        self.assertFalse(Interview.objects.filter(participant=self.p2).exists())
        with self.assertRaises(svc.ConsentMissing):
            svc.start_interview(
                resolve_access(self.coord1, self.event), self.p2, mode="asistida", station=None, confirmed_consents=set(),
                session=self.client.session,
            )
