"""Invariant 3: church data is tenant-isolated, also by guessed ids (issue #36, roadmap P1-10).

Church A is ``CampFixture``; church B is built here directly, not through ``seed_second_church``, so a change to
the seed cannot weaken what these tests prove. Every denial has a control that shows the same request works for
someone who may make it, so a 403/404 here cannot come from a broken URL or fixture.
"""

import os
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Church, Role, UserChurchRole
from core.services import assign_role
from events import urls as event_urls
from events.access import accessible_events
from events.models import AccessRequest, Answer, Event, EventMembership, EventParticipant, EventQuestionnaire, FollowUp
from events.models import Interview, MembershipRole, Question, QuestionnaireVersion, Sede
from events.services.event_questionnaires import use_version
from events.services.questionnaire import DEFAULT_CAMP_QUESTIONNAIRE, create_questionnaire_from_definition, options_by_code
from events.services.reports import generate_snapshot, scopes_for_event
from events.tests.base import CampFixture, make_user
from people.models import Person
from people.persons import persons_for

HIDDEN_LEVELS = (Question.Sensitivity.SENSITIVE, Question.Sensitivity.CONFIDENTIAL)


class TwoChurches(CampFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.sede_a = Sede.objects.create(church=cls.church, name="Centro A", slug="centro-a")
        cls.person_a = Person.objects.create(church=cls.church, first_name="Alma", last_name="Arias")
        cls.mentor_a = make_user("mentora@prueba.mx", "Mina", "Mentora")
        assign_role(cls.mentor_a, cls.church, Role.MENTOR)
        cls.church_b = Church.objects.create(name="Iglesia B", slug="iglesia-b")
        cls.admin_b = make_user("admin@iglesia-b.mx", "Beatriz", "Bravo")
        assign_role(cls.admin_b, cls.church_b, Role.CHURCH_ADMIN)
        cls.pastor_b = make_user("pastor@iglesia-b.mx", "Benito", "Barrera")
        assign_role(cls.pastor_b, cls.church_b, Role.PASTOR)
        cls.version_b = create_questionnaire_from_definition(cls.church_b, DEFAULT_CAMP_QUESTIONNAIRE)
        cls.event_b = Event.objects.create(
            church=cls.church_b, name="Campamento Iglesia B", slug="campamento-b-2026",
            procedures_approved_at=timezone.now(), starts_on=timezone.localdate(), ends_on=timezone.localdate(),
        )
        use_version(cls.event_b, cls.version_b)
        cls.m_b = EventMembership.objects.create(
            event=cls.event_b, user=make_user("coord@iglesia-b.mx", "Brenda", "Bueno"), role=MembershipRole.COORDINATOR
        )
        cls.sede_b = Sede.objects.create(church=cls.church_b, name="Centro B", slug="centro-b")
        cls.p_b = EventParticipant.objects.create(
            event=cls.event_b, number=1, full_name="Bruno Beltrán Ochoa", age=21, room="1", church_group="B",
            coordinator=cls.m_b, sede=cls.sede_b,
        )
        cls.person_b = Person.objects.create(church=cls.church_b, first_name="Berenice", last_name="Blanco")

    def url_b(self, name, *args):
        return reverse(f"events:{name}", args=[self.event_b.slug, *args])


class ChurchAdminCannotReachChurchBTests(TwoChurches):
    def test_church_b_event_is_forbidden(self):
        path = f"/eventos/{self.event_b.slug}/"
        self.assertEqual(self.url_b("home"), path)
        self.login(self.admin_b)
        self.assertEqual(self.client.get(path).status_code, 302)  # control: its own admin goes to their home
        self.login(self.admin)
        self.assertEqual(self.client.get(path).status_code, 403)

    def test_guessed_church_b_ids_are_not_found_under_church_a_slug(self):
        self.login(self.admin)
        cases = [
            ("participant_detail", self.p1.public_id, self.p_b.public_id),
            ("member_edit", self.m_coord1.id, self.m_b.id),
            ("sede_edit", self.sede_a.pk, self.sede_b.pk),
            ("version_detail", self.version.pk, self.version_b.pk),
            ("coordinator_detail", self.m_coord1.id, self.m_b.id),
        ]
        for name, own, foreign in cases:
            with self.subTest(name):
                self.assertEqual(self.client.get(self.url(name, own)).status_code, 200)  # control
                self.assertEqual(self.client.get(self.url(name, foreign)).status_code, 404)

    def test_guessed_church_b_ids_cannot_be_written_under_church_a_slug(self):
        self.login(self.admin)
        edit = {"role": MembershipRole.ADMIN, "is_active": "on", "can_view_sensitive": "on"}
        self.assertEqual(self.client.post(self.url("member_edit", self.m_b.id), edit).status_code, 404)
        self.assertEqual(self.client.post(self.url("sede_edit", self.sede_b.pk), {"name": "Tomada"}).status_code, 404)
        self.assertEqual(self.client.post(self.url("sede_delete", self.sede_b.pk)).status_code, 404)
        self.m_b.refresh_from_db()
        self.assertEqual((self.m_b.role, self.m_b.can_view_sensitive), (MembershipRole.COORDINATOR, False))
        self.assertEqual(Sede.objects.get(pk=self.sede_b.pk).name, "Centro B")

    def test_posting_a_church_b_sede_to_sedes_is_not_applied(self):
        self.login(self.admin)
        for chosen in ([self.sede_b.pk], [self.sede_a.pk, self.sede_b.pk]):
            with self.subTest(chosen=chosen):
                self.assertEqual(self.client.post(self.url("sedes"), {"sedes": chosen}).status_code, 200)  # form error
                self.assertEqual(self.event.sedes.count(), 0)
        self.assertRedirects(self.client.post(self.url("sedes"), {"sedes": [self.sede_a.pk]}), self.url("sedes"))
        self.assertEqual(list(self.event.sedes.all()), [self.sede_a])  # control: its own sede is applied
        self.assertEqual(self.sede_b.events.count(), 0)

    def test_accessible_events_exclude_church_b(self):
        self.assertEqual(set(accessible_events(self.admin)), {self.event, self.other_event})
        self.assertEqual(set(accessible_events(self.admin_b)), {self.event_b})
        self.assertEqual(set(accessible_events(self.pastor_b)), {self.event_b})

    def test_answers_export_of_church_b_is_forbidden_even_with_confidential_access_in_a(self):
        EventMembership.objects.create(event=self.event, user=self.admin, role=MembershipRole.ADMIN, can_view_sensitive=True)
        self.login(self.admin)
        self.assertEqual(self.client.get(self.url("answers_export")).status_code, 200)  # control
        self.assertEqual(self.client.get(self.url_b("answers_export")).status_code, 403)

    def test_persons_for_excludes_church_b(self):
        self.assertEqual(list(persons_for(self.admin)), [self.person_a])
        with self.assertRaises(Person.DoesNotExist):
            persons_for(self.admin).get(pk=self.person_b.pk)
        self.assertEqual(list(persons_for(self.admin_b)), [self.person_b])  # control


class ChurchRoleWithoutMembershipTests(TwoChurches):
    def _complete(self, participant, choices, texts):
        interview = Interview.objects.create(
            event=self.event, participant=participant, version=self.version, status=Interview.Status.COMPLETED,
            completed_at=timezone.now(), conducted_by=participant.coordinator.user,
        )
        for code, values in choices.items():
            question = self.question(code)
            answer = Answer.objects.create(interview=interview, question=question)
            answer.options.set([options_by_code(question)[value] for value in values])
        for code, text in texts.items():
            Answer.objects.create(interview=interview, question=self.question(code), text_value=text)
        return interview

    def _sample_kwargs(self):
        interview = self._complete(self.p1, {"salvo": ["si"]}, {})
        snapshot = generate_snapshot(self.event, scopes_for_event(self.event)[0], trigger="manual", cutoff_at=timezone.now())
        question = self.question("salvo")
        return {
            "participant": self.p1.public_id,
            "interview": interview.public_id,
            "report": snapshot.public_id,
            "participant_id": self.p1.public_id,
            "membership_id": self.m_coord1.id,
            "follow_up_id": FollowUp.objects.create(event=self.event, participant=self.p1, created_by=self.coord1).id,
            "pk": AccessRequest.objects.create(event=self.event, full_name="Ana Pide", email="pide@prueba.mx",
                                               requested_role=MembershipRole.TUTOR).pk,
            "version_id": self.version.id,
            "section_id": question.section_id,
            "question_id": question.id,
            "sede_id": self.sede_a.pk,
            "number": 1,
        }

    def test_church_role_without_membership_gets_403_on_every_event_url(self):
        samples = self._sample_kwargs()
        checked = 0
        for user in (self.mentor_a, self.admin_b, self.pastor_b):
            self.login(user)
            for pattern in event_urls.urlpatterns:
                keys = set(pattern.pattern.converters)
                if "slug" not in keys:
                    continue
                kind = "interview" if pattern.name.startswith("interview_") and pattern.name != "interview_start" else (
                    "report" if pattern.name.startswith("report_") else "participant")
                kwargs = {k: samples[kind] if k == "public_id" else samples[k] for k in keys - {"slug"}}
                url = reverse(f"events:{pattern.name}", kwargs={"slug": self.event.slug, **kwargs})
                status = self.client.get(url).status_code
                if status == 405:  # POST-only views refuse other methods before anything else
                    status = self.client.post(url).status_code
                with self.subTest(user=user.email, url=pattern.name):
                    self.assertEqual(status, 403)
                checked += 1
        slug_patterns = [p for p in event_urls.urlpatterns if "slug" in p.pattern.converters]
        self.assertEqual(checked, 3 * len(slug_patterns))
        self.assertGreater(len(slug_patterns), 60)
        self.login(self.admin)
        self.assertEqual(self.client.get(self.url("admin_home")).status_code, 200)  # control: A's admin is let in

    def test_tutor_reads_no_individual_answer_at_the_default_level(self):
        self.assertEqual(self.event.confidential_level, Event.ConfidentialLevel.CARE)
        interview = self._complete(
            self.p1, {"salvo": ["si"], "dudas": ["si"], "apoyo": ["familia"], "decision": ["seguir_aprendiendo"]},
            {"dudas_detalle": "DUDA-SINTETICA-1", "impedimento": "IMPEDIMENTO-SINTETICO-2"},
        )
        url = self.url("interview_detail", interview.public_id)
        self.login(self.tutor1)  # supervises coord1, who has p1: the tutor sees the campero, not the answers
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        rows = [r for r in response.context["rows"] if r["question"].sensitivity in HIDDEN_LEVELS]
        self.assertEqual({r["question"].sensitivity for r in rows}, set(HIDDEN_LEVELS))  # both kinds are listed
        for row in rows:
            with self.subTest(row["question"].code):
                self.assertFalse(row["visible"])
                self.assertEqual(row["display"], "")
        self.assertNotContains(response, "DUDA-SINTETICA-1")
        self.assertNotContains(response, "IMPEDIMENTO-SINTETICO-2")
        self.login(self.coord1)  # control: the assigned coordinator reads the "sensible" answers
        visible = {r["question"].code for r in self.client.get(url).context["rows"] if r["visible"]}
        self.assertIn("salvo", visible)


@override_settings(DEBUG=True)
class SeedSecondChurchTests(TestCase):
    PASSWORD = "clave-sintetica-456"

    def seed(self, **opts):
        out = StringIO()
        call_command("seed_second_church", stdout=out, stderr=out, **opts)
        return out.getvalue()

    def counts(self):
        models = (Church, User, UserChurchRole, Event, EventMembership, EventParticipant, Sede, QuestionnaireVersion,
                  EventQuestionnaire, Person)
        return {model.__name__: model.objects.count() for model in models}

    def test_creates_church_b_once(self):
        first = self.seed(password=self.PASSWORD)
        after_first = self.counts()
        second = self.seed(password=self.PASSWORD)
        self.assertEqual(self.counts(), after_first)
        self.assertNotIn("Se creó", second)
        self.assertEqual(second.count("Ya existía"), first.count("Se creó"))
        self.assertEqual(first.count("Se creó"), 10)
        for output in (first, second):
            self.assertNotIn(self.PASSWORD, output)
        church = Church.objects.get(slug="iglesia-b-demo")
        roles = dict(UserChurchRole.objects.filter(church=church).values_list("role", "user__email"))
        self.assertEqual(set(roles), {Role.CHURCH_ADMIN, Role.PASTOR})
        for user in User.objects.all():
            self.assertTrue(user.email.endswith("@demo.invalid"), user.email)
            self.assertTrue(user.check_password(self.PASSWORD), user.email)
        event = Event.objects.get(slug="campamento-b-2026", church=church)
        self.assertEqual(list(event.memberships.values_list("role", flat=True)), [MembershipRole.COORDINATOR])
        self.assertEqual(event.participants.count(), 1)
        self.assertEqual(church.sedes.count(), 1)
        link = EventQuestionnaire.objects.get(event=event)
        self.assertEqual((link.version.status, link.version.questionnaire.church), (QuestionnaireVersion.Status.PUBLISHED, church))
        self.assertEqual(Person.objects.filter(church=church).count(), 1)

    def test_password_comes_from_the_environment_and_is_never_generated(self):
        with mock.patch.dict(os.environ, {"EMMAUS_DEMO_PASSWORD": ""}):
            with self.assertRaisesMessage(CommandError, "--password"):
                self.seed()
            self.assertFalse(Church.objects.exists())
            os.environ["EMMAUS_DEMO_PASSWORD"] = self.PASSWORD
            output = self.seed()
        self.assertNotIn(self.PASSWORD, output)
        self.assertTrue(User.objects.get(username="admin.iglesia-b@demo.invalid").check_password(self.PASSWORD))

    @override_settings(DEBUG=False)
    def test_refuses_without_debug(self):
        with self.assertRaisesMessage(CommandError, "DEBUG=true"):
            self.seed(password=self.PASSWORD)
        self.assertFalse(Church.objects.exists())

    def test_refuses_to_join_another_churchs_event_or_account(self):
        other = Church.objects.create(name="Otra", slug="otra")
        Event.objects.create(church=other, name="Ajeno", slug="campamento-b-2026")
        with self.assertRaisesMessage(CommandError, "Ya existe el evento campamento-b-2026 en otra iglesia"):
            self.seed(password=self.PASSWORD)
        Event.objects.filter(slug="campamento-b-2026").delete()
        account = User.objects.create_user("admin.iglesia-b@demo.invalid", "admin.iglesia-b@demo.invalid", "x")
        assign_role(account, other, Role.MENTOR)
        with self.assertRaisesMessage(CommandError, "admin.iglesia-b@demo.invalid"):
            self.seed(password=self.PASSWORD)
        self.assertFalse(Church.objects.filter(slug="iglesia-b-demo").exists())  # nothing half-made is left
