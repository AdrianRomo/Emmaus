"""Several questionnaires per event, on the screens: start, done, kiosk, camper record, lists, paper sheet,
safety report, readiness.

Each test names the defect it would catch. A one-questionnaire event must look exactly as before.
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.template import Context, Template
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from events.access import resolve_access
from events.models import FollowUp, Interview
from events.services import interviews as svc
from events.services.event_questionnaires import set_in_order
from events.services.questionnaire import AnswerValue, write_answers

from .base import CampFixture
from .test_camp_visual import css_rules, media_applies, stylesheet
from .test_multi_questionnaires import IsolatedCacheMixin, ServiceHelpers, TwoQuestionnairesFixture

START_ADULT = {"mode": "asistida", "consent_consent": "on"}
PILL = '<span class="status status--{}"><span class="status__mark" aria-hidden="true"></span>{}</span>'

# templates/events/participant_detail.html, the "Entrevista" block before several questionnaires (b14e9ca).
ORIGINAL_INTERVIEW_BLOCK = """{% load emmaus %}
      <h2 id="entrevista" class="heading">Entrevista</h2>
      {% if interview %}
        <p>{% status interview.status %}</p>
        <p class="row__meta">{{ interview.get_mode_display }}, iniciada {{ interview.started_at|fecha_corta }}{% if interview.completed_at %}, completada {{ interview.completed_at|fecha_corta }}{% endif %}.</p>
        <p class="more"><a href="{% url 'events:interview_detail' event.slug interview.public_id %}">Ver respuestas</a></p>
      {% else %}
        <p>{% status "pendiente" %}</p>
      {% endif %}
    """


def interview_block(html: str) -> str:
    match = re.search(r'<section class="block" aria-labelledby="entrevista">(.*?)</section>', html, re.DOTALL)
    assert match, "no Entrevista block"
    return match.group(1)


def list_row(html: str, name: str) -> str:
    """The <li class="row"> of Mis camperos that names ``name``."""
    rows = [row for row in re.findall(r'<li class="row">(.*?)</li>', html, re.DOTALL) if f">{name}</a>" in row]
    assert len(rows) == 1, f"{len(rows)} rows for {name}"
    return rows[0]


class FlowFixture(TwoQuestionnairesFixture):
    """Event A uses "Primer cuestionario" (q1) then "Segundo cuestionario" (q2); p2 is Diego, an adult of coord1."""

    def page(self, name, *args, user=None, **params):
        self.login(user or self.coord1)
        response = self.client.get(self.url(name, *args), params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


# ---- 1. Start ----------------------------------------------------------------------------------------------


class OneQuestionnaireStartTests(IsolatedCacheMixin, CampFixture):
    def test_the_start_page_is_the_one_it_always_was(self):
        """Defect: a one-questionnaire event asking "¿Qué cuestionario?" or saying "cuatro cosas"."""
        self.login(self.coord1)
        html = self.client.get(self.url("interview_start", self.p2.public_id)).content.decode()
        self.assertIn('<p class="lede">Confirma tres cosas. Toma menos de un minuto.</p>', html)
        self.assertNotIn("¿Qué cuestionario?", html)
        self.assertNotIn('name="questionnaire"', html)
        self.assertIn('<h2 id="paso-3" class="checks__title"><span class="checks__n">3</span>¿Cómo se va a responder?</h2>', html)

    def test_a_completed_campero_goes_to_the_interview_with_todays_message(self):
        """Defect: the one-questionnaire message replaced by the "todos los cuestionarios" one."""
        self.login(self.coord1)
        self.client.post(self.url("interview_start", self.p2.public_id), START_ADULT)
        interview = Interview.objects.get(participant=self.p2)
        Interview.objects.filter(pk=interview.pk).update(status=Interview.Status.COMPLETED, completed_at=timezone.now())
        self.p2.interview_status = "completada"
        self.p2.save(update_fields=["interview_status"])
        response = self.client.get(self.url("interview_start", self.p2.public_id), follow=True)
        self.assertRedirects(response, self.url("interview_detail", interview.public_id))
        self.assertContains(response, f"{self.p2.full_name} ya tiene su entrevista completada.")


class FreeOrderStartTests(FlowFixture):
    def test_the_start_page_asks_which_questionnaire_and_requires_an_answer(self):
        """Defect: no way to pick the questionnaire in free order, the choice rendered under "Autorización", or a
        missing choice that re-renders silently."""
        html = self.page("interview_start", self.p2.public_id)
        self.assertIn('<p class="lede">Confirma cuatro cosas. Toma menos de un minuto.</p>', html)
        self.assertIn('<h2 id="paso-3" class="checks__title"><span class="checks__n">3</span>¿Qué cuestionario?</h2>', html)
        self.assertIn('<h2 id="paso-4" class="checks__title"><span class="checks__n">4</span>¿Cómo se va a responder?</h2>', html)
        radios = re.findall(r'<input type="radio" name="questionnaire" value="(\d+)"', html)
        self.assertEqual(radios, [str(self.q1.id), str(self.q2.id)])
        self.assertIn("<span>Primer cuestionario</span>", html)
        self.assertIn("<span>Segundo cuestionario</span>", html)
        authorization = html.split('id="paso-2"', 1)[1].split('id="paso-3"', 1)[0]
        self.assertNotIn('name="questionnaire"', authorization)

        response = self.client.post(self.url("interview_start", self.p2.public_id), START_ADULT)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Elige el cuestionario.")
        self.assertContains(response, '<a href="#id_questionnaire">¿Qué cuestionario? Elige el cuestionario.</a>', html=True)
        self.assertFalse(Interview.objects.filter(participant=self.p2).exists())

    def test_posting_the_second_starts_the_second(self):
        """Defect: the choice ignored and the first questionnaire started."""
        self.login(self.coord1)
        response = self.client.post(self.url("interview_start", self.p2.public_id), {**START_ADULT, "questionnaire": self.q2.id})
        interview = Interview.objects.get(participant=self.p2)
        self.assertEqual((interview.questionnaire, interview.version), (self.q2, self.second))
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/entrevistas/{interview.public_id}/", response["Location"])

    def test_continuar_preselects_the_questionnaire_it_names(self):
        """Defect: "Continuar con «Segundo cuestionario»" landing on a form with the first one chosen."""
        html = self.page("interview_start", self.p2.public_id, cuestionario=self.q2.id)
        self.assertRegex(html, rf'<input type="radio" name="questionnaire" value="{self.q2.id}"[^>]*checked')
        self.assertNotRegex(html, rf'<input type="radio" name="questionnaire" value="{self.q1.id}"[^>]*checked')

    def test_a_campero_who_answered_both_goes_to_the_newest_with_the_plural_message(self):
        """Defect: a campero with every questionnaire done sent to an empty start form."""
        self.complete(self.begin(questionnaire=self.q2))
        newest = self.begin(questionnaire=self.q1)
        self.complete(newest)
        self.login(self.coord1)
        response = self.client.get(self.url("interview_start", self.p2.public_id), follow=True)
        self.assertRedirects(response, self.url("interview_detail", newest.public_id))
        self.assertContains(response, f"{self.p2.full_name} ya respondió todos los cuestionarios.")

    def test_one_left_shows_its_title_and_what_was_answered(self):
        """Defect: a radio with a single option, or no word on which questionnaire is about to start."""
        self.complete(self.begin(questionnaire=self.q1))
        html = self.page("interview_start", self.p2.public_id)
        self.assertNotIn('type="radio" name="questionnaire"', html)
        self.assertIn(f'<input type="hidden" name="questionnaire" value="{self.q2.id}">', html)  # the one the page names
        self.assertIn("<p>Cuestionario: <strong>Segundo cuestionario</strong>.</p>", html)
        self.assertIn('<p class="hint">Ya respondió: Primer cuestionario.</p>', html)
        self.assertNotIn("Después:", html)  # free order: nothing waits for this one

    def test_a_choice_made_stale_by_another_computer_is_refused_not_swapped(self):
        """Defect: the page offered both; another computer completed "Segundo" meanwhile. The POST naming "Segundo"
        reached a form with no choice left, which started "Primer cuestionario" without a word."""
        html = self.page("interview_start", self.p2.public_id)
        self.assertEqual(re.findall(r'<input type="radio" name="questionnaire" value="(\d+)"', html), [str(self.q1.id), str(self.q2.id)])
        elsewhere = self.complete(self.begin(questionnaire=self.q2))
        before = list(Interview.objects.filter(participant=self.p2).values_list("pk", "status"))
        self.login(self.coord1)
        response = self.client.post(self.url("interview_start", self.p2.public_id), {**START_ADULT, "questionnaire": self.q2.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ese cuestionario ya se respondió.")
        self.assertEqual(list(Interview.objects.filter(participant=self.p2).values_list("pk", "status")), before)
        self.assertEqual(before, [(elsewhere.pk, Interview.Status.COMPLETED)])
        self.assertContains(response, f'<input type="hidden" name="questionnaire" value="{self.q1.id}">')  # what it now names


class InOrderStartTests(FlowFixture):
    def setUp(self):
        super().setUp()
        set_in_order(self.event, True)

    def test_the_start_page_names_the_next_and_the_one_after_without_a_radio(self):
        """Defect: in order, a choice offered that the service would refuse, or no word on what comes after."""
        html = self.page("interview_start", self.p2.public_id)
        self.assertNotIn('type="radio" name="questionnaire"', html)
        self.assertIn(f'<input type="hidden" name="questionnaire" value="{self.q1.id}">', html)
        self.assertIn("<p>Cuestionario: <strong>Primer cuestionario</strong>.</p>", html)
        self.assertIn('<p class="hint">Después: Segundo cuestionario.</p>', html)
        self.assertIn("Confirma cuatro cosas.", html)
        self.client.post(self.url("interview_start", self.p2.public_id), START_ADULT)
        self.assertEqual(Interview.objects.get(participant=self.p2).questionnaire, self.q1)


# ---- 2. Done page, lists ------------------------------------------------------------------------------------


class DoneAndListTests(FlowFixture):
    def test_after_the_first_the_done_page_continues_and_the_list_says_one_of_two(self):
        """Defect: after questionnaire 1 the campero looks finished ("Entrevista completada", no way on), shows
        "Retomar" with nothing to resume, or "Sin entrevista" although one is done."""
        first = self.begin(questionnaire=self.q1)
        self.complete(first)
        html = self.page("interview_done", first.public_id)
        start = self.url("interview_start", self.p2.public_id)
        self.assertIn(
            f'<a class="btn btn--primary btn--large" href="{start}?cuestionario={self.q2.id}">Continuar con «Segundo cuestionario»</a>', html
        )
        self.assertIn('<a class="btn btn--secondary btn--large" href="', html)
        self.assertIn("A Diego le falta 1 cuestionario. ", html)

        row = list_row(self.page("my_participants"), self.p2.full_name)
        self.assertIn(PILL.format("pending", "1 de 2 cuestionarios"), row)
        self.assertIn(f"Entrevistar<span class=\"visually-hidden\"> a {self.p2.full_name}</span>", row)
        self.assertNotIn("Retomar", row)

        self.choose_station()  # "Iniciar entrevista" asks for the computer first
        search = self.page("interview_search")
        self.assertIn(PILL.format("pending", "1 de 2 cuestionarios"), search)

        second = self.begin(questionnaire=self.q2)
        self.complete(second)
        row = list_row(self.page("my_participants"), self.p2.full_name)
        self.assertIn(PILL.format("done", "Completada"), row)
        html = self.page("interview_done", second.public_id)
        self.assertNotIn("Continuar con", html)
        self.assertNotIn("le falta", html)

    def test_a_campero_with_nothing_done_keeps_the_plain_pill(self):
        """Defect: "0 de 2 cuestionarios" where the list always said "Sin entrevista"."""
        row = list_row(self.page("my_participants"), self.p2.full_name)
        self.assertIn(PILL.format("pending", "Sin entrevista"), row)

    def test_the_coordinators_pending_page_and_home_mark_the_partly_done(self):
        """Defect: a campero with questionnaire 1 done listed under "Sin iniciar" as if nothing was done."""
        self.complete(self.begin(questionnaire=self.q1))
        live = svc.start_interview(  # p1 is a minor: the assent, not the adult's consent
            resolve_access(self.coord1, self.event), self.p1, mode="asistida", station=None,
            confirmed_consents={"assent"}, session=self.session, questionnaire=self.q2,
        )
        pending = self.page("coord_pending")
        self.assertIn(PILL.format("pending", "1 de 2 cuestionarios"), pending)
        self.assertIn("«Segundo cuestionario»", pending)  # the live interview's row says which
        home = self.page("coord_home")
        self.assertIn(PILL.format("pending", "1 de 2 cuestionarios"), home)
        self.assertEqual(live.questionnaire, self.q2)

    def count_line(self):
        html = self.page("coord_home")
        head = re.search(r'<h2 id="mi-avance" class="voice coord__count">(.*?)</h2>', html).group(1)
        note = re.search(r'<p class="coord__count-note">(.*?) <a href=', html).group(1)
        return head, note

    def test_the_home_count_tells_the_halfway_camperos_from_the_untouched(self):
        """Defect (wording): with two questionnaires the head read "Llevas 0 de 3. 3 sin iniciar." although two camperos
        had answered the first one: "sin iniciar" counted the halfway camperos as never interviewed."""
        self.complete(self.begin(questionnaire=self.q1))  # p2
        self.complete(svc.start_interview(  # p1 is a minor: the assent, not the adult's consent
            resolve_access(self.coord1, self.event), self.p1, mode="asistida", station=None,
            confirmed_consents={"assent"}, session=self.session, questionnaire=self.q1,
        ))
        self.assertEqual(self.count_line(), ("Llevas 0 de 3 con todo.", "2 a medias y 1 sin iniciar."))
        self.begin(questionnaire=self.q2)  # p2 now has one in progress
        self.assertEqual(self.count_line(), ("Llevas 0 de 3 con todo.", "1 en curso, 1 a medias y 1 sin iniciar."))

    def recent_rows(self):
        html = self.page("coord_progress")
        recent = re.search(r'<section class="block" aria-labelledby="recientes">(.*?)</section>', html, re.S).group(1)
        return [
            (title, meta)
            for title, meta in re.findall(r'class="row__link"[^>]*>([^<]*)</a></p>\s*<p class="row__meta">([^<]*)</p>', recent)
        ]

    def test_completadas_hace_poco_names_the_questionnaire_of_each_row(self):
        """Defect: a campero who answered both questionnaires listed twice under "Completadas hace poco" as two
        identical rows (same name, "Hace un momento"), with nothing to tell which is which."""
        self.complete(self.begin(questionnaire=self.q1))
        self.complete(self.begin(questionnaire=self.q2))
        rows = self.recent_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual({title for title, _ in rows}, {self.p2.full_name})
        self.assertEqual(sorted(meta.rsplit(", ", 1)[1] for _, meta in rows), ["«Primer cuestionario»", "«Segundo cuestionario»"])

    def test_on_a_phone_the_pill_and_entrevistar_go_under_the_name(self):
        """Defect (layout): with two questionnaires "1 de 2 cuestionarios" and "Entrevistar" sit beside the name in a
        .row__end that cannot shrink, so at 390 px the coordinator's home scrolled sideways by 28 px (Playwright:
        document scrollWidth 418). The rows of "Por entrevistar" need the phone wrap the other lists have."""
        self.complete(self.begin(questionnaire=self.q1))
        queue = re.search(r'<section class="coord__queue".*?</section>', self.page("coord_home"), re.S).group(0)
        row = next(r for r in re.findall(r'<li class="row">(.*?)</li>', queue, re.S) if self.p2.full_name in r)
        self.assertRegex(row, r'<div class="row__end"><span class="status status--pending">.*?</span>\s*<a class="btn')
        rules = css_rules(stylesheet())

        def declared(selector, width):
            return " ".join(body for media, prelude, body in rules if selector in [s.strip() for s in prelude.split(",")] and media_applies(media, width))

        self.assertIn("flex-wrap: wrap", declared(".coord__queue .row:has(> .row__end)", 390))
        self.assertIn("width: 100%", declared(".coord__queue .row__end", 390))
        self.assertEqual((declared(".coord__queue .row:has(> .row__end)", 1024), declared(".coord__queue .row__end", 1024)), ("", ""))

    def test_the_supervision_lists_say_one_of_two(self):
        """Defect: the tutor's and the pastor's lists showing "Sin entrevista" for a campero halfway through."""
        self.complete(self.begin(questionnaire=self.q1))
        for name, args in (("interviews", ()), ("coordinator_detail", (self.m_coord1.id,))):
            html = self.page(name, *args, user=self.tutor1)
            self.assertIn(PILL.format("pending", "1 de 2 cuestionarios"), html, name)
        # Only the camperos who answered something: the period filter must not shrink the count it shows.
        html = self.page("interviews", user=self.tutor1, periodo="hoy")
        self.assertIn(PILL.format("pending", "1 de 2 cuestionarios"), html)

    def test_interview_rows_name_their_questionnaire(self):
        """Defect: two rows "Diego, completada hace 1 minuto" that cannot be told apart."""
        self.complete(self.begin(questionnaire=self.q1))
        self.begin(questionnaire=self.q2)
        html = self.page("tutor_home", user=self.tutor1)
        self.assertIn(", «Primer cuestionario»</p>", html)
        self.assertIn(", «Segundo cuestionario»</p>", html)


class OneQuestionnaireDoneTests(IsolatedCacheMixin, ServiceHelpers, CampFixture):
    def setUp(self):
        super().setUp()
        self.session = {}

    def test_the_done_page_and_the_list_are_the_ones_they_always_were(self):
        """Defect: a one-questionnaire event offering "Continuar con …" or naming questionnaires."""
        interview = self.begin()
        Interview.objects.filter(pk=interview.pk).update(status=Interview.Status.COMPLETED, completed_at=timezone.now())
        self.p2.interview_status = "completada"
        self.p2.save(update_fields=["interview_status"])
        self.login(self.coord1)
        html = self.client.get(self.url("interview_done", interview.public_id)).content.decode()
        self.assertIn('<div class="step__actions">\n  <a class="btn btn--primary btn--large" href="', html)
        self.assertNotIn("Continuar con", html)
        self.assertNotIn("le falta", html)
        row = list_row(self.client.get(self.url("my_participants")).content.decode(), self.p2.full_name)
        self.assertIn(PILL.format("done", "Completada"), row)

    def test_the_home_count_is_the_line_it_always_was(self):
        """Defect: a one-questionnaire event's head gaining "con todo" or "a medias"."""
        self.begin()
        self.login(self.coord1)
        html = self.client.get(self.url("coord_home")).content.decode()
        self.assertIn('<h2 id="mi-avance" class="voice coord__count">Llevas 0 de 3.</h2>', html)
        self.assertIn('<p class="coord__count-note">1 en curso y 2 sin iniciar. <a href="', html)
        self.assertNotIn("a medias", html)

    def test_completadas_hace_poco_is_the_row_it_always_was(self):
        """Defect: a one-questionnaire event's "Completadas hace poco" rows gaining the questionnaire's title."""
        self.complete(self.begin())
        self.login(self.coord1)
        html = self.client.get(self.url("coord_progress")).content.decode()
        recent = re.search(r'<section class="block" aria-labelledby="recientes">(.*?)</section>', html, re.S).group(1)
        self.assertRegex(recent, r'<p class="row__meta">Hace [^<,«]*</p>')
        self.assertNotIn("«", recent)


# ---- 3. Kiosk, interview detail ---------------------------------------------------------------------------------


class KioskHandbackTests(FlowFixture):
    def test_the_camper_is_told_this_questionnaire_is_done(self):
        """Defect: "Ya terminaste." to a campero who still has a questionnaire to answer."""
        self.login(self.coord1)
        self.choose_station(number=5)
        self.client.post(self.url("interview_start", self.p2.public_id), {**START_ADULT, "mode": "supervisada", "questionnaire": self.q1.id})
        interview = Interview.objects.get(participant=self.p2)
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.client.post(reverse("kiosk:finish"))
        self.assertContains(self.client.get(reverse("kiosk:home")), "Ya terminaste este cuestionario. Avisa a tu coordinador")


class OneQuestionnaireKioskTests(IsolatedCacheMixin, CampFixture):
    def test_the_handback_copy_is_unchanged(self):
        """Defect: "este cuestionario" said in an event that has only one."""
        self.login(self.coord1)
        self.choose_station(number=5)
        self.start(self.p2, mode="supervisada")
        interview = Interview.objects.get(participant=self.p2)
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.client.post(reverse("kiosk:finish"))
        self.assertContains(self.client.get(reverse("kiosk:home")), "Ya terminaste. Avisa a tu coordinador")


class InterviewDetailTests(FlowFixture):
    def test_the_meta_names_the_questionnaire(self):
        """Defect: two interviews of one campero whose pages cannot be told apart."""
        interview = self.begin(questionnaire=self.q2)
        html = self.page("interview_detail", interview.public_id)
        self.assertIn("<span>«Segundo cuestionario», Entrevista asistida, versión 1 del cuestionario", html)


class ConfidentialTagTests(IsolatedCacheMixin, ServiceHelpers, CampFixture):
    def setUp(self):
        super().setUp()
        self.session = {}

    def test_a_confidential_answer_shown_to_the_care_team_keeps_its_tag(self):
        """Defect: the "Confidencial" tag shown only when the answer is hidden, so whoever may read it cannot tell
        it is confidential (owner's ask, 2026-10-26)."""
        interview = self.begin()
        question = self.question("impedimento")
        self.assertEqual(question.sensitivity, "confidencial")
        write_answers(interview, {question.id: AnswerValue(text="RESPUESTA-CONFIDENCIAL")}, actor=self.coord1, by_participant=False)
        html = self.page_for(self.care, interview)
        self.assertIn('<dd><span class="privacy__tag">Confidencial</span> RESPUESTA-CONFIDENCIAL</dd>', html)
        salvo = self.question("salvo")
        write_answers(interview, {salvo.id: AnswerValue(codes=["si"])}, actor=self.coord1, by_participant=False)
        html = self.page_for(self.care, interview)
        self.assertIn("<dd>Sí</dd>", html)  # a non-confidential answer has no tag

    def page_for(self, user, interview):
        client = self.login(user, Client())
        response = client.get(self.url("interview_detail", interview.public_id))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


# ---- 4. Camper record ------------------------------------------------------------------------------------------


class ParticipantRecordTests(FlowFixture):
    def test_one_row_per_questionnaire_with_its_state(self):
        """Defect: the record showing only the newest interview, so questionnaire 1's answers cannot be reached."""
        first = self.begin(questionnaire=self.q1)
        self.complete(first)
        block = interview_block(self.page("participant_detail", self.p2.public_id))
        rows = re.findall(r'<li class="row">(.*?)</li>', block, re.DOTALL)
        self.assertEqual(len(rows), 2)
        self.assertIn('<p class="row__title">Primer cuestionario</p>', rows[0])
        self.assertIn(PILL.format("done", "Completada"), rows[0])
        self.assertIn(self.url("interview_detail", first.public_id), rows[0])
        self.assertIn('<p class="row__title">Segundo cuestionario</p>', rows[1])
        self.assertIn(PILL.format("pending", "Sin entrevista"), rows[1])
        self.assertNotIn("Ver respuestas", rows[1])

    def test_in_order_the_waiting_one_says_despues_and_the_head_offers_to_go_on(self):
        """Defect: a questionnaire that cannot start yet shown as just "Sin entrevista"; "Iniciar entrevista" gone
        once one interview exists."""
        set_in_order(self.event, True)
        html = self.page("participant_detail", self.p2.public_id)
        rows = re.findall(r'<li class="row">(.*?)</li>', interview_block(html), re.DOTALL)
        self.assertIn(PILL.format("pending", "Después"), rows[1])
        self.complete(self.begin(questionnaire=self.q1))
        html = self.page("participant_detail", self.p2.public_id)
        self.assertIn(f'href="{self.url("interview_start", self.p2.public_id)}">Iniciar entrevista</a>', html)
        live = self.begin(questionnaire=self.q2)
        html = self.page("participant_detail", self.p2.public_id)
        self.assertIn(f'href="{self.url("interview_start", self.p2.public_id)}">Retomar entrevista</a>', html)
        self.complete(live)
        html = self.page("participant_detail", self.p2.public_id)
        self.assertNotIn("Iniciar entrevista", html)
        self.assertNotIn("Retomar entrevista", html)


class OneQuestionnaireRecordTests(IsolatedCacheMixin, ServiceHelpers, CampFixture):
    def setUp(self):
        super().setUp()
        self.session = {}

    def block_as_before(self, interview):
        with timezone.override(self.event.time_zone):
            return Template(ORIGINAL_INTERVIEW_BLOCK).render(Context({"interview": interview, "event": self.event}))

    def test_the_interview_block_is_the_markup_it_always_was(self):
        """Defect: a one-questionnaire record turned into a list of one questionnaire."""
        self.login(self.coord1)
        url = self.url("participant_detail", self.p2.public_id)
        self.assertEqual(interview_block(self.client.get(url).content.decode()), self.block_as_before(None))
        interview = self.begin()
        html = self.client.get(url).content.decode()
        self.assertEqual(interview_block(html), self.block_as_before(interview))
        self.assertIn(">Retomar entrevista</a>", html)


# ---- 5. Paper sheet ---------------------------------------------------------------------------------------------


class PaperSheetTests(FlowFixture):
    def test_one_sheet_per_questionnaire_in_order(self):
        """Defect: the paper fallback printing only the first questionnaire."""
        self.complete(self.begin(questionnaire=self.q1))
        html = self.page("backup_list")
        self.assertIn('<section class="mt-8" id="cuestionario" aria-labelledby="cuestionario-titulo">', html)
        self.assertIn('<section class="mt-8" id="cuestionario-2" aria-labelledby="cuestionario-2-titulo">', html)
        self.assertNotIn('id="cuestionario-3"', html)
        self.assertIn('<h2 id="cuestionario-titulo">Cuestionario para entrevistar en papel: «Primer cuestionario»</h2>', html)
        self.assertIn('<h2 id="cuestionario-2-titulo">Cuestionario para entrevistar en papel: «Segundo cuestionario»</h2>', html)
        first, second = html.split('id="cuestionario-2"', 1)
        self.assertIn("1. ¿Pregunta de Primero?", first)
        self.assertIn("1. ¿Pregunta de Segundo?", second)  # the numbering restarts on each sheet
        self.assertLess(html.index("¿Pregunta de Primero?"), html.index("¿Pregunta de Segundo?"))
        self.assertIn(f"<td>{self.p2.full_name}</td>", html)
        self.assertIn("<td>1 de 2 cuestionarios</td>", html)


# ---- 6. Safety report ---------------------------------------------------------------------------------------------


class SafetyReportTests(FlowFixture):
    def test_the_report_goes_with_the_live_interview(self):
        """Defect: with questionnaire 1 done and 2 in progress, the report (and its way back to the review) attached
        to whichever interview started last, not to the one on screen."""
        done = self.begin(questionnaire=self.q1)
        self.complete(done)
        station = self.login(self.coord1, Client())
        station.post(self.url("interview_start", self.p2.public_id), START_ADULT)
        live = Interview.objects.get(participant=self.p2, status=Interview.Status.IN_PROGRESS)
        self.assertEqual(live.questionnaire, self.q2)
        # The finished one reads as newer (a paper interview captured late): "the newest" is the wrong rule.
        Interview.objects.filter(pk=done.pk).update(started_at=timezone.now() + timedelta(minutes=5))
        review = self.url("interview_review", live.public_id)
        report = self.url("safety_report", self.p2.public_id)
        page = station.get(report, {"siguiente": review})
        self.assertEqual(page.context["interview"], live)
        self.assertEqual(page.context["back_url"], review)
        response = station.post(f"{report}?siguiente={review}", {"details": "Dijo algo que preocupa."})
        self.assertRedirects(response, review, fetch_redirect_response=False)
        self.assertEqual(FollowUp.objects.get(participant=self.p2, kind=FollowUp.Kind.SAFETY).interview, live)


# ---- 7. Readiness --------------------------------------------------------------------------------------------------


class ReadinessTests(FlowFixture):
    def step(self):
        self.login(self.admin)
        response = self.client.get(self.url("admin_home"))
        return next(step for step in response.context["steps"] if step["key"] == "cuestionario")

    def test_the_questionnaire_step_lists_every_questionnaire(self):
        """Defect: "Preparación" naming only the first questionnaire."""
        step = self.step()
        self.assertEqual(step["state"], "listo")
        self.assertEqual(step["meta"], ["Primer cuestionario, versión 1.", "Segundo cuestionario, versión 1."])
        set_in_order(self.event, True)
        self.assertEqual(self.step()["meta"][-1], "Se responden en orden.")

    def test_without_questionnaires_the_step_and_the_problem_say_so(self):
        """Defect: the readiness check reading the old single field and calling an event without links ready."""
        self.event.questionnaire_links.all().delete()
        step = self.step()
        self.assertEqual((step["state"], step["meta"]), ("falta", ["Sin cuestionario publicado."]))
        response = self.client.get(self.url("admin_home"))
        self.assertIn("El evento no tiene un cuestionario publicado.", [text for text, _ in response.context["problems"]])
