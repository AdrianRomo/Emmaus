"""Several questionnaires per event (EventQuestionnaire): links, order, start/finalize/discard, derived status.

Each test names the defect it would catch.
"""

from __future__ import annotations

from importlib import import_module
from unittest import mock

from django.apps import apps as django_apps
from django.db import IntegrityError, migrations, transaction
from django.db.models.query import QuerySet
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Church
from events.access import resolve_access
from events.forms import InterviewStartForm
from events.models import Event, EventParticipant, EventQuestionnaire, Interview, QuestionnaireVersion
from events.services import interviews as svc
from events.services import questionnaire
from events.services.event_questionnaires import (
    annotate_questionnaire_progress,
    derive_interview_status,
    links_for,
    move,
    pending_links,
    progress_for,
    refresh_interview_statuses,
    set_in_order,
    stop_using,
    use_version,
)
from events.services.questionnaire import (
    AnswerValue,
    all_questions,
    create_questionnaire,
    create_questionnaire_from_definition,
    duplicate_version,
    publish_version,
    write_answers,
)

from .base import CampFixture

NO_QUESTIONNAIRE = "Todavía no hay un cuestionario publicado para este evento. Avisa a la administración."
LAST_QUESTIONNAIRE = (
    "No se puede quitar el único cuestionario mientras el evento tenga entrevistas. Agrega otro primero o usa otra versión."
)
# The module name starts with a digit: import it by name to call its functions without running migrations.
MIGRATION_0011 = import_module("events.migrations.0011_event_questionnaires")


def definition(name, title, code):
    """A published questionnaire of one screen whose only question is required."""
    return {
        "name": name,
        "title": title,
        "sections": [
            {
                "title": f"Sección de {name}",
                "questions": [
                    {
                        "code": code,
                        "text": f"¿Pregunta de {name}?",
                        "kind": "unica",
                        "required": True,
                        "options": [{"code": "si", "label": "Sí"}, {"code": "no", "label": "No"}],
                    }
                ],
            }
        ],
    }


class IsolatedCacheMixin:
    def setUp(self):
        super().setUp()
        # build_steps caches published structures by version id, and SQLite reuses ids after a rollback.
        patcher = mock.patch.dict(questionnaire._structure_cache, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class ServiceHelpers:
    def begin(self, participant=None, questionnaire=None):
        return svc.start_interview(
            resolve_access(self.coord1, self.event),
            participant or self.p2,
            mode="asistida",
            station=None,
            confirmed_consents={"consent"},
            session=self.session,
            questionnaire=questionnaire,
        )

    def answer_required(self, interview):
        for question in all_questions(interview.version):
            if question.required:
                write_answers(interview, {question.id: AnswerValue(codes=["si"])}, actor=self.coord1, by_participant=False)

    def finish(self, interview):
        interview.refresh_from_db()
        return svc.finalize(
            interview,
            token=svc.session_claim_token(self.session, interview),
            revision=interview.revision,
            decision="no_requerido",
            actor=self.coord1,
        )

    def complete(self, interview):
        self.answer_required(interview)
        return self.finish(interview)

    def status(self, participant=None):
        return EventParticipant.objects.get(pk=(participant or self.p2).pk).interview_status

    def fresh(self, participant=None):
        return EventParticipant.objects.get(pk=(participant or self.p2).pk)

    def progress_row(self, participant=None):
        participants = EventParticipant.objects.filter(pk=(participant or self.p2).pk)
        return annotate_questionnaire_progress(participants, self.event).get()


class TwoQuestionnairesFixture(IsolatedCacheMixin, ServiceHelpers, CampFixture):
    """Event A uses "Primer cuestionario" then "Segundo cuestionario" (the fixture's questionnaire is replaced)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.first = create_questionnaire_from_definition(cls.church, definition("Primero", "Primer cuestionario", "uno"))
        cls.second = create_questionnaire_from_definition(cls.church, definition("Segundo", "Segundo cuestionario", "dos"))
        cls.event.questionnaire_links.all().delete()
        use_version(cls.event, cls.first)
        use_version(cls.event, cls.second)
        cls.q1, cls.q2 = cls.first.questionnaire, cls.second.questionnaire

    def setUp(self):
        super().setUp()
        self.session = {}


# ---- 1. Links: append, swap in place, remove, reorder --------------------------------------------------


class LinkManagementTests(IsolatedCacheMixin, CampFixture):
    def orders(self):
        return [(link.version, link.order) for link in links_for(self.event)]

    def test_another_questionnaire_is_appended_and_a_new_version_takes_the_old_ones_place(self):
        """Defect: "Usar" on a second questionnaire replacing the first (the old single field), or a new version of
        the same questionnaire added next to the old one instead of in its place."""
        other = create_questionnaire_from_definition(self.church, definition("Segundo", "Segundo cuestionario", "dos"))
        link = use_version(self.event, other)
        self.assertEqual(link.order, 2)
        self.assertEqual(self.orders(), [(self.version, 1), (other, 2)])
        draft = duplicate_version(self.version, self.admin)
        publish_version(draft, self.admin)
        swapped = use_version(self.event, draft)
        self.assertEqual((swapped.version, swapped.order), (draft, 1))
        self.assertEqual(self.orders(), [(draft, 1), (other, 2)])
        self.assertEqual(self.event.questionnaire_versions(), [draft, other])

    def test_a_draft_cannot_be_used(self):
        """Defect: an event pointing at a version that can still change under the interviews."""
        draft = duplicate_version(self.version, self.admin)
        with self.assertRaisesMessage(ValueError, "Solo se puede usar una versión publicada."):
            use_version(self.event, draft)
        self.assertEqual(self.orders(), [(self.version, 1)])

    def test_stop_using_renumbers_and_move_swaps_with_the_neighbour(self):
        """Defect: a gap in the order after "Quitar", or "Subir"/"Bajar" falling off either end."""
        second = create_questionnaire_from_definition(self.church, definition("Segundo", "Segundo cuestionario", "dos"))
        third = create_questionnaire_from_definition(self.church, definition("Tercero", "Tercer cuestionario", "tres"))
        use_version(self.event, second)
        use_version(self.event, third)
        move(self.event, third.questionnaire, "subir")
        self.assertEqual(self.orders(), [(self.version, 1), (third, 2), (second, 3)])
        move(self.event, self.version.questionnaire, "subir")  # already first
        move(self.event, second.questionnaire, "bajar")  # already last
        self.assertEqual(self.orders(), [(self.version, 1), (third, 2), (second, 3)])
        move(self.event, self.version.questionnaire, "bajar")
        self.assertEqual(self.orders(), [(third, 1), (self.version, 2), (second, 3)])
        stop_using(self.event, self.version.questionnaire)
        self.assertEqual(self.orders(), [(third, 1), (second, 2)])
        stop_using(self.event, self.version.questionnaire)  # no longer used: nothing happens
        self.assertEqual(self.orders(), [(third, 1), (second, 2)])

    def test_two_versions_of_one_questionnaire_are_refused_by_the_database(self):
        """Defect: the denormalised questionnaire drifting from the version, which would defeat the constraint."""
        draft = duplicate_version(self.version, self.admin)
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventQuestionnaire.objects.create(event=self.event, version=draft, order=2)
        other = create_questionnaire_from_definition(self.church, definition("Segundo", "Segundo cuestionario", "dos"))
        with self.assertRaises(ValueError):
            EventQuestionnaire(event=self.event, questionnaire=self.version.questionnaire, version=other).save()


# ---- 2. Free order --------------------------------------------------------------------------------------


class FreeOrderTests(TwoQuestionnairesFixture):
    def test_the_second_can_go_first_and_the_campero_is_complete_only_after_both(self):
        """Defect: finalize marking the campero "completada" after ONE of two questionnaires (the old unconditional
        write), or start ignoring the questionnaire asked for."""
        interview = self.begin(questionnaire=self.q2)
        self.assertEqual((interview.questionnaire, interview.version), (self.q2, self.second))
        self.assertEqual(self.status(), "en_curso")
        with self.assertRaisesMessage(svc.InterviewError, "Falta responder"):
            self.finish(interview)  # the required question is real
        self.complete(interview)
        self.assertEqual(self.status(), "pendiente")
        with self.assertRaisesMessage(svc.QuestionnaireNotAvailable, "Ese cuestionario ya se respondió."):
            self.begin(questionnaire=self.q2)
        first = self.begin()  # no choice: the next one the campero can answer
        self.assertEqual((first.questionnaire, first.version), (self.q1, self.first))
        self.complete(first)
        self.assertEqual(self.status(), "completada")
        with self.assertRaisesMessage(svc.AlreadyCompleted, "Este campero ya tiene una entrevista completada."):
            self.begin()

    def test_a_questionnaire_the_event_does_not_use_is_refused(self):
        """Defect: starting an interview on any questionnaire of the church, used by the event or not."""
        with self.assertRaisesMessage(svc.QuestionnaireNotAvailable, "Ese cuestionario no está en uso en este evento."):
            self.begin(questionnaire=self.version.questionnaire)  # the fixture's, replaced in this event
        self.assertFalse(Interview.objects.filter(participant=self.p2).exists())

    def test_both_are_pending_with_no_progress(self):
        """Defect: free order reporting the second questionnaire as waiting for the first."""
        self.assertEqual([p.state for p in progress_for(self.fresh())], ["pendiente", "pendiente"])
        self.assertEqual([link.questionnaire for link in pending_links(self.fresh())], [self.q1, self.q2])


# ---- 3. In order ----------------------------------------------------------------------------------------


class InOrderTests(TwoQuestionnairesFixture):
    def setUp(self):
        super().setUp()
        set_in_order(self.event, True)

    def test_the_second_opens_when_the_first_is_complete(self):
        """Defect: "Los camperos los responden en este orden" not enforced by the service (only by the screens)."""
        with self.assertRaisesMessage(svc.QuestionnaireNotAvailable, "Primero hay que completar «Primer cuestionario»."):
            self.begin(questionnaire=self.q2)
        self.assertFalse(Interview.objects.filter(participant=self.p2).exists())
        self.assertEqual([link.questionnaire for link in pending_links(self.fresh())], [self.q1])
        self.assertEqual([p.state for p in progress_for(self.fresh())], ["pendiente", "bloqueado"])
        first = self.begin()
        self.assertEqual(first.questionnaire, self.q1)
        self.assertEqual([p.state for p in progress_for(self.fresh())], ["en_curso", "bloqueado"])
        self.complete(first)
        self.assertEqual([link.questionnaire for link in pending_links(self.fresh())], [self.q2])
        self.assertEqual(self.begin(questionnaire=self.q2).questionnaire, self.q2)


# ---- 4. One live interview per campero; the database allows one per questionnaire ------------------------


class LiveInterviewTests(TwoQuestionnairesFixture):
    def test_a_live_interview_blocks_starting_the_other_questionnaire(self):
        """Defect: two live interviews of one campero, each on its own computer."""
        live = self.begin(questionnaire=self.q1)
        with self.assertRaisesMessage(svc.AlreadyInProgress, "Este campero ya tiene una entrevista en curso.") as raised:
            self.begin(questionnaire=self.q2)
        self.assertEqual(raised.exception.interview, live)
        self.assertEqual(Interview.objects.filter(participant=self.p2).count(), 1)

    def test_the_database_allows_a_done_first_and_a_live_second_but_not_two_live_of_one(self):
        """Defect: the old one-live-interview-per-campero index refusing the second questionnaire, or the new
        index not refusing a duplicate of the same questionnaire."""
        self.complete(self.begin(questionnaire=self.q1))
        second = Interview.objects.create(event=self.event, participant=self.p2, version=self.second)
        self.assertEqual(second.questionnaire, self.q2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Interview.objects.create(event=self.event, participant=self.p2, version=self.second)


# ---- 5. Discard ------------------------------------------------------------------------------------------


class DiscardTests(TwoQuestionnairesFixture):
    def test_discarding_the_second_keeps_the_first_done(self):
        """Defect: discard forgetting the questionnaire already answered, or leaving the campero "en curso"."""
        self.complete(self.begin(questionnaire=self.q1))
        live = self.begin(questionnaire=self.q2)
        self.assertEqual(self.status(), "en_curso")
        svc.discard(live, actor=self.coord1, reason="repetir")
        self.assertEqual(self.status(), "pendiente")
        row = self.progress_row()
        self.assertEqual((row.done_count, row.questionnaire_total), (1, 2))
        self.assertEqual([link.questionnaire for link in pending_links(self.fresh())], [self.q2])

    def test_discarding_an_interview_of_a_removed_questionnaire_leaves_the_campero_complete(self):
        """Defect: discard writing "pendiente" unconditionally (the old code) when every questionnaire the event
        still uses is answered."""
        self.complete(self.begin(questionnaire=self.q1))
        live = self.begin(questionnaire=self.q2)
        stop_using(self.event, self.q2)
        self.assertEqual(self.status(), "en_curso")  # still being interviewed
        svc.discard(live, actor=self.coord1, reason="repetir")
        self.assertEqual(self.status(), "completada")

    def test_discarding_the_only_interview_leaves_pendiente(self):
        """Defect: a discarded interview counted as progress."""
        svc.discard(self.begin(), actor=self.coord1, reason="repetir")
        self.assertEqual(self.status(), "pendiente")
        self.assertEqual(self.progress_row().done_count, 0)


# ---- 6. Changing the event's questionnaires recomputes every campero --------------------------------------


class RefreshTests(IsolatedCacheMixin, ServiceHelpers, CampFixture):
    def setUp(self):
        super().setUp()
        self.session = {}

    def test_a_second_questionnaire_reopens_a_finished_campero_and_removing_it_closes_them_again(self):
        """Defect: a campero shown "Completada" while a questionnaire of the event is still unanswered (or the
        other way round after it is removed)."""
        self.complete(self.begin())
        self.assertEqual(self.status(), "completada")
        other = create_questionnaire_from_definition(self.church, definition("Segundo", "Segundo cuestionario", "dos"))
        use_version(self.event, other)
        self.assertEqual(self.status(), "pendiente")
        row = self.progress_row()
        self.assertEqual((row.done_count, row.questionnaire_total), (1, 2))
        stop_using(self.event, other.questionnaire)
        self.assertEqual(self.status(), "completada")
        row = self.progress_row()
        self.assertEqual((row.done_count, row.questionnaire_total), (1, 1))
        self.assertEqual(refresh_interview_statuses(self.event), 0)  # saves only what changes

    def test_a_live_interview_stays_en_curso_through_a_change(self):
        """Defect: the bulk recompute dropping a campero who is being interviewed out of every "in progress" list."""
        self.begin()
        other = create_questionnaire_from_definition(self.church, definition("Segundo", "Segundo cuestionario", "dos"))
        use_version(self.event, other)
        self.assertEqual(self.status(), "en_curso")


# ---- 7. The start form ------------------------------------------------------------------------------------


class StartFormTests(SimpleTestCase):
    MODE = {"mode": "asistida"}

    def test_one_questionnaire_adds_no_field(self):
        """Defect: a one-questionnaire event asking "¿Qué cuestionario?" (the screen must look as before)."""
        for choices in ((), [("5", "Conversación espiritual")]):
            form = InterviewStartForm(self.MODE, requirements=[], questionnaire_choices=choices)
            self.assertNotIn("questionnaire", form.fields)
            self.assertTrue(form.is_valid(), form.errors)
        form =InterviewStartForm(self.MODE, requirements=[], questionnaire_choices=[("5", "Conversación espiritual")])
        form.is_valid()
        self.assertEqual(form.chosen_questionnaire_id(), 5)
        form = InterviewStartForm(self.MODE, requirements=[])
        form.is_valid()
        self.assertIsNone(form.chosen_questionnaire_id())

    def test_two_choices_add_a_radio_with_the_first_chosen(self):
        """Defect: no way to pick the questionnaire in free order, or nothing preselected."""
        choices = [("5", "Primer cuestionario"), ("7", "Segundo cuestionario")]
        form = InterviewStartForm(requirements=[], questionnaire_choices=choices)
        field = form.fields["questionnaire"]
        self.assertEqual(field.label, "¿Qué cuestionario?")
        self.assertEqual(type(field.widget).__name__, "RadioSelect")
        self.assertEqual(list(field.choices), choices)
        self.assertEqual(form["questionnaire"].value(), "5")
        posted = InterviewStartForm({**self.MODE, "questionnaire": "7"}, requirements=[], questionnaire_choices=choices)
        self.assertTrue(posted.is_valid(), posted.errors)
        self.assertEqual(posted.chosen_questionnaire_id(), 7)
        missing = InterviewStartForm(self.MODE, requirements=[], questionnaire_choices=choices)
        self.assertFalse(missing.is_valid())
        self.assertEqual(missing.errors["questionnaire"], ["Elige el cuestionario."])


# ---- 8. Interview.questionnaire, an event without questionnaires --------------------------------------------


class ModelTests(CampFixture):
    def test_an_interview_takes_its_questionnaire_from_its_version(self):
        """Defect: Interview rows written without a questionnaire (code that only sets the version)."""
        interview = Interview.objects.create(event=self.event, participant=self.p3, version=self.version)
        self.assertEqual(interview.questionnaire_id, self.version.questionnaire_id)
        self.assertEqual(Interview.objects.get(pk=interview.pk).questionnaire, self.version.questionnaire)

    def test_an_interview_refuses_a_version_of_another_questionnaire(self):
        """Defect: an interview counted for one questionnaire while its answers follow another one's version."""
        other = create_questionnaire_from_definition(self.church, definition("Segundo", "Segundo cuestionario", "dos"))
        with self.assertRaisesMessage(ValueError, "La versión no pertenece a ese cuestionario."):
            Interview.objects.create(
                event=self.event, participant=self.p3, version=self.version, questionnaire=other.questionnaire
            )
        self.assertFalse(Interview.objects.filter(participant=self.p3).exists())

    def test_an_event_without_questionnaires_is_not_open(self):
        """Defect: interviews starting in an event that uses no questionnaire."""
        self.assertTrue(self.event.is_open_for_interviews)
        self.event.questionnaire_links.all().delete()
        event = Event.objects.get(pk=self.event.pk)
        self.assertFalse(event.has_questionnaire)
        self.assertFalse(event.is_open_for_interviews)
        self.assertEqual(svc.event_blockers(event), [NO_QUESTIONNAIRE])

    def test_creating_an_event_uses_the_churchs_questionnaire(self):
        """Defect: "Nuevo evento" losing the questionnaire it picks (set before the event has a primary key)."""
        self.client.force_login(self.admin)
        data = {"name": "Retiro de Jóvenes", "kind": "campamento", "slug": "", "status": "activo", "location": "", "starts_on": "", "ends_on": ""}
        self.client.post(reverse("events:create"), data)
        event = Event.objects.get(name="Retiro de Jóvenes")
        self.assertEqual(event.questionnaire_versions(), [self.version])

    def test_using_a_version_from_its_page_adds_it_to_the_event(self):
        """Defect: "Usar en este evento" failing once the event has no questionnaire column."""
        other = create_questionnaire_from_definition(self.church, definition("Segundo", "Segundo cuestionario", "dos"))
        self.client.force_login(self.admin)
        response = self.client.post(self.url("version_detail", other.id), {"action": "use"})
        self.assertRedirects(response, self.url("version_detail", other.id), fetch_redirect_response=False)
        self.assertEqual(Event.objects.get(pk=self.event.pk).questionnaire_versions(), [self.version, other])


# ---- 9. "Nuevo cuestionario" -------------------------------------------------------------------------------


class CreateQuestionnaireTests(CampFixture):
    def test_a_new_questionnaire_is_an_empty_draft_named_after_itself(self):
        """Defect: a new questionnaire published empty, or with no title for the camperos."""
        version = create_questionnaire(self.church, name="Seguimiento del domingo", actor=self.admin)
        self.assertEqual((version.number, version.status), (1, QuestionnaireVersion.Status.DRAFT))
        self.assertEqual(version.title, "Seguimiento del domingo")
        self.assertEqual(version.questionnaire.name, "Seguimiento del domingo")
        self.assertEqual(version.questionnaire.church, self.church)
        self.assertEqual(version.created_by, self.admin)
        self.assertFalse(version.sections.exists())
        titled = create_questionnaire(self.church, name="Interno", title="Para ti", intro_text="Hola.")
        self.assertEqual((titled.title, titled.intro_text), ("Para ti", "Hola."))


# ---- 10. Links: what the services refuse -------------------------------------------------------------------


class LinkRefusalTests(IsolatedCacheMixin, CampFixture):
    def orders(self):
        return [(link.version, link.order) for link in links_for(self.event)]

    def test_the_only_questionnaire_stays_while_the_event_has_interviews(self):
        """Defect: "Quitar" on the event's only questionnaire turning every finished campero "pendiente" and
        closing interviews in the middle of the camp."""
        Interview.objects.create(event=self.event, participant=self.p2, version=self.version)
        with self.assertRaisesMessage(ValueError, LAST_QUESTIONNAIRE):
            stop_using(self.event, self.version.questionnaire)
        self.assertEqual(self.orders(), [(self.version, 1)])
        self.assertEqual(Event.objects.get(pk=self.event.pk).questionnaire_versions(), [self.version])

    def test_the_only_questionnaire_of_an_event_without_interviews_can_be_removed(self):
        """Defect: the refusal above also locking an event that never interviewed anyone (a discarded interview
        is not one)."""
        Interview.objects.create(
            event=self.event, participant=self.p2, version=self.version, status=Interview.Status.DISCARDED
        )
        stop_using(self.event, self.version.questionnaire)
        self.assertEqual(self.orders(), [])
        self.assertFalse(Event.objects.get(pk=self.event.pk).has_questionnaire)

    def test_another_churchs_questionnaire_cannot_be_used(self):
        """Defect: an event of one church running (and reporting on) a questionnaire of another church."""
        other_church = Church.objects.create(name="Otra iglesia", slug="otra-iglesia")
        foreign = create_questionnaire_from_definition(other_church, definition("Ajeno", "Cuestionario ajeno", "ajeno"))
        with self.assertRaisesMessage(ValueError, "Ese cuestionario es de otra iglesia."):
            use_version(self.event, foreign)
        self.assertEqual(self.orders(), [(self.version, 1)])

    def test_the_event_lock_lets_foreign_key_checks_through(self):
        """Defect: a PostgreSQL deadlock between "Usar"/"Quitar" (the event row FOR UPDATE, then the camperos) and
        start/finalize (a campero's row, then FOR KEY SHARE on the event from the new rows' foreign keys).
        FOR NO KEY UPDATE does not conflict with FOR KEY SHARE; SQLite ignores both, so the request is checked."""
        other = create_questionnaire_from_definition(self.church, definition("Segundo", "Segundo cuestionario", "dos"))
        original = QuerySet.select_for_update
        requested = []

        def spy(queryset, *args, **kwargs):
            requested.append((queryset.model, args, kwargs))
            return original(queryset, *args, **kwargs)

        with mock.patch.object(QuerySet, "select_for_update", spy):
            use_version(self.event, other)
            move(self.event, other.questionnaire, "subir")
            stop_using(self.event, other.questionnaire)
        event_locks = [(args, kwargs) for model, args, kwargs in requested if model is Event]
        self.assertEqual(event_locks, [((), {"no_key": True})] * 3)


# ---- 11. Progress and derived status: the edges -------------------------------------------------------------


class ProgressEdgeTests(TwoQuestionnairesFixture):
    def test_done_count_ignores_a_questionnaire_the_event_no_longer_uses(self):
        """Defect: "1 de 1 cuestionarios" for a campero who answered only a questionnaire the event dropped."""
        self.complete(self.begin(questionnaire=self.q1))
        stop_using(self.event, self.q1)
        row = self.progress_row()
        self.assertEqual((row.done_count, row.questionnaire_total), (0, 1))
        self.assertEqual(self.status(), "pendiente")

    def test_refresh_recomputes_inactive_camperos_too(self):
        """Defect: a campero who left the camp keeping a status the event's questionnaires no longer support, which
        reappears the day they are reactivated."""
        self.complete(self.begin(questionnaire=self.q1))
        self.complete(self.begin(questionnaire=self.q2))
        self.assertEqual(self.status(), "completada")
        EventParticipant.objects.filter(pk=self.p2.pk).update(is_active=False, withdrawn_reason="Se fue antes")
        third = create_questionnaire_from_definition(self.church, definition("Tercero", "Tercer cuestionario", "tres"))
        use_version(self.event, third)
        self.assertEqual(self.status(), "pendiente")

    def test_no_questionnaires_is_pendiente_even_after_a_completed_interview(self):
        """Defect: an event without questionnaires counting every campero as done (``all()`` of nothing is true)."""
        self.complete(self.begin(questionnaire=self.q1))
        self.event.questionnaire_links.all().delete()  # stop_using refuses the last one; a repair by hand does not
        self.assertEqual(derive_interview_status(self.fresh()), "pendiente")
        self.assertEqual(derive_interview_status(self.fresh(), links=[]), "pendiente")
        refresh_interview_statuses(self.event)
        self.assertEqual(self.status(), "pendiente")


# ---- 12. Migration 0011 -----------------------------------------------------------------------------------------


class MigrationReverseGuardTests(TwoQuestionnairesFixture):
    ONE_CAMPERO = (
        "No se puede revertir events/0011: 1 campero tiene entrevistas de varios cuestionarios. "
        "Descarta las sobrantes antes de revertir."
    )
    TWO_CAMPEROS = (
        "No se puede revertir events/0011: 2 camperos tienen entrevistas de varios cuestionarios. "
        "Descarta las sobrantes antes de revertir."
    )

    def refuse(self):
        return MIGRATION_0011._refuse_reverse_with_multiple_live_interviews(django_apps, None)

    def test_the_guard_is_the_last_operation_so_it_runs_first_on_reverse(self):
        """Defect: the guard running after RemoveConstraint is reversed, i.e. after the old one-interview-per-campero
        index failed with "UNIQUE constraint failed: events_interview.participant_id"."""
        last = MIGRATION_0011.Migration.operations[-1]
        self.assertIsInstance(last, migrations.RunPython)
        self.assertIs(last.code, migrations.RunPython.noop)
        self.assertIs(last.reverse_code, MIGRATION_0011._refuse_reverse_with_multiple_live_interviews)

    def test_reverse_refuses_while_a_campero_has_interviews_of_two_questionnaires(self):
        """Defect: reversing 0011 half-way into an IntegrityError instead of saying what to do first."""
        svc.discard(self.begin(questionnaire=self.q1), actor=self.coord1, reason="repetir")
        self.complete(self.begin(questionnaire=self.q1))
        self.assertIsNone(self.refuse())  # one kept interview (a discarded one does not count)
        self.begin(questionnaire=self.q2)
        with self.assertRaisesMessage(RuntimeError, self.ONE_CAMPERO):
            self.refuse()

    def test_the_refusal_counts_every_crowded_campero_in_grammatical_spanish(self):
        """Defect: the refusal saying "1 camperos tienen"; the count must still cover every crowded campero."""
        self.complete(self.begin(questionnaire=self.q1))
        self.begin(questionnaire=self.q2)
        with self.assertRaisesMessage(RuntimeError, self.ONE_CAMPERO):
            self.refuse()
        for version in (self.first, self.second):
            Interview.objects.create(
                event=self.event, participant=self.p1, version=version, status=Interview.Status.COMPLETED
            )
        with self.assertRaisesMessage(RuntimeError, self.TWO_CAMPEROS):
            self.refuse()


class MigrationStatusRecomputeTests(IsolatedCacheMixin, CampFixture):
    def set_status(self, participant, status):
        EventParticipant.objects.filter(pk=participant.pk).update(interview_status=status)

    def status(self, participant):
        return EventParticipant.objects.get(pk=participant.pk).interview_status

    def test_forward_recomputes_the_stored_status_with_the_derived_rule(self):
        """Defect: after 0011 a campero stays "completada" for a questionnaire the event had already switched away
        from under the old single field (the derived status says "pendiente"), until some later change refreshes it."""
        other = create_questionnaire_from_definition(self.church, definition("Segundo", "Segundo cuestionario", "dos"))
        # Event A had switched to `other` under the old field: its only link. Event B still uses the fixture's.
        EventQuestionnaire.objects.filter(event=self.event).delete()
        EventQuestionnaire.objects.create(event=self.event, version=other, order=1)
        done = {"status": Interview.Status.COMPLETED, "completed_at": timezone.now()}
        Interview.objects.create(event=self.event, participant=self.p1, version=self.version, **done)
        self.set_status(self.p1, "completada")  # completed the questionnaire A no longer uses
        Interview.objects.create(event=self.event, participant=self.p2, version=other)
        self.set_status(self.p2, "pendiente")  # live interview
        Interview.objects.create(event=self.other_event, participant=self.p_other_event, version=self.version, **done)
        self.set_status(self.p_other_event, "pendiente")  # completed B's only questionnaire
        self.set_status(self.p3, "pendiente")  # nothing: stays as it is

        self.assertEqual(MIGRATION_0011.recompute_interview_statuses(django_apps), 3)
        self.assertEqual(self.status(self.p1), "pendiente")
        self.assertEqual(self.status(self.p2), "en_curso")
        self.assertEqual(self.status(self.p_other_event), "completada")
        self.assertEqual(self.status(self.p3), "pendiente")
        for participant in (self.p1, self.p2, self.p_other_event, self.p3):
            self.assertEqual(self.status(participant), derive_interview_status(self.fresh_participant(participant)))
        self.assertEqual(MIGRATION_0011.recompute_interview_statuses(django_apps), 0)

    def fresh_participant(self, participant):
        return EventParticipant.objects.select_related("event").get(pk=participant.pk)
