"""«Descargar las respuestas»: every campero of the event and their answers to each questionnaire, one CSV row each
(admin.answers_export, services.exports.answers_rows). Only for administración with confidential access.

Each test names the defect it would catch.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from events.access import resolve_access
from events.models import (
    AuditEvent,
    ConsentRecord,
    EventMembership,
    EventParticipant,
    Interview,
    MembershipRole,
    Question,
    QuestionOption,
)
from events.services import questionnaire
from events.services.event_questionnaires import (
    links_for,
    move,
    stop_using,
    use_version,
)
from events.services.exports import answers_rows
from events.services.metrics import local_tz
from events.services.questionnaire import (
    AnswerValue,
    all_questions,
    create_questionnaire_from_definition,
    duplicate_version,
    publish_version,
    write_answers,
)
from events.views.common import nav_items

from .base import CampFixture, make_user
from .test_multi_questionnaires import IsolatedCacheMixin, definition
from .test_ui import ENGLISH, analyse

REGISTRY = ["Número", "Nombre", "Edad", "Cuarto", "Iglesia o grupo", "Sede", "Coordinador", "Participa", "Autorización", "Registro"]
FIRST = "Conversación espiritual"  # the fixture's questionnaire (DEFAULT_CAMP_QUESTIONNAIRE)
SECOND = "Cuestionario de servicio"
SUMMARY = "Descargó las respuestas de todos los camperos (CSV)"
NO_FLAG = "Solo quien tiene acceso confidencial puede descargar las respuestas."
INTRO = (
    "Un archivo CSV con todos los camperos y sus respuestas a cada cuestionario, para abrirlo en Excel. "
    "Queda registrado en el registro de actividad."
)
# 26 Oct 2026 17:05 UTC is 11:05 in Ciudad de México (UTC-6, no daylight saving time since 2022).
AT = datetime(2026, 10, 26, 17, 5, tzinfo=UTC)
AT_LOCAL = "26/10/2026 11:05"


class Helpers(IsolatedCacheMixin):
    """Event A uses the fixture's questionnaire, then "Cuestionario de servicio" (one question, "Sí"/"No")."""

    def setUp(self):
        super().setUp()
        self.care_admin = make_user("admin.cuidado@prueba.mx", "Alma", "Cuidado")
        EventMembership.objects.create(event=self.event, user=self.care_admin, role=MembershipRole.ADMIN, can_view_sensitive=True)
        self.service = create_questionnaire_from_definition(self.church, definition("Servicio", SECOND, "servir"))
        use_version(self.event, self.service)

    def interview(self, participant, version, answers=None, *, status=Interview.Status.COMPLETED, completed_at=AT):
        """An interview of ``participant`` on ``version`` with ``answers`` ({code: AnswerValue}), written as a
        coordinador's screen writes them (hidden conditional answers are pruned)."""
        interview = Interview.objects.create(
            event=self.event,
            participant=participant,
            version=version,
            status=status,
            completed_at=completed_at if status == Interview.Status.COMPLETED else None,
        )
        if answers:
            by_code = {question.code: question for question in all_questions(version)}
            write_answers(interview, {by_code[code].id: value for code, value in answers.items()}, actor=self.care_admin, by_participant=False)
        return interview

    def download(self, user=None):
        response = self.login(user or self.care_admin, Client()).get(self.url("answers_export"))
        self.assertEqual(response.status_code, 200)
        return response

    def table(self, response=None):
        """(header, {name: {column: cell}}) of the downloaded file."""
        text = (response or self.download()).content.decode("utf-8")
        self.assertTrue(text.startswith("\ufeff"), "sin BOM")
        rows = list(csv.reader(io.StringIO(text[1:])))
        return rows[0], {row[1]: dict(zip(rows[0], row, strict=True)) for row in rows[1:]}

    def page(self, name, user):
        response = self.login(user, Client()).get(self.url(name))
        self.assertEqual(response.status_code, 200, name)
        return response.content.decode()


def choice(*codes):
    return AnswerValue(codes=list(codes))


def text(value):
    return AnswerValue(text=value)


# ---- Who downloads it, and where the link is ----------------------------------------------------------------------


class AccessTests(Helpers, CampFixture):
    def test_pastor_tutor_and_coordinator_are_refused(self):
        """Defect: a leader who is not administración downloading every campero's answers by typing the address,
        also the pastor of the care team who holds confidential access."""
        for user in (self.pastor, self.care, self.tutor1, self.coord1):
            response = self.login(user, Client()).get(self.url("answers_export"))
            self.assertEqual(response.status_code, 403, user.username)
        self.assertFalse(AuditEvent.objects.filter(event=self.event, action="exportacion").exists())

    def test_an_administrador_without_confidential_access_is_refused_and_told_why_on_configuracion(self):
        """Defect: every answer, confidential ones included, in the hands of an administrador the church never gave
        confidential access (the per-campero JSON already refuses them); or a link that answers 403."""
        member_admin = make_user("admin.sin.acceso@prueba.mx", "Adán", "Sinacceso")
        EventMembership.objects.create(event=self.event, user=member_admin, role=MembershipRole.ADMIN)
        link = reverse("events:answers_export", args=[self.event.slug])
        for user in (self.admin, member_admin):
            self.assertEqual(self.login(user, Client()).get(self.url("answers_export")).status_code, 403, user.username)
            for name in ("settings", "participants", "admin_home"):
                html = self.page(name, user)
                self.assertNotIn(link, html, name)
                self.assertNotIn("descargar las respuestas</a>", html.lower(), name)
            self.assertIn(NO_FLAG, self.page("settings", user))
            self.assertIn('<h2 id="t-exportar" class="heading">Exportar datos</h2>', self.page("settings", user))
        for name in ("participants", "admin_home"):  # the sentence is for Configuración only
            self.assertNotIn(NO_FLAG, self.page(name, self.admin), name)
        self.assertFalse(AuditEvent.objects.filter(event=self.event, action="exportacion").exists())

    def test_an_administrador_with_confidential_access_downloads_it_and_sees_the_three_links(self):
        """Defect: the export built but reachable from nowhere, or a page of the administración turning English."""
        self.download()
        link = f'href="{reverse("events:answers_export", args=[self.event.slug])}"'
        settings = self.page("settings", self.care_admin)
        self.assertIn(f'<a class="btn btn--secondary" {link}>Descargar las respuestas</a>', settings)
        self.assertIn(INTRO, settings)
        self.assertIn('<li><a href="#exportar">Exportar datos</a></li>', settings)
        self.assertNotIn(NO_FLAG, settings)
        participants = self.page("participants", self.care_admin)
        self.assertIn(f"(CSV, sin respuestas) o <a {link}>descargar las respuestas</a>.</p>", participants)
        home = self.page("admin_home", self.care_admin)
        self.assertIn(f"Descargar la lista de camperos</a> o <a {link}>descargar las respuestas</a></p>", home)
        for name, html in (("settings", settings), ("participants", participants), ("admin_home", home)):
            match = ENGLISH.search(" ".join(analyse(html).text))
            self.assertIsNone(match, f"{name}: texto en inglés «{match.group(0) if match else ''}»")

    def test_the_download_keeps_camperos_highlighted_in_the_bar(self):
        """Defect: a page of the export lighting no item of the administración's bar."""
        items = nav_items(self.event, resolve_access(self.care_admin, self.event))
        camperos = next(item for item in items if item["label"] == "Camperos")
        self.assertIn("answers_export", camperos["names"])


# ---- What the file holds ------------------------------------------------------------------------------------------


class ContentTests(Helpers, CampFixture):
    """p1 answered both; p3 has the first one en curso; p2 has none; p4 no longer takes part; Lucía's interview was
    discarded."""

    def setUp(self):
        super().setUp()
        EventParticipant.objects.filter(pk=self.p1.pk).update(created_at=AT)
        self.interview(
            self.p1,
            self.version,
            {
                "salvo": choice("si"),
                "dudas": choice("no"),  # "dudas_detalle" stays hidden: an unanswered conditional
                "apoyo": choice("bienestar_emocional", "familia"),  # chosen out of order on purpose
                "apoyo_detalle": text("=HIPERVINCULO(\"x\")"),
                "decision": choice("prefiero_no"),
                "impedimento": text("Necesito más tiempo."),
            },
        )
        self.interview(self.p1, self.service, {"servir": choice("si")})
        self.interview(self.p3, self.version, {"salvo": choice("no_seguro")}, status=Interview.Status.IN_PROGRESS)
        EventParticipant.objects.filter(pk=self.p4.pk).update(is_active=False)
        discarded = self.interview(self.p_minor_no_auth, self.version, {"salvo": choice("no"), "impedimento": text("Borrado")})
        Interview.objects.filter(pk=discarded.pk).update(status=Interview.Status.DISCARDED)
        # p4's authorization was received and later withdrawn: the latest record decides.
        ConsentRecord.objects.create(
            participant=self.p4, kind=ConsentRecord.Kind.GUARDIAN, status=ConsentRecord.Status.REVOKED, recorded_at=timezone.now() + timezone.timedelta(minutes=1)
        )
        self.header, self.rows = self.table()

    def col(self, title, code, version=None):
        question = next(q for q in all_questions(version or self.version) if q.code == code)
        return f"{title}: {question.text}"

    def test_the_header_is_the_registry_then_each_questionnaire_in_the_event_order(self):
        """Defect: answers under columns nobody can tell apart (no questionnaire title), questions out of the order the
        campero answered them, or the questionnaires in the order they were created instead of the event's."""
        first = [f"{FIRST}: estado", f"{FIRST}: fecha en que se completó", *(f"{FIRST}: {q.text}" for q in all_questions(self.version))]
        second = [f"{SECOND}: estado", f"{SECOND}: fecha en que se completó", f"{SECOND}: ¿Pregunta de Servicio?"]
        self.assertEqual(self.header, REGISTRY + first + second)
        move(self.event, self.service.questionnaire, "subir")
        header, _ = self.table()
        self.assertEqual(header, REGISTRY + second + first)

    def test_a_campero_who_answered_both_has_every_answer_in_words(self):
        """Defect: option codes ("familia", "prefiero_no") instead of the words the campero chose, a multiple-choice
        answer cut to one option, a long text lost, or an unanswered conditional filled with something."""
        row = self.rows[self.p1.full_name]
        self.assertEqual(row[f"{FIRST}: estado"], "Completada")
        self.assertEqual(row[f"{FIRST}: fecha en que se completó"], AT_LOCAL)
        self.assertEqual(row[self.col(FIRST, "salvo")], "Sí")
        self.assertEqual(row[self.col(FIRST, "dudas")], "No")
        self.assertEqual(row[self.col(FIRST, "dudas_detalle")], "")
        self.assertEqual(row[self.col(FIRST, "apoyo")], "Familia, Bienestar emocional")  # the questionnaire's order, not alphabetical
        self.assertEqual(row[self.col(FIRST, "decision")], "Prefiero no responder")
        self.assertEqual(row[self.col(FIRST, "impedimento")], "Necesito más tiempo.")
        self.assertEqual(row[self.col(FIRST, "orientacion")], "")
        self.assertEqual(row[f"{SECOND}: estado"], "Completada")
        self.assertEqual(row[f"{SECOND}: ¿Pregunta de Servicio?"], "Sí")

    def test_an_interview_en_curso_shows_its_answers_so_far(self):
        """Defect: an interview still open left out, or its answers so far dropped until it is completed."""
        row = self.rows[self.p3.full_name]
        self.assertEqual(row[f"{FIRST}: estado"], "En curso")
        self.assertEqual(row[f"{FIRST}: fecha en que se completó"], "")
        self.assertEqual(row[self.col(FIRST, "salvo")], "No lo tengo claro")
        self.assertEqual(row[self.col(FIRST, "dudas")], "")
        self.assertEqual(row[f"{SECOND}: estado"], "Sin entrevista")

    def test_a_campero_without_interviews_is_in_the_file_with_empty_answers(self):
        """Defect: camperos nobody interviewed missing from the file, so it cannot be used to see who is left."""
        row = self.rows[self.p2.full_name]
        for title in (FIRST, SECOND):
            self.assertEqual(row[f"{title}: estado"], "Sin entrevista")
            self.assertEqual(row[f"{title}: fecha en que se completó"], "")
        self.assertEqual({row[column] for column in self.header[len(REGISTRY):] if not column.endswith(": estado")}, {""})

    def test_a_campero_who_no_longer_takes_part_is_in_the_file(self):
        """Defect: a withdrawn campero's answers lost from the only file that keeps them (the list CSV keeps them too)."""
        self.assertEqual(self.rows[self.p4.full_name]["Participa"], "No")
        self.assertEqual(self.rows[self.p1.full_name]["Participa"], "Sí")
        self.assertEqual(len(self.rows), 5)
        self.assertNotIn("Persona Otro Evento", self.rows)  # another event's campero

    def test_a_discarded_interview_contributes_nothing(self):
        """Defect: answers of an interview the team threw away reaching the file as if they were the campero's."""
        row = self.rows[self.p_minor_no_auth.full_name]
        self.assertEqual(row[f"{FIRST}: estado"], "Sin entrevista")
        self.assertEqual(row[self.col(FIRST, "salvo")], "")
        self.assertNotIn("Borrado", self.download().content.decode("utf-8"))

    def test_a_cell_that_starts_like_a_formula_is_neutralised(self):
        """Defect: a campero's text starting with "=" run by Excel as a formula on the administrador's computer."""
        self.assertEqual(self.rows[self.p1.full_name][self.col(FIRST, "apoyo_detalle")], "'=HIPERVINCULO(\"x\")")

    def test_autorizacion_follows_the_latest_record_and_is_empty_for_adults(self):
        """Defect: a minor whose authorization was withdrawn exported as authorized, or adults marked "No"."""
        self.assertEqual(self.rows[self.p1.full_name]["Autorización"], "Sí")
        self.assertEqual(self.rows[self.p_minor_no_auth.full_name]["Autorización"], "No")
        self.assertEqual(self.rows[self.p4.full_name]["Autorización"], "No")
        self.assertEqual(self.rows[self.p2.full_name]["Autorización"], "")  # 19 years old

    def test_autorizacion_is_empty_at_the_adult_age_and_when_the_event_does_not_ask_for_it(self):
        """Defect: a campero exactly at the adult age marked "No", or "No" for every minor in a camp that never asks
        for guardian authorization (the rest of the app shows nothing then)."""
        just_adult = EventParticipant.objects.create(
            event=self.event, number=90, full_name="Justo Mayor Edad", age=self.event.adult_age, room="90", church_group="Iglesia Centro",
            coordinator=self.m_coord1,
        )
        _, rows = self.table()
        self.assertEqual(rows[just_adult.full_name]["Autorización"], "")
        self.event.requires_guardian_authorization = False
        self.event.save(update_fields=["requires_guardian_authorization"])
        _, rows = self.table()
        self.assertEqual(rows[self.p_minor_no_auth.full_name]["Autorización"], "")
        self.assertEqual(rows[self.p1.full_name]["Autorización"], "")

    def test_a_header_that_starts_like_a_formula_is_neutralised_too(self):
        """Defect: a questionnaire title starting with "=" run by Excel as a formula from the header row."""
        third = create_questionnaire_from_definition(self.church, definition("Tercero", "=Tercero", "tercero"))
        use_version(self.event, third)
        header, _ = self.table()
        self.assertIn("'=Tercero: estado", header)
        self.assertNotIn("=Tercero: estado", header)

    def test_the_registry_columns_and_the_date_format(self):
        """Defect: a date in UTC or in a format Excel in Mexico reads as month/day."""
        row = self.rows[self.p1.full_name]
        self.assertEqual(
            [row[column] for column in REGISTRY],
            ["0001", "Mariana López Ruiz", "15", "1", "Iglesia Centro", "", self.m_coord1.display_name, "Sí", "Sí", AT_LOCAL],
        )
        self.assertEqual(list(self.rows), [p.full_name for p in EventParticipant.objects.filter(event=self.event).order_by("number")])

    def test_a_questionnaire_the_event_no_longer_uses_is_not_read(self):
        """Defect: columns and answers of a questionnaire the administración removed from the event."""
        stop_using(self.event, self.service.questionnaire)
        header, rows = self.table()
        self.assertFalse([column for column in header if column.startswith(f"{SECOND}:")])
        self.assertEqual(len(header), len(REGISTRY) + 2 + len(all_questions(self.version)))
        self.assertEqual(len(rows[self.p1.full_name]), len(header))


class OldVersionTests(Helpers, CampFixture):
    def test_answers_on_an_older_version_land_in_the_columns_of_the_version_in_use(self):
        """Defect: after "Usar" on a new version, every interview of the old one exported empty (its question ids are
        not the new version's), or an answer to a question the new version dropped put under another question."""
        old = self.interview(self.p1, self.version, {"salvo": choice("si"), "apoyo": choice("familia"), "impedimento": text("Pregunta quitada")})
        draft = duplicate_version(self.version, self.care_admin)
        salvo = Question.objects.get(section__version=draft, code="salvo")
        salvo.text = "¿Hoy te consideras salvo?"
        salvo.save()
        option = QuestionOption.objects.get(question=salvo, code="si")
        option.label = "Sí, me considero salvo"
        option.save()
        Question.objects.get(section__version=draft, code="impedimento").delete()
        publish_version(draft, self.care_admin)
        use_version(self.event, draft)
        new = self.interview(self.p2, draft, {"salvo": choice("si")})
        self.assertNotEqual(old.version_id, new.version_id)

        header, rows = self.table()
        self.assertEqual(header[len(REGISTRY):len(REGISTRY) + 2 + len(all_questions(draft))][2:], [f"{FIRST}: {q.text}" for q in all_questions(draft)])
        self.assertNotIn(f"{FIRST}: ¿Hay algo que te impida dar el siguiente paso en tu fe?", header)
        self.assertEqual(rows[self.p1.full_name][f"{FIRST}: ¿Hoy te consideras salvo?"], "Sí")  # the words the campero chose
        self.assertEqual(rows[self.p1.full_name][f"{FIRST}: ¿Hay alguna situación en tu vida en la que te gustaría recibir apoyo?"], "Familia")
        self.assertEqual(rows[self.p2.full_name][f"{FIRST}: ¿Hoy te consideras salvo?"], "Sí, me considero salvo")
        self.assertNotIn("Pregunta quitada", self.download().content.decode("utf-8"))


# ---- The record of the download, the file's name, the cost --------------------------------------------------------


class AuditAndFileTests(Helpers, CampFixture):
    def test_one_audit_row_with_counts_and_no_names(self):
        """Defect: a download of every answer that leaves no trace, or a summary (shown on Inicio, "Descargas
        recientes") that names camperos."""
        self.interview(self.p1, self.version, {"salvo": choice("si")})
        self.download()
        rows = list(AuditEvent.objects.filter(event=self.event, action="exportacion"))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.summary, SUMMARY)
        self.assertEqual(row.metadata, {"kind": "respuestas", "camperos": 5, "cuestionarios": 2})
        self.assertEqual((row.actor, row.object_type, row.object_id), (self.care_admin, "", ""))
        for participant in EventParticipant.objects.filter(event=self.event):
            self.assertNotIn(participant.full_name, row.summary)
            self.assertNotIn(participant.code, row.summary)
        self.assertIn(f'<p class="row__title">{SUMMARY}</p>', self.page("admin_home", self.care_admin))

    def test_the_file_is_a_csv_named_by_event_and_local_time(self):
        """Defect: a file Excel opens with broken accents (no BOM), every download overwriting the last one, or a name
        stamped in UTC."""
        self.event.time_zone = "America/Tijuana"
        self.event.save(update_fields=["time_zone"])
        before = timezone.localtime(timezone.now(), local_tz(self.event)).strftime("%Y%m%d-%H%M")
        response = self.download()
        after = timezone.localtime(timezone.now(), local_tz(self.event)).strftime("%Y%m%d-%H%M")
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        match = re.fullmatch(r'attachment; filename="respuestas-campamento-a-(\d{8}-\d{4})\.csv"', response["Content-Disposition"])
        self.assertIsNotNone(match, response["Content-Disposition"])
        self.assertIn(match.group(1), {before, after})

    def test_the_list_without_answers_is_unchanged(self):
        """Defect: moving the formula guard out of the views changing the list CSV (its columns are read by position)."""
        EventParticipant.objects.filter(pk=self.p2.pk).update(church_group="=Iglesia")
        response = self.login(self.care_admin, Client()).get(self.url("participant_export"))
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["Número", "Nombre", "Edad", "Cuarto", "Iglesia o grupo", "Sede", "Coordinador", "Entrevista", "Participa", "Registro"])
        self.assertEqual(next(row for row in rows if row[1] == self.p2.full_name)[4], "'=Iglesia")
        self.assertEqual(AuditEvent.objects.get(event=self.event, action="exportacion").metadata, {"kind": "lista"})


class QueryCountTests(Helpers, CampFixture):
    BOUND = 10

    def add_camperos(self, upto):
        for number in range(EventParticipant.objects.filter(event=self.event).count() + 1, upto + 1):
            participant = EventParticipant.objects.create(
                event=self.event, number=number, full_name=f"Campero {number:02d} Prueba", age=16, room=str(number),
                church_group="Iglesia Centro", coordinator=self.m_coord2,
            )
            ConsentRecord.objects.create(participant=participant, kind=ConsentRecord.Kind.GUARDIAN)
        for participant in EventParticipant.objects.filter(event=self.event, interviews__isnull=True):
            self.interview(participant, self.version, {"salvo": choice("si"), "apoyo": choice("familia", "relaciones"), "impedimento": text("Nada.")})
            self.interview(participant, self.service, {"servir": choice("no")})

    def count(self):
        with CaptureQueriesContext(connection) as queries:
            _, rows = answers_rows(self.event)
        return len(queries), rows

    def test_the_export_reads_a_fixed_number_of_queries(self):
        """Defect: one query per campero or per answer, so the file of a 400-campero camp takes minutes.
        Measured: 6 queries with the questionnaires' structure in the process cache (published versions are cached by
        build_steps), 12 on a cold cache (3 more per questionnaire, once per process)."""
        self.add_camperos(30)
        self.assertEqual(Interview.objects.filter(event=self.event).count(), 60)
        warm, rows = self.count()
        self.assertEqual(len(rows), 30)
        self.assertLessEqual(warm, self.BOUND)
        questionnaire._structure_cache.clear()  # IsolatedCacheMixin restores it
        cold, _ = self.count()
        self.assertLessEqual(cold, warm + 3 * len(links_for(self.event)))
        self.add_camperos(40)
        more, rows = self.count()
        self.assertEqual(len(rows), 40)
        self.assertEqual(more, warm)
