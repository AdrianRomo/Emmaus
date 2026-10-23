from __future__ import annotations

from io import StringIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    AIDraft,
    Church,
    DiscipleshipPath,
    Enrollment,
    FollowUpRequest,
    Invitation,
    Lesson,
    LessonProgress,
    ParticipantProfile,
    ParticipantQuestion,
    PastoralNote,
    QuestionResponse,
    ReflectionAnswer,
    ReflectionQuestion,
    Role,
    UserChurchRole,
)
from .forms import EmailAuthenticationForm
from .services import (
    assign_role,
    enroll_participant_in_path,
    notify_follow_up_created,
    notify_question_escalated,
)


PASSWORD = "StrongPass12345!"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class InitialSetupTests(TestCase):
    def test_initial_church_setup_creates_church_and_admin(self):
        response = self.client.post(
            reverse("initial_setup"),
            {
                "church_name": "Grace Church",
                "church_slug": "grace",
                "admin_name": "Ada Admin",
                "admin_email": "ada@example.org",
                "admin_password": PASSWORD,
            },
        )

        self.assertRedirects(response, reverse("church_dashboard"))
        church = Church.objects.get(slug="grace")
        admin = User.objects.get(email="ada@example.org")
        self.assertEqual(church.name, "Grace Church")
        self.assertEqual(church.theme_primary_color, "#111111")
        self.assertEqual(church.theme_accent_color, "#B85C38")
        self.assertEqual(UserChurchRole.objects.get(user=admin, church=church).role, Role.CHURCH_ADMIN)


class EmmausFixture(TestCase):
    def setUp(self):
        self.church = Church.objects.create(name="Emmaus Church", slug="emmaus", contact_email="hello@example.org")
        self.admin = self.make_user("admin@example.org", "Admin", "User", Role.CHURCH_ADMIN)
        self.pastor = self.make_user("pastor@example.org", "Pastor", "User", Role.PASTOR)
        self.mentor = self.make_user("mentor@example.org", "Mentor", "User", Role.MENTOR)
        self.other_mentor = self.make_user("other-mentor@example.org", "Other", "Mentor", Role.MENTOR)
        self.participant = self.make_user("participant@example.org", "Pat", "Participant", Role.PARTICIPANT)
        self.other_participant = self.make_user("other@example.org", "Other", "Participant", Role.PARTICIPANT)
        self.profile = ParticipantProfile.objects.create(
            user=self.participant,
            church=self.church,
            assigned_mentor=self.mentor,
            consent_accepted_at=timezone.now(),
            emergency_disclaimer_accepted_at=timezone.now(),
        )
        self.other_profile = ParticipantProfile.objects.create(
            user=self.other_participant,
            church=self.church,
            assigned_mentor=self.other_mentor,
            consent_accepted_at=timezone.now(),
            emergency_disclaimer_accepted_at=timezone.now(),
        )
        self.path = DiscipleshipPath.objects.create(
            church=self.church,
            title="First Steps With Jesus",
            description="Starter path",
            target_audience="New believers",
            is_default=True,
            created_by=self.admin,
        )
        self.lesson = Lesson.objects.create(
            path=self.path,
            order=1,
            title="Who is Jesus?",
            scripture_references="John 1:1-14",
            body="Jesus reveals God's heart and invites us to follow him.",
            practical_step="Read a Gospel story.",
        )
        self.reflection_question = ReflectionQuestion.objects.create(
            lesson=self.lesson,
            order=1,
            question_text="What do you notice about Jesus?",
        )
        self.enrollment = enroll_participant_in_path(self.participant, self.path)
        self.other_enrollment = enroll_participant_in_path(self.other_participant, self.path)

    def make_user(self, email: str, first_name: str, last_name: str, role: str) -> User:
        user = User.objects.create_user(
            username=email,
            email=email,
            password=PASSWORD,
            first_name=first_name,
            last_name=last_name,
            is_staff=role in [Role.CHURCH_ADMIN, Role.PASTOR],
        )
        assign_role(user, self.church, role)
        return user


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class InvitationFlowTests(EmmausFixture):
    def test_admin_can_create_participant_invite_without_mentor_and_view_list(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("create_invitation"),
            {
                "email": "created-no-mentor@example.org",
                "role": Role.PARTICIPANT,
                "assigned_mentor": "",
                "path": self.path.id,
            },
        )
        list_response = self.client.get(reverse("invitations_list"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Invitation.objects.filter(email="created-no-mentor@example.org").exists())
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "created-no-mentor@example.org")
        self.assertContains(list_response, "Sin asignar")

    def test_invitation_list_renders_invites_without_assigned_mentor(self):
        Invitation.objects.create(
            church=self.church,
            email="nomentor@example.org",
            role=Role.PARTICIPANT,
            invited_by=self.admin,
            assigned_mentor=None,
            path=self.path,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("invitations_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "nomentor@example.org")
        self.assertContains(response, "Sin asignar")

    def test_invitation_token_creates_user_with_correct_role(self):
        invitation = Invitation.objects.create(
            church=self.church,
            email="new@example.org",
            role=Role.PARTICIPANT,
            invited_by=self.admin,
            assigned_mentor=self.mentor,
            path=self.path,
        )

        response = self.client.post(
            reverse("accept_invitation", kwargs={"token": invitation.token}),
            {
                "full_name": "New Person",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "preferred_contact_method": ParticipantProfile.ContactMethod.EMAIL,
                "phone_number": "",
                "accept_privacy": "on",
                "accept_emergency_disclaimer": "on",
            },
        )

        self.assertRedirects(response, reverse("participant_dashboard"))
        user = User.objects.get(email="new@example.org")
        self.assertEqual(UserChurchRole.objects.get(user=user, church=self.church).role, Role.PARTICIPANT)
        self.assertEqual(user.participant_profile.assigned_mentor, self.mentor)
        self.assertTrue(Enrollment.objects.filter(participant=user, path=self.path).exists())
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)

    def test_invitation_token_cannot_be_reused(self):
        invitation = Invitation.objects.create(church=self.church, email="reuse@example.org", role=Role.MENTOR)
        invitation.accepted_at = timezone.now()
        invitation.save()

        response = self.client.get(reverse("accept_invitation", kwargs={"token": invitation.token}))

        self.assertEqual(response.status_code, 410)

    def test_expired_invitation_cannot_be_accepted(self):
        invitation = Invitation.objects.create(
            church=self.church,
            email="expired@example.org",
            role=Role.PARTICIPANT,
            expires_at=timezone.now() - timezone.timedelta(days=1),
        )

        response = self.client.post(reverse("accept_invitation", kwargs={"token": invitation.token}), {})

        self.assertEqual(response.status_code, 410)
        self.assertFalse(User.objects.filter(email="expired@example.org").exists())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AccessControlTests(EmmausFixture):
    def test_participant_can_only_see_own_dashboard(self):
        self.client.force_login(self.participant)

        response = self.client.get(reverse("participant_dashboard"))
        forbidden = self.client.get(reverse("participant_detail", kwargs={"profile_id": self.other_profile.id}))

        self.assertContains(response, "Pat Participant")
        self.assertNotContains(response, "Other Participant")
        self.assertEqual(forbidden.status_code, 403)

    def test_participant_cannot_see_pastoral_notes_or_ai_drafts(self):
        question = ParticipantQuestion.objects.create(
            church=self.church,
            participant=self.participant,
            question_text="Can I ask about prayer?",
        )
        PastoralNote.objects.create(
            church=self.church,
            participant=self.participant,
            author=self.pastor,
            body="Private pastoral note",
            visibility=PastoralNote.Visibility.PASTOR_ONLY,
        )
        AIDraft.objects.create(
            church=self.church,
            related_question=question,
            generated_by=self.pastor,
            generated_text="Hidden AI draft",
        )
        self.client.force_login(self.participant)

        dashboard = self.client.get(reverse("participant_dashboard"))
        question_page = self.client.get(reverse("participant_question_detail", kwargs={"question_id": question.id}))

        self.assertNotContains(dashboard, "Private pastoral note")
        self.assertNotContains(question_page, "Hidden AI draft")

    def test_mentor_can_only_see_assigned_participants(self):
        self.client.force_login(self.mentor)

        assigned = self.client.get(reverse("participant_detail", kwargs={"profile_id": self.profile.id}))
        unassigned = self.client.get(reverse("participant_detail", kwargs={"profile_id": self.other_profile.id}))

        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(unassigned.status_code, 404)

    def test_pastor_can_see_all_church_participants(self):
        self.client.force_login(self.pastor)

        response = self.client.get(reverse("participants_list"))

        self.assertContains(response, "Pat Participant")
        self.assertContains(response, "Other Participant")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class WorkflowTests(EmmausFixture):
    def test_admin_can_update_church_theme_colors(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("church_settings"),
            {
                "name": self.church.name,
                "public_welcome_text": self.church.public_welcome_text,
                "contact_email": self.church.contact_email,
                "basic_disclaimer_text": self.church.basic_disclaimer_text,
                "self_registration_enabled": "on",
                "leaders_can_invite_participants": "on",
                "theme_primary_color": "#000000",
                "theme_accent_color": "#C0663F",
                "theme_secondary_color": "#64745B",
                "theme_background_color": "#F2F2F0",
                "theme_surface_color": "#FFFFFF",
                "theme_text_color": "#111111",
                "theme_muted_text_color": "#6E6A62",
                "theme_border_color": "#D9D9D6",
            },
        )

        self.assertRedirects(response, reverse("church_settings"))
        self.church.refresh_from_db()
        self.assertEqual(self.church.theme_primary_color, "#000000")
        self.assertEqual(self.church.theme_accent_color, "#C0663F")

        settings_page = self.client.get(reverse("church_settings"))
        self.assertContains(settings_page, 'type="color"')
        self.assertContains(settings_page, 'value="#C0663F"')

        # The saved colors reach the page theme once the church opts into its own palette.
        Church.objects.filter(pk=self.church.pk).update(custom_theme_enabled=True)
        themed_page = self.client.get(reverse("church_settings"))
        # Rótulo maps the church palette onto its tokens: primary -> añil (action), accent -> maíz (wayfinding)
        self.assertContains(themed_page, "--anil:#000000")
        self.assertContains(themed_page, "--maiz:#C0663F")

    def test_participant_can_submit_reflection_answer(self):
        self.client.force_login(self.participant)

        response = self.client.post(
            reverse("participant_lesson", kwargs={"lesson_id": self.lesson.id}),
            {f"question_{self.reflection_question.id}": "Jesus is merciful.", "mark_complete": "1"},
        )

        self.assertRedirects(response, reverse("participant_dashboard"))
        self.assertTrue(
            ReflectionAnswer.objects.filter(
                enrollment=self.enrollment,
                question=self.reflection_question,
                answer_text="Jesus is merciful.",
            ).exists()
        )
        self.assertTrue(
            LessonProgress.objects.get(enrollment=self.enrollment, lesson=self.lesson).completed_at
        )

    def test_participant_can_ask_question(self):
        self.client.force_login(self.participant)

        response = self.client.post(
            reverse("ask_question"),
            {
                "question_text": "How should I start praying?",
                "sensitivity_level": ParticipantQuestion.Sensitivity.NORMAL,
            },
        )

        self.assertRedirects(response, reverse("participant_questions"))
        self.assertTrue(
            ParticipantQuestion.objects.filter(
                church=self.church,
                participant=self.participant,
                question_text="How should I start praying?",
            ).exists()
        )

    def test_mentor_can_respond_to_assigned_participant_question(self):
        question = ParticipantQuestion.objects.create(
            church=self.church,
            participant=self.participant,
            question_text="How should I pray?",
        )
        self.client.force_login(self.mentor)

        response = self.client.post(
            reverse("question_detail", kwargs={"question_id": question.id}),
            {"action": "respond", "response-response_body": "Start simply and honestly."},
        )

        self.assertRedirects(response, reverse("question_detail", kwargs={"question_id": question.id}))
        self.assertTrue(QuestionResponse.objects.filter(question=question, responder=self.mentor).exists())
        question.refresh_from_db()
        self.assertEqual(question.status, ParticipantQuestion.Status.ANSWERED)

    def test_pastor_can_respond_to_escalated_question(self):
        question = ParticipantQuestion.objects.create(
            church=self.church,
            participant=self.participant,
            question_text="I need pastoral help.",
            status=ParticipantQuestion.Status.ESCALATED,
        )
        self.client.force_login(self.pastor)

        response = self.client.post(
            reverse("church_question_detail", kwargs={"question_id": question.id}),
            {"action": "respond", "response-response_body": "Let us meet this week."},
        )

        self.assertRedirects(response, reverse("church_question_detail", kwargs={"question_id": question.id}))
        self.assertTrue(QuestionResponse.objects.filter(question=question, responder=self.pastor).exists())

    def test_inactive_participant_command_creates_follow_up_request_without_duplicates(self):
        old_date = timezone.now() - timezone.timedelta(days=9)
        User.objects.filter(pk=self.participant.pk).update(date_joined=old_date)
        ParticipantProfile.objects.filter(pk=self.profile.pk).update(date_joined=old_date)
        LessonProgress.objects.filter(enrollment=self.enrollment).delete()

        output = StringIO()
        call_command("generate_followups_for_inactive_participants", stdout=output)
        call_command("generate_followups_for_inactive_participants", stdout=output)

        self.assertEqual(
            FollowUpRequest.objects.filter(
                participant=self.participant,
                source=FollowUpRequest.Source.INACTIVITY,
                status=FollowUpRequest.Status.OPEN,
            ).count(),
            1,
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RenderFlowSmokeTests(EmmausFixture):
    def setUp(self):
        super().setUp()
        self.unassigned_participant = self.make_user(
            "unassigned@example.org",
            "Una",
            "Assigned",
            Role.PARTICIPANT,
        )
        self.unassigned_profile = ParticipantProfile.objects.create(
            user=self.unassigned_participant,
            church=self.church,
            assigned_mentor=None,
            consent_accepted_at=timezone.now(),
            emergency_disclaimer_accepted_at=timezone.now(),
        )
        self.question = ParticipantQuestion.objects.create(
            church=self.church,
            participant=self.participant,
            question_text="Can a leader help me understand prayer?",
        )
        self.escalated_question = ParticipantQuestion.objects.create(
            church=self.church,
            participant=self.participant,
            question_text="This needs pastoral care.",
            status=ParticipantQuestion.Status.ESCALATED,
            sensitivity_level=ParticipantQuestion.Sensitivity.PASTORAL_CARE_REQUIRED,
        )
        self.follow_up = FollowUpRequest.objects.create(
            church=self.church,
            participant=self.participant,
            created_by=self.admin,
            assigned_to=None,
            reason="Please check in personally.",
        )
        PastoralNote.objects.create(
            church=self.church,
            participant=self.participant,
            author=self.pastor,
            body="Follow up with care.",
            visibility=PastoralNote.Visibility.PASTOR_AND_MENTOR,
        )
        Invitation.objects.create(
            church=self.church,
            email="pending@example.org",
            role=Role.PARTICIPANT,
            invited_by=self.admin,
            assigned_mentor=None,
            path=self.path,
        )

    def assert_pages_render(self, user, url_names):
        self.client.force_login(user)
        for name, kwargs in url_names:
            with self.subTest(user=user.email, url=name):
                response = self.client.get(reverse(name, kwargs=kwargs))
                self.assertEqual(response.status_code, 200)

    def test_admin_core_flows_render_without_template_errors(self):
        self.assert_pages_render(
            self.admin,
            [
                ("church_dashboard", {}),
                ("participants_list", {}),
                ("user_list", {}),
                ("questions_inbox", {}),
                ("escalations", {}),
                ("church_follow_up_list", {}),
                ("notes_list", {}),
                ("invitations_list", {}),
                ("create_invitation", {}),
                ("church_settings", {}),
                ("path_list", {}),
                ("path_detail", {"path_id": self.path.id}),
                ("path_create", {}),
                ("lesson_create", {"path_id": self.path.id}),
                ("lesson_edit", {"lesson_id": self.lesson.id}),
                ("reflection_question_create", {"lesson_id": self.lesson.id}),
                ("assign_mentor", {"profile_id": self.unassigned_profile.id}),
                ("participant_detail", {"profile_id": self.unassigned_profile.id}),
                ("church_question_detail", {"question_id": self.question.id}),
            ],
        )

    def test_pastor_core_flows_render_without_template_errors(self):
        self.assert_pages_render(
            self.pastor,
            [
                ("church_dashboard", {}),
                ("participants_list", {}),
                ("questions_inbox", {}),
                ("escalations", {}),
                ("church_follow_up_list", {}),
                ("notes_list", {}),
                ("path_list", {}),
                ("participant_detail", {"profile_id": self.profile.id}),
                ("church_question_detail", {"question_id": self.escalated_question.id}),
            ],
        )

    def test_mentor_core_flows_render_without_template_errors(self):
        self.assert_pages_render(
            self.mentor,
            [
                ("mentor_dashboard", {}),
                ("participant_detail", {"profile_id": self.profile.id}),
                ("question_detail", {"question_id": self.question.id}),
                ("follow_up_list", {}),
            ],
        )

    def test_participant_core_flows_render_without_template_errors(self):
        self.assert_pages_render(
            self.participant,
            [
                ("participant_dashboard", {}),
                ("participant_lesson", {"lesson_id": self.lesson.id}),
                ("participant_questions", {}),
                ("participant_question_detail", {"question_id": self.question.id}),
                ("ask_question", {}),
                ("request_follow_up", {}),
            ],
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class NotificationPrivacyTests(EmmausFixture):
    QUESTION_TEXT = "Tengo una duda muy personal sobre mi familia."

    def assert_email_hides(self, *private_texts):
        self.assertTrue(mail.outbox, "Se esperaba al menos un correo.")
        for message in mail.outbox:
            for text in private_texts:
                self.assertNotIn(text, message.subject)
                self.assertNotIn(text, message.body)

    def test_question_created_email_does_not_include_question_text(self):
        self.client.force_login(self.participant)

        self.client.post(
            reverse("ask_question"),
            {"question_text": self.QUESTION_TEXT, "sensitivity_level": ParticipantQuestion.Sensitivity.NORMAL},
        )

        self.assertEqual(mail.outbox[0].subject, "Emmaus: hay una pregunta nueva")
        self.assert_email_hides(self.QUESTION_TEXT, "Pat", "Participant", self.participant.email)

    def test_follow_up_and_escalation_emails_do_not_include_participant_content(self):
        question = ParticipantQuestion.objects.create(
            church=self.church,
            participant=self.participant,
            question_text=self.QUESTION_TEXT,
        )
        follow_up = FollowUpRequest.objects.create(
            church=self.church,
            participant=self.participant,
            assigned_to=self.mentor,
            reason="Quiero platicar sobre algo que me pasó.",
        )

        notify_question_escalated(question)
        notify_follow_up_created(follow_up)

        self.assertEqual(len(mail.outbox), 2)
        self.assert_email_hides(self.QUESTION_TEXT, follow_up.reason, "Pat", "Participant", self.participant.email)


class LandingTests(TestCase):
    """The public front door says what Emmaus is for *this* church, with one way in."""

    def test_camp_only_church_landing_does_not_promise_discipleship(self):
        Church.objects.create(name="Iglesia del Campamento", slug="campamento")  # discipleship is off by default
        response = self.client.get(reverse("landing"))
        self.assertContains(response, "Iglesia del Campamento usa Emmaus en sus campamentos.")
        self.assertContains(response, "En el campamento")
        self.assertNotContains(response, "En el discipulado")
        self.assertNotContains(response, "borradores con IA")
        self.assertNotContains(response, "asignar mentores")
        self.assertContains(response, f'href="{reverse("login")}"')

    def test_landing_adds_discipleship_when_the_church_turns_it_on(self):
        Church.objects.create(name="Iglesia con Discipulado", slug="discipulado", discipleship_enabled=True)
        response = self.client.get(reverse("landing"))
        self.assertContains(response, "Iglesia con Discipulado usa Emmaus en sus campamentos y en su discipulado.")
        self.assertContains(response, "En el discipulado")
        self.assertContains(response, "borradores con IA")


class SeedDevDataTests(TestCase):
    @override_settings(DEBUG=False)
    def test_seed_dev_data_refuses_when_debug_is_false(self):
        with self.assertRaises(CommandError):
            call_command("seed_dev_data", password="DemoPass-12345", stdout=StringIO())

        self.assertFalse(Church.objects.filter(slug="emmaus-demo").exists())
        self.assertFalse(User.objects.filter(email="admin@example.org").exists())

    @override_settings(DEBUG=True)
    def test_seed_dev_data_uses_given_password_without_printing_it(self):
        output = StringIO()

        call_command("seed_dev_data", password="DemoPass-12345", stdout=output)

        admin = User.objects.get(email="admin@example.org")
        self.assertTrue(admin.check_password("DemoPass-12345"))
        self.assertNotIn("DemoPass-12345", output.getvalue())
        self.assertTrue(DiscipleshipPath.objects.filter(church__slug="emmaus-demo", is_default=True).exists())


class EmailAuthenticationFormTests(TestCase):
    def test_login_form_uses_spanish_labels_and_error(self):
        User.objects.create_user(username="ana@example.org", email="ana@example.org", password=PASSWORD)

        form = EmailAuthenticationForm(data={"username": "ana@example.org", "password": "incorrecta"})

        self.assertEqual(form.fields["username"].label, "Correo electrónico")
        self.assertEqual(form.fields["password"].label, "Contraseña")
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Revisa tu correo y contraseña. Si no puedes entrar, pide ayuda a la administración del evento.",
            form.non_field_errors(),
        )


class HealthTests(TestCase):
    def test_health_endpoint_returns_200(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
