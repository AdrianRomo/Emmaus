"""Package "Discipulado" of the round "cada rol sabe qué hacer" (2026-10-26).

Each class reproduces a defect of the discipleship screens that this package fixes: navigation twins and an item
with no page, a module flag that gated nothing, two words for one follow-up state, open follow-ups listed last, a
create form that could file a follow-up already finished, e-mails standing for people's names, invitation links
printed as text, figures that overclaimed, and a participant list that hid who needs a mentor.
"""

from __future__ import annotations

import importlib
import re
from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Church,
    DiscipleshipPath,
    Enrollment,
    FollowUpRequest,
    Invitation,
    LessonProgress,
    ParticipantProfile,
    ParticipantQuestion,
    ReflectionAnswer,
    ReflectionQuestion,
    Role,
)
from .tests import EmmausFixture

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


def bar_nav(html: str) -> list[tuple[str, str]]:
    """(href, label) of each link of the bar's module nav, in order; [] when the bar draws no nav."""
    match = re.search(r'<nav class="bar__nav" aria-label="Discipulado">(.*?)</nav>', html, re.S)
    return re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', match.group(1)) if match else []


def main_of(response) -> str:
    return re.search(r"<main\b.*?</main>", response.content.decode(), re.S).group(0)


def visible_text(html: str) -> str:
    """What a person reads: the markup without its tags (and so without any attribute value)."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def option_labels(html: str) -> list[str]:
    """The words of every <option> that stands for a record (numeric value)."""
    return [label.strip() for label in re.findall(r'<option value="\d+"[^>]*>([^<]*)</option>', html)]


def figure(label: str, value: int) -> str:
    return f'<dt>{label}</dt><dd class="figures__value">{value}</dd>'


class UxFixture(EmmausFixture):
    def enable_module(self):
        Church.objects.filter(pk=self.church.pk).update(discipleship_enabled=True)

    def page(self, user, url):
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response

    def make_quiet(self, user, enrollment, days):
        """No activity for `days` days: joined then, and the lesson progress last touched then."""
        moment = timezone.now() - timedelta(days=days)
        User.objects.filter(pk=user.pk).update(date_joined=moment)
        LessonProgress.objects.filter(enrollment=enrollment).update(last_viewed_at=moment)


@override_settings(EMAIL_BACKEND=LOCMEM)
class NavigationIsDataTests(UxFixture):
    """(1) The bar listed the mentor's home four times, "Mi cuenta" pointed at "Mi camino", "Rutas" and "Lecciones" were
    one page, and with the module off a church admin still got eight discipleship items."""

    def homes(self):
        return [
            (self.participant, reverse("participant_dashboard")),
            (self.mentor, reverse("mentor_dashboard")),
            (self.pastor, reverse("church_dashboard")),
            (self.admin, reverse("church_dashboard")),
        ]

    def test_no_role_has_two_items_for_one_address_or_an_item_without_a_page(self):
        self.enable_module()
        for user, url in self.homes():
            with self.subTest(user=user.email):
                nav = bar_nav(self.page(user, url).content.decode())
                hrefs = [href for href, _ in nav]
                labels = [label for _, label in nav]
                self.assertTrue(nav)
                self.assertEqual(len(hrefs), len(set(hrefs)), nav)
                self.assertEqual([href for href in hrefs if "#" in href], [], nav)
                self.assertNotIn("Mi cuenta", labels)
                self.assertNotIn("Lecciones", labels)

    def test_each_role_has_one_name_per_destination(self):
        self.enable_module()
        expected = {
            self.participant.email: ["Mi camino", "Lección actual", "Preguntas", "Pedir una conversación"],
            self.mentor.email: ["Inicio", "Seguimientos"],
            self.pastor.email: ["Inicio", "Participantes", "Preguntas", "Para el pastor", "Seguimientos", "Notas pastorales"],
            self.admin.email: ["Inicio", "Participantes", "Invitaciones", "Rutas", "Preguntas", "Seguimientos", "Configuración"],
        }
        for user, url in self.homes():
            with self.subTest(user=user.email):
                nav = bar_nav(self.page(user, url).content.decode())
                self.assertEqual([label for _, label in nav], expected[user.email])

    def test_the_mentor_home_stays_marked_on_a_participant_and_on_a_question(self):
        question = ParticipantQuestion.objects.create(church=self.church, participant=self.participant, question_text="¿Cómo oro?")
        marked = f'<a href="{reverse("mentor_dashboard")}" aria-current="page">Inicio</a>'
        for url in (reverse("participant_detail", args=[self.profile.id]), reverse("question_detail", args=[question.id])):
            with self.subTest(url=url):
                self.assertContains(self.page(self.mentor, url), marked)

    def test_lessons_and_reflection_questions_mark_rutas(self):
        self.enable_module()
        marked = f'<a href="{reverse("path_list")}" aria-current="page">Rutas</a>'
        for url in (
            reverse("lesson_create", args=[self.path.id]),
            reverse("lesson_edit", args=[self.lesson.id]),
            reverse("reflection_question_create", args=[self.lesson.id]),
        ):
            with self.subTest(url=url):
                self.assertContains(self.page(self.admin, url), marked)

    def test_the_current_lesson_leaves_the_bar_once_the_path_is_done(self):
        labels = lambda: [label for _, label in bar_nav(self.page(self.participant, reverse("participant_dashboard")).content.decode())]  # noqa: E731
        self.assertIn("Lección actual", labels())
        self.enrollment.lesson_progress.update(completed_at=timezone.now())
        self.assertNotIn("Lección actual", labels())

    def test_with_the_module_off_the_admin_keeps_only_settings_and_the_pastor_nothing(self):
        self.assertFalse(Church.objects.get(pk=self.church.pk).discipleship_enabled)  # the field's default
        self.assertEqual(
            bar_nav(self.page(self.admin, reverse("church_settings")).content.decode()),
            [(reverse("church_settings"), "Configuración")],
        )
        self.assertEqual(bar_nav(self.page(self.pastor, reverse("church_dashboard")).content.decode()), [])

    def test_with_the_module_off_the_dashboard_sends_admin_and_pastor_to_the_events(self):
        for user in (self.admin, self.pastor):
            with self.subTest(user=user.email):
                self.client.force_login(user)
                self.assertRedirects(self.client.get(reverse("dashboard")), reverse("events:list"), fetch_redirect_response=False)

    def test_with_the_module_on_the_pastor_still_lands_on_the_overview(self):
        self.enable_module()
        self.client.force_login(self.pastor)
        self.assertRedirects(self.client.get(reverse("dashboard")), reverse("church_dashboard"), fetch_redirect_response=False)


@override_settings(EMAIL_BACKEND=LOCMEM)
class FollowUpVocabularyTests(UxFixture):
    """(2) The module said "Abierto/Completado" where the camps say "Pendiente/Atendido", and mixed both on one screen."""

    def setUp(self):
        super().setUp()
        self.follow_up = FollowUpRequest.objects.create(
            church=self.church, participant=self.participant, created_by=self.participant,
            assigned_to=self.mentor, reason="Quiero platicar en persona.",
        )

    def test_the_states_have_the_camps_words(self):
        self.assertEqual(
            dict(FollowUpRequest.Status.choices),
            {"open": "Pendiente", "in_progress": "En proceso", "completed": "Atendido", "cancelled": "Cancelado"},
        )

    def test_no_page_that_lists_follow_ups_calls_one_open(self):
        pages = [
            (self.mentor, reverse("mentor_dashboard")),
            (self.mentor, reverse("follow_up_list")),
            (self.admin, reverse("church_follow_up_list")),
            (self.pastor, reverse("church_dashboard")),
            (self.mentor, reverse("participant_detail", args=[self.profile.id])),
            (self.participant, reverse("participant_dashboard")),
        ]
        for user, url in pages:
            with self.subTest(url=url):
                main = main_of(self.page(user, url))
                self.assertIsNone(re.search(r"(?i)\babiertos?\b", main), url)
                self.assertIn('<span class="status status--pending"><span class="status__mark" aria-hidden="true"></span>Pendiente</span>', main)

    def test_marking_one_done_says_atendido(self):
        main = main_of(self.page(self.mentor, reverse("follow_up_list")))
        self.assertIn("Marcar como atendido", main)
        self.assertNotIn("Marcar como completado", main)

        response = self.client.post(reverse("complete_follow_up", args=[self.follow_up.id]), follow=True)

        self.assertContains(response, "El seguimiento quedó atendido.")
        self.assertContains(response, '<span class="status status--done"><span class="status__mark" aria-hidden="true"></span>Atendido</span>')


@override_settings(EMAIL_BACKEND=LOCMEM)
class FollowUpListOrderTests(UxFixture):
    """(3) The list sorted the stored string, so the open follow-ups came last (cancelled, completed, in_progress, open)."""

    def test_pending_ones_come_first_then_in_progress_then_attended_then_cancelled(self):
        now = timezone.now()
        Status = FollowUpRequest.Status
        for reason, status, days in (
            ("R-cancelado", Status.CANCELLED, 1),
            ("R-atendido", Status.COMPLETED, 2),
            ("R-en-proceso", Status.IN_PROGRESS, 3),
            ("R-pendiente-viejo", Status.OPEN, 5),
            ("R-pendiente-nuevo", Status.OPEN, 1),
        ):
            follow_up = FollowUpRequest.objects.create(
                church=self.church, participant=self.participant, assigned_to=self.mentor, reason=reason, status=status
            )
            FollowUpRequest.objects.filter(pk=follow_up.pk).update(created_at=now - timedelta(days=days))

        main = main_of(self.page(self.mentor, reverse("follow_up_list")))

        order = ["R-pendiente-nuevo", "R-pendiente-viejo", "R-en-proceso", "R-atendido", "R-cancelado"]
        self.assertEqual(sorted(order, key=main.index), order)
        # The create form follows the list, folded
        self.assertIn('<details class="fold" id="nuevo-seguimiento">', main)
        self.assertLess(main.index("R-cancelado"), main.index('id="nuevo-seguimiento"'))
        self.assertIn("Crear un seguimiento", main)

    def test_the_participant_record_lists_them_in_the_same_order(self):
        FollowUpRequest.objects.create(church=self.church, participant=self.participant, reason="R-atendido", status=FollowUpRequest.Status.COMPLETED)
        FollowUpRequest.objects.create(church=self.church, participant=self.participant, reason="R-pendiente")
        main = main_of(self.page(self.mentor, reverse("participant_detail", args=[self.profile.id])))
        self.assertLess(main.index("R-pendiente"), main.index("R-atendido"))

    def test_only_unfinished_ones_can_be_marked_attended(self):
        Status = FollowUpRequest.Status
        made = {
            status: FollowUpRequest.objects.create(church=self.church, participant=self.participant, assigned_to=self.mentor, reason=status, status=status)
            for status in (Status.OPEN, Status.IN_PROGRESS, Status.COMPLETED, Status.CANCELLED)
        }
        main = main_of(self.page(self.mentor, reverse("follow_up_list")))
        for status, follow_up in made.items():
            with self.subTest(status=status):
                action = f'action="{reverse("complete_follow_up", args=[follow_up.id])}"'
                if status in (Status.OPEN, Status.IN_PROGRESS):
                    self.assertIn(action, main)
                else:
                    self.assertNotIn(action, main)

    def test_a_rejected_form_opens_its_fold(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("church_follow_up_list"), {"reason": ""})
        self.assertContains(response, '<details class="fold" id="nuevo-seguimiento" open>')
        self.assertContains(response, "<title>Error: Seguimientos")


@override_settings(EMAIL_BACKEND=LOCMEM)
class CreateFollowUpTests(UxFixture):
    """(4) The create form offered "Estado", so a follow-up could be filed already "Completado" or "Cancelado"."""

    def test_the_form_has_no_state_and_what_it_files_is_pending(self):
        url = reverse("participant_detail", args=[self.profile.id])
        html = self.page(self.mentor, url).content.decode()
        self.assertNotIn('name="follow-status"', html)
        for kept in ('name="follow-reason"', 'name="follow-assigned_to"', 'name="note-visibility"', 'name="action" value="follow"', 'name="action" value="note"'):
            self.assertIn(kept, html)

        response = self.client.post(url, {
            "action": "follow",
            "follow-participant": self.participant.id,
            "follow-assigned_to": self.mentor.id,
            "follow-reason": "Llamar esta semana.",
            "follow-status": FollowUpRequest.Status.COMPLETED,  # a stale form: ignored
        })

        self.assertRedirects(response, url)
        self.assertEqual(FollowUpRequest.objects.get(reason="Llamar esta semana.").status, FollowUpRequest.Status.OPEN)

    def test_the_list_form_has_no_state_either(self):
        html = self.page(self.admin, reverse("church_follow_up_list")).content.decode()
        self.assertNotIn('name="status"', html)
        self.assertIn('name="reason"', html)


@override_settings(EMAIL_BACKEND=LOCMEM)
class PeopleByNameTests(UxFixture):
    """(5) Every person select showed the e-mail (User.__str__ is the username), never the name."""

    def test_person_selects_show_names_not_emails(self):
        pages = [
            reverse("participant_detail", args=[self.profile.id]),
            reverse("church_follow_up_list"),
            reverse("create_invitation"),
            reverse("assign_mentor", args=[self.profile.id]),
        ]
        for url in pages:
            with self.subTest(url=url):
                labels = option_labels(self.page(self.admin, url).content.decode())
                self.assertTrue(labels)
                self.assertEqual([label for label in labels if "@" in label], [])
        html = self.page(self.admin, reverse("participant_detail", args=[self.profile.id])).content.decode()
        self.assertIn(">Pat Participant</option>", html)
        self.assertIn(">Mentor User</option>", html)


@override_settings(EMAIL_BACKEND=LOCMEM)
class InvitationLinkTests(UxFixture):
    """(6) The list printed every invitation's personal link as text, in a five-column table, and capped at 100 silently."""

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.pending = Invitation.objects.create(
            church=self.church, email="pendiente@example.org", role=Role.PARTICIPANT, invited_by=self.admin, assigned_mentor=self.mentor
        )
        self.accepted = Invitation.objects.create(church=self.church, email="aceptada@example.org", role=Role.MENTOR, accepted_at=now)
        self.expired = Invitation.objects.create(
            church=self.church, email="vencida@example.org", role=Role.PARTICIPANT, expires_at=now - timedelta(days=1)
        )

    def test_no_link_is_printed_and_only_a_pending_one_can_be_copied(self):
        main = main_of(self.page(self.admin, reverse("invitations_list")))
        self.assertNotIn("<table", main)
        for invitation in (self.pending, self.accepted, self.expired):
            self.assertNotIn(reverse("accept_invitation", args=[invitation.token]), visible_text(main))
        self.assertEqual(main.count(self.pending.token), 1)  # once: the value of its folded field
        for invitation in (self.accepted, self.expired):
            self.assertNotIn(invitation.token, main)  # a used or expired link is not in the page at all

        folds = re.findall(r'<details class="fold fold--inline">\s*<summary>Ver enlace</summary>(.*?)</details>', main, re.S)
        self.assertEqual(len(folds), 1)  # closed: no "open"
        targets = re.findall(r'<button type="button" class="btn btn--secondary btn--small" data-copy="([^"]+)">Copiar enlace', folds[0])
        self.assertEqual(len(targets), 1)
        self.assertIn(f'<label class="label" for="{targets[0]}">Enlace para pendiente@example.org</label>', folds[0])
        value = re.search(rf'<input type="text" class="input" id="{targets[0]}" value="([^"]+)" readonly>', folds[0]).group(1)
        self.assertTrue(value.endswith(reverse("accept_invitation", args=[self.pending.token])), value)

    def test_each_row_says_who_mentors_when_it_was_sent_and_its_state(self):
        main = main_of(self.page(self.admin, reverse("invitations_list")))
        self.assertIn("Mentor: Mentor User", main)
        self.assertIn("Sin asignar", main)
        self.assertIn("enviada hoy", main)
        for kind, label in (("pending", "Pendiente"), ("done", "Aceptada"), ("closed", "Vencida")):
            self.assertIn(f'<span class="status status--{kind}"><span class="status__mark" aria-hidden="true"></span>{label}</span>', main)

    def test_a_capped_list_says_how_many_it_shows(self):
        main = main_of(self.page(self.admin, reverse("invitations_list")))
        self.assertNotIn("Mostrando", main)
        Invitation.objects.bulk_create(
            [Invitation(church=self.church, email=f"p{n}@example.org", role=Role.PARTICIPANT) for n in range(98)]
        )
        main = main_of(self.page(self.admin, reverse("invitations_list")))
        self.assertIn("Mostrando 100 de 101.", main)


@override_settings(EMAIL_BACKEND=LOCMEM)
class ChurchDashboardFigureTests(UxFixture):
    """(7) "Participantes activos" counted every profile, "en una ruta" was never checked, "Nadie: todos tienen mentor"
    showed with nobody, and who went quiet was computed but never shown."""

    def test_with_no_participants_nobody_is_said_to_have_a_mentor(self):
        ParticipantProfile.objects.all().delete()
        main = main_of(self.page(self.pastor, reverse("church_dashboard")))
        self.assertNotIn("Nadie: todos tienen mentor", main)
        self.assertIn("Todavía no hay participantes.", main)
        self.assertIn(figure("Participantes", 0), main)
        self.assertIn(figure("En una ruta", 0), main)

    def test_on_a_path_counts_only_people_with_an_active_enrollment(self):
        waiting = User.objects.create_user(username="w@example.org", email="w@example.org", first_name="Wendy", last_name="Waiting")
        ParticipantProfile.objects.create(user=waiting, church=self.church)
        main = main_of(self.page(self.pastor, reverse("church_dashboard")))
        self.assertIn(figure("Participantes", 3), main)
        self.assertIn(figure("En una ruta", 2), main)
        self.assertIn(figure("Sin mentor", 1), main)
        self.assertNotIn("Participantes activos", main)
        self.assertNotIn("Nadie: todos tienen mentor", main)

    def test_a_finished_or_inactive_path_is_not_counted_as_walking_one(self):
        Enrollment.objects.filter(pk=self.enrollment.pk).update(completed_at=timezone.now())
        DiscipleshipPath.objects.create(church=self.church, title="Inactiva", is_active=False)
        main = main_of(self.page(self.pastor, reverse("church_dashboard")))
        self.assertIn(figure("En una ruta", 1), main)

    def test_who_went_quiet_is_listed_with_a_way_to_open_them(self):
        self.make_quiet(self.participant, self.enrollment, days=9)
        main = main_of(self.page(self.pastor, reverse("church_dashboard")))
        section = main.split("¿Quién se quedó callado?", 1)[1].split("</section>", 1)[0]
        self.assertIn("Pat Participant", section)
        self.assertIn("Última actividad hace 9 días.", section)
        self.assertIn(f'href="{reverse("participant_detail", args=[self.profile.id])}">Ver', section)
        self.assertNotIn("Other Participant", section)

    def test_nobody_quiet_says_so(self):
        main = main_of(self.page(self.pastor, reverse("church_dashboard")))
        self.assertIn("Nadie lleva más de 7 días sin actividad.", main)

    def test_the_sections_come_in_the_order_of_what_to_do(self):
        main = main_of(self.page(self.pastor, reverse("church_dashboard")))
        order = ["¿Qué preguntas esperan respuesta?", "¿Qué seguimientos siguen pendientes?", "¿Quién se quedó callado?", "¿Cómo va el discipulado?"]
        self.assertEqual(sorted(order, key=main.index), order)


@override_settings(EMAIL_BACKEND=LOCMEM)
class ParticipantsListOrderTests(UxFixture):
    """(8) The list was in name order: whoever had no mentor, or had gone quiet, was wherever their name fell."""

    def test_who_has_no_mentor_comes_first_then_the_quietest(self):
        waiting = User.objects.create_user(username="w@example.org", email="w@example.org", first_name="Wendy", last_name="Waiting")
        ParticipantProfile.objects.create(user=waiting, church=self.church)
        self.make_quiet(self.participant, self.enrollment, days=20)

        main = main_of(self.page(self.admin, reverse("participants_list")))

        names = re.findall(r'class="row__link" href="[^"]+">([^<]+)</a>', main)
        self.assertEqual(names, ["Wendy Waiting", "Pat Participant", "Other Participant"])
        self.assertIn("Mentor: Mentor User, 0 de 1 lección, última actividad hace 20 días.", main)
        self.assertIn("Sin mentor, sin ruta, última actividad hoy.", main)

    def test_invite_is_the_one_filled_button(self):
        main = main_of(self.page(self.admin, reverse("participants_list")))
        self.assertEqual(re.findall(r'class="btn btn--primary"[^>]*>([^<]+)<', main), ["Invitar participante"])
        self.assertIn("Las personas de tu iglesia en el discipulado.", main)

    def test_a_pastor_who_cannot_invite_is_not_offered_to(self):
        Church.objects.filter(pk=self.church.pk).update(leaders_can_invite_participants=False)
        main = main_of(self.page(self.pastor, reverse("participants_list")))
        self.assertNotIn(reverse("create_invitation"), main.split("</header>", 1)[0])


@override_settings(EMAIL_BACKEND=LOCMEM)
class MentorHomeTests(UxFixture):
    """Four stacked lists repeated the same people and questions; the view wrote the rows' advice."""

    def test_three_sections_in_the_order_of_what_to_do(self):
        FollowUpRequest.objects.create(church=self.church, participant=self.participant, assigned_to=self.mentor, reason="Platicar.")
        main = main_of(self.page(self.mentor, reverse("mentor_dashboard")))
        headings = re.findall(r'<h2 id="[^"]+" class="heading">([^<]+?)(?: <span|</h2>)', main)
        self.assertEqual(headings, ["Esperan tu respuesta", "Seguimientos pendientes", "Mis participantes"])
        for advice in ("Sin pendientes", "Revisar reflexiones", "Continuar seguimiento", "Contactar esta semana", "Responder pregunta"):
            self.assertNotIn(advice, main)
        self.assertIn('id="open-questions"', main)
        self.assertIn('id="assigned-participants"', main)

    def test_a_follow_up_row_says_who_why_and_since_when(self):
        FollowUpRequest.objects.create(church=self.church, participant=self.participant, assigned_to=self.mentor, reason="Platicar.")
        main = main_of(self.page(self.mentor, reverse("mentor_dashboard")))
        section = main.split("Seguimientos pendientes", 1)[1].split("</section>", 1)[0]
        self.assertIn("Pat Participant", section)
        self.assertIn("Platicar.", section)
        self.assertIn("Creado hoy.", section)

    def test_a_follow_up_in_someone_elses_name_is_not_listed(self):
        FollowUpRequest.objects.create(church=self.church, participant=self.participant, assigned_to=self.pastor, reason="Lo ve el pastor.")
        main = main_of(self.page(self.mentor, reverse("mentor_dashboard")))
        self.assertNotIn("Lo ve el pastor.", main)

    def test_an_unassigned_follow_up_of_my_participant_is_on_my_home(self):
        """Refuter round 1: in nobody's name, it was on nobody's home."""
        FollowUpRequest.objects.create(church=self.church, participant=self.participant, assigned_to=None, reason="Sin responsable.")
        FollowUpRequest.objects.create(church=self.church, participant=self.other_participant, assigned_to=None, reason="De otra persona.")
        main = main_of(self.page(self.mentor, reverse("mentor_dashboard")))
        section = main.split("Seguimientos pendientes", 1)[1].split("</section>", 1)[0]
        self.assertIn("Sin responsable.", section)
        self.assertNotIn("De otra persona.", main)  # unassigned, but someone else's participant

    def test_a_follow_up_made_before_a_mentor_was_assigned_reaches_the_new_mentor(self):
        """Refuter round 1: a request sent while the participant had no mentor stays unassigned after assign_mentor."""
        ParticipantProfile.objects.filter(pk=self.profile.pk).update(assigned_mentor=None)
        self.client.force_login(self.participant)
        self.client.post(reverse("request_follow_up"), {"reason": "Quiero platicar."})
        self.assertIsNone(FollowUpRequest.objects.get(reason="Quiero platicar.").assigned_to)
        self.client.force_login(self.admin)
        self.client.post(reverse("assign_mentor", args=[self.profile.id]), {"mentor": self.mentor.id})

        main = main_of(self.page(self.mentor, reverse("mentor_dashboard")))

        self.assertIn("Quiero platicar.", main.split("Seguimientos pendientes", 1)[1].split("</section>", 1)[0])

    def test_a_follow_up_in_the_previous_mentors_name_stays_on_their_home(self):
        """After assign_mentor, one already in the previous mentor's name is still on that mentor's home (not lost)."""
        FollowUpRequest.objects.create(church=self.church, participant=self.participant, assigned_to=self.mentor, reason="Del mentor anterior.")
        self.client.force_login(self.admin)
        self.client.post(reverse("assign_mentor", args=[self.profile.id]), {"mentor": self.other_mentor.id})
        self.assertIn("Del mentor anterior.", main_of(self.page(self.mentor, reverse("mentor_dashboard"))))

    def test_a_participant_row_has_at_most_three_data(self):
        main = main_of(self.page(self.mentor, reverse("mentor_dashboard")))
        self.assertIn("Última actividad hoy, 0 de 1 lección.", main)


@override_settings(EMAIL_BACKEND=LOCMEM)
class ParticipantHomeTests(UxFixture):
    """The finished path had no next step, "Reflexiones recientes" were in lesson order, and the no-path copy promised
    an enrollment no screen can make."""

    def test_a_finished_path_points_to_a_conversation(self):
        self.enrollment.lesson_progress.update(completed_at=timezone.now())
        main = main_of(self.page(self.participant, reverse("participant_dashboard")))
        self.assertNotIn('class="sign"', main)
        self.assertIn(
            f'Terminaste la ruta. Si quieres platicar, <a href="{reverse("request_follow_up")}">pide una conversación</a>.', main
        )

    def test_the_next_step_line_is_not_shown_while_a_lesson_is_left(self):
        main = main_of(self.page(self.participant, reverse("participant_dashboard")))
        self.assertNotIn("Terminaste la ruta.", main)

    def test_without_an_active_path_the_page_says_so(self):
        Enrollment.objects.all().delete()
        DiscipleshipPath.objects.all().update(is_active=False)
        main = main_of(self.page(self.participant, reverse("participant_dashboard")))
        self.assertIn("Tu iglesia todavía no tiene una ruta activa. Cuando la tenga, aparecerá aquí.", main)
        self.assertNotIn("puede inscribirte", main)

    def test_recent_reflections_are_the_three_newest(self):
        now = timezone.now()
        for n in range(4):
            question = ReflectionQuestion.objects.create(lesson=self.lesson, order=n + 2, question_text=f"Pregunta {n}")
            answer = ReflectionAnswer.objects.create(enrollment=self.enrollment, lesson=self.lesson, question=question, answer_text=f"Respuesta-{n}")
            # The first question of the lesson was answered last
            ReflectionAnswer.objects.filter(pk=answer.pk).update(updated_at=now - timedelta(days=4 - n))
        main = main_of(self.page(self.participant, reverse("participant_dashboard")))
        section = main.split("Reflexiones recientes", 1)[1].split("</section>", 1)[0]
        shown = re.findall(r"Respuesta-\d", section)
        self.assertEqual(shown, ["Respuesta-3", "Respuesta-2", "Respuesta-1"])


@override_settings(EMAIL_BACKEND=LOCMEM)
class DiscipleshipFlagMigrationTests(EmmausFixture):
    """Refuter round 1: the flag now gates the leaders' bar and /dashboard/; churches already using the module must keep it."""

    def run_migration(self):
        from django.apps import apps

        migration = importlib.import_module("core.migrations.0010_discipleship_flag_for_churches_in_use")
        migration.turn_on_where_in_use(apps, None)
        return migration

    def flag(self, church):
        return Church.objects.get(pk=church.pk).discipleship_enabled

    def test_churches_with_a_path_or_participants_get_the_module_and_empty_ones_do_not(self):
        with_path = Church.objects.create(name="Con ruta", slug="con-ruta")
        DiscipleshipPath.objects.create(church=with_path, title="Ruta")
        with_people = Church.objects.create(name="Con personas", slug="con-personas")
        person = User.objects.create_user(username="p@example.org", email="p@example.org")
        ParticipantProfile.objects.create(user=person, church=with_people)
        empty = Church.objects.create(name="Vacía", slug="vacia")
        self.assertFalse(self.flag(with_path))

        migration = self.run_migration()
        self.run_migration()  # idempotent

        self.assertTrue(self.flag(with_path))
        self.assertTrue(self.flag(with_people))
        self.assertTrue(self.flag(self.church))  # the fixture's church has both
        self.assertFalse(self.flag(empty))
        from django.db.migrations import RunPython

        self.assertIs(migration.Migration.operations[0].reverse_code, RunPython.noop)

    @override_settings(DEBUG=True)
    def test_the_demo_church_uses_the_module(self):
        call_command("seed_dev_data", password="DemoPass-12345", stdout=StringIO())
        self.assertTrue(Church.objects.get(slug="emmaus-demo").discipleship_enabled)


@override_settings(EMAIL_BACKEND=LOCMEM)
class QuestionPageTests(UxFixture):
    def test_one_filled_button_and_the_title_names_the_person(self):
        question = ParticipantQuestion.objects.create(church=self.church, participant=self.participant, question_text="¿Cómo oro?")
        response = self.page(self.mentor, reverse("question_detail", args=[question.id]))
        main = main_of(response)
        self.assertEqual(re.findall(r'class="btn btn--primary"[^>]*>([^<]+)<', main), ["Responder"])
        self.assertIn('<button class="btn btn--secondary" type="submit">Pasar al pastor</button>', main)
        self.assertIn('<button class="btn btn--link" type="submit">Generar borrador</button>', main)
        self.assertContains(response, "<title>Pregunta de Pat Participant")


class StateMarkTests(UxFixture):
    """Audit UI-07: an inactive path or lesson ended; it was drawn with the "not started" outline."""

    def test_inactive_paths_and_lessons_are_closed(self):
        DiscipleshipPath.objects.filter(pk=self.path.pk).update(is_active=False)
        self.lesson.is_active = False
        self.lesson.save()
        closed = '<span class="status status--closed"><span class="status__mark" aria-hidden="true"></span>Inactiva</span>'
        for url in (reverse("path_list"), reverse("path_detail", args=[self.path.id])):
            with self.subTest(url=url):
                self.assertContains(self.page(self.admin, url), closed)
