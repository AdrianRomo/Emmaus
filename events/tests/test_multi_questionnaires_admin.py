"""Administración with several questionnaires per event: Cuestionarios (new, order, remove, in-order box), the version
page (use, stop), Configuración, the exports; who reads the confidential answers (per event, never retroactive);
and the team report opened from a team's tab.

Each test names the defect it would catch.
"""

from __future__ import annotations

import csv
import io
import json
import re
from unittest import mock

from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from events.access import resolve_access
from events.forms import EventSettingsForm
from events.models import AuditEvent, Event, EventMembership, Interview, MembershipRole, Questionnaire, QuestionnaireVersion
from events.services import pdf as pdf_module
from events.services.event_questionnaires import links_for, use_version
from events.services.questionnaire import create_questionnaire_from_definition, duplicate_version, publish_version

from .base import CampFixture, make_user
from .test_multi_questionnaires import LAST_QUESTIONNAIRE, IsolatedCacheMixin, definition
from .test_ui import ENGLISH, analyse

SECTION = r'<section class="admin-narrow" aria-labelledby="en-este-evento">(.*?)</section>'
ROW = re.compile(r'<li class="row">(.*?)</li>', re.S)


def section(html, pattern=SECTION):
    match = re.search(pattern, html, re.S)
    assert match, pattern
    return match.group(1)


class AdminHelpers(IsolatedCacheMixin):
    def page(self, name, *args, user=None, client=None):
        client = client or self.login(user or self.admin, Client())
        response = client.get(self.url(name, *args))
        self.assertEqual(response.status_code, 200, name)
        return response.content.decode()

    def assert_spanish_and_labelled(self, html, name):
        self.assertEqual(len(re.findall(r"<h1\b", html)), 1, name)
        parsed = analyse(html)
        match = ENGLISH.search(" ".join(parsed.text))
        self.assertIsNone(match, f"{name}: texto en inglés «{match.group(0) if match else ''}»")
        for tag, attrs in parsed.controls:
            if "visually-hidden" in attrs.get("class", ""):
                continue
            self.assertTrue(attrs.get("id") in parsed.labels_for or attrs.get("aria-label"), f"{name}: {tag} {attrs.get('name')} sin etiqueta")

    def second(self):
        """A second published questionnaire of the church, used by the event after the fixture's."""
        version = create_questionnaire_from_definition(self.church, definition("Servicio", "Cuestionario de servicio", "servir"))
        use_version(self.event, version)
        return version

    def orders(self):
        return [(link.version, link.order) for link in links_for(self.event)]

    def last_audit(self):
        return AuditEvent.objects.filter(event=self.event).order_by("-id").first()


# ---- 1-4. Cuestionarios: "Nuevo cuestionario", the event's list, its row actions -----------------------------------


class QuestionnairesPageTests(AdminHelpers, CampFixture):
    def test_the_page_offers_nuevo_cuestionario_and_both_pages_are_spanish_and_labelled(self):
        """Defect (owner's ask 3): Cuestionarios had no way to create a questionnaire."""
        self.second()  # two: every control of the page is drawn (Subir, Bajar, the in-order box)
        html = self.page("questionnaires")
        self.assertIn(f'<a class="btn btn--primary" href="{self.url("questionnaire_create")}">Nuevo cuestionario</a>', html)
        self.assert_spanish_and_labelled(html, "questionnaires")
        form = self.page("questionnaire_create")
        self.assert_spanish_and_labelled(form, "questionnaire_create")
        self.assertIn(f'<a href="{self.url("questionnaires")}" aria-current="page">Cuestionarios</a>', form)  # the bar stays on it
        self.assertIn('<button class="btn btn--primary" type="submit">Crear cuestionario</button>', form)
        self.assertIn(f'<a class="back-link" href="{self.url("questionnaires")}">Cuestionarios</a>', form)

    def test_creating_a_questionnaire_makes_an_empty_draft_and_opens_it(self):
        """Defect: "Nuevo cuestionario" publishing an empty questionnaire, or not saying what happens next."""
        client = self.login(self.admin, Client())
        response = client.post(self.url("questionnaire_create"), {"name": "Seguimiento del domingo", "title": "", "intro_text": ""})
        questionnaire = Questionnaire.objects.get(church=self.church, name="Seguimiento del domingo")
        (version,) = questionnaire.versions.all()
        self.assertEqual((version.number, version.status, version.title), (1, QuestionnaireVersion.Status.DRAFT, "Seguimiento del domingo"))
        self.assertRedirects(response, self.url("version_detail", version.id), fetch_redirect_response=False)
        self.assertContains(
            client.get(response["Location"]),
            "Cuestionario creado como borrador. Agrega secciones y preguntas; cuando esté listo, publícalo y úsalo en este evento.",
        )
        audit = self.last_audit()
        self.assertEqual((audit.action, audit.summary), ("cuestionario", "Creó el cuestionario «Seguimiento del domingo»"))
        self.assertEqual(self.orders(), [(self.version, 1)])  # a draft is not used by the event

    def test_a_nameless_questionnaire_stays_on_the_form_with_the_error(self):
        """Defect: an empty form creating a questionnaire without a name."""
        client = self.login(self.admin, Client())
        before = Questionnaire.objects.count()
        response = client.post(self.url("questionnaire_create"), {"name": "  "})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hay un problema")
        self.assertContains(response, "<title>Error: ")
        self.assertContains(response, "Escribe el nombre del cuestionario.")
        self.assertEqual(Questionnaire.objects.count(), before)

    def test_only_the_administracion_creates_questionnaires(self):
        """Defect: a tutor, pastor or coordinator creating questionnaires of the church."""
        for user in (self.tutor1, self.pastor, self.coord1):
            client = self.login(user, Client())
            self.assertEqual(client.get(self.url("questionnaire_create")).status_code, 403, user)
            self.assertEqual(client.post(self.url("questionnaire_create"), {"name": "Intruso"}).status_code, 403, user)
        self.assertFalse(Questionnaire.objects.filter(name="Intruso").exists())

    def test_the_event_list_is_in_order_with_its_moves_and_the_box_only_with_two(self):
        """Defect: "Subir" on the first row, "Bajar" on the last, or the in-order box adding a question to a one-questionnaire event."""
        one = section(self.page("questionnaires"))
        (row,) = ROW.findall(one)
        self.assertNotIn(">Subir<", row)
        self.assertNotIn(">Bajar<", row)
        self.assertIn(">Quitar<", row)
        self.assertNotIn('name="questionnaires_in_order"', one)
        self.assertIn(f'{self.version.questionnaire.name}, versión 1, 0 entrevistas.', row)

        second = self.second()
        two = section(self.page("questionnaires"))
        first_row, last_row = ROW.findall(two)
        self.assertIn(self.version.title, first_row)
        self.assertIn(second.title, last_row)
        self.assertNotIn(">Subir<", first_row)
        self.assertIn(f'>Bajar<span class="visually-hidden"> {self.version.title}</span></button>', first_row)
        self.assertIn(f'>Subir<span class="visually-hidden"> {second.title}</span></button>', last_row)
        self.assertNotIn(">Bajar<", last_row)
        self.assertIn(f'data-confirm="¿Quitar «{second.title}» de este evento? Las entrevistas hechas se conservan."', last_row)
        self.assertIn('name="questionnaires_in_order"', two)
        self.assertIn("Los camperos los responden en este orden", two)

    def test_bajar_remove_and_the_box_save_and_are_recorded(self):
        """Defect: the row actions changing nothing, changing the wrong questionnaire, or leaving no trace."""
        second = self.second()
        client = self.login(self.admin, Client())
        response = client.post(self.url("questionnaires"), {"action": "bajar", "questionnaire": self.version.questionnaire_id})
        self.assertRedirects(response, self.url("questionnaires"), fetch_redirect_response=False)
        self.assertEqual(self.orders(), [(second, 1), (self.version, 2)])
        self.assertEqual(self.last_audit().summary, f"Movió «{self.version.title}» al lugar 2")

        client.post(self.url("questionnaires"), {"action": "order", "questionnaires_in_order": "on"})
        self.assertTrue(Event.objects.get(pk=self.event.pk).questionnaires_in_order)
        audit = self.last_audit()
        self.assertEqual((audit.action, audit.summary, audit.metadata), ("evento_configurado", "Los cuestionarios se responden en orden", {"form": "cuestionarios"}))
        self.assertContains(client.get(self.url("questionnaires")), "Orden guardado.")
        client.post(self.url("questionnaires"), {"action": "order"})
        self.assertFalse(Event.objects.get(pk=self.event.pk).questionnaires_in_order)
        self.assertEqual(self.last_audit().summary, "Los cuestionarios se responden en cualquier orden")

        response = client.post(self.url("questionnaires"), {"action": "remove", "questionnaire": second.questionnaire_id})
        self.assertEqual(self.orders(), [(self.version, 1)])
        self.assertContains(client.get(response["Location"]), f"«{second.title}» ya no se usa en este evento. Las entrevistas hechas se conservan.")
        audit = self.last_audit()
        self.assertEqual((audit.action, audit.summary), ("cuestionario", f"Dejó de usar «{second.title}» en este evento"))

    def test_the_in_order_box_is_ignored_with_one_questionnaire(self):
        """Defect: a forged "order" post on a one-questionnaire event saving and recording a setting the page does not
        offer (the box is drawn only with two or more)."""
        client = self.login(self.admin, Client())
        before = AuditEvent.objects.count()
        response = client.post(self.url("questionnaires"), {"action": "order", "questionnaires_in_order": "on"})
        self.assertRedirects(response, self.url("questionnaires"), fetch_redirect_response=False)
        self.assertFalse(Event.objects.get(pk=self.event.pk).questionnaires_in_order)
        self.assertEqual(AuditEvent.objects.count(), before)
        self.assertNotContains(client.get(response["Location"]), "Orden guardado.")

    def test_an_unknown_or_unused_questionnaire_is_404(self):
        """Defect: a row action reaching a questionnaire this event does not use (or another church's)."""
        unused = create_questionnaire_from_definition(self.church, definition("Libre", "Sin usar", "libre"))
        client = self.login(self.admin, Client())
        for value in ("", "abc", "²", "999999", str(unused.questionnaire_id)):
            for action in ("remove", "subir", "bajar"):
                with self.subTest(action=action, questionnaire=value):
                    self.assertEqual(client.post(self.url("questionnaires"), {"action": action, "questionnaire": value}).status_code, 404)
        self.assertEqual(client.post(self.url("questionnaires"), {"action": "otra"}).status_code, 404)
        self.assertEqual(self.orders(), [(self.version, 1)])

    def test_removing_the_last_questionnaire_of_an_event_with_interviews_is_refused_on_screen(self):
        """Defect: the service's refusal surfacing as a 500, or recorded as if it had happened."""
        Interview.objects.create(event=self.event, participant=self.p2, version=self.version)
        client = self.login(self.admin, Client())
        before = AuditEvent.objects.count()
        response = client.post(self.url("questionnaires"), {"action": "remove", "questionnaire": self.version.questionnaire_id})
        self.assertRedirects(response, self.url("questionnaires"), fetch_redirect_response=False)
        self.assertContains(client.get(response["Location"]), LAST_QUESTIONNAIRE)
        response = client.post(self.url("version_detail", self.version.id), {"action": "stop"})
        self.assertRedirects(response, self.url("version_detail", self.version.id), fetch_redirect_response=False)
        self.assertContains(client.get(response["Location"]), LAST_QUESTIONNAIRE)
        self.assertEqual(self.orders(), [(self.version, 1)])
        self.assertEqual(AuditEvent.objects.count(), before)

    def test_the_event_rows_count_this_events_interviews_only(self):
        """Defect: "En este evento" saying "N entrevistas" for the version's interviews in every event of the church."""
        self.assertEqual(self.other_event.questionnaire_versions(), [self.version])  # both events use it
        Interview.objects.create(event=self.other_event, participant=self.p_other_event, version=self.version)
        Interview.objects.create(event=self.event, participant=self.p2, version=self.version)
        Interview.objects.create(event=self.event, participant=self.p3, version=self.version)
        html = self.page("questionnaires")
        (row,) = ROW.findall(section(html))
        self.assertIn(f"{self.version.questionnaire.name}, versión 1, 2 entrevistas.", row)
        self.assertIn("<p class=\"row__meta\">3 entrevistas", html)  # the church's list still counts every event


# ---- 5. The version page: use (add or swap), stop -------------------------------------------------------------


class VersionPageTests(AdminHelpers, CampFixture):
    def test_using_another_questionnaire_adds_it_last(self):
        """Defect: "Usar en este evento" replacing the event's questionnaire instead of adding one."""
        other = create_questionnaire_from_definition(self.church, definition("Servicio", "Cuestionario de servicio", "servir"))
        html = self.page("version_detail", other.id)
        self.assertIn('<button class="btn btn--primary" name="action" value="use">Usar en este evento</button>', html)
        client = self.login(self.admin, Client())
        response = client.post(self.url("version_detail", other.id), {"action": "use"})
        self.assertEqual(self.orders(), [(self.version, 1), (other, 2)])
        self.assertContains(client.get(response["Location"]), "Este cuestionario se agregó al evento. Las entrevistas nuevas lo incluirán.")
        self.assertEqual(self.last_audit().summary, "Usará la versión 1 en entrevistas nuevas")

    def test_a_new_version_takes_the_old_ones_place(self):
        """Defect: a new version of a questionnaire appended as a second questionnaire, or moved to the end."""
        self.second()
        newer = duplicate_version(self.version, self.admin)
        publish_version(newer, self.admin)
        html = self.page("version_detail", newer.id)
        self.assertIn("En este evento se usa la versión 1 de este cuestionario.", html)
        self.assertIn('<button class="btn btn--primary" name="action" value="use">Usar esta versión en este evento</button>', html)
        client = self.login(self.admin, Client())
        response = client.post(self.url("version_detail", newer.id), {"action": "use"})
        self.assertEqual([order for _, order in self.orders()], [1, 2])
        self.assertEqual(self.orders()[0][0], newer)
        self.assertContains(client.get(response["Location"]), "Las entrevistas nuevas usarán esta versión. Las anteriores conservan la suya.")

    def test_stop_using_removes_it_from_the_event(self):
        """Defect: no way to stop using a questionnaire from its page."""
        second = self.second()
        html = self.page("version_detail", second.id)
        self.assertIn("En uso en este evento.", html)
        self.assertIn('name="action" value="stop"', html)
        self.assertIn(">Dejar de usar en este evento</button>", html)
        self.assertNotIn('value="use"', html)
        client = self.login(self.admin, Client())
        response = client.post(self.url("version_detail", second.id), {"action": "stop"})
        self.assertEqual(self.orders(), [(self.version, 1)])
        self.assertContains(client.get(response["Location"]), "Este cuestionario ya no se usa en el evento. Las entrevistas hechas se conservan.")
        self.assertEqual(self.last_audit().summary, f"Dejó de usar «{second.title}» en este evento")
        self.assertNotIn('value="stop"', self.page("version_detail", second.id))

    def test_a_draft_is_never_used_from_its_page(self):
        """Defect: a draft (still changing) reaching the camperos through a forged "use"."""
        draft = duplicate_version(self.version, self.admin)
        self.assertNotIn('value="use"', self.page("version_detail", draft.id))
        self.login(self.admin, Client()).post(self.url("version_detail", draft.id), {"action": "use"})
        self.assertEqual(self.orders(), [(self.version, 1)])


# ---- 6. Configuración -------------------------------------------------------------------------------------------


class SettingsTests(AdminHelpers, CampFixture):
    def test_settings_has_no_questionnaire_list_and_links_to_cuestionarios(self):
        """Defect: two places choosing the event's questionnaires (the old single list could undo the event's list)."""
        html = self.page("settings")
        self.assertNotIn("cfg-questionnaire", html)
        self.assertEqual([name for name in EventSettingsForm(instance=self.event).fields if "questionnaire" in name], [])
        self.assertIn(f'Los cuestionarios del evento se eligen en <a href="{self.url("questionnaires")}">Cuestionarios</a>.', html)
        self.assertIn(
            f'Quién lee las respuestas confidenciales: <a href="{self.url("confidential_level")}">'
            "Solo quien tiene acceso confidencial (y su coordinador mientras la anota)</a>.",
            html,
        )

    def test_saving_the_settings_keeps_the_questionnaires(self):
        """Defect: saving Configuración dropping the event's questionnaires (the old empty list meant "Sin cuestionario")."""
        self.second()
        before = self.orders()
        data = {f"cfg-{name}": value for name, value in {
            "time_zone": "America/Mexico_City", "reports_enabled": "on", "report_interval_minutes": "120",
            "report_window_start": "08:00", "report_window_end": "22:00", "adult_age": "18", "requires_guardian_authorization": "on",
            "min_group_size": "5", "retention_days": "365", "privacy_notice": "Aviso.", "safeguarding_instructions": "Llama.",
        }.items()}
        response = self.login(self.admin, Client()).post(self.url("settings"), {"form": "settings", **data})
        self.assertRedirects(response, self.url("settings") + "#cortes", fetch_redirect_response=False)
        self.assertEqual(self.orders(), before)


# ---- 7. "Versiones": grouped by questionnaire, "En uso" from the links ---------------------------------------------


class VersionsListTests(AdminHelpers, CampFixture):
    def test_en_uso_marks_only_the_linked_versions_and_use_is_offered_for_published_ones(self):
        """Defect: "En uso" read from one version per event, or "Usar" offered on a draft."""
        other = create_questionnaire_from_definition(self.church, definition("Servicio", "Cuestionario de servicio", "servir"))
        draft = duplicate_version(self.version, self.admin)
        html = section(self.page("questionnaires"), r'<section class="admin-narrow admin-section" aria-labelledby="versiones">(.*?)</section>')
        self.assertIn(f'<h3 class="heading mt-6">{self.version.questionnaire.name}</h3>', html)
        self.assertIn('<h3 class="heading mt-6">Servicio</h3>', html)
        rows = {re.search(r'versión (\d+)</a>', row).group(0) + re.search(r'>([^<]+), versión', row).group(1): row for row in ROW.findall(html)}
        in_use = '<span class="status status--done"><span class="status__mark" aria-hidden="true"></span>En uso</span>'
        used = rows[f"versión 1</a>{self.version.title}"]
        self.assertIn(in_use, used)
        self.assertNotIn('value="use"', used)
        offered = rows[f"versión 1</a>{other.title}"]
        self.assertNotIn(in_use, offered)
        self.assertIn(f'action="{self.url("version_detail", other.id)}"', offered)
        self.assertIn(f'Usar en este evento<span class="visually-hidden"> {other.title}, versión 1</span>', offered)
        unpublished = rows[f"versión {draft.number}</a>{draft.title}"]
        self.assertNotIn(in_use, unpublished)
        self.assertNotIn('value="use"', unpublished)
        self.assertIn('<span class="status status--done"><span class="status__mark" aria-hidden="true"></span>Publicada</span>', html)


# ---- 8. Exports: one person's data, the list of camperos ----------------------------------------------------------


class ExportTests(AdminHelpers, CampFixture):
    def setUp(self):
        super().setUp()
        self.care_admin = make_user("admin.cuidado@prueba.mx", "Alma", "Cuidado")
        EventMembership.objects.create(event=self.event, user=self.care_admin, role=MembershipRole.ADMIN, can_view_sensitive=True)

    def export(self):
        response = self.login(self.care_admin, Client()).get(self.url("participant_data_export", self.p1.public_id))
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)

    def test_one_interview_keeps_todays_shape(self):
        """Defect: the export of a one-questionnaire event changing shape for the families who already read it."""
        Interview.objects.create(event=self.event, participant=self.p1, version=self.version, status=Interview.Status.COMPLETED)
        data = self.export()
        self.assertNotIn("entrevistas", data)
        self.assertEqual(list(data["entrevista"]), ["estado", "versión del cuestionario", "fecha en que se completó", "respuestas"])

    def test_two_interviews_are_both_in_the_export(self):
        """Defect: the export of a person who answered two questionnaires leaving one of them out."""
        second = self.second()
        first = Interview.objects.create(event=self.event, participant=self.p1, version=self.version, status=Interview.Status.COMPLETED)
        later = Interview.objects.create(event=self.event, participant=self.p1, version=second, status=Interview.Status.IN_PROGRESS)
        Interview.objects.filter(pk=first.pk).update(started_at=timezone.now() - timezone.timedelta(hours=1))
        data = self.export()
        self.assertEqual(data["entrevista"]["estado"], later.get_status_display())  # the newest, as before
        self.assertEqual([i["cuestionario"] for i in data["entrevistas"]], [self.version.title, second.title])
        self.assertEqual(
            list(data["entrevistas"][0]), ["cuestionario", "estado", "versión del cuestionario", "fecha en que se completó", "respuestas"]
        )
        self.assertEqual([i["estado"] for i in data["entrevistas"]], ["Completada", "En curso"])

    def test_the_list_says_how_many_questionnaires_a_partly_done_campero_answered(self):
        """Defect: a campero who finished one of two questionnaires listed as "Sin entrevista" in the CSV."""
        self.second()
        Interview.objects.create(event=self.event, participant=self.p1, version=self.version, status=Interview.Status.COMPLETED)
        response = self.login(self.admin, Client()).get(self.url("participant_export"))
        rows = {row[1]: row for row in csv.reader(io.StringIO(response.content.decode("utf-8-sig")))}
        self.assertEqual(rows[self.p1.full_name][7], "1 de 2 cuestionarios")
        self.assertEqual(rows[self.p2.full_name][7], "Sin entrevista")


class ParticipantsListTests(AdminHelpers, CampFixture):
    PILL = '<span class="status status--pending"><span class="status__mark" aria-hidden="true"></span>{}</span>'

    def test_the_admin_list_says_how_many_questionnaires_a_partly_done_campero_answered(self):
        """Defect: a campero who finished one of two questionnaires shown as "Sin entrevista" on the admin Camperos list."""
        self.second()
        Interview.objects.create(event=self.event, participant=self.p1, version=self.version, status=Interview.Status.COMPLETED)
        html = self.page("participants")
        self.assertEqual(html.count(self.PILL.format("1 de 2 cuestionarios")), 2)  # the phone row and the table cell
        self.assertEqual(html.count("de 2 cuestionarios"), 2)  # only that campero
        self.assertIn(self.PILL.format("Sin entrevista"), html)  # the others, nothing started


class EventCreateTests(AdminHelpers, CampFixture):
    def test_a_new_event_starts_with_one_published_questionnaire(self):
        """Defect: "Nuevo evento" writing the removed column instead of a link."""
        self.login(self.admin)
        data = {"name": "Retiro de Otoño", "kind": "campamento", "slug": "", "status": "activo", "location": "", "starts_on": "", "ends_on": ""}
        self.client.post(reverse("events:create"), data)
        event = Event.objects.get(name="Retiro de Otoño")
        self.assertEqual([(link.version, link.order) for link in links_for(event)], [(self.version, 1)])
        self.assertTrue(event.has_questionnaire)


# ---- 11. Who reads the confidential answers ---------------------------------------------------------------------


class ConfidentialLevelTests(AdminHelpers, CampFixture):
    PHRASES = {
        "cuidado": "solo el equipo de cuidado autorizado",
        "coordinacion": "quien te entrevistó, tu coordinador y el equipo de cuidado",
        "todo_el_equipo": "tu coordinador y el equipo del campamento",
    }

    def set_level(self, level):
        Event.objects.filter(pk=self.event.pk).update(confidential_level=level)

    def readers(self, interview, code="apoyo"):
        event = Event.objects.get(pk=self.event.pk)  # what the next request reads
        interview = Interview.objects.get(pk=interview.pk)
        people = (self.coord1, self.coord2, self.tutor1, self.tutor2, self.pastor, self.admin, self.care)
        return {u.email for u in people if resolve_access(u, event).can_view_answer(interview, self.question(code))}

    def interview(self, level):
        return Interview.objects.create(
            event=self.event, participant=self.p1, version=self.version, status=Interview.Status.COMPLETED, confidential_level=level
        )

    def test_each_level_opens_the_confidential_answers_to_its_readers(self):
        """Defect: a level that reads one way on screen and opens the answers to someone else."""
        expected = {
            "cuidado": {self.care.email},
            "coordinacion": {self.coord1.email, self.care.email},  # the other coordinator, the tutor and the pastor do not
            "todo_el_equipo": {self.coord1.email, self.tutor1.email, self.pastor.email, self.admin.email, self.care.email},
        }
        for level, people in expected.items():
            with self.subTest(level=level):
                Interview.objects.filter(participant=self.p1).delete()
                self.set_level(level)
                self.assertEqual(self.readers(self.interview(level)), people)
        self.assertEqual(self.readers(Interview.objects.get(participant=self.p1), code="salvo"), {self.coord1.email, self.care.email})  # "sensible" unchanged

    def test_opening_the_level_does_not_reach_answers_given_before(self):
        """Defect (privacy): widening the level opening answers a campero gave under the narrower promise."""
        before = self.interview("cuidado")
        self.set_level("todo_el_equipo")
        self.assertEqual(self.readers(before), {self.care.email})

    def test_closing_the_level_applies_at_once(self):
        """Defect (privacy): narrowing the level leaving earlier answers open."""
        before = self.interview("todo_el_equipo")
        self.set_level("cuidado")
        self.assertEqual(self.readers(before), {self.care.email})
        self.set_level("coordinacion")
        self.assertEqual(self.readers(before), {self.coord1.email, self.care.email})

    def test_an_interview_takes_the_events_level_when_it_starts(self):
        """Defect: an interview started under an open level read as "cuidado" (or the other way around)."""
        self.set_level("coordinacion")
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        self.assertEqual(Interview.objects.get(participant=self.p2).confidential_level, "coordinacion")

    def test_the_page_is_for_administracion_and_pastoral_only(self):
        """Defect: a tutor or a coordinator opening the answers of their own camperos."""
        for user in (self.tutor1, self.coord1):
            client = self.login(user, Client())
            self.assertEqual(client.get(self.url("confidential_level")).status_code, 403, user)
            self.assertEqual(client.post(self.url("confidential_level"), {"confidential_level": "todo_el_equipo"}).status_code, 403, user)
        self.assertEqual(Event.objects.get(pk=self.event.pk).confidential_level, "cuidado")
        admin_page = self.page("confidential_level")
        self.assert_spanish_and_labelled(admin_page, "confidential_level")
        self.assertIn(f'<a class="back-link" href="{self.url("settings")}">Configuración</a>', admin_page)
        self.assertIn("Si abres el acceso, aplica a las entrevistas que empiecen después. Si lo restringes, aplica de inmediato.", admin_page)
        self.assertIn('name="confidential_level" value="cuidado"', admin_page)
        pastor_page = self.page("confidential_level", user=self.pastor)
        self.assert_spanish_and_labelled(pastor_page, "confidential_level (pastor)")
        self.assertIn(f'<a class="back-link" href="{self.url("pastor_home")}">Resumen general</a>', pastor_page)
        self.assertIn(f'<a href="{self.url("pastor_home")}" aria-current="page">Resumen general</a>', pastor_page)
        home = self.page("pastor_home", user=self.pastor)
        self.assertIn(f'<p class="more">Quién lee las respuestas confidenciales: <a href="{self.url("confidential_level")}">', home)

    def test_saving_a_level_is_recorded_and_asks_to_check_the_notice(self):
        """Defect: a change of who reads the answers with no trace, or without a reminder about the privacy notice."""
        client = self.login(self.pastor, Client())
        response = client.post(self.url("confidential_level"), {"confidential_level": "todo_el_equipo"})
        self.assertRedirects(response, self.url("confidential_level"), fetch_redirect_response=False)
        self.assertEqual(Event.objects.get(pk=self.event.pk).confidential_level, "todo_el_equipo")
        audit = self.last_audit()
        self.assertEqual(
            (audit.action, audit.summary, audit.actor),
            ("acceso_confidencial", "Las respuestas confidenciales las lee: Todo el equipo que ve al campero", self.pastor),
        )
        self.assertContains(client.get(response["Location"]), "Guardado. Revisa que el aviso de privacidad del evento diga lo mismo.")
        response = client.post(self.url("confidential_level"), {"confidential_level": "cuidado"})
        page = client.get(response["Location"])
        self.assertContains(page, "Guardado.")
        self.assertNotContains(page, "Revisa que el aviso")
        self.assertEqual(self.last_audit().summary, "Las respuestas confidenciales las lee: Solo quien tiene acceso confidencial (y su coordinador mientras la anota)")
        self.assertEqual(client.post(self.url("confidential_level"), {"confidential_level": "nadie"}).status_code, 200)
        self.assertEqual(Event.objects.get(pk=self.event.pk).confidential_level, "cuidado")

    def kiosk(self, level_at_start, level_now=None):
        self.set_level(level_at_start)
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", interview.public_id))
        if level_now:
            self.set_level(level_now)
        return self.client.get(reverse("kiosk:home"))

    def test_the_kiosk_sentence_follows_the_level(self):
        """Defect: the campero told "solo el equipo de cuidado" while the whole team reads their answer."""
        response = self.kiosk("todo_el_equipo")
        self.assertContains(response, "Las marcadas “Confidencial”, tu coordinador y el equipo del campamento.", count=1)
        self.assertNotContains(response, "solo el equipo de cuidado autorizado")
        step = self.client.get(reverse("kiosk:step", args=[self.step("apoyo").number]))
        self.assertContains(step, "Esta respuesta la leen tu coordinador y el equipo del campamento.")
        self.assertNotContains(step, "Solo el equipo de cuidado autorizado podrá leer esta respuesta.")

    # templates/events/kiosk/_who_reads.html before the levels (b14e9ca), byte for byte.
    WHO_READS_BEFORE = (
        "<p class=\"{{ css|default:'who-reads' }}\">Tus respuestas las lee tu coordinador y, las generales, también el equipo "
        "del campamento. Las de temas personales, solo quien te entrevistó, tu coordinador y el equipo de cuidado. Las "
        "marcadas “Confidencial”, solo el equipo de cuidado autorizado.</p>\n"
    )

    def test_who_reads_renders_the_bytes_it_always_did_at_cuidado(self):
        """Defect (cosmetic): the partial's new comment line adding a newline before the paragraph at every level."""
        interview = self.interview("cuidado")
        for context in ({}, {"css": "aside-note"}, {"interview": interview}, {"interview": interview, "css": "aside-note"}):
            with self.subTest(context=sorted(context)):
                self.assertEqual(
                    render_to_string("events/kiosk/_who_reads.html", context), Template(self.WHO_READS_BEFORE).render(Context(context))
                )
        Interview.objects.filter(pk=interview.pk).update(confidential_level="todo_el_equipo")
        self.set_level("todo_el_equipo")
        wider = render_to_string("events/kiosk/_who_reads.html", {"interview": Interview.objects.get(pk=interview.pk)})
        self.assertIn("Las marcadas “Confidencial”, tu coordinador y el equipo del campamento.", wider)
        self.assertTrue(wider.startswith('<p class="who-reads">'), wider[:40])

    def test_the_kiosk_keeps_the_promise_the_interview_started_with(self):
        """Defect: an opening after the interview started showing the campero a wider promise than applies (or the
        reverse: a closing that the campero is not told about)."""
        response = self.kiosk("cuidado", level_now="todo_el_equipo")
        self.assertContains(response, "Las marcadas “Confidencial”, solo el equipo de cuidado autorizado.", count=1)
        step = self.client.get(reverse("kiosk:step", args=[self.step("apoyo").number]))
        self.assertContains(step, "Solo el equipo de cuidado autorizado podrá leer esta respuesta.")
        Interview.objects.filter(participant=self.p1).update(confidential_level="todo_el_equipo")
        self.set_level("coordinacion")
        self.assertContains(self.client.get(reverse("kiosk:home")), f"Las marcadas “Confidencial”, {self.PHRASES['coordinacion']}.")

    def test_the_interview_page_says_who_reads_the_hidden_answers(self):
        """Defect: the interview page telling the team "solo el equipo de cuidado" while the level opens the
        confidential answers to more people."""
        interview = self.interview("coordinacion")
        self.set_level("coordinacion")
        html = self.page("interview_detail", interview.public_id, user=self.tutor1)
        self.assertIn(
            '<p class="aside-note">Algunas respuestas son confidenciales y llevan su etiqueta: las leen quien entrevistó a '
            f"{self.p1.first_name}, su coordinador y el equipo de cuidado.</p>",
            html,
        )
        self.assertNotIn("solo las puede leer", html)
        self.set_level("cuidado")  # closing applies at once: today's sentence, byte for byte
        html = self.page("interview_detail", interview.public_id, user=self.tutor1)
        self.assertIn('<p class="aside-note">Algunas respuestas solo las puede leer el equipo de cuidado autorizado.</p>', html)
        self.assertNotIn("llevan su etiqueta", html)
        Interview.objects.filter(pk=interview.pk).update(confidential_level="todo_el_equipo")
        self.set_level("todo_el_equipo")
        html = self.page("interview_detail", interview.public_id, user=self.tutor1)  # the "sensible" answers stay hidden
        self.assertIn(
            '<p class="aside-note">Algunas respuestas son confidenciales y llevan su etiqueta: las leen su coordinador y el '
            "equipo del campamento.</p>",
            html,
        )

    def test_the_review_tags_a_visible_confidential_answer(self):
        """Defect (owner's ask): a confidential answer the interviewer may read shown without its tag."""
        self.set_level("coordinacion")
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        option = self.question("apoyo").options.exclude(is_decline=True).first()
        self.answer(interview, "apoyo", option.code)
        html = self.client.get(self.url("interview_review", interview.public_id)).content.decode()
        row = re.search(rf'<dt>{re.escape(self.question("apoyo").text)}</dt>\s*<dd>(.*?)</dd>', html, re.S).group(1)
        self.assertTrue(row.startswith('<span class="privacy__tag">Confidencial</span> '), row)
        self.assertIn(option.label, row)

    HANDOFF_CUIDADO = (
        "<li>Dile que la pantalla de bienvenida le explica quién lee sus respuestas, y que las marcadas “Confidencial” "
        "solo las lee el equipo de cuidado autorizado.</li>"
    )

    def handoff(self, level):
        Interview.objects.filter(participant=self.p1).delete()
        self.set_level(level)
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        response = self.client.get(self.url("interview_handoff", interview.public_id))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_handoff_tells_the_campero_who_reads_the_confidential_answers(self):
        """Defect (privacy promise): the coordinator told to say "solo las lee el equipo de cuidado" while the level
        opens the campero's confidential answers to the whole team."""
        html = self.handoff("todo_el_equipo")
        self.assertIn(f"«Las marcadas “Confidencial”, {self.PHRASES['todo_el_equipo']}.»", html)
        self.assertNotIn("solo las lee el equipo de cuidado autorizado", html)
        html = self.handoff("coordinacion")
        self.assertIn(f"«Las marcadas “Confidencial”, {self.PHRASES['coordinacion']}.»", html)
        self.assertIn(self.HANDOFF_CUIDADO, self.handoff("cuidado"))  # today's sentence, byte for byte

    PAPER_CUIDADO = (
        '<p class="privacy"><span class="privacy__tag">Confidencial</span> Solo el equipo de cuidado autorizado podrá '
        "leer esta respuesta.</p>"
    )

    def test_the_paper_sheet_says_who_reads_the_confidential_answers(self):
        """Defect (privacy promise): the paper questionnaire promising "solo el equipo de cuidado" while the answers
        typed in from it are read by the whole team (they are created at the event's level)."""
        self.set_level("todo_el_equipo")
        html = self.page("backup_list", user=self.coord1)
        self.assertIn(
            '<p class="privacy"><span class="privacy__tag">Confidencial</span> Esta respuesta la leen '
            f"{self.PHRASES['todo_el_equipo']}.</p>",
            html,
        )
        self.assertNotIn("Solo el equipo de cuidado autorizado podrá leer esta respuesta.", html)
        self.set_level("cuidado")
        html = self.page("backup_list", user=self.coord1)
        self.assertIn(self.PAPER_CUIDADO, html)  # today's sentence, byte for byte
        self.assertNotIn("Esta respuesta la leen", html)

    def test_the_question_editor_does_not_promise_one_level(self):
        """Defect (privacy promise): the editor of a questionnaire (a church's, used by several events, each with its own
        level) saying a confidential answer is read only by the care team, true for some events and false for others."""
        draft = duplicate_version(self.version, self.admin)
        section = draft.sections.order_by("order").first()
        expected = (
            "<dt>Confidencial</dt><dd>La leen las personas que permita cada evento que use este cuestionario (en "
            f'Configuración, <a href="{self.url("confidential_level")}">Respuestas confidenciales</a>). Úsala para temas '
            "íntimos y textos libres.</dd>"
        )
        for level in ("cuidado", "todo_el_equipo"):
            with self.subTest(level=level):
                self.set_level(level)
                html = self.page("question_add", draft.id, section.id)
                self.assertIn(expected, html)
                self.assertNotIn("Solo la lee el equipo de cuidado autorizado", html)
                self.assert_spanish_and_labelled(html, f"question_add ({level})")

    def completed_by_coord1(self, level):
        """p2 interviewed by coord1 under ``level``: "dudas" with its confidential follow-up, and "apoyo" (confidential)."""
        self.set_level(level)
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        self.answer(interview, "dudas", "si", extra={f"q_{self.question('dudas_detalle').id}": "Sobre la oración"})
        self.answer(interview, "apoyo", "familia")
        interview.refresh_from_db()
        self.client.post(self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "no_requerido"})
        interview.refresh_from_db()
        self.assertEqual(interview.status, Interview.Status.COMPLETED)
        return interview

    def confidential_reads(self):
        return list(AuditEvent.objects.filter(event=self.event, action="consulta_confidencial").values_list("summary", flat=True))

    def test_the_correction_screen_records_reading_a_confidential_answer(self):
        """Defect (audit): with the level open, a coordinator reading a confidential answer on the correction screen
        with no "consulta_confidencial" row, while the interview page records one for the same answer."""
        interview = self.completed_by_coord1("todo_el_equipo")
        summary = f"Consultó respuestas confidenciales del campero #{self.p2.code}"
        before = self.confidential_reads()
        for code in ("apoyo", "dudas"):  # its own screen; a screen whose confidential follow-up is answered
            with self.subTest(code=code):
                response = self.client.get(self.url("interview_correct", interview.public_id, self.step(code).number))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.confidential_reads()[len(before):], [summary])
                before = self.confidential_reads()
        self.client.get(self.url("interview_correct", interview.public_id, self.step("salvo").number))  # not confidential
        self.assertEqual(self.confidential_reads(), before)

    def test_the_correction_screen_of_a_confidential_answer_stays_closed_at_cuidado(self):
        """Defect (privacy): the audit change opening the confidential correction screen to the coordinator."""
        interview = self.completed_by_coord1("cuidado")
        before = self.confidential_reads()
        response = self.client.get(self.url("interview_correct", interview.public_id, self.step("apoyo").number))
        self.assertEqual(response.status_code, 403)
        response = self.client.get(self.url("interview_correct", interview.public_id, self.step("dudas").number))
        self.assertEqual(response.status_code, 200)  # the follow-up is left off the screen: nothing confidential read
        self.assertNotContains(response, "Sobre la oración")
        self.assertEqual(self.confidential_reads(), before)

    def hidden_tags(self, html):
        """{question text: the tag of its hidden answer} on an answers list."""
        return dict(re.findall(r'<dt>(.*?)</dt>\s*<dd><span class="privacy__tag">([^<]*)</span></dd>', html))

    def own_levels(self, tags):
        """The tag each of those questions should carry: its own privacy level."""
        labels = {step_question.text: step_question.get_sensitivity_display() for step in self.steps for step_question in step.questions}
        return {text: labels[text] for text in tags}

    def test_a_hidden_answer_is_tagged_with_its_own_privacy_level(self):
        """Defect (contradictory copy): every hidden answer tagged "Confidencial", so with the level open a tutor reads
        hidden "Sensible" answers tagged "Confidencial" under a note saying the team reads the confidential ones."""
        salvo, apoyo = self.question("salvo"), self.question("apoyo")
        interview = self.completed_by_coord1("todo_el_equipo")
        html = self.page("interview_detail", interview.public_id, user=self.tutor1)
        tags = self.hidden_tags(html)
        self.assertEqual(tags[salvo.text], "Sensible")
        self.assertEqual(set(tags.values()), {"Sensible"})  # the tutor reads every confidential answer at this level
        self.assertIn(f'<dt>{apoyo.text}</dt>\n          <dd><span class="privacy__tag">Confidencial</span> ', html)  # read, tagged
        Interview.objects.filter(pk=interview.pk).update(confidential_level="cuidado")
        tags = self.hidden_tags(self.page("interview_detail", interview.public_id, user=self.tutor1))
        self.assertEqual((tags[salvo.text], tags[apoyo.text]), ("Sensible", "Confidencial"))
        self.assertEqual(tags, self.own_levels(tags))

    def test_the_review_tags_a_hidden_answer_with_its_own_privacy_level(self):
        """Defect: the review of an administrador who picks up a coordinator's interview tagging the hidden "Sensible"
        answers "Confidencial"."""
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        self.answer(interview, "salvo", "si")
        self.answer(interview, "apoyo", "familia")
        Interview.objects.filter(pk=interview.pk).update(lock_token_hash="", lock_holder=None, lock_expires_at=None, station=None)
        client = self.login(self.admin, Client())
        response = client.get(self.url("interview_review", interview.public_id))
        self.assertEqual(response.status_code, 200)
        tags = self.hidden_tags(response.content.decode())
        self.assertEqual((tags[self.question("salvo").text], tags[self.question("apoyo").text]), ("Sensible", "Confidencial"))
        self.assertEqual(tags, self.own_levels(tags))


# ---- 12. The presentation and the report of a team ---------------------------------------------------------------


class TeamReportTests(AdminHelpers, CampFixture):
    def team_key(self):
        return f"equipo:{self.m_tutor1.id}"

    def test_the_presentation_opened_from_a_team_is_that_teams(self):
        """Defect (owner): looking at a team, "Mostrar presentación" showed the whole event."""
        client = self.login(self.pastor, Client())
        reports = client.get(self.url("reports") + f"?alcance={self.team_key()}").content.decode()
        link = re.search(r'<a class="btn btn--secondary" href="([^"]+)">Mostrar presentación</a>', reports).group(1).replace("&amp;", "&")
        self.assertIn(f"alcance={self.team_key().replace(':', '%3A')}", link)
        html = client.get(link).content.decode()
        self.assertIn(f'data-live-url="{self.url("presentation_live")}?alcance=equipo%3A{self.m_tutor1.id}"', html)
        # The team's deck: its own four camperos (coord1's three and coord2's one), not the event's five.
        self.assertIn("Campamento A, un equipo", html)
        self.assertIn("Llevamos 0 de 4 entrevistas.", html)
        whole_deck = client.get(self.url("presentation_live")).content.decode()
        self.assertIn("Llevamos 0 de 5 entrevistas.", whole_deck)
        whole = client.get(self.url("reports")).content.decode()
        link = re.search(r'<a class="btn btn--secondary" href="([^"]+)">Mostrar presentación</a>', whole).group(1)
        self.assertNotIn("alcance=", link)  # the default scope (the whole event) needs no parameter

    def test_a_scope_outside_the_persons_own_is_ignored(self):
        """Defect (access): a tutor reading another team's presentation by editing the address."""
        other = f"equipo:{self.m_tutor2.id}"
        client = self.login(self.tutor1, Client())
        response = client.get(self.url("presentation_live") + f"?alcance={other}")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn("alcance=", re.search(r'<div class="deck__frame"[^>]*>', html).group(0))
        self.assertIn("Llevamos 0 de 4 entrevistas.", html)  # their own team (tutor2's has one campero)

    def cutoff(self, client, scope_key):
        """(snapshot, detail page HTML, every text handed to ReportLab for its PDF) of a manual cutoff of ``scope_key``."""
        response = client.post(self.url("report_generate"), {"scope": scope_key})
        self.assertEqual(response.status_code, 302)
        detail = client.get(response["Location"])
        self.assertEqual(detail.status_code, 200)
        snapshot = detail.context["snapshot"]
        with mock.patch.object(pdf_module, "Paragraph", wraps=pdf_module.Paragraph) as paragraph, \
                mock.patch.object(pdf_module, "Table", wraps=pdf_module.Table) as table:
            pdf = client.get(self.url("report_pdf", snapshot.public_id))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        texts = [str(call.args[0]) for call in paragraph.call_args_list]
        for call in table.call_args_list:
            texts += [str(cell) for row in call.args[0] for cell in row]
        return snapshot, detail.content.decode(), "\n".join(texts)

    def test_a_team_cutoff_is_a_team_report_on_screen_and_in_the_pdf(self):
        """Verification (owner): a cutoff generated for a team names the team and lists only its coordinators."""
        client = self.login(self.pastor, Client())
        # Control: the event's cutoff names the other team (its PDF breaks down by team, its page also by coordinator),
        # so the absences below mean something.
        _, event_html, event_text = self.cutoff(client, "evento")
        self.assertIn(self.m_coord3.display_name, event_html)
        self.assertIn(self.m_tutor2.team_label, event_text)

        snapshot, html, text = self.cutoff(client, self.team_key())
        self.assertEqual((snapshot.scope_key, snapshot.scope_label), (self.team_key(), self.m_tutor1.team_label))
        self.assertIn(self.m_tutor1.team_label, html)
        for name in (self.m_coord1.display_name, self.m_coord2.display_name):
            self.assertIn(name, html)
        self.assertNotIn(self.m_coord3.display_name, html)
        self.assertNotIn(self.m_tutor2.team_label, html)
        self.assertIn(self.m_tutor1.team_label, text)
        for name in (self.m_coord1.display_name, self.m_coord2.display_name):
            self.assertIn(name, text)
        self.assertNotIn(self.m_coord3.display_name, text)
        self.assertNotIn(self.m_tutor2.team_label, text)
