"""Ronda "cada rol sabe qué hacer", package Coordinación + campero.

Each test reproduces something that was wrong before this package:
1. Mis camperos was sorted by room only: a campero with an interview half done sat among the ones not
   started and the finished ones. (test_inside_one_state_the_room_and_name_decide is a regression guard, not a
   defect: inside one state the order stays the room as text, so "10" comes before "2", as it did before.)
2. Mis camperos had one empty line for every case: "No encontramos camperos con esa búsqueda" with no search,
   and the administration (the view is open to it) was told about "tus camperos".
3. A Mis camperos row carried up to five data on one meta line.
4. Mi inicio never said that no computer had been chosen.
5. "Completadas hace poco" was on Mi inicio and not on Mi avance.
6. Deciding a follow-up from "Entrevistas pendientes" came back to the interview record, not to the list (UX-21);
   a decision that fails stays on the record and keeps the way back for the next try.
7. The kiosk welcome promised "Nadie más verá tus respuestas en esta pantalla." (LANG-27) and mixed who reads
   the answers into the promises.
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from events.models import EventMembership, EventParticipant, FollowUp, Interview, InterviewStatus, MembershipRole

from . import test_audit_flows
from .base import CampFixture, make_user

NO_STATION = "Todavía no eliges computadora."
RECENT_TITLE = "Completadas hace poco"


def give_interview(participant, status, *, decision="no_requerido", minutes_ago=10):
    """The campero's interview in that state, as the station leaves it."""
    now = timezone.now()
    interview = None
    if status != InterviewStatus.PENDING:
        done = status == InterviewStatus.COMPLETED
        interview = Interview.objects.create(
            event=participant.event,
            participant=participant,
            version=participant.event.questionnaire_version,
            status=Interview.Status.COMPLETED if done else Interview.Status.IN_PROGRESS,
            started_at=now - timedelta(minutes=minutes_ago + 10),
            last_activity_at=now - timedelta(minutes=minutes_ago),
            completed_at=now - timedelta(minutes=minutes_ago) if done else None,
            follow_up_decision=decision if done else "",
            conducted_by=participant.coordinator.user,
        )
    participant.interview_status = status
    participant.save(update_fields=["interview_status"])
    return interview


def results_section(html: str) -> str:
    match = re.search(r'<section class="results" aria-labelledby="lista">(.*?)</section>', html, re.DOTALL)
    assert match, "no results section"
    return match.group(1)


class CoordinatorUxFixture(CampFixture):
    def newcomer(self):
        """A coordinator of the event with no camperos at all."""
        user = make_user("nuevo@prueba.mx", "Nora", "Nueva")
        EventMembership.objects.create(event=self.event, user=user, role=MembershipRole.COORDINATOR)
        return user

    def html(self, response) -> str:
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


# ---- 1. Mis camperos: what needs action first ------------------------------------------------------------


class MisCamperosOrderTests(CoordinatorUxFixture):
    def test_an_interview_in_progress_comes_first_then_not_started_then_done_whatever_the_room(self):
        give_interview(self.p1, InterviewStatus.COMPLETED)  # room 1
        give_interview(self.p_minor_no_auth, InterviewStatus.IN_PROGRESS)  # room 5
        # p2 (room 2) has no interview yet
        self.login(self.coord1)
        listed = results_section(self.html(self.client.get(self.url("my_participants"))))
        names = [self.p_minor_no_auth.full_name, self.p2.full_name, self.p1.full_name]
        positions = [listed.index(f">{name}</a>") for name in names]
        self.assertEqual(positions, sorted(positions), "en curso, then sin entrevista, then completada")

    def test_inside_one_state_the_room_and_name_decide(self):
        """Regression guard: the room is text, so "10" < "2" < "9". Not the numeric order, not the names'."""
        for participant, room in ((self.p1, "2"), (self.p2, "9"), (self.p_minor_no_auth, "10")):
            participant.room = room
            participant.save(update_fields=["room"])
        self.login(self.coord1)
        listed = results_section(self.html(self.client.get(self.url("my_participants"))))  # all three sin entrevista
        names = [self.p_minor_no_auth.full_name, self.p1.full_name, self.p2.full_name]  # rooms "10", "2", "9"
        positions = [listed.index(f">{name}</a>") for name in names]
        self.assertEqual(positions, sorted(positions))


# ---- 2. Mis camperos: an honest empty state for each case ---------------------------------------------------


class MisCamperosEmptyStatesTests(CoordinatorUxFixture):
    def assert_one_empty_line(self, response, sentence_html, reason):
        self.assertEqual(response.context["empty_reason"], reason)
        self.assertContains(response, f'<p class="empty-line">{sentence_html}</p>', count=1, html=False)
        self.assertContains(response, 'class="empty-line"', count=1)

    def test_without_camperos_it_says_so_and_links_to_registering_the_first(self):
        self.login(self.newcomer())
        response = self.client.get(self.url("my_participants"))
        create_url = self.url("participant_create")
        self.assert_one_empty_line(
            response,
            f'Todavía no tienes camperos asignados. <a href="{create_url}">Registra al primero</a> o pide a la administración que te los asigne.',
            "sin_camperos",
        )
        self.assertNotContains(response, "No encontramos")

    def test_without_camperos_a_search_still_says_there_are_none(self):
        self.login(self.newcomer())
        response = self.client.get(self.url("my_participants"), {"q": "mar"})
        self.assertEqual(response.context["empty_reason"], "sin_camperos")
        self.assertNotContains(response, "No encontramos")

    def test_a_search_without_results(self):
        self.login(self.coord1)
        response = self.client.get(self.url("my_participants"), {"q": "zzz"})
        self.assert_one_empty_line(response, "No encontramos camperos con esa búsqueda. Prueba con su número o con menos letras.", "busqueda")

    def test_an_empty_tab_names_the_state_not_a_search(self):
        self.login(self.coord1)
        for state, sentence in (
            ("en_curso", "No tienes entrevistas en curso."),
            ("completada", "Todavía no completas ninguna entrevista."),
        ):
            with self.subTest(state):
                response = self.client.get(self.url("my_participants"), {"estado": state})
                self.assert_one_empty_line(response, sentence, "filtro")
                self.assertNotContains(response, "No encontramos")
                self.assertNotContains(response, "No hay entrevistas")
        for p in (self.p1, self.p2, self.p_minor_no_auth):
            give_interview(p, InterviewStatus.COMPLETED)
        response = self.client.get(self.url("my_participants"), {"estado": "pendiente"})
        self.assert_one_empty_line(response, "No queda ningún campero sin entrevista.", "filtro")

    def test_the_administration_is_told_about_the_event_without_camperos(self):
        EventParticipant.objects.filter(event=self.event).update(is_active=False)
        self.login(self.admin)
        response = self.client.get(self.url("my_participants"))
        self.assert_one_empty_line(
            response,
            f'Todavía no hay camperos en este evento. <a href="{self.url("participant_import")}">Importa la lista</a> '
            f'o <a href="{self.url("participant_create")}">registra al primero</a>.',
            "sin_camperos",
        )
        self.assertNotContains(response, "Todavía no tienes camperos")

    def test_the_administration_reads_an_empty_tab_in_impersonal_words(self):
        self.login(self.admin)
        for state, sentence in (
            ("en_curso", "No hay entrevistas en curso."),
            ("completada", "Todavía no hay entrevistas completadas."),
        ):
            with self.subTest(state):
                response = self.client.get(self.url("my_participants"), {"estado": state})
                self.assert_one_empty_line(response, sentence, "filtro")
                self.assertNotContains(response, "No tienes")
                self.assertNotContains(response, "completas")
        for p in EventParticipant.objects.filter(event=self.event):
            give_interview(p, InterviewStatus.COMPLETED)
        response = self.client.get(self.url("my_participants"), {"estado": "pendiente"})
        self.assert_one_empty_line(response, "No queda ningún campero sin entrevista.", "filtro")

    def test_a_search_with_results_shows_no_empty_state(self):
        self.login(self.coord1)
        response = self.client.get(self.url("my_participants"), {"q": "mar"})
        self.assertEqual(response.context["empty_reason"], "")
        self.assertContains(response, self.p1.full_name)
        self.assertNotContains(response, 'class="empty-line"')


# ---- 3. Mis camperos: two meta lines, at most three data on the first -----------------------------------------


class MisCamperosRowTests(CoordinatorUxFixture):
    def test_every_row_has_two_meta_lines_and_the_first_has_three_data_at_most(self):
        self.login(self.coord1)
        listed = results_section(self.html(self.client.get(self.url("my_participants"))))
        rows = re.findall(r'<li class="row">(.*?)</li>', listed, re.DOTALL)
        self.assertEqual(len(rows), 3)
        for row in rows:
            metas = re.findall(r'<p class="row__meta">(.*?)</p>', row, re.DOTALL)
            self.assertEqual(len(metas), 2, row)
            self.assertLessEqual(len(metas[0].split(", ")), 3, metas[0])
        self.assertIn('<p class="row__meta">Cuarto 1, 15 años</p>', listed)
        self.assertIn(f'<p class="row__meta">#{self.p1.code}, Iglesia Centro</p>', listed)


# ---- 4. Mi inicio: say when no computer is chosen ------------------------------------------------------------


class CoordinatorHomeStationTests(CoordinatorUxFixture):
    def test_without_a_computer_it_says_so_and_links_to_choosing_one(self):
        self.login(self.coord1)
        response = self.client.get(self.url("coord_home"))
        self.assertContains(response, NO_STATION, count=1)
        href = f'{self.url("choose_station")}?siguiente={self.url("coord_home")}'
        self.assertContains(response, f'<a href="{href}">Elegir computadora</a>', count=1, html=False)

    def test_with_a_computer_chosen_it_says_nothing(self):
        self.login(self.coord1)
        station = self.choose_station(number=3)
        response = self.client.get(self.url("coord_home"))
        self.assertNotContains(response, NO_STATION)
        self.assertContains(response, f"Estás en <strong>{station.name}</strong>")  # the bar says it

    def test_an_event_without_active_computers_needs_none(self):
        self.event.stations.update(is_active=False)
        self.login(self.coord1)
        self.assertNotContains(self.client.get(self.url("coord_home")), NO_STATION)


# ---- 5. "Completadas hace poco" lives in Mi avance -------------------------------------------------------------


class RecentlyCompletedTests(CoordinatorUxFixture):
    def test_mi_avance_lists_the_completed_interviews_page_and_fragment(self):
        interview = give_interview(self.p1, InterviewStatus.COMPLETED)
        self.login(self.coord1)
        link = f'<a class="row__link" href="{self.url("interview_detail", interview.public_id)}">{self.p1.full_name}</a>'
        for response in (
            self.client.get(self.url("coord_progress")),
            self.client.get(self.url("coord_progress"), HTTP_X_REQUESTED_WITH="fetch"),
        ):
            self.assertContains(response, f'<h2 id="recientes" class="heading">{RECENT_TITLE}</h2>', count=1, html=False)
            self.assertContains(response, link, count=1, html=False)
            self.assertNotIn("recent", response.context["payload"])  # not stored in report snapshots

    def test_mi_avance_without_completed_interviews_says_so(self):
        self.login(self.coord1)
        self.assertContains(self.client.get(self.url("coord_progress")), '<p class="empty-line">Aún no completas entrevistas.</p>', html=False)

    def test_mi_inicio_no_longer_lists_them(self):
        give_interview(self.p1, InterviewStatus.COMPLETED)
        self.login(self.coord1)
        for response in (
            self.client.get(self.url("coord_home")),
            self.client.get(self.url("coord_home"), HTTP_X_REQUESTED_WITH="fetch"),
        ):
            self.assertNotContains(response, RECENT_TITLE)
            self.assertNotContains(response, 'id="recientes"')


class CoordinatorHomeWaitsForYouTests(CoordinatorUxFixture):
    def test_nothing_waiting_says_so_in_one_line(self):
        self.login(self.coord1)
        for response in (
            self.client.get(self.url("coord_home")),
            self.client.get(self.url("coord_home"), HTTP_X_REQUESTED_WITH="fetch"),
        ):
            self.assertContains(response, '<h2 id="seguimientos" class="heading">Esperan algo de ti</h2>', count=1, html=False)
            self.assertContains(response, '<p class="empty-line">Nada espera tu decisión por ahora.</p>', count=1, html=False)

    def test_a_decision_and_a_follow_up_wait_and_the_follow_up_link_returns_home(self):
        give_interview(self.p1, InterviewStatus.COMPLETED, decision="por_evaluar")
        follow_up = FollowUp.objects.create(event=self.event, participant=self.p2, kind=FollowUp.Kind.CARE)
        self.login(self.coord1)
        for response in (
            self.client.get(self.url("coord_home")),
            self.client.get(self.url("coord_home"), HTTP_X_REQUESTED_WITH="fetch"),
        ):
            self.assertContains(response, "1 entrevista espera tu decisión")
            self.assertContains(response, f'href="{self.url("coord_pending")}#por-evaluar">Decidir</a>')
            href = f'{self.url("follow_up_detail", follow_up.id)}?siguiente={self.url("coord_home")}'
            self.assertContains(response, f'<a class="row__link" href="{href}">{self.p2.full_name}</a>', count=1, html=False)
            self.assertNotContains(response, "Nada espera tu decisión")


# ---- 6. Deciding a follow-up returns to where it was opened (UX-21) ---------------------------------------------


class DecisionReturnsToOriginTests(CoordinatorUxFixture):
    def setUp(self):
        self.interview = give_interview(self.p1, InterviewStatus.COMPLETED, decision="por_evaluar")
        self.detail = self.url("interview_detail", self.interview.public_id)
        self.pending = self.url("coord_pending")
        self.login(self.coord1)

    def decide(self, siguiente=None, query=""):
        data = {"action": "decision", "decision": "no_requerido"}
        if siguiente is not None:
            data["siguiente"] = siguiente
        return self.client.post(self.detail + query, data)

    def test_decidir_on_pendientes_carries_the_way_back(self):
        response = self.client.get(self.pending)
        self.assertContains(response, f'href="{self.detail}?siguiente={self.pending}#seguimiento">Decidir', count=1)

    def test_the_record_keeps_the_way_back_in_the_decision_form(self):
        response = self.client.get(self.detail, {"siguiente": self.pending})
        self.assertContains(response, f'<input type="hidden" name="siguiente" value="{self.pending}">', count=1, html=False)

    def test_saving_returns_to_pendientes(self):
        self.assertRedirects(self.decide(self.pending), self.pending, fetch_redirect_response=False)
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.follow_up_decision, "no_requerido")

    def test_the_way_back_in_the_address_counts_too(self):
        self.assertRedirects(self.decide(query=f"?siguiente={self.pending}"), self.pending, fetch_redirect_response=False)

    def test_a_foreign_way_back_falls_back_to_the_record(self):
        for foreign in ("https://evil.example/", "//evil.example/robar", "/\\evil.example/robar", "javascript:alert(1)"):
            with self.subTest(foreign):
                Interview.objects.filter(pk=self.interview.pk).update(follow_up_decision="por_evaluar")
                self.assertRedirects(self.decide(foreign), self.detail, fetch_redirect_response=False)

    def test_without_a_way_back_it_stays_on_the_record(self):
        self.assertRedirects(self.decide(), self.detail, fetch_redirect_response=False)

    def test_a_decision_that_fails_stays_on_the_record_and_keeps_the_way_back(self):
        Interview.objects.filter(pk=self.interview.pk).update(follow_up_decision="no_requerido")
        response = self.decide(self.pending)
        self.assertRedirects(response, f"{self.detail}?{urlencode({'siguiente': self.pending})}", fetch_redirect_response=False)

    def test_after_a_failed_try_the_next_one_still_returns_to_pendientes(self):
        failed = self.client.post(self.detail, {"action": "decision", "decision": "tal_vez", "siguiente": self.pending})
        self.assertEqual(failed.status_code, 302)
        record = self.client.get(failed["Location"])  # the form is shown again, with the way back
        self.assertContains(record, f'<input type="hidden" name="siguiente" value="{self.pending}">', count=1, html=False)
        self.assertRedirects(self.decide(self.pending), self.pending, fetch_redirect_response=False)

    def test_a_failed_decision_drops_a_foreign_or_missing_way_back(self):
        for foreign in (None, "", "https://evil.example/", "javascript:alert(1)"):
            with self.subTest(foreign):
                Interview.objects.filter(pk=self.interview.pk).update(follow_up_decision="no_requerido")
                self.assertRedirects(self.decide(foreign), self.detail, fetch_redirect_response=False)

    def test_the_follow_up_links_on_the_record_carry_the_way_back(self):
        follow_up = FollowUp.objects.create(event=self.event, participant=self.p1, kind=FollowUp.Kind.CARE)
        record = self.url("participant_detail", self.p1.public_id)
        href = f'{self.url("follow_up_detail", follow_up.id)}?siguiente={record}'
        self.assertContains(self.client.get(record), f'<a class="row__link" href="{href}">', count=1, html=False)


# ---- 7. Kiosk welcome: three promises, then who reads the answers ---------------------------------------------


class KioskWelcomeTests(test_audit_flows.KioskFixture):
    SENTENCES = test_audit_flows.WhoReadsTheAnswersTests.SENTENCES

    def test_who_reads_has_its_own_heading_and_the_false_promise_is_gone(self):
        response = self.client.get(reverse("kiosk:home"))
        self.assertContains(response, "¿Quién lee tus respuestas?", count=1)
        self.assertContains(response, '<section class="welcome__who" aria-labelledby="quien-lee">', count=1, html=False)
        self.assertContains(response, '<h2 id="quien-lee" class="welcome__who-title">', count=1, html=False)
        self.assertNotContains(response, "Nadie más verá")

    def test_the_promises_are_three_at_most_and_none_is_about_who_reads(self):
        html = self.client.get(reverse("kiosk:home")).content.decode()
        promises = re.search(r'<ul class="promises">(.*?)</ul>', html, re.DOTALL).group(1)
        self.assertLessEqual(promises.count("<li>"), 3)
        self.assertIn("<li>Si quieres parar, toca “Parar por ahora” en cualquier momento.</li>", promises)
        self.assertNotIn("guardad", promises)  # the kiosk footer already says the answers are saved
        for sentence in self.SENTENCES:
            self.assertNotIn(sentence, promises)

    def test_welcome_and_review_say_each_sentence_once(self):
        for name, css in (("kiosk:home", "who-reads"), ("kiosk:review", "aside-note")):
            response = self.client.get(reverse(name))
            with self.subTest(name):
                for sentence in self.SENTENCES:
                    self.assertContains(response, sentence, count=1)
                self.assertContains(response, f'<p class="{css}">{" ".join(self.SENTENCES)}</p>', count=1, html=False)


# ---- Integration seams (2026-10-27): what the merged packages must agree on -------------------------------


class HandoffChecklistTests(CampFixture):
    """The coordinator's hand-over checklist must not restate the promise the kiosk welcome dropped (LANG-27)."""

    def test_the_checklist_points_at_the_welcome_instead_of_promising_privacy(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        response = self.client.get(self.url("interview_handoff", interview.public_id))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "nadie más verá")
        self.assertContains(response, "la pantalla de bienvenida le explica quién lee sus respuestas")


class KioskStepPromiseTests(test_audit_flows.KioskFixture):
    """Each kiosk question keeps "Prefiero no responder" and drops the physical promise the system cannot keep (LANG-27)."""

    def test_the_question_page_does_not_promise_that_nobody_else_sees_the_screen(self):
        response = self.client.get(reverse("kiosk:step", args=[1]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Puedes elegir “Prefiero no responder” en cualquier pregunta.")
        self.assertNotContains(response, "Nadie más ve esta pantalla")
