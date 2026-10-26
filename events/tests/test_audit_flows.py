"""UX audit (auditoria-ux): flow fixes.

UX-01 / A11Y-03  radio wrappers reused the first radio's id: its label pointed at a <div>.
UX-03 / UX-04    the deck takes the keys back after a toolbar button; it says on the slide when the session ended.
UX-05            a step form offline keeps the choice on screen (template side; the browser check is Playwright).
UX-06            "Parar por ahora" saves what is on screen; the review after a pause offers to keep going.
UX-07            "Cambiar" from a review opens one question and comes back to the review.
LANG-01          the camper is told who reads the answers the way events/access.py decides it.
"""

from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
from unittest import mock

from django.test import Client
from django.urls import reverse

from events.models import Interview, Question
from events.services import interviews as svc
from events.services import questionnaire
from events.services.questionnaire import first_unanswered_step, load_answers

from .base import PASSWORD, CampFixture


class _Page(HTMLParser):
    """The bits of a page these tests read: ids, labels, radios, links, forms and every element's attributes."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.tag_by_id: dict[str, str] = {}
        self.label_fors: list[str] = []
        self.radios: list[dict] = []
        self.elements: list[tuple[str, dict]] = []
        self._text_stack: list[list] = []
        self.texts: list[tuple[str, dict, str]] = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if "id" in attrs:
            self.ids.append(attrs["id"])
            self.tag_by_id.setdefault(attrs["id"], tag)
        if tag == "label" and attrs.get("for"):
            self.label_fors.append(attrs["for"])
        if tag == "input" and attrs.get("type") == "radio":
            self.radios.append(attrs)
        if tag in ("a", "button", "span", "p", "div", "form", "li"):
            self._text_stack.append([tag, attrs, []])

    def handle_endtag(self, tag):
        for i in range(len(self._text_stack) - 1, -1, -1):
            if self._text_stack[i][0] == tag:
                t, attrs, parts = self._text_stack.pop(i)
                text = " ".join("".join(parts).split())
                self.texts.append((t, attrs, text))
                if self._text_stack:
                    self._text_stack[-1][2].append(text)
                return

    def handle_data(self, data):
        if self._text_stack:
            self._text_stack[-1][2].append(data)

    def find(self, tag, text):
        return [attrs for t, attrs, body in self.texts if t == tag and body == text]

    def with_attr(self, name):
        return [(tag, attrs) for tag, attrs in self.elements if name in attrs]


class RadioIdsTests(CampFixture):
    """UX-01 / A11Y-03: every radio has its own id and a <label for> that points at it, and no id repeats."""

    def assert_ids_sound(self, response):
        self.assertEqual(response.status_code, 200)
        page = _Page(response.content.decode())
        duplicates = sorted(i for i, n in Counter(page.ids).items() if n > 1)
        self.assertEqual(duplicates, [], "duplicate id= attributes")
        self.assertTrue(page.radios, "the page should have radios")
        for radio in page.radios:
            self.assertIn(radio["id"], page.label_fors, f"radio {radio['id']} has no <label for>")
        for target in page.label_fors:
            self.assertIn(page.tag_by_id.get(target), ("input", "textarea", "select"), f"<label for={target}> points at {page.tag_by_id.get(target)}")
        # The error summary links to something on the page.
        for tag, attrs in page.elements:
            href = attrs.get("href") or ""
            if tag == "a" and href.startswith("#") and len(href) > 1:
                self.assertIn(href[1:], page.ids, f"link {href} has no target")
        return page

    def test_choose_station(self):
        self.login(self.coord1)
        self.assert_ids_sound(self.client.get(self.url("choose_station")))

    def test_choose_station_with_an_error(self):
        self.login(self.coord1)
        page = self.assert_ids_sound(self.client.post(self.url("choose_station"), {"siguiente": "/"}))
        self.assertEqual(page.tag_by_id.get("id_station"), "div", "the error summary link lands on the group of options")

    def test_interview_start(self):
        self.login(self.coord1)
        self.choose_station()
        self.assert_ids_sound(self.client.get(self.url("interview_start", self.p2.public_id)))

    def test_interview_start_with_an_error(self):
        self.login(self.coord1)
        self.choose_station()
        page = self.assert_ids_sound(self.client.post(self.url("interview_start", self.p2.public_id), {}))
        self.assertEqual(page.tag_by_id.get("id_mode"), "div", "the error summary link lands on the group of options")

    def test_interview_step_and_kiosk_step(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        self.assert_ids_sound(self.client.get(self.url("interview_step", interview.public_id, 1)))
        self.start(self.p1, mode="supervisada")
        self.client.post(self.url("interview_handoff", Interview.objects.get(participant=self.p1).public_id))
        self.assert_ids_sound(self.client.get(reverse("kiosk:step", args=[1])))


class DeckTemplateTests(CampFixture):
    """UX-03 / UX-04, the part a test without a browser can see (the behaviour is in the Playwright check)."""

    ENDED = "Sesión terminada: vuelve a entrar para ver datos actuales"

    def test_deck_can_take_focus_back(self):
        self.login(self.admin)
        page = _Page(self.client.get(self.url("presentation_live")).content.decode())
        (deck,) = [attrs for tag, attrs in page.elements if "deck" in (attrs.get("class") or "").split()]
        self.assertEqual(deck.get("tabindex"), "-1")

    def test_every_live_label_has_a_hidden_session_ended_label(self):
        self.login(self.admin)
        page = _Page(self.client.get(self.url("presentation_live")).content.decode())
        live = page.with_attr("data-deck-live")
        ended = [attrs for t, attrs, text in page.texts if "data-deck-ended" in attrs and text == self.ENDED]
        self.assertEqual(len(live), 5, "the cover and the top of the four other slides")
        self.assertEqual(len(ended), len(live))
        self.assertTrue(all("hidden" in attrs for attrs in ended))

    def test_refreshed_slides_keep_the_ended_label(self):
        self.login(self.admin)
        response = self.client.get(self.url("presentation_live"), HTTP_X_REQUESTED_WITH="fetch")
        self.assertContains(response, self.ENDED, count=5)  # the refreshed slides keep the label


class KioskFixture(CampFixture):
    def setUp(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p1, mode="supervisada")
        self.interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", self.interview.public_id))
        self.assertIn(svc.KIOSK_KEY, self.client.session)

    def post_kiosk(self, step, data, *, direction="next", query=""):
        self.interview.refresh_from_db()
        payload = {"revision": self.interview.revision, "direction": direction, **data}
        return self.client.post(reverse("kiosk:step", args=[step.number]) + query, payload)

    def answer_everything(self):
        """Answer every visible question through the kiosk, as a camper would."""
        while True:
            step = first_unanswered_step(self.version, load_answers(self.interview))
            if step is None:
                return
            q = step.question
            value = q.options.all()[0].code if q.is_choice else "Texto de prueba"
            self.assertEqual(self.post_kiosk(step, {f"q_{q.id}": value}).status_code, 302)


class KioskPauseTests(KioskFixture):
    """UX-06: "Parar por ahora" saves the choice on screen and the review offers to keep going."""

    def test_pause_is_a_submit_of_the_step_form(self):
        page = _Page(self.client.get(reverse("kiosk:step", args=[1])).content.decode())
        (button,) = page.find("button", "Parar por ahora")
        self.assertEqual((button.get("type"), button.get("form"), button.get("name"), button.get("value")), ("submit", "step-form", "direction", "pause"))
        self.assertEqual(page.find("a", "Parar por ahora"), [], "a link would leave without saving")

    def test_pause_saves_the_option_just_chosen(self):
        step = self.step("salvo")
        response = self.post_kiosk(step, {f"q_{step.question.id}": "si"}, direction="pause")
        self.assertRedirects(response, reverse("kiosk:review"), fetch_redirect_response=False)
        self.assertEqual(load_answers(self.interview)[step.question.id].codes, ["si"])

    def test_pause_does_not_require_an_answer(self):
        question = self.question("dudas")
        Question.objects.filter(pk=question.pk).update(required=True)  # a published version: only for this test
        with mock.patch.dict(questionnaire._structure_cache, clear=True):
            step = self.step("dudas")
            # "Continuar" with nothing chosen is refused...
            refused = self.post_kiosk(step, {})
            self.assertContains(refused, "Elige una respuesta para continuar.")
            # ... "Parar por ahora" with nothing chosen pauses and saves nothing.
            revision = Interview.objects.get(pk=self.interview.pk).revision
            response = self.post_kiosk(step, {}, direction="pause")
            self.assertRedirects(response, reverse("kiosk:review"), fetch_redirect_response=False)
            self.assertNotIn(question.id, load_answers(self.interview))
            self.assertEqual(Interview.objects.get(pk=self.interview.pk).revision, revision)

    def test_pause_with_a_required_follow_up_left_blank_saves_nothing_from_that_screen(self):
        detail = self.question("dudas_detalle")  # revealed by "dudas" = "si", on the same screen
        Question.objects.filter(pk=detail.pk).update(required=True)  # a published version: only for this test
        with mock.patch.dict(questionnaire._structure_cache, clear=True):
            step = self.step("dudas")
            data = {f"q_{step.question.id}": "si", f"q_{detail.id}": ""}
            self.assertContains(self.post_kiosk(step, data), "Escribe una respuesta para continuar.")
            revision = Interview.objects.get(pk=self.interview.pk).revision
            response = self.post_kiosk(step, data, direction="pause")
            self.assertRedirects(response, reverse("kiosk:review"), fetch_redirect_response=False)
            answers = load_answers(self.interview)
            self.assertNotIn(step.question.id, answers)
            self.assertNotIn(detail.id, answers)
            self.assertEqual(Interview.objects.get(pk=self.interview.pk).revision, revision)

    def test_a_paused_required_follow_up_can_be_completed_by_the_camper(self):
        """The dead end: "si" saved without its required confidential follow-up, then "Ya terminé". The coordinator
        cannot finalize ("Falta responder …") and may not open a confidential screen the camper answered. After the
        pause the review sends the camper back to that screen, and the interview can be finalized."""
        detail = self.question("dudas_detalle")
        Question.objects.filter(pk=detail.pk).update(required=True)  # a published version: only for this test
        with mock.patch.dict(questionnaire._structure_cache, clear=True):
            first, step = self.step("salvo"), self.step("dudas")
            self.assertEqual(self.post_kiosk(first, {f"q_{first.question.id}": "si"}).status_code, 302)
            response = self.post_kiosk(step, {f"q_{step.question.id}": "si", f"q_{detail.id}": ""}, direction="pause")
            self.assertRedirects(response, reverse("kiosk:review"), fetch_redirect_response=False)
            (keep_going,) = _Page(self.client.get(reverse("kiosk:review")).content.decode()).find("a", "Seguir contestando")
            self.assertEqual(keep_going["href"], reverse("kiosk:step", args=[step.number]))
            # The camper finishes that screen and the others, and taps "Ya terminé".
            done = self.post_kiosk(step, {f"q_{step.question.id}": "si", f"q_{detail.id}": "Texto de prueba"})
            self.assertEqual(done.status_code, 302)
            self.answer_everything()
            self.client.post(reverse("kiosk:finish"))
            # The coordinator recovers the computer and finalizes.
            self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
            self.interview.refresh_from_db()
            response = self.client.post(
                self.url("interview_review", self.interview.public_id), {"revision": self.interview.revision, "follow_up_decision": "no_requerido"}
            )
            self.assertRedirects(response, self.url("interview_done", self.interview.public_id), fetch_redirect_response=False)
            self.interview.refresh_from_db()
            self.assertEqual(self.interview.status, Interview.Status.COMPLETED)

    def test_review_sends_the_camper_back_to_a_required_follow_up_left_blank(self):
        """An answer stored before its follow-up became required: the review counts the follow-up as missing."""
        step, detail = self.step("dudas"), self.question("dudas_detalle")
        self.answer_everything()  # "dudas" = "si" (its first option), follow-up blank while it is optional
        self.assertEqual(load_answers(self.interview)[step.question.id].codes, ["si"])
        self.assertNotIn(detail.id, load_answers(self.interview))
        Question.objects.filter(pk=detail.pk).update(required=True)  # a published version: only for this test
        with mock.patch.dict(questionnaire._structure_cache, clear=True):
            response = self.client.get(reverse("kiosk:review"))
        (keep_going,) = _Page(response.content.decode()).find("a", "Seguir contestando")
        self.assertEqual(keep_going["href"], reverse("kiosk:step", args=[step.number]))
        self.assertContains(response, "Te falta 1 pregunta.")

    def test_erasing_a_stored_text_then_pausing_deletes_it(self):
        """A blank screen keeps what is stored only if nothing was stored: an erased answer is erased, as with
        "Continuar", and a required question is still not required while pausing."""
        step = self.step("impedimento")
        question = step.question
        self.assertEqual(self.post_kiosk(step, {f"q_{question.id}": "Algo privado"}).status_code, 302)
        Question.objects.filter(pk=question.pk).update(required=True)  # a published version: only for this test
        with mock.patch.dict(questionnaire._structure_cache, clear=True):
            response = self.post_kiosk(step, {f"q_{question.id}": ""}, direction="pause")
        self.assertRedirects(response, reverse("kiosk:review"), fetch_redirect_response=False)
        self.assertNotIn(question.id, load_answers(self.interview))

    def test_unchecking_every_option_then_pausing_deletes_the_answer(self):
        step = self.step("apoyo")
        question = step.question
        self.assertEqual(self.post_kiosk(step, {f"q_{question.id}": ["familia"]}).status_code, 302)
        response = self.post_kiosk(step, {}, direction="pause")
        self.assertRedirects(response, reverse("kiosk:review"), fetch_redirect_response=False)
        self.assertNotIn(question.id, load_answers(self.interview))

    def test_pause_keeps_the_other_checks(self):
        step = self.step("salvo")
        response = self.post_kiosk(step, {f"q_{step.question.id}": "inventada"}, direction="pause")
        self.assertContains(response, "Elige una de las opciones que aparecen en pantalla.")
        self.assertFalse(self.interview.answers.exists())

    def test_review_after_a_pause_offers_to_keep_going_and_to_finish(self):
        first = self.step("salvo")
        self.post_kiosk(first, {f"q_{first.question.id}": "si"}, direction="pause")
        page = _Page(self.client.get(reverse("kiosk:review")).content.decode())
        (keep_going,) = page.find("a", "Seguir contestando")
        nxt = first_unanswered_step(self.version, load_answers(self.interview))
        self.assertEqual(keep_going["href"], reverse("kiosk:step", args=[nxt.number]))
        self.assertEqual(len(page.find("button", "Ya terminé")), 1)

    def test_review_of_a_finished_questionnaire_only_offers_to_finish(self):
        self.answer_everything()
        page = _Page(self.client.get(reverse("kiosk:review")).content.decode())
        self.assertEqual(page.find("a", "Seguir contestando"), [])
        self.assertEqual(len(page.find("button", "Ya terminé")), 1)


class KioskChangeFromReviewTests(KioskFixture):
    """UX-07 in the kiosk: "Cambiar" → one question → back to the review."""

    def test_change_returns_to_the_review_after_one_step(self):
        self.answer_everything()
        step = self.step("salvo")
        change_url = reverse("kiosk:step", args=[step.number]) + "?volver=revision"
        review = _Page(self.client.get(reverse("kiosk:review")).content.decode())
        self.assertIn(change_url, [attrs.get("href") for tag, attrs in review.elements if tag == "a"])
        form = [attrs for tag, attrs in _Page(self.client.get(change_url).content.decode()).elements if tag == "form" and attrs.get("id") == "step-form"]
        self.assertEqual(form[0]["action"], change_url)
        response = self.post_kiosk(step, {f"q_{step.question.id}": "no"}, query="?volver=revision")
        self.assertRedirects(response, reverse("kiosk:review"), fetch_redirect_response=False)
        self.assertEqual(load_answers(self.interview)[step.question.id].codes, ["no"])

    def test_without_the_marker_the_next_question_follows(self):
        step = self.step("salvo")
        response = self.post_kiosk(step, {f"q_{step.question.id}": "no"})
        _, following = questionnaire.neighbor_steps(self.version, load_answers(self.interview), step.number)
        self.assertRedirects(response, reverse("kiosk:step", args=[following.number]), fetch_redirect_response=False)

    def test_an_unknown_marker_is_ignored(self):
        step = self.step("salvo")
        response = self.post_kiosk(step, {f"q_{step.question.id}": "no"}, query="?volver=https://example.org/")
        _, following = questionnaire.neighbor_steps(self.version, load_answers(self.interview), step.number)
        self.assertRedirects(response, reverse("kiosk:step", args=[following.number]), fetch_redirect_response=False)

    def test_back_keeps_the_marker(self):
        self.answer_everything()
        step = self.step("dudas")
        response = self.post_kiosk(step, {f"q_{step.question.id}": "no"}, direction="back", query="?volver=revision")
        previous, _ = questionnaire.neighbor_steps(self.version, load_answers(self.interview), step.number)
        self.assertRedirects(response, reverse("kiosk:step", args=[previous.number]) + "?volver=revision", fetch_redirect_response=False)

    def test_back_from_the_first_question_returns_to_the_review(self):
        self.answer_everything()
        step = self.step("salvo")
        response = self.post_kiosk(step, {f"q_{step.question.id}": "no"}, direction="back", query="?volver=revision")
        self.assertRedirects(response, reverse("kiosk:review"), fetch_redirect_response=False)


class StaffChangeFromReviewTests(CampFixture):
    """UX-07 in the assisted interview: "Cambiar" → one question → back to the review."""

    def setUp(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        self.interview = Interview.objects.get(participant=self.p2)
        while (step := first_unanswered_step(self.version, load_answers(self.interview))) is not None:
            q = step.question
            value = q.options.all()[0].code if q.is_choice else "Texto de prueba"
            self.assertEqual(self.answer(self.interview, q.code, value).status_code, 302)

    def test_change_returns_to_the_review_after_one_step(self):
        step = self.step("salvo")
        change_url = self.url("interview_step", self.interview.public_id, step.number) + "?volver=revision"
        review = _Page(self.client.get(self.url("interview_review", self.interview.public_id)).content.decode())
        self.assertIn(change_url, [attrs.get("href") for tag, attrs in review.elements if tag == "a"])
        form = [attrs for tag, attrs in _Page(self.client.get(change_url).content.decode()).elements if tag == "form" and attrs.get("id") == "step-form"]
        self.assertEqual(form[0]["action"], change_url)
        self.interview.refresh_from_db()
        response = self.client.post(change_url, {"revision": self.interview.revision, "direction": "next", f"q_{step.question.id}": "no"})
        self.assertRedirects(response, self.url("interview_review", self.interview.public_id), fetch_redirect_response=False)
        self.assertEqual(load_answers(self.interview)[step.question.id].codes, ["no"])

    def test_without_the_marker_the_next_question_follows(self):
        step = self.step("salvo")
        response = self.answer(self.interview, "salvo", "no")
        _, following = questionnaire.neighbor_steps(self.version, load_answers(self.interview), step.number)
        self.assertRedirects(response, self.url("interview_step", self.interview.public_id, following.number), fetch_redirect_response=False)

    def test_an_unknown_marker_is_ignored(self):
        step = self.step("salvo")
        self.interview.refresh_from_db()
        url = self.url("interview_step", self.interview.public_id, step.number) + "?volver=//example.org"
        response = self.client.post(url, {"revision": self.interview.revision, "direction": "next", f"q_{step.question.id}": "no"})
        _, following = questionnaire.neighbor_steps(self.version, load_answers(self.interview), step.number)
        self.assertRedirects(response, self.url("interview_step", self.interview.public_id, following.number), fetch_redirect_response=False)

    def test_back_keeps_the_marker(self):
        step = self.step("dudas")
        self.interview.refresh_from_db()
        url = self.url("interview_step", self.interview.public_id, step.number) + "?volver=revision"
        response = self.client.post(url, {"revision": self.interview.revision, "direction": "back", f"q_{step.question.id}": "no"})
        previous, _ = questionnaire.neighbor_steps(self.version, load_answers(self.interview), step.number)
        target = self.url("interview_step", self.interview.public_id, previous.number) + "?volver=revision"
        self.assertRedirects(response, target, fetch_redirect_response=False)

    def test_back_from_the_first_question_returns_to_the_review(self):
        step = self.step("salvo")
        self.interview.refresh_from_db()
        url = self.url("interview_step", self.interview.public_id, step.number) + "?volver=revision"
        response = self.client.post(url, {"revision": self.interview.revision, "direction": "back", f"q_{step.question.id}": "no"})
        self.assertRedirects(response, self.url("interview_review", self.interview.public_id), fetch_redirect_response=False)

    def test_save_and_exit_still_leaves(self):
        step = self.step("salvo")
        self.interview.refresh_from_db()
        url = self.url("interview_step", self.interview.public_id, step.number) + "?volver=revision"
        response = self.client.post(url, {"revision": self.interview.revision, "direction": "exit", f"q_{step.question.id}": "no"})
        self.assertRedirects(response, self.url("coord_home"), fetch_redirect_response=False)


class OfflineNoticeTemplateTests(KioskFixture):
    """UX-05, template side: the step form carries a hidden notice for the offline guard in emmaus.js."""

    TEXT = "Sin conexión. Cuando vuelva la red, toca el botón otra vez. Lo que elegiste sigue aquí."

    def assert_notice_inside_the_step_form(self, response):
        html = response.content.decode()
        page = _Page(html)
        notices = [attrs for t, attrs, text in page.texts if "data-offline-notice" in attrs and text == self.TEXT]
        self.assertEqual(len(notices), 1)
        self.assertIn("hidden", notices[0])
        self.assertIn("notice--warning", notices[0]["class"].split())
        start, end = html.index('id="step-form"'), html.index("</form>", html.index('id="step-form"'))
        self.assertIn("data-offline-notice", html[start:end], "inside the form: the guard finds it from the submitted form")

    def test_kiosk_step(self):
        self.assert_notice_inside_the_step_form(self.client.get(reverse("kiosk:step", args=[1])))

    def test_assisted_step(self):
        staff = Client()
        self.login(self.coord1, staff)
        self.choose_station(staff)
        self.start(self.p2, client=staff)
        interview = Interview.objects.get(participant=self.p2)
        self.assert_notice_inside_the_step_form(staff.get(self.url("interview_step", interview.public_id, 1)))


class WhoReadsTheAnswersTests(KioskFixture):
    """LANG-01: the camper reads the rule of events/access.py can_view_answer, not "el equipo del campamento"."""

    SENTENCES = (
        "Tus respuestas las lee tu coordinador y, las generales, también el equipo del campamento.",
        "Las de temas personales, solo quien te entrevistó, tu coordinador y el equipo de cuidado.",
        "Las marcadas “Confidencial”, solo el equipo de cuidado autorizado.",
    )

    def assert_says_who_reads(self, response):
        for sentence in self.SENTENCES:
            self.assertContains(response, sentence, count=1)
        self.assertNotContains(response, "El equipo del campamento podrá leer tus respuestas")

    def test_welcome(self):
        self.assert_says_who_reads(self.client.get(reverse("kiosk:home")))

    def test_review(self):
        self.assert_says_who_reads(self.client.get(reverse("kiosk:review")))

    def test_the_sentence_matches_the_access_rule(self):
        """general → anyone who sees the camper; sensible → the assigned coordinator, the administrator who
        conducted the interview ("quien te entrevistó") or sensitive access; confidencial → sensitive access only
        (the coordinator only while capturing an assisted interview)."""
        from events.access import resolve_access

        Interview.objects.filter(pk=self.interview.pk).update(mode=Interview.Mode.ASSISTED)
        self.interview.refresh_from_db()
        general = Question(sensitivity=Question.Sensitivity.GENERAL)
        sensible = Question(sensitivity=Question.Sensitivity.SENSITIVE)
        confidencial = Question(sensitivity=Question.Sensitivity.CONFIDENTIAL)
        coordinator = resolve_access(self.coord1, self.event)
        tutor = resolve_access(self.tutor1, self.event)  # "el equipo del campamento"
        care = resolve_access(self.care, self.event)  # "el equipo de cuidado"
        rows = {
            "general": [a.can_view_answer(self.interview, general) for a in (coordinator, tutor, care)],
            "sensible": [a.can_view_answer(self.interview, sensible) for a in (coordinator, tutor, care)],
            "confidencial": [a.can_view_answer(self.interview, confidencial) for a in (coordinator, tutor, care)],
        }
        self.assertEqual(rows, {"general": [True, True, True], "sensible": [True, False, True], "confidencial": [False, False, True]})
        self.assertTrue(coordinator.can_view_answer(self.interview, confidencial, during_capture=True))
        # An administrator reads the personal answers of an interview they conducted, and only of that one.
        admin = resolve_access(self.admin, self.event)
        self.assertFalse(admin.can_view_answer(self.interview, sensible))
        Interview.objects.filter(pk=self.interview.pk).update(conducted_by=self.admin)
        self.interview.refresh_from_db()
        self.assertEqual([admin.can_view_answer(self.interview, q) for q in (general, sensible, confidencial)], [True, True, False])
