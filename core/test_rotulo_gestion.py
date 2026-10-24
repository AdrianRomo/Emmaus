"""Discipleship management screens on the "Rótulo" design system (church dashboard, settings, people, paths)."""

from __future__ import annotations

import re

from django.contrib.auth.models import User
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import Church, FollowUpRequest, ParticipantProfile, ParticipantQuestion, PastoralNote, Role, UserChurchRole
from .tests import EmmausFixture

# Classes of the previous design system ("Cuaderno de campo") that a migrated screen must not use.
OLD_CLASS = re.compile(
    r"^(ledger.*|figure-row|chip.*|meter.*|q-.*|folio|page-head__row|section__head|subhead|tab-link|split--even|crumbs|rail.*"
    r"|stat-card.*|card.*|empty|empty__.*|btn--quiet|btn--sm|btn--hero|grid-auto|panel|sheet)$"
)


def class_tokens(html: str) -> set[str]:
    return {token for value in re.findall(r'class="([^"]*)"', html) for token in value.split()}


def signs(html: str) -> int:
    """How many yellow signs (`.sign`, not its `.sign__*` parts) a page draws."""
    return len(re.findall(r'class="sign[\s"]', html))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class DiscipleshipManagementRotuloTests(EmmausFixture):
    def setUp(self):
        super().setUp()
        self.waiting = User.objects.create_user(username="waiting@example.org", email="waiting@example.org", first_name="Wendy", last_name="Waiting")
        ParticipantProfile.objects.create(user=self.waiting, church=self.church)
        PastoralNote.objects.create(
            church=self.church, participant=self.participant, author=self.pastor,
            body="Only for pastors.", visibility=PastoralNote.Visibility.PASTOR_ONLY,
        )
        FollowUpRequest.objects.create(church=self.church, participant=self.participant, created_by=self.admin, reason="Check in.")

    def pages(self):
        profile_id = self.profile.id
        return [
            ("church_dashboard", {}), ("church_settings", {}), ("participants_list", {}),
            ("participant_detail", {"profile_id": profile_id}), ("assign_mentor", {"profile_id": profile_id}),
            ("invitations_list", {}), ("create_invitation", {}), ("path_list", {}), ("path_detail", {"path_id": self.path.id}),
            ("path_create", {}), ("path_edit", {"path_id": self.path.id}), ("lesson_create", {"path_id": self.path.id}),
            ("lesson_edit", {"lesson_id": self.lesson.id}), ("reflection_question_create", {"lesson_id": self.lesson.id}),
            ("user_list", {}), ("notes_list", {}),
        ]

    def test_screens_use_rotulo_markup_only(self):
        self.client.force_login(self.admin)
        for name, kwargs in self.pages():
            with self.subTest(url=name):
                response = self.client.get(reverse(name, kwargs=kwargs))
                html = response.content.decode()
                self.assertEqual(response.status_code, 200)
                self.assertIn("css/rotulo.css", html)
                self.assertEqual(len(re.findall(r"<h1\b", html)), 1)
                self.assertEqual(sorted(t for t in class_tokens(html) if OLD_CLASS.match(t)), [])
                self.assertNotIn("·", html.split("<main", 1)[1])
                self.assertLessEqual(signs(html), 1)

    def test_dashboard_sign_only_for_escalated_questions(self):
        self.client.force_login(self.pastor)
        url = reverse("church_dashboard")
        self.assertEqual(signs(self.client.get(url).content.decode()), 0)

        question = ParticipantQuestion.objects.create(
            church=self.church, participant=self.participant, question_text="Needs a pastor.", status=ParticipantQuestion.Status.ESCALATED,
        )
        response = self.client.get(url)
        self.assertEqual(signs(response.content.decode()), 1)
        self.assertContains(response, "Pat Participant espera una respuesta pastoral")
        self.assertContains(response, reverse("church_question_detail", args=[question.id]))

        ParticipantQuestion.objects.create(
            church=self.church, participant=self.other_participant, question_text="Also needs a pastor.", status=ParticipantQuestion.Status.ESCALATED,
        )
        response = self.client.get(url)
        self.assertContains(response, "2 preguntas escaladas esperan respuesta")
        self.assertContains(response, f'href="{reverse("escalations")}">Ver preguntas escaladas')

    def test_participant_record_keeps_pastoral_guards(self):
        url = reverse("participant_detail", kwargs={"profile_id": self.profile.id})
        self.client.force_login(self.pastor)
        pastor_view = self.client.get(url)
        self.assertContains(pastor_view, "Only for pastors.")
        self.assertContains(pastor_view, reverse("assign_mentor", args=[self.profile.id]))

        self.client.force_login(self.mentor)
        mentor_view = self.client.get(url)
        self.assertEqual(mentor_view.status_code, 200)
        self.assertNotContains(mentor_view, "Only for pastors.")
        self.assertNotContains(mentor_view, reverse("assign_mentor", args=[self.profile.id]))
        self.assertContains(mentor_view, "Confidencial")

    def test_participants_list_offers_a_mentor_to_whoever_lacks_one(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("participants_list"))
        waiting = ParticipantProfile.objects.get(user=self.waiting)
        self.assertContains(response, reverse("assign_mentor", args=[waiting.id]))
        self.assertNotContains(response, reverse("assign_mentor", args=[self.profile.id]))
        self.assertContains(response, 'class="ruler"')

    def test_settings_keep_the_theme_colour_pickers(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("church_settings"))
        self.assertContains(response, 'type="color"', count=8)

    def test_user_list_groups_people_by_role(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("user_list")).content.decode()
        headings = re.findall(r'class="heading">([^<]+?) <span class="count">(\d+)</span>', html)
        self.assertEqual(headings, [("Administración de la iglesia", "1"), ("Mentor", "2"), ("Participante", "2"), ("Pastor", "1")])
        self.assertIn(f'class="back-link" href="{reverse("church_settings")}"', html)

    def test_user_list_uses_this_churchs_role(self):
        """A mentor here who is a participant in another church is listed as a mentor, not by whichever role came first."""
        other = Church.objects.create(name="Otra iglesia", slug="otra")
        UserChurchRole.objects.filter(user=self.mentor).delete()
        UserChurchRole.objects.create(user=self.mentor, church=other, role=Role.PARTICIPANT)
        UserChurchRole.objects.create(user=self.mentor, church=self.church, role=Role.MENTOR)
        self.client.force_login(self.admin)
        self.client.get(reverse("user_list"))  # warm-up: the first request also saves the session
        with CaptureQueriesContext(connection) as before:
            html = self.client.get(reverse("user_list")).content.decode()
        for n in range(3):
            self.make_user(f"extra{n}@example.org", "Extra", str(n), Role.PARTICIPANT)
        with CaptureQueriesContext(connection) as after:
            self.client.get(reverse("user_list"))
        self.assertEqual(len(after), len(before))  # no query per person
        mentors = html.split('Mentor <span class="count">', 1)[1].split("</section>", 1)[0]
        self.assertIn(self.mentor.email, mentors)
        self.assertNotIn("Otra iglesia", html)
