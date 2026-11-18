"""The interview, the campero's screens, a campero's record and the authorization pages on «Ventanas» (ui_v2).

Their templates are shared with churches that keep Atrio: the other suites pin that markup with the module off, and
it is byte for byte what it was. These run with the module on and check what the redesign promises here: where an
interview is open is said plainly, with «Continuar aquí»; a follow-up decision says who and what; the button of a
step says what it will do; the offline notice is beside it; nothing about consent, a guardian's authorization, who
reads the answers or a risk report is shortened; the campero sees nobody else.
"""

from __future__ import annotations

import re
from unittest import mock

from django.test import Client
from django.urls import reverse

from events.models import Interview, InterviewStatus, ParticipantSession, Question
from events.services import interviews as svc
from events.services import questionnaire
from people import flags
from people.models import Person

from .base import CampFixture
from .test_ux_coordinator import give_interview


def main_of(response) -> str:
    return response.content.decode().split("<main", 1)[1]


def filled(html: str) -> list[str]:
    """The filled buttons and links of a page's <main>, not counting a closed dialog's («¿Sigues aquí?»)."""
    html = re.sub(r"<dialog.*?</dialog>", "", html, flags=re.DOTALL)
    return re.findall(r'<(?:a|button) class="btn btn--(?:primary|danger)\b[^>]*>(.*?)</(?:a|button)>', html, re.DOTALL)


class VentanasFixture(CampFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        flags.set_enabled(cls.church, "community", True)
        Person.objects.create(church=cls.church, user=cls.admin, first_name=cls.admin.first_name, last_name=cls.admin.last_name)

    def computer(self, number, user=None):
        """A browser of ``user`` (the first coordinator) at «Computadora <number>», its messages read."""
        client = self.login(user or self.coord1, Client())
        self.choose_station(client, number=number)
        client.get(self.url("coord_home"))
        return client


class OpenElsewhereTests(VentanasFixture):
    """«Retomar» while the interview is open on another computer: the page says where, with one action."""

    def setUp(self):
        self.first = self.computer(1)
        self.start(self.p1, client=self.first)
        self.interview = Interview.objects.get(participant=self.p1)
        self.second = self.computer(2)
        self.conflict = self.url("interview_conflict", self.interview.public_id)

    def test_retomar_lands_on_a_page_that_says_where_it_is_open(self):
        response = self.second.get(self.url("interview_resume", self.interview.public_id))
        self.assertRedirects(response, self.conflict, fetch_redirect_response=False)
        html = main_of(self.second.get(self.conflict))
        self.assertIn('<h1 class="voice page-head__title">Abierta en Computadora 1, no en esta</h1>', html)
        self.assertIn(f"{self.coord1.get_full_name()}, última actividad", html)
        self.assertEqual(filled(html), ["Continuar aquí"])
        self.assertNotIn('class="lede"', html)
        self.assertNotIn("Tomar el control", html)

    def test_continuar_aqui_takes_it_over_and_the_first_computer_is_told_where_it_went(self):
        response = self.second.post(self.conflict)
        self.assertRedirects(response, self.url("interview_resume", self.interview.public_id), fetch_redirect_response=False)
        self.answer(self.interview, "salvo", "si", client=self.second)
        self.assertEqual(self.interview.answers.count(), 1)
        refused = self.answer(self.interview, "salvo", "no", client=self.first)
        self.assertRedirects(refused, self.conflict, fetch_redirect_response=False)
        self.assertIn("Abierta en Computadora 2, no en esta", main_of(self.first.get(self.conflict)))
        self.assertEqual(self.interview.answers.get().options.get().code, "si")

    def test_a_campero_answering_alone_there_is_said_before_their_screen_closes(self):
        kiosk = self.computer(3)
        self.start(self.p2, mode="supervisada", client=kiosk)
        alone = Interview.objects.get(participant=self.p2)
        kiosk.post(self.url("interview_handoff", alone.public_id))
        self.assertTrue(ParticipantSession.objects.filter(interview=alone, state=ParticipantSession.State.ACTIVE).exists())
        html = main_of(self.second.get(self.url("interview_conflict", alone.public_id)))
        self.assertIn("Abierta en Computadora 3, no en esta", html)
        self.assertIn("Diego está respondiendo a solas ahí: si continúas aquí, su pantalla se cierra.", html)

    def test_released_says_so_once(self):
        svc.revoke_claim(self.interview, actor=self.admin)
        html = main_of(self.first.get(self.conflict))
        self.assertIn('<h1 class="voice page-head__title">La administración liberó esta entrevista</h1>', html)
        self.assertEqual(html.count("ya no puede guardar"), 1)
        self.assertNotIn("Computadora 1", html)
        self.assertEqual(filled(html), ["Continuar aquí"])


class FollowUpDecisionTests(VentanasFixture):
    """The record's «¿Necesita seguimiento?»: answered in place, and the message says who and what."""

    def setUp(self):
        self.client = self.computer(1)
        self.done = give_interview(self.p1, InterviewStatus.COMPLETED, decision="por_evaluar")
        self.record = self.url("interview_detail", self.done.public_id)

    def test_the_question_is_raised_with_its_two_answers_in_place(self):
        html = main_of(self.client.get(self.record))
        section = html.split('id="seguimiento"')[1].split("</section>")[0]
        self.assertIn('<div class="sign">', section)
        self.assertIn("¿Mariana necesita seguimiento?", section)
        self.assertIn('<form class="sign__actions sign__actions--pair" method="post" data-inline="seguimiento">', section)
        self.assertIn('value="requerido">Sí<span class="visually-hidden">, Mariana López Ruiz necesita seguimiento</span></button>', section)
        self.assertIn('value="no_requerido">No<span class="visually-hidden">, Mariana López Ruiz no necesita seguimiento</span></button>', section)

    def test_yes_names_the_campero_and_the_answer(self):
        response = self.client.post(self.record, {"action": "decision", "decision": "requerido"})
        self.assertRedirects(response, self.record, fetch_redirect_response=False)
        page = self.client.get(self.record)
        self.assertEqual([m.message for m in page.context["messages"]], ["Mariana López Ruiz necesita seguimiento."])
        self.assertIn("Necesita seguimiento.", main_of(page).split('id="seguimiento"')[1].split("</section>")[0])

    def test_no_names_the_campero_and_the_answer(self):
        self.client.post(self.record, {"action": "decision", "decision": "no_requerido"})
        page = self.client.get(self.record)
        self.assertEqual([m.message for m in page.context["messages"]], ["Mariana López Ruiz no necesita seguimiento."])

    def test_a_refusal_keeps_its_own_words(self):
        self.client.post(self.record, {"action": "decision", "decision": "no_requerido"})
        self.client.get(self.record)
        self.client.post(self.record, {"action": "decision", "decision": "requerido"})
        page = self.client.get(self.record)
        self.assertEqual([m.message for m in page.context["messages"]], ["El seguimiento de esta entrevista ya se decidió."])
        self.done.refresh_from_db()
        self.assertEqual(self.done.follow_up_decision, "no_requerido")

    def test_whoever_may_not_decide_reads_the_state_only(self):
        tutor = self.login(self.tutor1, Client())
        html = main_of(tutor.get(self.record))
        section = html.split('id="seguimiento"')[1].split("</section>")[0]
        self.assertNotIn("<form", section)
        self.assertIn("Por decidir", section)


class StepTests(VentanasFixture):
    """One question per screen: the button says what it will do; the offline notice is beside it."""

    def setUp(self):
        self.client = self.computer(1)
        self.start(self.p2)
        self.interview = Interview.objects.get(participant=self.p2)

    def step_page(self, code, url_name="interview_step"):
        return main_of(self.client.get(self.url(url_name, self.interview.public_id, self.step(code).number)))

    def test_an_optional_choice_says_it_will_be_skipped_until_one_is_chosen(self):
        html = self.step_page("salvo")
        self.assertIn('class="step step--saltable"', html)
        self.assertIn('<span class="step__con">Continuar</span><span class="step__sin">Saltar pregunta</span>', html)
        last = self.step_page("impedimento")  # a text question: no skipping words
        self.assertNotIn("step--saltable", last)
        self.assertIn('<span class="step__con">Revisar respuestas</span>', last)

    def test_skipping_leaves_the_question_unanswered_and_the_rules_are_the_service_s(self):
        response = self.answer(self.interview, "salvo", "")
        self.assertRedirects(response, self.url("interview_step", self.interview.public_id, self.step("dudas").number), fetch_redirect_response=False)
        self.assertFalse(self.interview.answers.exists())
        # No question is required, even one an older version marked "Obligatoria": its button says «Saltar» too, and
        # the service saves the blank as a blank.
        Question.objects.filter(pk=self.question("salvo").pk).update(required=True)
        with mock.patch.dict(questionnaire._structure_cache, clear=True):
            html = self.step_page("salvo")
            self.assertIn('class="step step--saltable"', html)
            skipped = self.answer(self.interview, "salvo", "")
            self.assertEqual(skipped.status_code, 302)
            self.assertFalse(self.interview.answers.exists())

    def test_the_offline_notice_sits_with_the_button(self):
        for code in ("salvo", "dudas"):
            actions = self.step_page(code).split('<div class="step__actions">')[1].split("</form>")[0]
            self.assertIn("data-offline-notice hidden", actions)
            self.assertLess(actions.index("data-offline-notice"), actions.index('name="direction" value="next"'))
            self.assertIn("Sin conexión. Cuando vuelva la red, toca el botón otra vez. Lo que elegiste sigue aquí.", actions)

    def test_atras_and_the_count_stay(self):
        html = self.step_page("dudas")
        self.assertIn('<button class="trail__back" type="submit" form="step-form" name="direction" value="back">Atrás</button>', html)
        self.assertIn("Pregunta <strong>2</strong> de", html)

    def test_a_confidential_question_says_who_reads_it(self):
        html = self.step_page("apoyo")
        self.assertIn('<span class="privacy__tag">Confidencial</span>', html)
        self.assertIn("Solo el equipo de cuidado autorizado podrá leer esta respuesta.", html)

    def test_the_computer_is_drawn_as_on_mi_inicio(self):
        page = self.client.get(self.url("interview_step", self.interview.public_id, self.step("salvo").number)).content.decode()
        bar = page.split('<div class="focusbar__actions">')[1].split("</div>")[0]
        self.assertIn('<p class="here"><svg class="icon icon--sm"', bar)
        self.assertNotIn("here__blaze", bar)


class StartAndReviewTests(VentanasFixture):
    """Before starting and before finishing: nothing about consent or who reads is shortened."""

    def setUp(self):
        self.client = self.computer(1)

    def test_every_consent_and_its_words_are_the_service_s(self):
        for participant in (self.p1, self.p2, self.p_minor_no_auth):
            with self.subTest(participant=participant.full_name):
                html = main_of(self.client.get(self.url("interview_start", participant.public_id)))
                self.assertNotIn('class="lede"', html)
                self.assertIn(self.event.privacy_notice.splitlines()[0], html)
                for requirement in svc.consent_requirements(participant):
                    if requirement.confirm_now:
                        self.assertIn(requirement.label, html)
                        self.assertIn(requirement.help, html)
                        self.assertIn(f'name="consent_{requirement.key}"', html)
                    else:
                        self.assertIn("Autorización de padre, madre o tutor legal: registrada.", html)
                self.assertEqual(filled(html), ["Comenzar entrevista"])

    def test_the_campero_is_drawn_as_a_person(self):
        html = main_of(self.client.get(self.url("interview_start", self.p1.public_id)))
        self.assertIn('<span class="mono mono--lg" aria-hidden="true">ML</span>', html)
        self.assertIn('<p class="checks__nombre">Mariana López Ruiz</p>', html)
        self.assertIn("15 años, menor de edad.", html)

    def test_the_review_decides_in_its_own_section_with_who_reads_the_confidential_answers(self):
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        self.answer(interview, "apoyo", "familia")
        self.answer(interview, "decision", "hablar_lider")
        html = main_of(self.client.get(self.url("interview_review", interview.public_id)))
        self.assertIn('<form method="post" class="revision__seguimiento" novalidate>', html)
        self.assertNotIn('class="decision"', html)
        self.assertIn("Lo que Diego pidió", html)
        self.assertEqual(filled(html), ["Finalizar entrevista"])
        self.assertIn("Si el campero contó algo que pone en riesgo su seguridad o la de otra persona.", html)
        self.assertIn('<a class="btn btn--link btn--quieto" href="' + self.url("interview_discard", interview.public_id) + '">Descartar esta entrevista</a>', html)

    def test_the_hand_over_keeps_what_the_campero_is_told(self):
        self.start(self.p2, mode="supervisada")
        interview = Interview.objects.get(participant=self.p2)
        html = main_of(self.client.get(self.url("interview_handoff", interview.public_id)))
        self.assertIn("Dile que puede responder con calma y que puede elegir “Prefiero no responder”.", html)
        self.assertIn("Dile que la pantalla de bienvenida le explica quién lee sus respuestas", html)
        self.assertIn("Tu sesión se cerrará en esta computadora: así el campero no puede ver nada más. Para recuperar la computadora necesitarás tu contraseña.", html)
        self.assertEqual(filled(html), ["Entregar la computadora"])

    def test_discarding_says_what_is_erased_beside_the_one_button(self):
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        html = main_of(self.client.get(self.url("interview_discard", interview.public_id)))
        self.assertIn("Se borrarán todas las respuestas guardadas. Diego quedará como “sin entrevista” y se podrá empezar de nuevo.", html)
        self.assertEqual(filled(html), ["Sí, descartar y borrar respuestas"])
        self.assertIn('<div class="choices" id="id_reason">', html)


class KioskTests(VentanasFixture):
    """The campero's own screens: warm, the words they are given kept, nobody else on them."""

    def setUp(self):
        self.client = self.computer(5)
        self.start(self.p1, mode="supervisada")
        self.interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", self.interview.public_id))

    def others(self):
        return [p.full_name for p in (self.p2, self.p3, self.p4, self.p_minor_no_auth)] + [p.first_name for p in (self.p2, self.p3, self.p4)]

    def test_no_screen_names_anybody_else(self):
        self.answer(self.interview, "salvo", "si", url_name="kiosk")
        pages = [reverse("kiosk:home"), reverse("kiosk:step", args=[self.step("dudas").number]), reverse("kiosk:review")]
        for url in pages:
            html = self.client.get(url).content.decode()
            for name in self.others():
                self.assertNotIn(name, html, url)
        self.client.post(reverse("kiosk:finish"))
        handback = self.client.get(reverse("kiosk:home")).content.decode()
        for name in self.others():
            self.assertNotIn(name, handback)

    def test_the_review_says_how_many_are_left_and_who_reads(self):
        self.answer(self.interview, "salvo", "si", url_name="kiosk")
        html = main_of(self.client.get(reverse("kiosk:review")))
        self.assertIn('<p class="page-head__meta">Te faltan', html)
        self.assertNotIn('class="lede"', html)
        self.assertIn("Tus respuestas las lee tu coordinador y, las generales, también el equipo del campamento.", html)
        self.assertEqual(filled(html), ["Seguir contestando"])

    def test_the_hand_back_names_who_the_button_is_for(self):
        self.client.post(reverse("kiosk:finish"))
        html = main_of(self.client.get(reverse("kiosk:home")))
        self.assertIn("¡Gracias!", html)
        self.assertIn(f'<a class="btn btn--secondary" href="{reverse("kiosk:unlock")}">Soy coordinador</a>', html)
        self.assertNotIn("Sí", html)

    def test_unlocking_names_the_coordinator_and_offers_another_one(self):
        self.client.post(reverse("kiosk:finish"))
        html = main_of(self.client.get(reverse("kiosk:unlock")))
        self.assertIn(f"Solo para coordinadores: {self.coord1.get_full_name()}.", html)
        self.assertIn(f'<a class="btn btn--link" href="{reverse("kiosk:unlock")}?otra=1">Soy otro coordinador</a>', html)

    def test_the_step_keeps_parar_por_ahora_and_the_reassurance(self):
        page = self.client.get(reverse("kiosk:step", args=[self.step("salvo").number])).content.decode()
        self.assertIn('value="pause">Parar por ahora</button>', page)
        self.assertIn("Puedes elegir “Prefiero no responder” en cualquier pregunta.", page)
        self.assertIn("step--saltable", page)
        actions = page.split('<div class="step__actions">')[1].split("</form>")[0]
        self.assertLess(actions.index("data-offline-notice"), actions.index('name="direction" value="next"'))


class RecordsTests(VentanasFixture):
    """The search, a campero's record and the authorization pages on the row specification, their words kept."""

    def setUp(self):
        self.client = self.computer(1)

    def test_search_rows_are_people_and_the_record_opens_from_the_name(self):
        give_interview(self.p2, InterviewStatus.COMPLETED)
        html = main_of(self.client.get(self.url("interview_search")))
        self.assertIn('placeholder="Número o nombre: “mat jim”"', html)
        self.assertIn('<a class="compu compu--elegida"', html)
        rows = html.split('<li class="row row--lead">')[1:]
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertIn('<span class="mono" aria-hidden="true">', row)
        done = next(r for r in rows if self.p2.full_name in r)
        self.assertIn(f'<a href="{self.url("participant_detail", self.p2.public_id)}">{self.p2.full_name}</a>', done)
        self.assertNotIn("row__end", done)  # no «Ver»: the row is the way in
        pending = next(r for r in rows if self.p1.full_name in r)
        self.assertIn(f'href="{self.url("interview_start", self.p1.public_id)}">Entrevistar', pending)
        self.assertNotIn(">Ver<", html)
        nobody = main_of(self.client.get(self.url("interview_search") + "?q=zzzz"))
        self.assertIn('<p class="empty__title">Nadie coincide</p>', nobody)

    def test_a_record_opens_its_interview_from_the_row(self):
        done = give_interview(self.p2, InterviewStatus.COMPLETED)
        html = main_of(self.client.get(self.url("participant_detail", self.p2.public_id)))
        self.assertIn(f'<a href="{self.url("interview_detail", done.public_id)}">Sus respuestas</a>', html)  # names what opens
        self.assertNotIn("Ver respuestas", html)  # the row is the link: no «Ver» button

    def test_the_authorization_pages_keep_every_word(self):
        attest = main_of(self.client.get(self.url("guardian_authorization", self.p_minor_no_auth.public_id)))
        self.assertIn(
            "Confirmas que la autorización firmada del padre, la madre o el tutor legal de Lucía Torres Gómez está en manos de la iglesia. "
            "Confírmalo solo si viste el formato firmado. Con esto, quien lo entreviste ya no tendrá que confirmarlo antes de empezar. "
            "Si te equivocas, puedes quitarla desde su ficha.",
            attest,
        )
        self.assertIn('aria-describedby="se-confirma">Sí, la iglesia la tiene</button>', attest)
        self.assertEqual(filled(attest), ["Sí, la iglesia la tiene"])
        withdraw = main_of(self.client.get(self.url("guardian_authorization_withdraw", self.p1.public_id)))
        self.assertIn(
            "Úsalo si se marcó por error o si la familia retiró su permiso. Las entrevistas ya completadas no cambian. "
            "Antes de una entrevista nueva, alguien tendrá que confirmar la autorización otra vez.",
            withdraw,
        )

    def test_the_risk_report_keeps_its_words_and_its_chile(self):
        html = main_of(self.client.get(self.url("safety_report", self.p2.public_id)))
        self.assertIn("Primero, actúa", html)
        self.assertIn(self.event.safeguarding_instructions.splitlines()[0], html)
        self.assertIn("Solo el equipo de cuidado autorizado podrá leer lo que escribas. La administración verá que existe un reporte, pero no lo que dice.", html)
        self.assertEqual(filled(html), ["Enviar reporte"])
        self.assertIn('<p class="page-head__meta">Sobre Diego Hernández Cruz, #0002.</p>', html)
