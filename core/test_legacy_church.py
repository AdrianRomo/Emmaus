"""The discipleship-era screens act in the church of the request (docs/v2/wave10/legado.md, completing issue #32).

``core.views.role_required`` asks for the role the account holds in the church its request acts in, and every
view behind it reads and writes only that church. Each test here fails when that scoping is removed: the same
account is first proved able to reach the row in its own church, then proved unable from the other one.
"""

import re

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, RequestFactory
from django.urls import reverse

from core import tenancy
from core.models import (
    AIDraft,
    DiscipleshipPath,
    FollowUpRequest,
    Invitation,
    Lesson,
    ParticipantProfile,
    ParticipantQuestion,
    PastoralNote,
    QuestionResponse,
    ReflectionQuestion,
    Role,
)
from core.services import assign_role, enroll_participant_in_path
from core.test_current_church import SWITCH, TwoChurchFixture
from events.models import Event, EventMembership, MembershipRole
from events.tests.base import make_user
from people import flags
from people.models import ChurchAuditEvent


def main(response) -> str:
    return response.content.decode().split("<main", 1)[1].split("</main>", 1)[0]


class Side:
    """What one church holds, each piece with a text only that church has."""


class LegacyFixture(TwoChurchFixture):
    """Churches A and B both use the discipleship module and each holds a path with a lesson, an invitation, a
    participant with a question, a follow-up, a note and an AI draft.

    * ``dual`` (the fixture's): administrator of both, A first.
    * ``mixed``: administrator of A (first role) and **mentor** in B, where B's participant is theirs.
    * ``visitor``: administrator of A and a person of B with no role there.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for church in (cls.church, cls.church_b):
            church.discipleship_enabled = True
            church.save(update_fields=["discipleship_enabled"])
        cls.mixed = make_user("mixta@prueba.mx", "Mirna", "Mixta")
        assign_role(cls.mixed, cls.church, Role.CHURCH_ADMIN)
        assign_role(cls.mixed, cls.church_b, Role.MENTOR)
        cls._person(cls.church, cls.mixed)
        cls._person(cls.church_b, cls.mixed)
        cls.visitor = make_user("visita@prueba.mx", "Vera", "Visita")
        assign_role(cls.visitor, cls.church, Role.CHURCH_ADMIN)
        cls._person(cls.church, cls.visitor)
        cls._person(cls.church_b, cls.visitor)
        cls.mentor_a = make_user("mentor-a@prueba.mx", "Marcos", "Mentor")
        assign_role(cls.mentor_a, cls.church, Role.MENTOR)
        cls.a = cls._side(cls.church, "A", cls.admin, cls.mentor_a)
        cls.b = cls._side(cls.church_b, "B", cls.admin_b, cls.mixed)

    @classmethod
    def _side(cls, church, letter, admin, mentor) -> Side:
        side = Side()
        side.church = church
        side.path = DiscipleshipPath.objects.create(church=church, title=f"Ruta secreta {letter}", is_default=True, created_by=admin)
        side.lesson = Lesson.objects.create(path=side.path, order=1, title=f"Lección secreta {letter}", body="Texto")
        side.reflection = ReflectionQuestion.objects.create(lesson=side.lesson, order=1, question_text=f"Reflexión {letter}")
        side.invitation = Invitation.objects.create(church=church, email=f"invitada-{letter.lower()}@prueba.mx", role=Role.PARTICIPANT, invited_by=admin)
        side.participant = make_user(f"participa-{letter.lower()}@prueba.mx", f"Participante{letter}", "Secreto")
        assign_role(side.participant, church, Role.PARTICIPANT)
        side.profile = ParticipantProfile.objects.create(user=side.participant, church=church, assigned_mentor=mentor)
        enroll_participant_in_path(side.participant, side.path)
        side.question = ParticipantQuestion.objects.create(church=church, participant=side.participant, question_text=f"Pregunta secreta {letter}")
        side.follow_up = FollowUpRequest.objects.create(
            church=church, participant=side.participant, created_by=admin, assigned_to=mentor, reason=f"Motivo secreto {letter}"
        )
        side.note = PastoralNote.objects.create(
            church=church, participant=side.participant, author=admin, body=f"Nota secreta {letter}",
            visibility=PastoralNote.Visibility.PASTOR_AND_MENTOR,
        )
        side.draft = AIDraft.objects.create(church=church, related_question=side.question, generated_by=admin, generated_text=f"Borrador secreto {letter}")
        return side

    def choose(self, church, client=None, **extra):
        response = super().choose(church, client, **extra)
        self.assertEqual(response.status_code, 302)
        return response

    # Every list of these screens, and the text of each church that only it shows there.
    LISTS = {
        "church_settings": "{church}",
        "user_list": "participa-{l}@prueba.mx",
        "church_dashboard": "Participante{L} Secreto",
        "participants_list": "Participante{L} Secreto",
        "path_list": "Ruta secreta {L}",
        "invitations_list": "invitada-{l}@prueba.mx",
        "questions_inbox": "Pregunta secreta {L}",
        "church_follow_up_list": "Motivo secreto {L}",
        "notes_list": "Nota secreta {L}",
    }

    def mark(self, pattern, side) -> str:
        letter = "A" if side is self.a else "B"
        return pattern.format(church=side.church.name, l=letter.lower(), L=letter)

    def detail_urls(self, side) -> list[str]:
        """Every address of these screens that names a row by its id."""
        return [
            reverse("path_detail", args=[side.path.pk]),
            reverse("path_edit", args=[side.path.pk]),
            reverse("lesson_create", args=[side.path.pk]),
            reverse("lesson_edit", args=[side.lesson.pk]),
            reverse("reflection_question_create", args=[side.lesson.pk]),
            reverse("participant_detail", args=[side.profile.pk]),
            reverse("assign_mentor", args=[side.profile.pk]),
            reverse("church_question_detail", args=[side.question.pk]),
            reverse("question_detail", args=[side.question.pk]),
            reverse("complete_follow_up", args=[side.follow_up.pk]),
        ]


class ListsTests(LegacyFixture):
    def test_each_list_shows_only_the_church_of_the_request(self):
        self.client.force_login(self.dual)
        for here, there in ((self.a, self.b), (self.b, self.a), (self.a, self.b)):  # A, then B, then back in A
            self.choose(here.church)
            for name, pattern in self.LISTS.items():
                with self.subTest(church=here.church.name, page=name):
                    response = self.client.get(reverse(name))
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(self.mark(pattern, here), main(response))
                    self.assertNotIn(self.mark(pattern, there), main(response))
                    self.assertNotIn("secret" + ("a B" if here is self.a else "a A"), main(response))

    def test_escalations_are_the_churchs(self):
        ParticipantQuestion.objects.update(status=ParticipantQuestion.Status.ESCALATED)
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        page = main(self.client.get(reverse("escalations")))
        self.assertIn("Pregunta secreta B", page)
        self.assertNotIn("Pregunta secreta A", page)

    def test_the_settings_form_is_the_church_of_the_request(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        form = self.client.get(reverse("church_settings")).context["form"]
        self.assertEqual(form.instance.pk, self.church_b.pk)


class GuessedIdTests(LegacyFixture):
    def test_a_row_of_the_other_church_is_not_found_by_its_id(self):
        self.client.force_login(self.dual)
        for url in self.detail_urls(self.a):
            if "complete" not in url:  # that one only redirects
                self.assertEqual(self.client.get(url).status_code, 200, url)  # in A, the administrator of A opens each one
        self.choose(self.church_b)
        for url in self.detail_urls(self.a):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        for url in self.detail_urls(self.b):
            if "complete" not in url:
                self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_a_post_to_a_row_of_the_other_church_changes_nothing(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        a = self.a
        posts = [
            (reverse("path_edit", args=[a.path.pk]), {"title": "Robada", "is_active": "on"}),
            (reverse("lesson_create", args=[a.path.pk]), {"order": 2, "title": "Colada", "body": "Texto"}),
            (reverse("lesson_edit", args=[a.lesson.pk]), {"order": 1, "title": "Robada", "body": "Texto"}),
            (reverse("reflection_question_create", args=[a.lesson.pk]), {"order": 2, "question_text": "Colada"}),
            (reverse("assign_mentor", args=[a.profile.pk]), {"mentor": self.mixed.pk}),
            (reverse("complete_follow_up", args=[a.follow_up.pk]), {}),
            (reverse("participant_detail", args=[a.profile.pk]), {"action": "note", "note-body": "Colada", "note-visibility": "pastor_only"}),
            (reverse("participant_detail", args=[a.profile.pk]), {"action": "follow", "follow-participant": a.participant.pk, "follow-reason": "Colado"}),
            (reverse("church_question_detail", args=[a.question.pk]), {"action": "respond", "response-response_body": "Colada"}),
            (reverse("church_question_detail", args=[a.question.pk]), {"action": "escalate"}),
            (reverse("church_question_detail", args=[a.question.pk]), {"action": "generate_ai"}),
            (reverse("church_question_detail", args=[a.question.pk]), {"action": "review_ai", "draft_id": a.draft.pk, "review_action": "approve"}),
        ]
        for url, data in posts:
            with self.subTest(url=url, data=data):
                self.assertEqual(self.client.post(url, data).status_code, 404)
        a.path.refresh_from_db()
        a.lesson.refresh_from_db()
        a.profile.refresh_from_db()
        a.follow_up.refresh_from_db()
        a.question.refresh_from_db()
        a.draft.refresh_from_db()
        self.assertEqual(a.path.title, "Ruta secreta A")
        self.assertEqual(a.lesson.title, "Lección secreta A")
        self.assertEqual(a.path.lessons.count(), 1)
        self.assertEqual(a.lesson.reflection_questions.count(), 1)
        self.assertEqual(a.profile.assigned_mentor, self.mentor_a)
        self.assertEqual(a.follow_up.status, FollowUpRequest.Status.OPEN)
        self.assertEqual(a.question.status, ParticipantQuestion.Status.OPEN)
        self.assertEqual(a.draft.status, AIDraft.Status.DRAFT)
        self.assertEqual(PastoralNote.objects.filter(church=self.church).count(), 1)
        self.assertEqual(FollowUpRequest.objects.filter(church=self.church).count(), 1)
        self.assertFalse(QuestionResponse.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_a_draft_of_the_other_church_is_not_reviewed_through_a_question_of_this_one(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        url = reverse("church_question_detail", args=[self.b.question.pk])
        response = self.client.post(url, {"action": "review_ai", "draft_id": self.a.draft.pk, "review_action": "approve"})
        self.assertEqual(response.status_code, 404)
        self.a.draft.refresh_from_db()
        self.assertEqual(self.a.draft.status, AIDraft.Status.DRAFT)

    def test_a_form_never_offers_or_accepts_a_person_of_the_other_church(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        page = self.client.get(reverse("church_follow_up_list"))
        self.assertNotContains(page, "ParticipanteA")
        response = self.client.post(reverse("church_follow_up_list"), {"participant": self.a.participant.pk, "reason": "Colado"})
        self.assertEqual(response.status_code, 200)  # the form came back with its error
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(FollowUpRequest.objects.count(), 2)
        response = self.client.post(reverse("assign_mentor", args=[self.b.profile.pk]), {"mentor": self.mentor_a.pk})
        self.assertTrue(response.context["form"].errors)  # a mentor of church A is not a choice in B
        self.b.profile.refresh_from_db()
        self.assertEqual(self.b.profile.assigned_mentor, self.mixed)


class WritesTests(LegacyFixture):
    def test_what_is_created_belongs_to_the_church_of_the_request(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        self.client.post(reverse("path_create"), {"title": "Ruta nueva", "is_active": "on"})
        self.assertEqual(DiscipleshipPath.objects.get(title="Ruta nueva").church, self.church_b)
        self.client.post(reverse("create_invitation"), {"email": "nueva@prueba.mx", "role": Role.MENTOR})
        self.assertEqual(Invitation.objects.get(email="nueva@prueba.mx").church, self.church_b)
        self.client.post(reverse("church_follow_up_list"), {"participant": self.b.participant.pk, "reason": "Nuevo"})
        self.assertEqual(FollowUpRequest.objects.get(reason="Nuevo").church, self.church_b)
        self.client.post(reverse("lesson_create", args=[self.b.path.pk]), {"order": 2, "title": "Otra lección", "body": "Texto"})
        self.assertEqual(Lesson.objects.get(title="Otra lección").path, self.b.path)

    def test_the_settings_are_saved_in_the_church_of_the_request(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        form = self.client.get(reverse("church_settings")).context["form"]
        data = {name: value for name, value in form.initial.items() if value not in (None, False) and not hasattr(value, "name")}
        data["name"] = "Iglesia B renombrada"
        response = self.client.post(reverse("church_settings"), data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["form"].errors)
        self.church.refresh_from_db()
        self.church_b.refresh_from_db()
        self.assertEqual(self.church_b.name, "Iglesia B renombrada")
        self.assertEqual(self.church.name, "Iglesia de prueba")

    def test_a_settings_form_opened_in_one_church_never_saves_into_the_other(self):
        """The settings form names no church. Opened in A and sent after the session moved to B, it is refused."""
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.dual)
        page = browser.get(reverse("church_settings"))
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        data = {name: value for name, value in page.context["form"].initial.items() if value not in (None, False) and not hasattr(value, "name")}
        data.update(name="Nombre escrito en A", csrfmiddlewaretoken=token)
        self.assertEqual(browser.post(SWITCH, {"church": self.church_b.pk, "csrfmiddlewaretoken": token}).status_code, 302)
        response = browser.post(reverse("church_settings"), data)
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Esta pantalla ya no está al día", status_code=403)
        self.church.refresh_from_db()
        self.church_b.refresh_from_db()
        self.assertEqual((self.church.name, self.church_b.name), ("Iglesia de prueba", "Iglesia B"))


class RoleOfTheRequestTests(LegacyFixture):
    """``mixed`` administers A and is a mentor in B: each request has the role of its own church."""

    ADMIN_ONLY = ("church_settings",)
    PASTORAL = ("user_list", "church_dashboard", "participants_list", "path_list", "questions_inbox", "notes_list", "path_create")

    def test_the_role_is_the_one_held_in_the_church_of_the_request(self):
        # a follow-up of church A in their own name: theirs as A's administration, never part of B's mentoring
        FollowUpRequest.objects.create(church=self.church, participant=self.a.participant, assigned_to=self.mixed, reason="Motivo de A a su nombre")
        self.client.force_login(self.mixed)
        for name in self.ADMIN_ONLY + self.PASTORAL:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("mentor_dashboard")).status_code, 403)  # in A they are no mentor
        self.choose(self.church_b)
        for name in self.ADMIN_ONLY + self.PASTORAL:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)  # a mentor of B, not its administration
        self.assertEqual(self.client.post(reverse("church_settings"), {"name": "Tomada"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("path_create"), {"title": "Colada", "is_active": "on"}).status_code, 403)
        self.church_b.refresh_from_db()
        self.assertEqual(self.church_b.name, "Iglesia B")
        self.assertFalse(DiscipleshipPath.objects.filter(title="Colada").exists())
        home = self.client.get(reverse("mentor_dashboard"))
        self.assertEqual(home.status_code, 200)
        self.assertIn("ParticipanteB", main(home))
        self.assertNotIn("ParticipanteA", main(home))
        self.assertIn("Motivo secreto B", main(home))
        self.assertNotIn("Motivo secreto A", main(home))
        self.assertNotIn("Motivo de A a su nombre", main(home))
        self.assertNotIn("Motivo de A a su nombre", main(self.client.get(reverse("follow_up_list"))))

    def test_administering_one_church_opens_no_person_of_it_from_the_other(self):
        self.client.force_login(self.mixed)
        self.assertEqual(self.client.get(reverse("participant_detail", args=[self.a.profile.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("church_question_detail", args=[self.a.question.pk])).status_code, 200)
        self.choose(self.church_b)
        for name, pk in (("participant_detail", self.a.profile.pk), ("question_detail", self.a.question.pk),
                         ("church_question_detail", self.a.question.pk), ("complete_follow_up", self.a.follow_up.pk)):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name, args=[pk])).status_code, 404)
                self.assertEqual(self.client.post(reverse(name, args=[pk]), {"action": "respond", "response-response_body": "x"}).status_code, 404)
        # and as B's mentor, their own participant opens, with the notes a mentor may read and no other church's
        page = self.client.get(reverse("participant_detail", args=[self.b.profile.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertIn("Nota secreta B", main(page))
        self.assertNotIn("secreta A", main(page))
        mentor_list = main(self.client.get(reverse("follow_up_list")))
        self.assertIn("Motivo secreto B", mentor_list)
        self.assertNotIn("Motivo secreto A", mentor_list)

    def test_a_mentor_of_one_church_answers_only_there(self):
        self.client.force_login(self.mixed)
        self.choose(self.church_b)
        url = reverse("question_detail", args=[self.b.question.pk])
        self.assertEqual(self.client.post(url, {"action": "respond", "response-response_body": "Respuesta de su mentora"}).status_code, 302)
        self.assertEqual(QuestionResponse.objects.get().question, self.b.question)

    def test_the_bar_offers_the_links_of_the_role_held_here(self):
        self.client.force_login(self.mixed)
        home = self.client.get(reverse("community:home")).content.decode()
        self.assertIn(reverse("church_settings"), home)
        self.assertNotIn(reverse("mentor_dashboard"), home)
        self.choose(self.church_b)
        home = self.client.get(reverse("community:home")).content.decode()
        self.assertNotIn(reverse("church_settings"), home)
        self.assertNotIn(reverse("path_list"), home)
        self.assertIn(reverse("mentor_dashboard"), home)
        # their home is the mentor's: the events of church A, which they administer, are not this church's
        self.assertRedirects(self.client.get(reverse("dashboard")), reverse("mentor_dashboard"), fetch_redirect_response=False)
        self.choose(self.church)
        self.assertRedirects(self.client.get(reverse("dashboard")), reverse("events:list"), fetch_redirect_response=False)

    def test_a_person_of_the_church_with_a_role_elsewhere_finds_no_page(self):
        self.client.force_login(self.visitor)
        self.assertEqual(self.client.get(reverse("church_settings")).status_code, 200)
        self.choose(self.church_b)
        for name in ("church_settings", "user_list", "path_list", "mentor_dashboard", "participant_dashboard", "invitations_list"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 404)
        for url in self.detail_urls(self.b) + self.detail_urls(self.a):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.post(reverse("church_settings"), {"name": "Tomada"}).status_code, 404)
        home = self.client.get(reverse("community:home")).content.decode()
        self.assertNotIn(reverse("church_settings"), home)
        self.assertNotIn("Administrador", home.split('class="rail__foot"', 1)[1].split("</a>", 1)[0])

    def test_signed_out_and_an_account_with_no_church(self):
        for name in ("church_settings", "path_list", "mentor_dashboard"):
            self.assertRedirects(self.client.get(reverse(name)), f"{reverse('login')}?next={reverse(name)}", fetch_redirect_response=False)
        self.client.force_login(self.outsider)  # works in an event; no church role, no person
        response = self.client.get(reverse("church_settings"))
        self.assertRedirects(response, reverse("landing"), fetch_redirect_response=False)

    def test_a_role_in_a_closed_church_is_what_it_was(self):
        """An account whose only church was closed has no church of the request: these screens keep following its
        role, as before (decision 0017, point 6)."""
        self.church_b.active = False
        self.church_b.save(update_fields=["active"])
        self.client.force_login(self.admin_b)
        page = self.client.get(reverse("path_list"))
        self.assertEqual(page.status_code, 200)
        self.assertIn("Ruta secreta B", main(page))

    def test_the_resolver_reads_the_role_of_the_church_and_no_other(self):
        request = RequestFactory().get("/")
        request.user, request.session = self.mixed, self.client.session
        where = tenancy.resolve(request)
        self.assertEqual((where.church, where.role.role), (self.church, Role.CHURCH_ADMIN))
        request.session["current_church"] = self.church_b.pk
        where = tenancy.resolve(request)
        self.assertEqual((where.church, where.role.role, where.role.church), (self.church_b, Role.MENTOR, self.church_b))
        request.user = self.visitor
        self.assertIsNone(tenancy.resolve(request).role)


class ParticipantTests(LegacyFixture):
    """An account has one path (``ParticipantProfile``). Its screens exist only in that profile's church."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.walker = cls.a.participant
        assign_role(cls.walker, cls.church_b, Role.PARTICIPANT)  # the same role in B, where they have no path
        cls._person(cls.church, cls.walker)
        cls._person(cls.church_b, cls.walker)

    def test_their_path_opens_in_its_church(self):
        self.client.force_login(self.walker)
        for name in ("participant_dashboard", "participant_questions", "ask_question", "request_follow_up"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("participant_lesson", args=[self.a.lesson.pk])).status_code, 200)
        self.assertIn("Pregunta secreta A", main(self.client.get(reverse("participant_questions"))))
        self.assertIn(reverse("participant_dashboard"), self.client.get(reverse("community:home")).content.decode())

    def test_in_another_church_it_is_not_there_and_nothing_is_written(self):
        self.client.force_login(self.walker)
        self.choose(self.church_b)
        for name in ("participant_dashboard", "participant_questions", "ask_question", "request_follow_up"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 404)
        self.assertEqual(self.client.get(reverse("participant_lesson", args=[self.a.lesson.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("participant_question_detail", args=[self.a.question.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("ask_question"), {"question_text": "Escrita en B"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("request_follow_up"), {"reason": "Pedida en B"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("participant_lesson", args=[self.a.lesson.pk]), {"mark_complete": "1"}).status_code, 404)
        self.assertFalse(ParticipantQuestion.objects.filter(question_text="Escrita en B").exists())
        self.assertFalse(FollowUpRequest.objects.filter(reason="Pedida en B").exists())
        self.assertFalse(self.a.path.enrollments.filter(lesson_progress__completed_at__isnull=False).exists())
        home = self.client.get(reverse("community:home")).content.decode()
        self.assertNotIn(reverse("participant_dashboard"), home)
        self.assertRedirects(self.client.get(reverse("dashboard")), reverse("community:home"), fetch_redirect_response=False)

    def test_a_participant_role_without_a_path_gets_no_page(self):
        lone = make_user("sin-camino@prueba.mx", "Sara", "Sin Camino")
        assign_role(lone, self.church, Role.PARTICIPANT)
        self.client.force_login(lone)
        self.assertEqual(self.client.get(reverse("participant_dashboard")).status_code, 404)

    def test_the_church_sees_only_what_the_person_wrote_in_it(self):
        """A row of the same person in another church (a profile that was moved) is not shown to this one."""
        ParticipantQuestion.objects.create(church=self.church_b, participant=self.walker, question_text="Escrita en la otra")
        FollowUpRequest.objects.create(church=self.church_b, participant=self.walker, reason="Pedido en la otra")
        self.client.force_login(self.admin)
        page = main(self.client.get(reverse("participant_detail", args=[self.a.profile.pk])))
        self.assertIn("Pregunta secreta A", page)
        self.assertNotIn("Escrita en la otra", page)
        self.assertNotIn("Pedido en la otra", page)
        self.client.force_login(self.walker)
        mine = main(self.client.get(reverse("participant_questions"))) + main(self.client.get(reverse("participant_dashboard")))
        self.assertNotIn("Escrita en la otra", mine)
        self.assertNotIn("Pedido en la otra", mine)


class EventListTests(LegacyFixture):
    """The event list and "Mi cuenta" are church screens: the events of the church of the request, and those where
    the account holds an event role of its own."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Event.objects.filter(pk__in=[cls.event.pk, cls.event_b.pk]).update(status=Event.Status.ACTIVE)

    def names(self, url=None) -> str:
        return main(self.client.get(url or reverse("events:list") + "?todos=1"))

    def test_a_church_role_lists_the_events_of_the_church_of_the_request(self):
        self.client.force_login(self.dual)
        page = self.names()
        self.assertIn("Campamento A", page)
        self.assertNotIn("Retiro B", page)
        self.assertNotIn("Es un evento de", page)
        self.assertNotIn("Retiro B", self.names(reverse("account")))
        self.choose(self.church_b)
        page = self.names()
        self.assertIn("Retiro B", page)
        self.assertNotIn("Campamento A", page)
        account = self.names(reverse("account"))
        self.assertIn("Retiro B", account)
        self.assertNotIn("Campamento A", account)
        self.assertIn("En <strong>Iglesia B</strong>: administración de la iglesia.", account)
        # Not listed is not closed: the event of the other church still answers by its own rule (events/access.py)
        self.assertEqual(self.client.get(reverse("events:admin_home", args=[self.event.slug])).status_code, 200)

    def test_an_event_role_stays_listed_and_says_whose_event_it_is(self):
        EventMembership.objects.create(event=self.event, user=self.dual, role=MembershipRole.TUTOR, team_name="Equipo Este")
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        page = self.names()
        self.assertIn("Campamento A", page)
        self.assertIn("Es un evento de Iglesia de prueba.", page)
        self.assertEqual(page.count("Es un evento de"), 1)
        self.assertLess(page.index("Retiro B"), page.index("Campamento A"))  # this church's first
        self.assertNotIn("Campamento B", page)  # A's other event, reached only through the church role there

    def test_the_way_in_goes_only_to_an_event_that_is_listed(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        session = self.client.session
        session["current_event"] = self.event.slug  # the last event opened, of the other church
        session.save()
        response = self.client.get(reverse("events:list"))  # B has two events: the list, never the other church's event
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Campamento A", main(response))
        self.choose(self.church)
        self.assertRedirects(self.client.get(reverse("events:list")), reverse("events:home", args=[self.event.slug]), fetch_redirect_response=False)

    def test_an_event_is_created_in_the_church_of_the_request_by_who_administers_it(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        self.assertContains(self.client.get(reverse("events:list") + "?todos=1"), reverse("events:create"))
        form = self.client.get(reverse("events:create")).context["form"]
        data = {name: field.initial if form.initial.get(name) is None else form.initial[name] for name, field in form.fields.items()}
        data = {name: value for name, value in data.items() if value not in (None, False)}
        data.update(name="Campamento creado en B", slug="creado-en-b", starts_on="2027-07-01", ends_on="2027-07-03")
        response = self.client.post(reverse("events:create"), data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["form"].errors)
        self.assertEqual(Event.objects.get(name="Campamento creado en B").church, self.church_b)

    def test_administering_another_church_creates_nothing_here(self):
        for user in (self.mixed, self.visitor):  # a mentor of B; a person of B: both administer A
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("events:create")).status_code, 200)
            self.choose(self.church_b)
            self.assertNotIn(reverse("events:create"), self.names())
            self.assertEqual(self.client.get(reverse("events:create")).status_code, 403)
            self.assertEqual(self.client.post(reverse("events:create"), {"name": "Colado"}).status_code, 403)
            self.assertFalse(Event.objects.filter(name="Colado").exists())
            self.client.logout()

    def test_the_welcome_is_the_one_of_the_role_held_here(self):
        self.client.force_login(self.mixed)
        self.assertContains(self.client.get(reverse("events:list") + "?todos=1"), "En Iglesia de prueba administras Emmaus.")
        self.choose(self.church_b)
        page = self.client.get(reverse("events:list") + "?todos=1")
        self.assertContains(page, "En Iglesia B acompañas a quienes te asignan.")
        self.assertNotContains(page, "administras Emmaus")


class EventPageTests(LegacyFixture):
    """A camp screen is a page about its event's church: its colours for who is part of that church, and never the
    general links of another church under its name."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Event.objects.filter(pk__in=[cls.event.pk, cls.event_b.pk]).update(status=Event.Status.ACTIVE)
        for church, colour in ((cls.church, "#112233"), (cls.church_b, "#445566")):
            church.custom_theme_enabled = True
            church.theme_primary_color = colour
            church.save(update_fields=["custom_theme_enabled", "theme_primary_color"])
        cls.url_a = reverse("events:admin_home", args=[cls.event.slug])

    def rail(self, response) -> str:
        return response.content.decode().split('<header class="rail', 1)[1].split("</header>", 1)[0]

    def test_in_its_own_church_the_page_is_what_it_was(self):
        self.client.force_login(self.dual)
        page = self.client.get(self.url_a)
        self.assertContains(page, "--petroleo:#112233")
        self.assertIn(reverse("community:home"), self.rail(page))
        self.assertIn(reverse("church_settings"), self.rail(page))
        self.assertIn('<p class="rail__church">Iglesia de prueba</p>', self.rail(page))

    def test_a_reader_of_both_churches_gets_the_events_colours_and_not_the_other_churchs_links(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        page = self.client.get(self.url_a)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "--petroleo:#112233")
        self.assertNotContains(page, "#445566")
        rail = self.rail(page)
        self.assertIn('<p class="rail__church">Iglesia de prueba</p>', rail)
        for name in ("community:home", "notices:list", "agenda:list", "people:list", "church_settings", "church_policies"):
            self.assertNotIn(f'href="{reverse(name)}"', rail, name)
        self.assertIn(reverse("events:list") + "?todos=1", rail)
        self.assertIn(reverse("events:participants", args=[self.event.slug]), rail)  # the event's own links stay
        self.assertNotIn("Cambiar a ", rail)
        self.assertNotIn("Administración de la iglesia", rail.split('class="rail__foot"', 1)[1])  # B's role, not this church's
        panel = page.content.decode().split('id="menu-panel"', 1)[1]
        self.assertNotIn(f'href="{reverse("community:home")}"', panel)
        # every link the rail draws opens
        for href in re.findall(r'<a class="rail__link[^"]*" href="([^"]+)"', rail):
            self.assertIn(self.client.get(href).status_code, (200, 302), href)

    def test_an_event_role_alone_keeps_its_colours_and_loses_the_links(self):
        """``member_b`` is a person of church B only and a tutor in A's event: no part of church A."""
        EventMembership.objects.create(event=self.event, user=self.member_b_user, role=MembershipRole.TUTOR, team_name="Equipo Este")
        self.client.force_login(self.member_b_user)
        page = self.client.get(reverse("events:tutor_home", args=[self.event.slug]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "--petroleo:#445566")  # the reader's own church, as before
        self.assertNotContains(page, "#112233")
        rail = self.rail(page)
        self.assertIn('<p class="rail__church">Iglesia de prueba</p>', rail)
        self.assertNotIn(f'href="{reverse("community:home")}"', rail)
        self.assertNotIn(f'href="{reverse("agenda:list")}"', rail)
        self.assertIn(reverse("events:list") + "?todos=1", rail)
        # in an event of their own church the rail is the whole one
        own = self.client.get(reverse("events:coord_home", args=[self.event_b.slug]))
        self.assertIn(f'href="{reverse("community:home")}"', self.rail(own))

    def test_an_account_with_no_church_keeps_the_bar_it_had(self):
        self.client.force_login(self.tutor1)
        page = self.client.get(reverse("events:tutor_home", args=[self.event.slug]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, '<header class="rail')
        self.assertNotContains(page, "--petroleo:#112233")


class ForbiddenPageTests(LegacyFixture):
    BOTH = "pide a la administración de tu iglesia o del evento que te dé acceso."
    CAMP = "pide a la administración del evento que te dé acceso."

    def test_a_church_with_the_community_module_reads_both(self):
        self.client.force_login(self.member_user)
        response = self.client.get(reverse("people:list"))
        self.assertContains(response, self.BOTH, status_code=403)
        self.assertNotContains(response, self.CAMP, status_code=403)

    def test_with_the_module_off_the_page_is_the_camps(self):
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.mentor_a)
        response = self.client.get(reverse("church_settings"))
        self.assertContains(response, self.CAMP, status_code=403)
        self.assertNotContains(response, "de tu iglesia o del evento", status_code=403)


class SwitchAuditTests(LegacyFixture):
    def rows(self, church):
        return ChurchAuditEvent.objects.filter(church=church, action="iglesia_elegida", actor=self.dual).count()

    def test_one_row_per_church_per_hour_and_the_switch_is_never_refused(self):
        from datetime import timedelta

        from django.utils import timezone

        self.client.force_login(self.dual)
        for _ in range(4):
            self.choose(self.church_b)
            self.assertEqual(self.client.session["current_church"], self.church_b.pk)
            self.choose(self.church)
            self.assertEqual(self.client.session["current_church"], self.church.pk)
        self.assertEqual((self.rows(self.church), self.rows(self.church_b)), (1, 1))
        # another account is counted by itself
        self.client.force_login(self.dual_member)
        self.choose(self.church_b)
        self.assertEqual(ChurchAuditEvent.objects.filter(church=self.church_b, action="iglesia_elegida").count(), 2)
        # after the hour, the next one is written again
        ChurchAuditEvent.objects.filter(action="iglesia_elegida").update(created_at=timezone.now() - timedelta(minutes=61))
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        self.assertEqual(self.rows(self.church_b), 2)


class OneChurchTests(LegacyFixture):
    """An account of one church: the role of the request is the role it always had."""

    def test_the_roles_of_church_a_open_what_they_opened(self):
        pastor = make_user("pastora-a@prueba.mx", "Paz", "Pastora")
        assign_role(pastor, self.church, Role.PASTOR)
        expected = {
            self.admin: {"church_settings": 200, "path_list": 200, "mentor_dashboard": 403, "participant_dashboard": 403},
            pastor: {"church_settings": 403, "path_list": 200, "church_dashboard": 200, "mentor_dashboard": 403},
            self.mentor_a: {"church_settings": 403, "path_list": 403, "mentor_dashboard": 200, "follow_up_list": 200},
            self.a.participant: {"church_settings": 403, "mentor_dashboard": 403, "participant_dashboard": 200},
        }
        for user, pages in expected.items():
            self.client.force_login(user)
            for name, status in pages.items():
                with self.subTest(user=user.email, page=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, status)
            for url in self.detail_urls(self.b):
                self.assertIn(self.client.get(url).status_code, (403, 404), url)

    def test_a_forged_session_value_opens_nothing_of_the_other_church(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session["current_church"] = self.church_b.pk
        session.save()
        page = main(self.client.get(reverse("path_list")))
        self.assertIn("Ruta secreta A", page)
        self.assertNotIn("Ruta secreta B", page)
        self.assertEqual(self.client.get(reverse("path_detail", args=[self.b.path.pk])).status_code, 404)
        self.assertEqual(User.objects.get(pk=self.admin.pk).church_roles.count(), 1)


class RoleWordsTests(LegacyFixture):
    """The role is named to its holder by the work, not by a gendered noun; "Pastor" is the churches' own title."""

    def test_the_rail_and_my_account_say_mentoria(self):
        self.client.force_login(self.mixed)
        self.choose(self.church_b)
        home = self.client.get(reverse("community:home")).content.decode()
        foot = home.split('class="rail__foot"', 1)[1].split("</a>", 1)[0]
        self.assertIn("Mentoría", foot)
        self.assertNotIn("Mentor<", foot)
        self.assertIn("En <strong>Iglesia B</strong>: mentoría.", main(self.client.get(reverse("account"))))
        self.assertNotIn("eres mentor", main(self.client.get(reverse("mentor_dashboard"))))
        self.choose(self.church)
        self.assertIn("Administración de la iglesia", self.client.get(reverse("community:home")).content.decode().split('class="rail__foot"', 1)[1].split("</a>", 1)[0])

    def test_the_two_links_of_the_mentor_have_their_own_icons(self):
        from core.shell import MODULE_ICONS

        self.assertNotEqual(MODULE_ICONS["mentor_dashboard"], MODULE_ICONS["follow_up_list"])

    def test_the_welcome_goes_after_the_work_when_something_waits(self):
        self.client.force_login(self.mixed)
        self.choose(self.church_b)
        page = main(self.client.get(reverse("mentor_dashboard")))  # a question and a follow-up wait
        self.assertEqual(page.count('id="bienvenida"'), 1)
        self.assertLess(page.index("espera tu respuesta"), page.index('id="bienvenida"'))
        ParticipantQuestion.objects.filter(church=self.church_b).delete()
        FollowUpRequest.objects.filter(church=self.church_b).delete()
        page = main(self.client.get(reverse("mentor_dashboard")))
        self.assertLess(page.index('id="bienvenida"'), page.index('id="acompanas"'))
