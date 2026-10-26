"""UX audit follow-ups (docs/auditoria-ux-2026-10.md).

UX-05 / UI-20   the paper backup list carries the questionnaire, so a coordinador can interview on paper.
LANG-15         with 0 camperos the empty states say so, with what to do: no praise, no "ya habíamos terminado".
LANG-04 / 17    the slug is the "nombre corto" on Equipo, on the sede form and on the first setup.
Typo            "Corregida 2 veces", not "vezes".
Coordinador     "Mi inicio" with 0 camperos: the sign says what to do, the list under it only that it is empty.
Kiosk           the welcome's "Retomar" opens the screen the review's "Seguir contestando" opens.
Known gap       a confidential and required question warns whoever edits it; docs say how to recover.
"""

from __future__ import annotations

from datetime import timedelta
from html.parser import HTMLParser
from unittest import mock

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.forms import InitialSetupForm
from events.forms import QuestionForm, SedeForm
from events.models import (
    Answer,
    Event,
    EventMembership,
    EventParticipant,
    Interview,
    InterviewStatus,
    MembershipRole,
    Question,
)
from events.services import interviews as svc
from events.services import questionnaire
from events.services.metrics import event_scope
from events.services.questionnaire import (
    DECLINE,
    all_questions,
    create_questionnaire_from_definition,
    duplicate_version,
    load_answers,
)
from events.services.reports import generate_snapshot

from .base import PASSWORD, CampFixture, make_user

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Node:
    def __init__(self, tag: str, attrs: dict, parent: _Node | None):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.parts: list = []  # text and child nodes, in document order

    @property
    def children(self) -> list[_Node]:
        return [p for p in self.parts if isinstance(p, _Node)]

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def iter(self):
        yield self
        for child in self.children:
            yield from child.iter()

    def by_id(self, element_id: str) -> _Node | None:
        return next((n for n in self.iter() if n.attrs.get("id") == element_id), None)

    def text(self, skip=lambda node: False) -> str:
        out = []

        def walk(node):
            for part in node.parts:
                if isinstance(part, _Node):
                    if not skip(part):
                        walk(part)
                else:
                    out.append(part)

        walk(self)
        return " ".join("".join(out).split())

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class _Tree(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {}, None)
        self._current = self.root
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self._current)
        self._current.parts.append(node)
        if tag not in VOID:
            self._current = node

    def handle_startendtag(self, tag, attrs):
        self._current.parts.append(_Node(tag, dict(attrs), self._current))

    def handle_endtag(self, tag):
        node = self._current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self._current = node.parent

    def handle_data(self, data):
        self._current.parts.append(data)


def _is_question_block(node: _Node) -> bool:
    return (node.attrs.get("id") or "").startswith("papel-q")


# ---- UX-05 / UI-20: the questionnaire on paper -------------------------------------------------------


class PaperQuestionnaireTests(CampFixture):
    def isolate_structure_cache(self):
        """build_steps caches published versions by id for the process; ids come back after a test's rollback."""
        patcher = mock.patch.dict(questionnaire._structure_cache, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sheet(self, user=None):
        self.login(user or self.coord1)
        response = self.client.get(self.url("backup_list"))
        self.assertEqual(response.status_code, 200)
        return response, _Tree(response.content.decode()).root

    def section(self, root) -> _Node:
        section = root.by_id("cuestionario")
        self.assertIsNotNone(section, "the sheet has no questionnaire section")
        return section

    def block(self, section, question) -> _Node:
        block = section.by_id(f"papel-q{question.id}")
        self.assertIsNotNone(block, question.text)
        return block

    def own_text(self, block) -> str:
        """The block's words without the follow-ups nested in it."""
        return block.text(skip=_is_question_block)

    def options(self, block) -> list[str]:
        (listing,) = [n for n in block.iter() if n.tag == "ul" and "admin-answers" in n.classes and next(
            a for a in n.ancestors() if _is_question_block(a)) is block]
        return [li.text() for li in listing.children if li.tag == "li"]

    def test_every_question_is_printed_in_order_with_its_options(self):
        response, root = self.sheet()
        section = self.section(root)
        self.assertEqual(section.by_id("cuestionario-titulo").text(), "Cuestionario para entrevistar en papel")
        questions = all_questions(self.version)
        printed = [n.attrs["id"] for n in section.iter() if _is_question_block(n)]
        self.assertEqual(printed, [f"papel-q{q.id}" for q in questions])
        for question in questions:
            block = self.block(section, question)
            self.assertIn(question.text, self.own_text(block))
            if question.is_choice:
                # One small square per option, in the order the screen shows them: "Prefiero no responder" and the
                # other exclusive options ("Por ahora no") last, after the others.
                listed = list(question.options.all())
                quiet = [o for o in listed if o.is_decline or o.is_exclusive]
                expected = [o for o in listed if o not in quiet] + quiet
                self.assertEqual(self.options(block), [f"☐ {o.label}" for o in expected], question.text)
            else:
                room = [n for n in block.iter() if {"textarea", "input"} & set(n.classes)]
                self.assertEqual(len(room), 1, question.text)

    def test_exclusive_options_go_last_as_on_the_screen(self):
        self.isolate_structure_cache()
        draft = duplicate_version(self.version, self.admin)
        apoyo = Question.objects.get(section__version=draft, code="apoyo")
        codes = ["ninguna", "prefiero_no", "familia", "bienestar_emocional", "vida_personal", "relaciones", "sexualidad", "otra"]
        for order, code in enumerate(codes, start=1):  # exclusive options first: the editor allows any order
            option = apoyo.options.get(code=code)
            option.order = order
            option.save()
        draft.status = draft.Status.PUBLISHED
        draft.save(update_fields=["status"])
        Event.objects.filter(pk=self.event.pk).update(questionnaire_version=draft)
        _, root = self.sheet()
        printed = self.options(self.block(self.section(root), apoyo))
        self.assertEqual(printed, ["☐ Familia", "☐ Bienestar emocional", "☐ Vida personal", "☐ Relaciones", "☐ Sexualidad",
                                   "☐ Otra situación", "☐ Por ahora no", "☐ Prefiero no responder"])

    def test_the_decline_option_is_printed(self):
        _, root = self.sheet()
        section = self.section(root)
        declining = [q for q in all_questions(self.version) if q.options.filter(is_decline=True).exists()]
        self.assertEqual([q.code for q in declining], ["salvo", "dudas", "apoyo", "decision"])
        for question in declining:
            self.assertIn(f"☐ {DECLINE['label']}", self.options(self.block(section, question)))

    def test_follow_ups_sit_under_their_parent_with_the_condition_in_words(self):
        _, root = self.sheet()
        section = self.section(root)
        expected = {
            "dudas_detalle": ("dudas", "Si contestó “Sí”:"),
            "apoyo_detalle": ("apoyo", "Si contestó “Familia”, “Bienestar emocional”, “Vida personal”, “Relaciones”, “Sexualidad” o “Otra situación”:"),
            "orientacion": ("decision", "Si contestó “Decidí recibir a Cristo”:"),
        }
        for code, (parent_code, condition) in expected.items():
            block = self.block(section, self.question(code))
            self.assertIn("reveal", block.classes, code)  # indented, like the follow-up on the screen
            parents = [a for a in block.ancestors() if _is_question_block(a)]
            self.assertEqual([p.attrs["id"] for p in parents], [f"papel-q{self.question(parent_code).id}"], code)
            self.assertTrue(self.own_text(block).startswith(condition), self.own_text(block))
        for code in ("salvo", "dudas", "apoyo", "decision", "impedimento"):
            block = self.block(section, self.question(code))
            self.assertNotIn("reveal", block.classes, code)
            self.assertNotIn("contestó", self.own_text(block), code)

    def test_confidential_questions_are_marked(self):
        _, root = self.sheet()
        section = self.section(root)
        for question in all_questions(self.version):
            marked = "Confidencial Solo el equipo de cuidado autorizado podrá leer esta respuesta." in self.own_text(self.block(section, question))
            self.assertEqual(marked, question.sensitivity == Question.Sensitivity.CONFIDENTIAL, question.text)

    def test_a_question_shown_by_an_answer_on_another_screen_says_which(self):
        self.isolate_structure_cache()
        version = create_questionnaire_from_definition(self.church, {
            "name": "Dos pantallas", "title": "Dos pantallas",
            "sections": [
                {"title": "Primera", "questions": [
                    {"code": "grupo", "text": "¿Vienes con un grupo?", "kind": "unica", "sensitivity": "general",
                     "options": [{"code": "si", "label": "Sí"}, {"code": "no", "label": "No"}, DECLINE]},
                ]},
                {"title": "Segunda", "questions": [
                    {"code": "cual", "text": "¿Con qué grupo vienes?", "kind": "texto_corto", "sensitivity": "general",
                     "condition": ("grupo", ["si"])},
                ]},
            ],
        })
        Event.objects.filter(pk=self.event.pk).update(questionnaire_version=version)
        _, root = self.sheet()
        section = self.section(root)
        follow = Question.objects.get(section__version=version, code="cual")
        block = self.block(section, follow)
        self.assertNotIn("reveal", block.classes)  # its own screen: not indented under a parent it does not share
        self.assertIn("Solo si en “¿Vienes con un grupo?” contestó “Sí”.", self.own_text(block))
        self.assertEqual([a for a in block.ancestors() if _is_question_block(a)], [])

    def test_no_stored_answer_is_printed(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        detail = self.question("dudas_detalle")
        self.answer(interview, "dudas", "si", extra={f"q_{detail.id}": "RESPUESTA-PRIVADA-PAPEL"})
        self.answer(interview, "impedimento", "OTRA-RESPUESTA-PRIVADA")
        self.assertTrue(Answer.objects.filter(interview=interview, text_value="RESPUESTA-PRIVADA-PAPEL").exists())
        self.assertTrue(Answer.objects.filter(interview=interview, text_value="OTRA-RESPUESTA-PRIVADA").exists())
        for user in (self.coord1, self.admin, self.m_care.user):
            response, root = self.sheet(user)
            self.assertIsNotNone(root.by_id("cuestionario"))
            self.assertNotContains(response, "RESPUESTA-PRIVADA-PAPEL")
            self.assertNotContains(response, "OTRA-RESPUESTA-PRIVADA")
            self.assertNotContains(response, "checked")

    def test_without_a_questionnaire_the_sheet_says_so(self):
        Event.objects.filter(pk=self.event.pk).update(questionnaire_version=None)
        response, root = self.sheet()
        self.assertContains(response, "Todavía no hay un cuestionario publicado para este evento. Avisa a la administración.")
        self.assertNotContains(response, self.question("salvo").text)
        self.assertEqual([n for n in root.iter() if _is_question_block(n)], [])
        self.assertContains(response, self.p1.full_name)  # the list of camperos is still there

    def test_a_draft_is_not_printed(self):
        """Only the Django admin can point an event at a draft; a draft can still change, so it is not handed out on paper."""
        draft = duplicate_version(self.version, self.admin)
        Event.objects.filter(pk=self.event.pk).update(questionnaire_version=draft)
        response, root = self.sheet()
        self.assertContains(response, "Todavía no hay un cuestionario publicado para este evento. Avisa a la administración.")
        self.assertEqual([n for n in root.iter() if _is_question_block(n)], [])


# ---- LANG-15: empty states with 0 camperos --------------------------------------------------------------


class NoCamperosEmptyStatesTests(CampFixture):
    COORDINATOR = "Todavía no tienes camperos. Registra al primero o pide a la administración que te los asigne."
    EVENT = "Todavía no hay camperos registrados. La administración los registra o importa."
    TEAM = "Tu equipo todavía no tiene camperos. Los registran sus coordinadores o la administración."

    def nobody(self):
        EventParticipant.objects.filter(event=self.event).update(is_active=False)

    def test_the_search_without_camperos(self):
        newcomer = make_user("nuevo@prueba.mx", "Nora", "Nueva")
        EventMembership.objects.create(event=self.event, user=newcomer, role=MembershipRole.COORDINATOR)
        self.login(newcomer)
        self.choose_station()
        response = self.client.get(self.url("interview_search"))
        self.assertContains(response, f'<p class="empty-line">{self.COORDINATOR}</p>', html=False)
        self.assertNotContains(response, "ya tienen su entrevista completada")
        self.nobody()
        self.login(self.admin)
        self.choose_station()
        response = self.client.get(self.url("interview_search"))
        self.assertContains(response, "Todavía no hay camperos registrados. Regístralos aquí o")
        self.assertContains(response, f'href="{self.url("participant_import")}"')
        self.assertNotContains(response, "ya tienen su entrevista completada")

    def test_the_search_with_camperos_lists_them(self):
        self.login(self.coord1)
        self.choose_station()
        response = self.client.get(self.url("interview_search"))
        self.assertContains(response, self.p1.full_name)
        self.assertNotContains(response, self.COORDINATOR)

    def test_pendientes_without_camperos(self):
        self.nobody()
        self.login(self.tutor1)
        response = self.client.get(self.url("pending"))
        self.assertContains(response, f'<p class="empty-line">{self.TEAM}</p>', html=False)
        self.assertNotContains(response, "Todas las entrevistas ya se iniciaron.")
        for user in (self.pastor, self.admin):
            self.login(user)
            response = self.client.get(self.url("pending"))
            self.assertContains(response, f'<p class="empty-line">{self.EVENT}</p>', html=False)
            self.assertNotContains(response, "Todas las entrevistas ya se iniciaron.")

    def test_pendientes_with_every_interview_started(self):
        EventParticipant.objects.filter(event=self.event).update(interview_status=InterviewStatus.IN_PROGRESS)
        self.login(self.tutor1)
        response = self.client.get(self.url("pending"))
        self.assertContains(response, "Todas las entrevistas ya se iniciaron.")
        self.assertNotContains(response, self.TEAM)

    def cutoff(self):
        now = timezone.now().replace(microsecond=0)
        return generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=now - timedelta(minutes=5))

    def test_a_cutoff_without_camperos(self):
        self.nobody()
        snapshot = self.cutoff()
        self.login(self.pastor)
        response = self.client.get(self.url("report_detail", snapshot.public_id))
        self.assertContains(response, "Nada, porque aún no había a quién entrevistar. Los cortes siguientes contarán a los camperos que se registren.")
        self.assertNotContains(response, "ya habíamos terminado")

    def test_a_cutoff_with_every_interview_done(self):
        EventParticipant.objects.filter(event=self.event).update(interview_status=InterviewStatus.COMPLETED)
        snapshot = self.cutoff()
        self.login(self.pastor)
        response = self.client.get(self.url("report_detail", snapshot.public_id))
        self.assertContains(response, "Nada: ya habíamos terminado.")
        self.assertNotContains(response, "aún no había a quién entrevistar")


# ---- coordinador's "Mi inicio" with 0 camperos: one sentence, not two ------------------------------------


class CoordinatorHomeWithoutCamperosTests(CampFixture):
    def test_the_list_under_the_sign_only_says_it_is_empty(self):
        newcomer = make_user("nuevo@prueba.mx", "Nora", "Nueva")
        EventMembership.objects.create(event=self.event, user=newcomer, role=MembershipRole.COORDINATOR)
        self.login(newcomer)
        page = self.client.get(self.url("coord_home"))
        self.assertContains(page, "Todavía no tienes camperos asignados")  # the sign keeps what to do
        self.assertContains(page, "Registra al primero o pide a la administración que te los asigne.", count=1)
        self.assertContains(page, '<p class="empty-line">Sin camperos por entrevistar.</p>', count=1, html=False)
        live = self.client.get(self.url("coord_home"), HTTP_X_REQUESTED_WITH="fetch")
        self.assertContains(live, '<p class="empty-line">Sin camperos por entrevistar.</p>', count=1, html=False)
        self.assertNotContains(live, "Registra al primero")

    def test_with_camperos_the_list_is_unchanged(self):
        self.login(self.coord1)
        live = self.client.get(self.url("coord_home"), HTTP_X_REQUESTED_WITH="fetch")
        self.assertContains(live, self.p1.full_name)
        self.assertNotContains(live, "Sin camperos por entrevistar.")


# ---- LANG-04 / LANG-17: "nombre corto" ------------------------------------------------------------------


class NombreCortoTests(CampFixture):
    def test_equipo_calls_the_slug_the_nombre_corto(self):
        self.login(self.admin)
        response = self.client.get(self.url("team"))
        code = self.event.join_code
        self.assertContains(response, f"y escribe el código <strong>{code}</strong> o el nombre corto <strong>{self.event.slug}</strong>.", html=False)
        self.assertContains(response, 'data-confirm="El código anterior y el nombre corto dejarán de funcionar."')
        self.assertNotContains(response, "nombre del evento")
        Event.objects.filter(pk=self.event.pk).update(join_by_name=False)
        response = self.client.get(self.url("team"))
        self.assertContains(response, "El nombre corto dejó de servir para unirse cuando se generó otro código.")
        self.assertContains(response, ">Volver a permitir el nombre corto</button>", html=False)
        self.assertNotContains(response, "nombre del evento")

    def test_the_sede_field(self):
        field = SedeForm(church=self.church).fields["slug"]
        self.assertEqual((field.label, field.help_text), ("Nombre corto", "Se genera del nombre; puedes cambiarlo."))
        self.login(self.admin)
        response = self.client.get(self.url("sede_add"))
        self.assertContains(response, 'for="id_slug">Nombre corto <span class="optional">(opcional)</span></label>', html=False)
        self.assertContains(response, "Se genera del nombre; puedes cambiarlo.")
        self.assertNotContains(response, ">Identificador")

    def test_the_first_setup_field(self):
        self.assertEqual(InitialSetupForm().fields["church_slug"].label, "Nombre corto de la iglesia para la dirección")


# ---- typo: "vezes" --------------------------------------------------------------------------------------


class CorrectionCountTests(CampFixture):
    def detail_with(self, count):
        interview = Interview.objects.create(
            event=self.event, participant=self.p1, version=self.version, conducted_by=self.coord1,
            status=Interview.Status.COMPLETED, completed_at=timezone.now(), correction_count=count,
            follow_up_decision=Interview.FollowUpDecision.NOT_REQUIRED,
        )
        self.login(self.coord1)
        return self.client.get(self.url("interview_detail", interview.public_id))

    def test_two_corrections(self):
        response = self.detail_with(2)
        self.assertContains(response, "Corregida 2 veces.")
        self.assertNotContains(response, "vezes")

    def test_one_correction(self):
        self.assertContains(self.detail_with(1), "Corregida 1 vez.")


# ---- kiosk: "Retomar" opens the screen "Seguir contestando" opens ---------------------------------------


class KioskResumeTests(CampFixture):
    def setUp(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p1, mode="supervisada")
        self.interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", self.interview.public_id))
        self.assertIn(svc.KIOSK_KEY, self.client.session)

    def post_kiosk(self, step, data, direction="next"):
        self.interview.refresh_from_db()
        return self.client.post(reverse("kiosk:step", args=[step.number]), {"revision": self.interview.revision, "direction": direction, **data})

    def welcome_link(self):
        tree = _Tree(self.client.get(reverse("kiosk:home")).content.decode()).root
        links = [n for n in tree.iter() if n.tag == "a" and "btn--primary" in n.classes]
        self.assertEqual(len(links), 1)
        return links[0].text(), links[0].attrs["href"]

    def test_retomar_opens_a_required_follow_up_left_blank(self):
        first, doubts, detail = self.step("salvo"), self.step("dudas"), self.question("dudas_detalle")
        self.assertEqual(self.post_kiosk(first, {f"q_{first.question.id}": "si"}).status_code, 302)
        # "Sí" stored with its follow-up blank while it was optional ...
        self.assertEqual(self.post_kiosk(doubts, {f"q_{doubts.question.id}": "si", f"q_{detail.id}": ""}).status_code, 302)
        self.assertNotIn(detail.id, load_answers(self.interview))
        # ... and then the follow-up became required.
        Question.objects.filter(pk=detail.pk).update(required=True)  # a published version: only for this test
        with mock.patch.dict(questionnaire._structure_cache, clear=True):
            self.assertEqual(self.welcome_link(), ("Retomar", reverse("kiosk:step", args=[doubts.number])))
            review = _Tree(self.client.get(reverse("kiosk:review")).content.decode()).root
            (keep_going,) = [n for n in review.iter() if n.tag == "a" and n.text() == "Seguir contestando"]
            self.assertEqual(keep_going.attrs["href"], reverse("kiosk:step", args=[doubts.number]))

    def test_retomar_opens_the_first_question_left(self):
        first = self.step("salvo")
        self.assertEqual(self.welcome_link(), ("Empezar", reverse("kiosk:step", args=[first.number])))
        self.assertEqual(self.post_kiosk(first, {f"q_{first.question.id}": "si"}).status_code, 302)
        self.assertEqual(self.welcome_link(), ("Retomar", reverse("kiosk:step", args=[self.step("dudas").number])))


# ---- known gap: a confidential and required question --------------------------------------------------


WARNING = "Si el campero no la contesta, solo el equipo de cuidado autorizado podrá completarla."


class ConfidentialRequiredWarningTests(CampFixture):
    def setUp(self):
        self.draft = duplicate_version(self.version, self.admin)
        self.login(self.admin)

    def edit_url(self, question):
        return self.url("question_edit", self.draft.id, question.id)

    def draft_question(self, code, **values):
        question = Question.objects.get(section__version=self.draft, code=code)
        for name, value in values.items():
            setattr(question, name, value)
        question.save()
        return question

    def test_the_warning_shows_only_for_a_confidential_required_question(self):
        cases = [
            ("impedimento", "confidencial", True, True),
            ("impedimento", "confidencial", False, False),
            ("salvo", "sensible", True, False),
            ("salvo", "general", True, False),
            ("salvo", "sensible", False, False),
        ]
        for code, sensitivity, required, warned in cases:
            question = self.draft_question(code, sensitivity=sensitivity, required=required)
            response = self.client.get(self.edit_url(question))
            self.assertEqual(response.status_code, 200)
            if warned:
                self.assertContains(response, f'<small id="id_required_helptext">{WARNING}</small>', count=1, html=False)
                self.assertContains(response, 'aria-describedby="id_required_helptext"')
            else:
                self.assertNotContains(response, WARNING, msg_prefix=f"{code} {sensitivity} {required}")

    def test_the_warning_follows_what_was_just_submitted(self):
        question = self.draft_question("salvo", sensitivity="sensible", required=False)
        data = {"text": "", "help_text": "", "kind": "unica", "sensitivity": "confidencial", "required": "on", "order": 1}
        response = self.client.post(self.edit_url(question), data)  # no text: the form comes back with the error
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, WARNING, count=1)
        response = self.client.post(self.edit_url(question), {**data, "required": ""})
        self.assertNotContains(response, WARNING)

    def test_a_new_question_has_no_warning(self):
        section = self.draft.sections.first()
        response = self.client.get(self.url("question_add", self.draft.id, section.id))
        self.assertNotContains(response, WARNING)
        self.assertFalse(QuestionForm(version=self.draft).fields["required"].help_text)


class ConfidentialRequiredGapTests(CampFixture):
    """The documented gap (docs/roles-and-permissions.md, "Known gaps") and both ways out, as they are today."""

    def leave_blank(self, code, *, answer_first=False):
        """Make ``code`` required, hand the computer over, and let the campero tap "Ya terminé" without answering it."""
        question = self.question(code)
        Question.objects.filter(pk=question.pk).update(required=True)  # a published version: only for this test
        patcher = mock.patch.dict(questionnaire._structure_cache, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p1, mode="supervisada")
        self.interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", self.interview.public_id))
        if answer_first:
            self.kiosk_answer(self.step("salvo"), "si")
        self.client.post(reverse("kiosk:finish"))
        self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        return question

    def kiosk_answer(self, step, value):
        self.interview.refresh_from_db()
        posted = self.client.post(reverse("kiosk:step", args=[step.number]),
                                  {"revision": self.interview.revision, "direction": "next", f"q_{step.question.id}": value})
        self.assertEqual(posted.status_code, 302)

    def finalize(self, client):
        self.interview.refresh_from_db()
        return client.post(self.url("interview_review", self.interview.public_id),
                           {"revision": self.interview.revision, "follow_up_decision": "no_requerido"})

    def assert_dead_end(self, question, step, *, offers_change):
        self.assertContains(self.finalize(self.client), f"Falta responder “{question.text}”.")
        step_url = self.url("interview_step", self.interview.public_id, step.number)
        review = self.client.get(self.url("interview_review", self.interview.public_id))
        self.assertContains(review, f'href="{step_url}?volver=revision"', count=1 if offers_change else 0)
        response = self.client.get(step_url)
        self.assertRedirects(response, self.url("interview_review", self.interview.public_id), fetch_redirect_response=False)

    def test_a_required_confidential_question_is_a_dead_end_for_the_coordinador(self):
        question = self.leave_blank("impedimento")
        self.assert_dead_end(question, self.step("impedimento"), offers_change=False)

    def test_so_is_a_required_question_sharing_its_screen_with_a_confidential_follow_up(self):
        question = self.leave_blank("dudas")  # sensible itself; "dudas_detalle" on its screen is confidential
        self.assert_dead_end(question, self.step("dudas"), offers_change=True)  # "Cambiar" is there, and bounces

    def test_the_camper_can_answer_it_on_the_kiosk_again(self):
        question = self.leave_blank("impedimento", answer_first=True)
        response = self.client.post(self.url("interview_handoff", self.interview.public_id))
        self.assertRedirects(response, reverse("kiosk:home"), fetch_redirect_response=False)
        page = _Tree(self.client.get(reverse("kiosk:home")).content.decode()).root
        (resume,) = [n for n in page.iter() if n.tag == "a" and "btn--primary" in n.classes]
        self.assertEqual((resume.text(), resume.attrs["href"]), ("Retomar", reverse("kiosk:step", args=[self.step("dudas").number])))
        self.kiosk_answer(self.step("impedimento"), "Algo")
        self.assertIn(question.id, load_answers(self.interview))
        self.client.post(reverse("kiosk:finish"))
        self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        self.assertRedirects(self.finalize(self.client), self.url("interview_done", self.interview.public_id), fetch_redirect_response=False)

    def test_an_administrador_with_confidential_access_can_complete_it(self):
        question = self.leave_blank("impedimento")
        EventMembership.objects.create(event=self.event, user=self.admin, role=MembershipRole.ADMIN, can_view_sensitive=True)
        care = self.login(self.admin, Client())
        self.choose_station(care, number=2)
        care.post(self.url("interview_conflict", self.interview.public_id))  # "Tomar el control aquí"
        self.interview.refresh_from_db()
        step_url = self.url("interview_step", self.interview.public_id, self.step("impedimento").number)
        self.assertEqual(care.get(step_url).status_code, 200)
        posted = care.post(step_url, {"revision": self.interview.revision, "direction": "next", f"q_{question.id}": "Algo"})
        self.assertEqual(posted.status_code, 302)
        self.assertRedirects(self.finalize(care), self.url("interview_done", self.interview.public_id), fetch_redirect_response=False)

    def test_the_assigned_coordinador_with_confidential_access_can_complete_it(self):
        question = self.leave_blank("impedimento")
        EventMembership.objects.filter(pk=self.m_coord1.pk).update(can_view_sensitive=True)
        self.interview.refresh_from_db()
        step_url = self.url("interview_step", self.interview.public_id, self.step("impedimento").number)
        self.assertEqual(self.client.get(step_url).status_code, 200)
        posted = self.client.post(step_url, {"revision": self.interview.revision, "direction": "next", f"q_{question.id}": "Algo"})
        self.assertEqual(posted.status_code, 302)
        self.assertRedirects(self.finalize(self.client), self.url("interview_done", self.interview.public_id), fetch_redirect_response=False)
