"""Administration on the Rótulo design system: one sign only when something blocks, marked rows, danger buttons."""

import io
import re

from django.test import Client
from django.utils import timezone

from events.models import EventParticipant, ReportSnapshot

from .base import CampFixture

SIGN = re.compile(r'class="sign[ "]')  # the sign itself, not its parts (sign__title, sign__text...)

IMPORT_CSV = (
    "Nombre,Edad,Cuarto,Iglesia o grupo,Coordinador,Autorización\n"
    "Valeria Ruiz Ortega,15,4,Iglesia Centro,,sí\n"
    "Mariana López Ruiz,15,1,Iglesia Centro,,sí\n"  # already registered (self.p1)
    "Valeria Ruiz Ortega,15,4,Iglesia Centro,,sí\n"  # repeated in the file
    "Marco Díaz Luna,abc,7,Iglesia Centro,,no\n"  # error: age
)


class AdminScreensTests(CampFixture):
    def _unassigned_camper(self):
        number = EventParticipant.objects.filter(event=self.event).count() + 1
        return EventParticipant.objects.create(event=self.event, number=number, full_name="Rocío Sin Coordinador", age=17, room="9", church_group="Iglesia Centro")

    def test_admin_screens_use_rotulo(self):
        self.login(self.admin)
        urls = [
            self.url("admin_home"), self.url("participants"), self.url("participant_import"), self.url("member_add"),
            self.url("member_edit", self.m_coord1.id), self.url("assignments"), self.url("questionnaires"),
            self.url("version_detail", self.version.id), self.url("settings"), self.url("sessions"), self.url("audit"),
            self.url("participant_delete", self.p1.public_id), self.url("join_code_sign"), self.url("sedes"), self.url("sede_add"),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertContains(response, "css/rotulo.css", msg_prefix=url)
            self.assertNotContains(response, "css/emmaus.css", msg_prefix=url)
            self.assertEqual(response.content.decode().count("<h1"), 1, url)

    def test_home_has_no_sign_when_nothing_blocks(self):
        self.login(self.admin)
        response = self.client.get(self.url("admin_home"))
        self.assertEqual(len(SIGN.findall(response.content.decode())), 0)
        self.assertContains(response, "Llevamos 0 de 5 entrevistas.")

    def test_home_sign_sends_to_campers_without_a_coordinator(self):
        self._unassigned_camper()
        self.login(self.admin)
        response = self.client.get(self.url("admin_home"))
        html = response.content.decode()
        self.assertEqual(len(SIGN.findall(html)), 1)
        self.assertContains(response, "Asigna coordinador a 1 campero")
        self.assertContains(response, self.url("participants") + "?coordinador=ninguno")

    def test_procedures_take_the_only_sign(self):
        self._unassigned_camper()
        self.event.procedures_approved_at = None
        self.event.save(update_fields=["procedures_approved_at"])
        self.login(self.admin)
        response = self.client.get(self.url("admin_home"))
        self.assertEqual(len(SIGN.findall(response.content.decode())), 1)
        self.assertContains(response, "Confirma los procedimientos de la iglesia")
        self.assertNotContains(response, "Asigna coordinador a")
        self.assertContains(response, self.url("settings") + "#procedimientos")

    def test_failed_cut_marks_the_reports_row(self):
        ReportSnapshot.objects.create(
            event=self.event, scope_key="evento", scope_label="Todo el campamento", trigger=ReportSnapshot.Trigger.AUTOMATIC,
            status=ReportSnapshot.Status.FAILED, cutoff_at=timezone.now(), interval_minutes=120, metrics_version="1",
        )
        self.login(self.admin)
        response = self.client.get(self.url("admin_home"))
        self.assertContains(response, "1 corte no se pudo generar.")
        # The live fragment carries the same row
        fragment = self.client.get(self.url("admin_home"), HTTP_X_REQUESTED_WITH="fetch")
        self.assertContains(fragment, "1 corte no se pudo generar.")

    def test_every_failed_cut_is_counted(self):
        for _ in range(5):
            ReportSnapshot.objects.create(
                event=self.event, scope_key="evento", scope_label="Todo el campamento", trigger=ReportSnapshot.Trigger.AUTOMATIC,
                status=ReportSnapshot.Status.FAILED, cutoff_at=timezone.now(), interval_minutes=120, metrics_version="1",
            )
        self.login(self.admin)
        response = self.client.get(self.url("admin_home"))
        self.assertContains(response, "5 cortes no se pudieron generar.")
        self.assertNotContains(response, "3 cortes no se pudieron generar.")

    def test_import_review_marks_duplicates_and_errors(self):
        self.login(self.admin)
        upload = io.BytesIO(IMPORT_CSV.encode("utf-8"))
        upload.name = "camperos.csv"
        self.client.post(self.url("participant_import"), {"file": upload})
        response = self.client.get(self.url("participant_import"))
        html = response.content.decode()
        self.assertEqual(html.count('class="is-attention"'), 2)  # already registered + repeated in the file
        self.assertEqual(html.count('class="is-error"'), 1)
        self.assertEqual(html.count('name="include"'), 3)  # rows with errors cannot be imported
        self.assertEqual(html.count(" checked>"), 1)  # possible duplicates start unchecked
        self.assertContains(response, 'name="action" value="confirm"')
        self.assertContains(response, 'name="action" value="cancel"')
        self.client.post(self.url("participant_import"), {"action": "cancel"})
        self.assertEqual(EventParticipant.objects.filter(full_name="Valeria Ruiz Ortega").count(), 0)

    def test_destructive_actions_use_danger_and_keep_their_confirmation(self):
        coordinator = Client()
        self.login(self.coord1, coordinator)
        self.choose_station(coordinator)
        self.start(self.p2, client=coordinator)
        self.login(self.admin)
        sessions = self.client.get(self.url("sessions"))
        self.assertContains(sessions, 'class="btn btn--danger btn--small" data-confirm="¿Liberar esta entrevista?')
        delete = self.client.get(self.url("participant_delete", self.p1.public_id))
        self.assertContains(delete, '<button class="btn btn--danger" type="submit">Eliminar definitivamente</button>', html=True)
        self.assertContains(delete, 'name="confirm_name"')
