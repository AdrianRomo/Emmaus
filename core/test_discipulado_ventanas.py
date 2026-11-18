"""The discipleship screens on «Ventanas» (docs/v2/DESIGN_SYSTEM.md), served only where the church uses the community
module. The participant's side reads like «Mi iglesia»; the mentor's and the pastor's are rows, precise. Pastoral care
is drawn as risk and never softened; the notes' rule, the safety notice and the reassurance that no reflection is
required stay word for word."""

from __future__ import annotations

import re

from django.urls import reverse

from people import flags

from .models import FollowUpRequest, ParticipantQuestion, PastoralNote
from .tests import EmmausFixture

RISK = '<span class="status status--risk"><span class="status__mark" aria-hidden="true"></span>Requiere cuidado pastoral</span>'


def main_of(response) -> str:
    return re.search(r"<main\b.*?</main>", response.content.decode(), re.S).group(0)


def rows_without_lead(main: str) -> list[str]:
    return [row for row in re.findall(r'<li class="(row[^"]*)"', main) if "row--lead" not in row]


class DiscipleshipVentanasTests(EmmausFixture):
    def setUp(self):
        super().setUp()
        flags.set_enabled(self.church, "community", True)
        self.care = ParticipantQuestion.objects.create(
            church=self.church, participant=self.participant, question_text="Necesito hablar con un pastor.",
            status=ParticipantQuestion.Status.ESCALATED, sensitivity_level=ParticipantQuestion.Sensitivity.PASTORAL_CARE_REQUIRED,
        )
        self.open = ParticipantQuestion.objects.create(
            church=self.church, participant=self.participant, question_text="¿Cómo oro?",
        )
        self.follow_up = FollowUpRequest.objects.create(
            church=self.church, participant=self.participant, created_by=self.pastor, assigned_to=self.mentor, reason="Llamarle",
        )
        PastoralNote.objects.create(church=self.church, participant=self.participant, author=self.pastor, body="Nota.")

    def page(self, user, url):
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        self.assertContains(response, 'data-ui="2"')
        return main_of(response)

    def test_the_participant_home_names_the_place_and_raises_only_the_next_lesson(self):
        main = self.page(self.participant, reverse("participant_dashboard"))
        self.assertIn(">Mi camino</h1>", main)
        self.assertNotIn("Hola,", main)
        self.assertEqual(main.count("btn--primary"), 1)
        self.assertIn(f'href="{reverse("participant_lesson", args=[self.lesson.id])}">Empezar lección</a>', main)
        self.assertIn('aria-current="step"', main)
        self.assertEqual(rows_without_lead(main), [])

    def test_a_lesson_keeps_its_reflection_optional_and_one_filled_button(self):
        main = self.page(self.participant, reverse("participant_lesson", args=[self.lesson.id]))
        self.assertIn("No tienes que responder todas. Si prefieres no contestar alguna, déjala en blanco; puedes volver "
                      "cuando quieras.", main)
        self.assertIn(f'name="question_{self.reflection_question.id}"', main)
        self.assertEqual(main.count("btn--primary"), 1)

    def test_asking_keeps_who_reads_it_and_the_safety_notice(self):
        main = self.page(self.participant, reverse("ask_question"))
        self.assertIn("Tu pregunta la leerán líderes de confianza de tu iglesia que pueden acompañarte en persona.", main)
        self.assertIn('aria-label="Aviso de seguridad"', main)
        self.assertIn("911", main)
        self.assertEqual(main.count("btn--primary"), 1)

    def test_pastoral_care_is_drawn_as_risk_wherever_a_leader_meets_the_question(self):
        for user, url in (
            (self.pastor, reverse("escalations")),
            (self.pastor, reverse("questions_inbox")),
            (self.pastor, reverse("church_question_detail", args=[self.care.id])),
            (self.mentor, reverse("mentor_dashboard")),
            (self.mentor, reverse("participant_detail", args=[self.profile.id])),
        ):
            with self.subTest(url=url):
                main = self.page(user, url)
                self.assertIn(RISK, main)
                self.assertEqual(rows_without_lead(main), [])

    def test_the_mentor_raises_who_waited_longest_and_lists_it_once(self):
        main = self.page(self.mentor, reverse("mentor_dashboard"))
        self.assertIn('class="sign sign--lead"', main)
        self.assertEqual(main.count(f'href="{reverse("question_detail", args=[self.open.id])}"'), 1)
        self.assertEqual(main.count("btn--primary"), 1)

    def test_the_notes_keep_their_rule(self):
        self.assertIn("Confidencial. Los participantes nunca ven estas notas.", self.page(self.pastor, reverse("notes_list")))
        detail = self.page(self.pastor, reverse("participant_detail", args=[self.profile.id]))
        self.assertIn("Confidencial. El participante nunca ve estas notas.", detail)

    def test_a_follow_up_is_marked_as_attended_in_place(self):
        main = self.page(self.mentor, reverse("follow_up_list"))
        action = reverse("complete_follow_up", args=[self.follow_up.id])
        self.assertIn('<section id="lista-seguimientos"', main)
        self.assertIn(f'action="{action}" data-inline="lista-seguimientos"', main)
        response = self.client.post(action)
        self.assertRedirects(response, reverse("follow_up_list"), fetch_redirect_response=False)
        after = main_of(self.client.get(reverse("follow_up_list")))
        self.assertIn('<section id="lista-seguimientos"', after)  # the list stays, the row in its new state
        self.assertNotIn(f'action="{action}"', after)
        self.assertIn("Atendido", after)

    def test_with_the_module_off_the_screens_are_what_they_were(self):
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.participant)
        html = self.client.get(reverse("participant_dashboard")).content.decode()
        self.assertNotIn('data-ui="2"', html)
        self.assertIn("Hola, Pat.", html)
