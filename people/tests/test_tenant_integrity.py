"""``manage.py tenant_integrity``: counts cross-church and cross-event rows the schema cannot refuse (issue #38).

Each violation is written through the ORM (``QuerySet.update()`` or a direct create), never through the
services, which would refuse it: the point is to see what the command finds when the services were bypassed.
"""

import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Church, Role
from core.services import assign_role
from events.models import EventParticipant, EventQuestionnaire, FollowUp, Interview, ReportSnapshot, Sede
from events.services.questionnaire import DEFAULT_CAMP_QUESTIONNAIRE, create_questionnaire_from_definition
from events.tests.base import CampFixture
from people.management.commands.tenant_integrity import camp_checks, checks
from people.models import Person

# The camp's checks, which this file plants; the ones of the church modules are in test_tenant_integrity_community.
KEYS = [check.key for check in camp_checks()]
ALL_KEYS = [check.key for check in checks()]
WARNING_KEYS = {"person_user_without_church_role"}


def run_integrity(*args):
    """(stdout, error message, exit code) the way ``manage.py`` would exit: CommandError carries the code."""
    out = StringIO()
    try:
        call_command("tenant_integrity", *args, stdout=out, stderr=StringIO())
    except CommandError as error:
        return out.getvalue(), str(error), error.returncode
    except SystemExit as error:  # pragma: no cover - the command raises CommandError, never exits by itself
        return out.getvalue(), "", error.code
    return out.getvalue(), "", 0


def json_counts():
    out, _, code = run_integrity("--json")
    return json.loads(out), code


def mismatches(payload):
    return {key: entry["mismatched"] for key, entry in payload["checks"].items()}


def snapshot(event, tutor):
    return ReportSnapshot.objects.create(
        event=event, scope_key=f"tutor-{tutor.pk}", scope_label=tutor.team_name, tutor=tutor,
        trigger=ReportSnapshot.Trigger.MANUAL, cutoff_at=timezone.now(), interval_minutes=60, metrics_version="1",
    )


class TenantIntegrityTests(CampFixture):
    """CampFixture (church A, events A and B) plus church B, with one valid row for every check."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.church_b = Church.objects.create(name="Iglesia Vecina", slug="iglesia-vecina")
        cls.version_b = create_questionnaire_from_definition(cls.church_b, DEFAULT_CAMP_QUESTIONNAIRE)
        cls.sede_a = Sede.objects.create(church=cls.church, name="Satélite", slug="satelite")
        cls.sede_b = Sede.objects.create(church=cls.church_b, name="Mérida", slug="merida")
        EventParticipant.objects.filter(event=cls.event).update(sede=cls.sede_a)
        # Valid rows, so every check reads at least one row that agrees.
        interview = Interview.objects.create(event=cls.event, participant=cls.p1, version=cls.version)
        FollowUp.objects.create(event=cls.event, participant=cls.p1, interview=interview)
        snapshot(cls.event, cls.m_tutor1)
        Person.objects.create(church=cls.church, first_name="Ada", last_name="Admin", user=cls.admin, created_from=Person.CreatedFrom.BACKFILL)
        Person.objects.create(church=cls.church, first_name="Sin", last_name="Cuenta")

    # One builder per check: each writes exactly one row that disagrees, bypassing the services.
    def break_interview_version_church(self):
        Interview.objects.create(event=self.event, participant=self.p2, version=self.version_b)

    def break_event_questionnaire_version_church(self):
        EventQuestionnaire.objects.create(event=self.event, version=self.version_b, order=2)

    def break_participant_sede_church(self):
        EventParticipant.objects.filter(pk=self.p2.pk).update(sede=self.sede_b)

    def break_participant_coordinator_event(self):
        EventParticipant.objects.filter(pk=self.p3.pk).update(coordinator=self.m_other)

    def break_interview_participant_event(self):
        # Same church, same questionnaire: only the event pair disagrees.
        Interview.objects.create(event=self.other_event, participant=self.p4, version=self.version)

    def break_follow_up_participant_event(self):
        FollowUp.objects.create(event=self.other_event, participant=self.p2)

    def break_snapshot_tutor_event(self):
        snapshot(self.other_event, self.m_tutor2)

    def break_person_user_without_church_role(self):
        # The outsider holds an event role in event B, never a church role.
        Person.objects.create(church=self.church, first_name="Óscar", last_name="Externo", user=self.outsider,
                              created_from=Person.CreatedFrom.BACKFILL)
        # A member of the church (registered by hand, with an account and no role) is not a finding.
        Person.objects.create(church=self.church, first_name="Marta", last_name="Miembro", user=self.other_coord)

    def assert_counted_alone_and_fails(self, key):
        getattr(self, f"break_{key}")()
        payload, code = json_counts()
        self.assertEqual(mismatches(payload), {k: int(k == key) for k in ALL_KEYS})
        self.assertEqual((code, payload["ok"], payload["errors"], payload["warnings"]), (1, False, 1, 0))

    # ---- a valid fixture --------------------------------------------------------------------------
    def test_valid_data_counts_zero_and_exits_zero(self):
        payload, code = json_counts()
        self.assertEqual(code, 0)
        self.assertEqual(list(payload["checks"]), ALL_KEYS)
        self.assertEqual(mismatches(payload), dict.fromkeys(ALL_KEYS, 0))
        # Every check of the camp read rows: a zero next to zero rows read would prove nothing.
        for key in KEYS:
            self.assertGreater(payload["checks"][key]["checked"], 0, key)
        self.assertEqual((payload["ok"], payload["errors"], payload["warnings"]), (True, 0, 0))
        out, message, code = run_integrity()
        self.assertEqual((message, code), ("", 0))
        self.assertIn("Resultado: sin discrepancias.", out)

    def test_nullable_links_left_empty_are_not_counted(self):
        EventParticipant.objects.update(sede=None, coordinator=None)
        ReportSnapshot.objects.update(tutor=None)
        Person.objects.update(user=None)
        payload, code = json_counts()
        self.assertEqual(code, 0)
        for key in ("participant_sede_church", "participant_coordinator_event", "snapshot_tutor_event", *WARNING_KEYS):
            entry = payload["checks"][key]
            self.assertEqual((entry["checked"] or 0, entry["mismatched"]), (0, 0), key)

    def test_runs_in_one_query_per_table(self):
        tables = {check.model for check in checks()}
        # 24 until wave 8 added the reading plans (2) and sitio.PublicMedia (1); wave 9: espacios.Booking (1), merges and dismissed pairs (2)
        # wave 10: people.PersonTag (1), formularios.Submission (1)
        self.assertEqual(len(tables), 32)
        with self.assertNumQueries(len(tables)):
            run_integrity("--json")

    # ---- one violation of each kind -----------------------------------------------------------------
    def test_interview_with_a_version_of_another_church(self):
        self.assert_counted_alone_and_fails("interview_version_church")

    def test_event_questionnaire_with_a_version_of_another_church(self):
        self.assert_counted_alone_and_fails("event_questionnaire_version_church")

    def test_participant_with_a_sede_of_another_church(self):
        self.assert_counted_alone_and_fails("participant_sede_church")

    def test_participant_with_a_coordinator_of_another_event(self):
        self.assert_counted_alone_and_fails("participant_coordinator_event")

    def test_interview_whose_participant_is_in_another_event(self):
        self.assert_counted_alone_and_fails("interview_participant_event")

    def test_follow_up_whose_participant_is_in_another_event(self):
        self.assert_counted_alone_and_fails("follow_up_participant_event")

    def test_snapshot_with_a_tutor_of_another_event(self):
        self.assert_counted_alone_and_fails("snapshot_tutor_event")

    def test_person_whose_account_has_no_role_in_its_church_is_only_a_warning(self):
        self.break_person_user_without_church_role()
        # The admin's role is in church A; the same account as a person of church B has no role there.
        Person.objects.create(church=self.church_b, first_name="Ada", last_name="Admin", user=self.admin,
                              created_from=Person.CreatedFrom.BACKFILL)
        payload, code = json_counts()
        self.assertEqual(mismatches(payload), {k: 2 if k in WARNING_KEYS else 0 for k in ALL_KEYS})
        self.assertEqual(payload["checks"]["person_user_without_church_role"]["severity"], "warning")
        self.assertEqual((code, payload["ok"], payload["errors"], payload["warnings"]), (0, True, 0, 2))
        out, message, code = run_integrity()
        self.assertEqual((message, code), ("", 0))
        self.assertIn("Resultado: sin discrepancias.", out)
        self.assertIn("Aviso: 2 persona(s) con cuenta", out)
        # A role in that church clears it.
        assign_role(self.outsider, self.church, Role.MENTOR)
        self.assertEqual(mismatches(json_counts()[0])["person_user_without_church_role"], 1)

    # ---- all of them at once ------------------------------------------------------------------------
    def test_every_violation_at_once_counts_each_and_prints_no_names_or_ids(self):
        for key in KEYS:
            getattr(self, f"break_{key}")()
        payload, code = json_counts()
        self.assertEqual(mismatches(payload), {k: int(k in KEYS) for k in ALL_KEYS})
        self.assertEqual((code, payload["ok"], payload["errors"], payload["warnings"]), (1, False, 7, 1))

        out, message, code = run_integrity()
        self.assertEqual(code, 1)
        self.assertIn("7 registro(s) con discrepancias en 7 revisión(es)", message)
        self.assertEqual(out.count("Revisar"), 7)
        self.assertEqual(out.count("Aviso"), 2)  # the row's status and the closing line
        self.assertIn("Resultado: 7 registro(s) con discrepancias.", out)
        json_out, _, _ = run_integrity("--json")
        participants, interviews = list(EventParticipant.objects.all()), list(Interview.objects.all())
        secrets = [
            *(p.full_name for p in participants), *(str(p.public_id) for p in participants),
            *(str(i.public_id) for i in interviews), self.outsider.email, self.admin.email, "Óscar", "Externo",
            self.church.name, self.church_b.name, self.event.slug, self.other_event.slug, self.event.name,
            self.sede_b.name, "Equipo Sur", "Equipo Norte",
        ]
        for text in (out, message, json_out):
            for secret in secrets:
                self.assertNotIn(secret, text)


class SeedsHaveNoMismatchesTests(TestCase):
    def test_synthetic_seeds_yield_zero(self):
        with override_settings(DEBUG=True):
            call_command("seed_camp_demo", "--participants", "20", "--hours", "30", password="x", stdout=StringIO())
            call_command("seed_dev_data", password="x", stdout=StringIO())
        payload, code = json_counts()
        self.assertEqual((code, payload["ok"]), (0, True))
        self.assertEqual(mismatches(payload), dict.fromkeys(ALL_KEYS, 0))
        # The seed exercises the camp checks; nothing creates a Person yet, so that check reads no row.
        for key in set(KEYS) - WARNING_KEYS:
            self.assertGreater(payload["checks"][key]["checked"], 0, key)
