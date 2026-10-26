"""Operations before a real camp: `purge_demo_data` takes a demo deployment back to empty."""

from __future__ import annotations

from io import StringIO

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Church, DiscipleshipPath, Enrollment, Invitation, Role
from core.services import assign_role
from events.models import (
    Answer,
    AuditEvent,
    AuthThrottle,
    ConsentRecord,
    Event,
    EventMembership,
    EventParticipant,
    FollowUp,
    Interview,
    MembershipRole,
    QuestionnaireVersion,
    ReportSnapshot,
)
from events.services.questionnaire import DEFAULT_CAMP_QUESTIONNAIRE, all_questions, create_questionnaire_from_definition

COUNTED = (
    Church, User, Event, EventParticipant, Interview, Answer, ReportSnapshot, FollowUp, DiscipleshipPath, Enrollment,
    ConsentRecord, AuditEvent, Invitation, LogEntry, Session, AuthThrottle,
)


def counts():
    return {model.__name__: model.objects.count() for model in COUNTED}


def purge(*args):
    out = StringIO()
    call_command("purge_demo_data", *args, stdout=out)
    return out.getvalue()


def seed_demo(participants=None):
    """Both seeds, as a demo server has them. They refuse to run unless DEBUG is on."""
    extra = ["--participants", str(participants)] if participants else []
    with override_settings(DEBUG=True):
        call_command("seed_camp_demo", "--reset", *extra, password="x", stdout=StringIO())
        call_command("seed_dev_data", password="x", stdout=StringIO())


class PurgeDemoDataTests(TestCase):
    """A seeded demo next to a real church that has its own event, interview and answer."""

    @classmethod
    def setUpTestData(cls):
        cls.real_church = Church.objects.create(name="Iglesia Real", slug="iglesia-real")
        cls.real_admin = User.objects.create_user("pastora@iglesia-real.mx", "pastora@iglesia-real.mx", "x")
        assign_role(cls.real_admin, cls.real_church, Role.CHURCH_ADMIN)
        # Ends in example.org.mx, not @example.org: no demo marker.
        cls.near_miss = User.objects.create_user("ana@example.org.mx", "ana@example.org.mx", "x")
        cls.real_version = create_questionnaire_from_definition(cls.real_church, DEFAULT_CAMP_QUESTIONNAIRE)
        cls.real_event = Event.objects.create(
            church=cls.real_church, name="Campamento real", slug="campamento-real", questionnaire_version=cls.real_version,
            starts_on=timezone.localdate(), ends_on=timezone.localdate(),
        )
        camper = EventParticipant.objects.create(event=cls.real_event, number=1, full_name="Campero Real", age=20)
        cls.real_interview = Interview.objects.create(
            event=cls.real_event, participant=camper, version=cls.real_version, conducted_by=cls.real_admin
        )
        question = next(q for q in all_questions(cls.real_version) if q.code == "dudas_detalle")
        Answer.objects.create(interview=cls.real_interview, question=question, text_value="Una respuesta real.")
        seed_demo()
        cls.demo_church = Church.objects.get(slug="iglesia-demo")

    def test_the_seed_has_everything_the_purge_must_remove(self):
        demo = Church.objects.filter(slug__in=["iglesia-demo", "emmaus-demo"])
        self.assertEqual(demo.count(), 2)
        self.assertTrue(Interview.objects.filter(event__church=self.demo_church).exists())
        self.assertTrue(Answer.objects.filter(interview__event__church=self.demo_church).exists())
        self.assertTrue(ReportSnapshot.objects.filter(event__church=self.demo_church).exists())
        self.assertTrue(FollowUp.objects.filter(event__church=self.demo_church).exists())
        self.assertTrue(DiscipleshipPath.objects.filter(church__slug="emmaus-demo").exists())
        self.assertTrue(User.objects.filter(email__endswith="@demo.invalid").exists())
        self.assertTrue(User.objects.filter(email__endswith="@example.org").exists())

    def test_a_plain_church_delete_is_blocked_by_protect(self):
        """Why the command deletes interviews and events first: PROTECT raises even inside one cascade."""
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.demo_church.delete()
        self.assertTrue(Church.objects.filter(slug="iglesia-demo").exists())

    def test_dry_run_lists_everything_and_deletes_nothing(self):
        before = counts()
        output = purge()
        self.assertEqual(counts(), before)
        self.assertIn("Se borraría:", output)
        self.assertIn("Simulación: no se borró nada", output)
        self.assertIn("iglesia-demo", output)
        self.assertIn("emmaus-demo", output)
        self.assertIn("laura.mendez@demo.invalid", output)
        self.assertIn("admin@example.org", output)
        self.assertNotIn("pastora@iglesia-real.mx", output)
        self.assertNotIn("ana@example.org.mx", output)
        demo_interviews = Interview.objects.filter(event__church=self.demo_church).count()
        self.assertRegex(output, rf"Entrevistas\s+{demo_interviews}\n")
        self.assertRegex(output, r"Iglesias\s+2\n")

    @override_settings(DEBUG=False)
    def test_yes_removes_the_demo_and_keeps_the_real_church(self):
        output = purge("--yes")
        self.assertIn("Se borró:", output)
        self.assertEqual(list(Church.objects.values_list("slug", flat=True)), ["iglesia-real"])
        self.assertEqual(
            sorted(User.objects.values_list("email", flat=True)), ["ana@example.org.mx", "pastora@iglesia-real.mx"]
        )
        self.assertEqual(list(Event.objects.values_list("slug", flat=True)), ["campamento-real"])
        self.assertEqual(list(Interview.objects.values_list("pk", flat=True)), [self.real_interview.pk])
        self.assertEqual(Answer.objects.count(), 1)
        self.assertEqual(ReportSnapshot.objects.count(), 0)
        self.assertEqual(FollowUp.objects.count(), 0)
        self.assertEqual(DiscipleshipPath.objects.count(), 0)
        self.assertEqual(QuestionnaireVersion.objects.exclude(questionnaire__church=self.real_church).count(), 0)
        self.assertTrue(self.real_admin.church_roles.filter(church=self.real_church, role=Role.CHURCH_ADMIN).exists())
        # Nothing is left to purge.
        self.assertIn("No hay datos de demostración", purge("--yes"))

    def test_yes_refuses_when_a_demo_user_has_data_in_a_real_church(self):
        demo_user = User.objects.get(email="laura.mendez@demo.invalid")
        EventMembership.objects.create(event=self.real_event, user=demo_user, role=MembershipRole.COORDINATOR)
        before = counts()
        for args in ((), ("--yes",)):
            with self.assertRaisesMessage(CommandError, "events.EventMembership.user: 1"):
                purge(*args)
        self.assertEqual(counts(), before)

    def test_yes_refuses_when_a_real_event_uses_a_demo_questionnaire(self):
        Event.objects.create(
            church=self.real_church, name="Retiro", slug="retiro-real", questionnaire_version=self.demo_church.events.get().questionnaire_version
        )
        before = counts()
        with self.assertRaises(CommandError) as raised:
            purge("--yes")
        message = str(raised.exception)
        self.assertIn("No se borró nada", message)
        # Names what blocks the purge, not the cascade step Django happened to be on.
        self.assertIn("events.Event (1): retiro-real", message)
        self.assertNotIn("Questionnaire.church", message)
        self.assertEqual(counts(), before)


    # ---- users that real data points at ----------------------------------------------------
    def _point_real_rows_at(self, user):
        """What a support account leaves behind after setting up a real camp (one FK of each kind)."""
        Event.objects.filter(pk=self.real_event.pk).update(
            created_by=user, procedures_approved_by=user, procedures_approved_at=timezone.now()
        )
        QuestionnaireVersion.objects.filter(pk=self.real_version.pk).update(created_by=user, published_by=user)
        Interview.objects.filter(pk=self.real_interview.pk).update(conducted_by=user)
        Answer.objects.filter(interview=self.real_interview).update(answered_by=user)
        ConsentRecord.objects.create(participant=self.real_interview.participant, kind=ConsentRecord.Kind.GUARDIAN, recorded_by=user)
        AuditEvent.objects.create(actor=user, actor_label="Soporte", event=self.real_event, action="evento_creado", summary="Creó")
        Invitation.objects.create(church=self.real_church, email="nuevo@iglesia-real.mx", role=Role.MENTOR, invited_by=user)
        LogEntry.objects.create(
            user=user, content_type=ContentType.objects.get_for_model(Church), object_id=str(self.real_church.pk),
            object_repr=str(self.real_church), action_flag=CHANGE, change_message="Cambió el nombre",
        )

    def _pointers(self):
        return {
            "event": list(Event.objects.filter(pk=self.real_event.pk).values_list("created_by", "procedures_approved_by")),
            "version": list(QuestionnaireVersion.objects.filter(pk=self.real_version.pk).values_list("created_by", "published_by")),
            "interview": list(Interview.objects.filter(pk=self.real_interview.pk).values_list("conducted_by", flat=True)),
            "answer": list(Answer.objects.filter(interview=self.real_interview).values_list("answered_by", flat=True)),
            "consent": list(ConsentRecord.objects.filter(participant__event=self.real_event).values_list("recorded_by", flat=True)),
            "audit": list(AuditEvent.objects.filter(event=self.real_event).values_list("actor", flat=True)),
            "invitation": list(Invitation.objects.filter(church=self.real_church).values_list("invited_by", flat=True)),
            "log": list(LogEntry.objects.values_list("user", flat=True)),
        }

    def test_a_superuser_with_a_demo_address_is_never_deleted(self):
        """`createsuperuser` with a placeholder address: the account real data points at must survive."""
        support = User.objects.create_superuser("soporte@example.org", "soporte@example.org", "x")
        self._point_real_rows_at(support)
        pointers = self._pointers()
        output = purge("--yes")
        self.assertIn("soporte@example.org", output.split("Usuarios de demostración")[0])
        self.assertIn("superusuario", output)
        self.assertTrue(User.objects.filter(pk=support.pk, is_superuser=True).exists())
        self.assertEqual(self._pointers(), pointers)
        self.assertFalse(Church.objects.filter(slug__in=["iglesia-demo", "emmaus-demo"]).exists())

    def test_a_demo_user_that_real_rows_point_at_is_refused(self):
        """Not a superuser: a demo-marked account with any real row pointing at it stops the purge."""
        support = User.objects.create_user("soporte@example.org", "soporte@example.org", "x", is_staff=True)
        self._point_real_rows_at(support)
        pointers, before = self._pointers(), counts()
        for args in ((), ("--yes",)):
            with self.assertRaises(CommandError) as raised:
                purge(*args)
            message = str(raised.exception)
            for needle in ("soporte@example.org", "events.Event.created_by: 1", "events.Interview.conducted_by: 1",
                           "core.Invitation.invited_by: 1", "No se borró nada"):
                self.assertIn(needle, message)
            # Its Django admin history is demo history: it never blocks (see the next test).
            self.assertNotIn("LogEntry", message)
        self.assertEqual(self._pointers(), pointers)
        self.assertEqual(counts(), before)

    def test_the_admin_history_of_a_demo_user_does_not_block_and_goes_with_it(self):
        support = User.objects.create_user("soporte@example.org", "soporte@example.org", "x", is_staff=True)
        church_type = ContentType.objects.get_for_model(Church)
        for _ in range(2):
            LogEntry.objects.create(
                user=support, content_type=church_type, object_id=str(self.real_church.pk),
                object_repr=str(self.real_church), action_flag=CHANGE, change_message="Cambió el nombre",
            )
        superuser = User.objects.create_superuser("admin-real@example.org", "admin-real@example.org", "x")
        LogEntry.objects.create(
            user=superuser, content_type=church_type, object_id=str(self.real_church.pk),
            object_repr=str(self.real_church), action_flag=CHANGE, change_message="Cambió el aviso",
        )
        dry_run = purge()
        self.assertRegex(dry_run, r"Historial del admin de Django\s+2\n")
        self.assertEqual(LogEntry.objects.count(), 3)
        output = purge("--yes")
        self.assertRegex(output, r"Historial del admin de Django\s+2\n")
        self.assertFalse(User.objects.filter(pk=support.pk).exists())
        # The superuser and its history are never touched.
        self.assertEqual(list(LogEntry.objects.values_list("user", flat=True)), [superuser.pk])

    def test_demo_rows_pointing_at_a_demo_user_do_not_block_the_purge(self):
        laura = User.objects.get(email="laura.mendez@demo.invalid")
        demo_event = self.demo_church.events.get()
        Event.objects.filter(pk=demo_event.pk).update(created_by=laura, procedures_approved_by=laura)
        AuditEvent.objects.create(actor=laura, actor_label="Laura", event=demo_event, action="evento_creado", summary="Creó")
        purge("--yes")
        self.assertFalse(User.objects.filter(pk=laura.pk).exists())

    def test_sessions_and_throttles_stay_while_a_real_church_remains(self):
        self.client.force_login(self.real_admin)
        AuthThrottle.objects.create(key="login:pastora@iglesia-real.mx")
        purge("--yes")
        self.assertEqual(Session.objects.count(), 1)
        self.assertEqual(AuthThrottle.objects.count(), 1)


class PurgeToEmptyTests(TestCase):
    """A server that only ever had the demo ends with an empty database and offers /setup/."""

    @classmethod
    def setUpTestData(cls):
        seed_demo(participants=40)

    def test_purge_empties_the_database_and_the_landing_offers_setup(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertGreater(ReportSnapshot.objects.count(), 0)
        self.assertGreater(Interview.objects.count(), 0)
        with override_settings(DEBUG=False):
            purge("--yes")
        self.assertEqual(Church.objects.count(), 0)
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(Interview.objects.count(), 0)
        self.assertEqual(ReportSnapshot.objects.count(), 0)
        self.assertEqual(QuestionnaireVersion.objects.count(), 0)
        self.assertRedirects(self.client.get("/"), reverse("initial_setup"))

    def test_an_empty_database_also_loses_every_session_and_attempt_counter(self):
        self.client.force_login(User.objects.get(email="laura.mendez@demo.invalid"))
        self.client.force_login(User.objects.get(email="admin@example.org"))
        AuthThrottle.objects.create(key="login:laura.mendez@demo.invalid", attempts=3)
        AuthThrottle.objects.create(key="login:alguien@otro.mx", attempts=1)
        sessions = Session.objects.count()
        self.assertGreater(sessions, 0)
        dry_run = purge()
        self.assertIn("sesiones", dry_run)
        self.assertEqual((Session.objects.count(), AuthThrottle.objects.count()), (sessions, 2))
        output = purge("--yes")
        self.assertRegex(output, r"Sesiones abiertas\s+\d+")
        self.assertEqual(Session.objects.count(), 0)
        self.assertEqual(AuthThrottle.objects.count(), 0)

    def test_without_demo_data_there_is_nothing_to_do(self):
        purge("--yes")
        before = counts()
        self.assertIn("No hay datos de demostración", purge())
        self.assertEqual(counts(), before)
