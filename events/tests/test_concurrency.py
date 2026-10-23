"""Real threads against PostgreSQL row locks: 10 stations working at the same time."""

import threading
from unittest import skipUnless

from django.db import connection, connections
from django.test import TransactionTestCase
from django.utils import timezone

from core.models import Church
from events.access import resolve_access
from events.models import (
    Event,
    EventMembership,
    EventParticipant,
    FollowUp,
    Interview,
    InterviewStation,
    MembershipRole,
)
from events.services import interviews as svc
from events.services.questionnaire import DEFAULT_CAMP_QUESTIONNAIRE, build_steps, create_questionnaire_from_definition

from .base import make_user


def run_in_threads(targets):
    errors, results = [], [None] * len(targets)
    barrier = threading.Barrier(len(targets))

    def wrap(index, fn):
        try:
            barrier.wait(timeout=10)
            results[index] = fn()
        except Exception as exc:  # collected for assertions
            results[index] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrap, args=(i, fn)) for i, fn in enumerate(targets)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results, errors


@skipUnless(connection.vendor == "postgresql", "Row-level locking needs PostgreSQL")
class StationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        church = Church.objects.create(name="Iglesia", slug="iglesia-concurrencia")
        version = create_questionnaire_from_definition(church, DEFAULT_CAMP_QUESTIONNAIRE)
        self.event = Event.objects.create(church=church, name="Campamento", slug="concurrencia", questionnaire_version=version, procedures_approved_at=timezone.now())
        self.stations = [InterviewStation.objects.create(event=self.event, number=n, name=f"Computadora {n}") for n in range(1, 11)]
        self.coordinators = []
        self.campers = []
        for n in range(10):
            user = make_user(f"c{n}@prueba.mx", f"Coord{n}", "Prueba")
            membership = EventMembership.objects.create(event=self.event, user=user, role=MembershipRole.COORDINATOR)
            self.coordinators.append(membership)
            self.campers.append(
                EventParticipant.objects.create(event=self.event, number=n + 1, full_name=f"Campero {n} Prueba", age=20, room="1", church_group="G", coordinator=membership)
            )
        self.steps = build_steps(version)

    def _access(self, membership):
        return resolve_access(membership.user, self.event)

    def test_ten_stations_start_and_finish_in_parallel(self):
        def station(i):
            def run():
                access = self._access(self.coordinators[i])
                session = {}
                interview = svc.start_interview(access, self.campers[i], mode="asistida", station=self.stations[i], confirmed_consents={"consent"}, session=session)
                for step in self.steps[:3]:
                    from django.http import QueryDict

                    data = QueryDict(mutable=True)
                    data["revision"] = str(Interview.objects.get(pk=interview.pk).revision)
                    option = step.question.options.first()
                    data[f"q_{step.question.id}"] = option.code
                    svc.save_step(interview, step, data, token=svc.session_claim_token(session, interview), actor=access.user, by_participant=False)
                interview.refresh_from_db()
                return svc.finalize(interview, token=svc.session_claim_token(session, interview), revision=interview.revision, decision="no_requerido", actor=access.user)
            return run

        results, _ = run_in_threads([station(i) for i in range(10)])
        failures = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(failures, [])
        self.assertEqual(Interview.objects.filter(event=self.event, status="completada").count(), 10)
        self.assertEqual(EventParticipant.objects.filter(event=self.event, interview_status="completada").count(), 10)

    def test_two_stations_racing_for_the_same_camper(self):
        camper = self.campers[0]
        access = self._access(self.coordinators[0])

        def attempt(i):
            return lambda: svc.start_interview(access, camper, mode="asistida", station=self.stations[i], confirmed_consents={"consent"}, session={})

        results, _ = run_in_threads([attempt(i) for i in range(5)])
        created = [r for r in results if isinstance(r, Interview)]
        refused = [r for r in results if isinstance(r, svc.AlreadyInProgress)]
        self.assertEqual(len(created), 1, results)
        self.assertEqual(len(refused), 4, results)
        self.assertEqual(Interview.objects.filter(participant=camper).count(), 1)

    def test_simultaneous_double_finalize_is_idempotent(self):
        camper = self.campers[1]
        access = self._access(self.coordinators[1])
        session = {}
        interview = svc.start_interview(access, camper, mode="asistida", station=None, confirmed_consents={"consent"}, session=session)
        token = svc.session_claim_token(session, interview)
        revision = interview.revision

        def finalize():
            return svc.finalize(Interview.objects.get(pk=interview.pk), token=token, revision=revision, decision="requerido", actor=access.user)

        results, _ = run_in_threads([finalize for _ in range(4)])
        self.assertTrue(all(isinstance(r, Interview) for r in results), results)
        self.assertEqual(FollowUp.objects.filter(interview=interview).count(), 1)
        self.assertEqual(Interview.objects.get(pk=interview.pk).status, "completada")

    def test_participant_numbers_stay_unique_under_parallel_registration(self):
        from events.services.participants import create_participant

        def register(i):
            return lambda: create_participant(
                self.event, actor=None, full_name=f"Nuevo {i} Campero", age=15, room="2", church_group="G", coordinator=self.coordinators[i % 10]
            )

        results, _ = run_in_threads([register(i) for i in range(10)])
        self.assertFalse([r for r in results if isinstance(r, Exception)], results)
        numbers = list(EventParticipant.objects.filter(event=self.event).values_list("number", flat=True))
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertEqual(len(numbers), 20)
