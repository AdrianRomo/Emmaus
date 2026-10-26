"""Leader screens on "Rótulo": tutor home, coordinators, interviews, pending, team and the event's progress."""

import re
from datetime import timedelta

from django.test import Client
from django.utils import timezone

from events.models import FollowUp, Interview

from .base import CampFixture


class LeaderScreensTests(CampFixture):
    def pages(self):
        return {
            self.tutor1: ["tutor_home", "coordinators", ("coordinator_detail", self.m_coord1.id), "interviews", "pending"],
            self.pastor: ["progress", "team", "coordinators", ("coordinator_detail", self.m_coord1.id), "interviews", "pending"],
            self.admin: ["team", "progress", "coordinators", "interviews", "pending"],
        }

    def test_leader_screens_use_rotulo_with_one_heading(self):
        for user, names in self.pages().items():
            client = Client()
            self.login(user, client)
            for name in names:
                url = self.url(*name) if isinstance(name, tuple) else self.url(name)
                response = client.get(url)
                html = response.content.decode()
                self.assertContains(response, "css/rotulo.css", msg_prefix=url)
                self.assertNotContains(response, "css/emmaus.css", msg_prefix=url)
                self.assertEqual(len(re.findall(r"<h1\b", html)), 1, url)

    def stall(self, participant, station_number=1, minutes=30):
        self.login(self.coord1)
        self.choose_station(number=station_number)
        self.start(participant)
        Interview.objects.filter(participant=participant).update(last_activity_at=timezone.now() - timedelta(minutes=minutes))

    def test_tutor_sign_says_whose_interviews_stalled_and_where(self):
        self.stall(self.p1, station_number=4)
        self.login(self.tutor1)
        response = self.client.get(self.url("tutor_home"))
        html = response.content.decode()
        self.assertEqual(len(re.findall(r'class="sign[ "]', html)), 1)  # one sign, never two
        self.assertContains(response, "Carla Uno lleva 1 entrevista sin movimiento")
        self.assertContains(response, "sin actividad en Computadora 4")
        self.assertContains(response, self.url("pending") + "#interrumpidas")
        # The row for Carla says where she is and that one interview stalled, in words (not in red)
        self.assertContains(response, "En Computadora 4")
        self.assertContains(response, "1 entrevista sin actividad desde hace 20 minutos o más")
        self.assertNotContains(response, "chip--alert")

    def test_tutor_sign_names_the_computer_of_the_stalled_interview(self):
        """Not where the coordinator went next: the stalled interview was left at Computadora 8."""
        self.stall(self.p1, station_number=8)
        self.login(self.coord1)
        self.choose_station(number=3)
        self.start(self.p2)  # a newer, active interview at another computer
        self.login(self.tutor1)
        response = self.client.get(self.url("tutor_home"))
        self.assertContains(response, "sin actividad en Computadora 8")
        self.assertNotContains(response, "sin actividad en Computadora 3")

    def test_tutor_sign_counts_every_stalled_coordinator(self):
        self.stall(self.p1)
        self.login(self.coord2)
        self.choose_station(number=2)
        self.start(self.p3)
        Interview.objects.filter(participant=self.p3).update(last_activity_at=timezone.now() - timedelta(minutes=45))
        self.login(self.tutor1)
        response = self.client.get(self.url("tutor_home"))
        self.assertContains(response, "2 entrevistas sin movimiento")
        self.assertContains(response, "Carla Uno lleva 1 y César Dos lleva 1.")

    def test_tutor_home_has_no_sign_while_nothing_is_stalled(self):
        self.login(self.tutor1)
        response = self.client.get(self.url("tutor_home"))
        self.assertNotRegex(response.content.decode(), r'class="sign[ "]')
        self.assertContains(response, "Equipo Norte, con Teresa.")
        self.assertContains(response, "Tu equipo lleva 0 de 4.")
        self.assertContains(response, "Sin computadora registrada")
        # The live region keeps its hooks and the status sits outside it
        html = response.content.decode()
        self.assertIn('data-live-status="estado-actualizacion"', html)
        self.assertLess(html.index('id="estado-actualizacion"'), html.index('id="equipo-vivo"'))

    def test_tutor_home_lists_team_follow_ups_but_never_confidential_ones(self):
        FollowUp.objects.create(event=self.event, participant=self.p2, reason=FollowUp.Reason.TALK_TO_LEADER)
        FollowUp.objects.create(event=self.event, participant=self.p3, confidential=True, created_by=self.care)
        FollowUp.objects.create(event=self.event, participant=self.p_minor_no_auth, kind=FollowUp.Kind.SAFETY)
        self.login(self.tutor1)
        response = self.client.get(self.url("tutor_home"))
        self.assertContains(response, "Diego Hernández Cruz")
        self.assertContains(response, "Pidió hablar con un líder")
        self.assertNotContains(response, "Sofía García Pérez")  # confidential: care team only
        self.assertNotContains(response, "Lucía Torres Gómez")  # risk reports are not on the tutor's home

    def test_coordinators_page_draws_one_mark_per_campero(self):
        self.login(self.tutor1)
        response = self.client.get(self.url("coordinators"))
        html = response.content.decode()
        self.assertEqual(html.count('class="tally"'), 2)
        self.assertContains(response, self.url("coordinator_detail", self.m_coord1.id))
        self.assertNotContains(response, "Cora Tres")  # another tutor's coordinator

    def test_tutor_without_coordinators_sees_an_empty_line(self):
        from events.models import EventMembership, MembershipRole

        from .base import make_user

        lone = make_user("tutor3@prueba.mx", "Tania", "Tres")
        EventMembership.objects.create(event=self.event, user=lone, role=MembershipRole.TUTOR)
        self.login(lone)
        response = self.client.get(self.url("tutor_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hola, Tania.")  # no team name yet: no "Equipo de Tania, con Tania."
        self.assertContains(response, "Tu equipo todavía no tiene camperos.")
        self.assertContains(response, "Aún no tienes coordinadores asignados")

    def test_coordinator_without_tutor_and_team_page_permissions(self):
        from events.models import TutorAssignment

        TutorAssignment.objects.filter(coordinator=self.m_coord3).delete()
        self.login(self.pastor)
        self.assertContains(self.client.get(self.url("coordinator_detail", self.m_coord3.id)), "Sin tutor")
        team = self.client.get(self.url("team"))
        self.assertContains(team, "Coordinadores sin tutor")
        # Only the administration manages the team
        self.assertNotContains(team, "Agregar persona")
        self.assertNotContains(team, "Asignar tutor")
        self.login(self.admin)
        team = self.client.get(self.url("team"))
        self.assertContains(team, "Agregar persona")
        self.assertContains(team, self.url("member_edit", self.m_coord3.id))


class CutoffPdfTests(CampFixture):
    """The PDF reads like the cutoff page: Figtree ExtraBold for the titles and questions, Regular and Bold for the rest."""

    def test_pdf_uses_the_atrio_fonts(self):
        from events.services.metrics import event_scope
        from events.services.reports import generate_snapshot

        snap = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=timezone.now())
        self.login(self.pastor)
        pdf = self.client.get(self.url("report_pdf", snap.public_id)).content
        self.assertTrue(pdf.startswith(b"%PDF"))
        for face in (b"Figtree-Regular", b"Figtree-Bold", b"Figtree-ExtraBold"):
            self.assertIn(face, pdf)
        for retired in (b"Recursive", b"Atkinson", b"Literata"):
            self.assertNotIn(retired, pdf)

    def pdf_texts(self, payload_changes):
        """Build the PDF from a snapshot whose payload is changed as given; return every text handed to ReportLab."""
        from unittest import mock

        from events.services import pdf as pdf_module
        from events.services.metrics import event_scope
        from events.services.reports import generate_snapshot

        snap = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=timezone.now())
        payload = {**snap.data, **payload_changes}
        with mock.patch.object(pdf_module, "Paragraph", wraps=pdf_module.Paragraph) as paragraph, \
                mock.patch.object(pdf_module, "Table", wraps=pdf_module.Table) as table:
            data = pdf_module.build_snapshot_pdf(snap, payload)
        self.assertTrue(data.startswith(b"%PDF"))
        texts = [str(call.args[0]) for call in paragraph.call_args_list]
        for call in table.call_args_list:
            texts += [str(cell) for row in call.args[0] for cell in row]
        return texts

    def test_rounded_counts_are_words_in_the_pdf(self):
        """Figtree has no "≈" (ReportLab would print a box): the PDF writes "unas 85", read from the value, so a
        cutoff saved while the display was still "≈ 85" prints words too."""
        old_style = {"value": 85, "display": "\u2248 85", "suppressed": False}
        outcomes = [{
            "code": "salvo", "title": "¿Eres salvo?", "kind": "single", "sensitivity": "spiritual",
            "respondents": old_style, "withheld": False,
            "rows": [
                {"label": "Sí", "code": "si", "decline": False, "value": 40, "display": "\u2248 40", "suppressed": False},
                {"label": "No", "code": "no", "decline": False, "value": None, "display": "Menos de 5", "suppressed": True},
            ],
        }]
        texts = self.pdf_texts({"outcomes": outcomes, "min_group_size": 5})
        joined = "\n".join(texts)
        self.assertNotIn("\u2248", joined)
        self.assertIn("Respondieron unas 85 personas.", joined)
        self.assertIn("unas 40", texts)
        self.assertIn("menos de 5", texts)
        self.assertIn("Los totales se redondean de 5 en 5 y las cantidades menores a 5 no se muestran, para que nadie pueda ser identificado.", joined)
        from events.services.pdf import _about

        self.assertEqual(_about({"value": 1, "display": "unas 1", "suppressed": False}, "personas"), "1 persona")  # minimum group of 1

    def test_the_pdf_speaks_of_camperos_and_hides_the_metrics_version(self):
        """Substrings of the whole text, not list membership. The glossary ("Cómo se calcula") prints
        metrics.DEFINITIONS, which another branch renames: its lines are left out while they still say
        "Participantes", and checked with everything else once they do not."""
        from xml.sax.saxutils import escape

        from events.services.metrics import DEFINITIONS

        texts = self.pdf_texts({})
        joined = "\n".join(texts)
        self.assertNotIn("métricas versión", joined)
        self.assertIn("Camperos", texts)  # the team table's column
        glossary = {f"<b>{escape(name)}:</b> {escape(text)}" for name, text in DEFINITIONS}
        self.assertTrue(glossary <= set(texts))  # the exclusion below really matches the glossary lines
        if any("articipantes" in name + text for name, text in DEFINITIONS):
            joined = "\n".join(text for text in texts if text not in glossary)
        self.assertNotIn("Participantes", joined)
        self.assertNotIn("participantes", joined)

    def test_rounded_follow_ups_on_the_deck_are_words_by_gender(self):
        from events.services.metrics import build_payload, event_scope
        from events.services.presentation import build_deck

        deck = build_deck(
            build_payload(event_scope(self.event)), event_name="X", kind_label="Campamento", k=5,
            released_follow_ups={"requested_total": 12, "open_total": 7, "done": 0, "to_evaluate": 11},
        )
        words = {key: deck["follow_ups"][key]["words"] for key in ("requested", "open", "done", "to_evaluate")}
        self.assertEqual(words, {"requested": "Unos 10", "open": "Unos 5", "done": "0", "to_evaluate": "Unas 10"})
        self.assertIn("Decidir el seguimiento de unas 10 entrevistas.", deck["next_steps"])  # never "... por decidir" twice
        self.assertIn("Asignar líderes a unos 5 seguimientos abiertos.", deck["next_steps"])
        small = build_deck(build_payload(event_scope(self.event)), event_name="X", kind_label="Campamento", k=5,
                           released_follow_ups={"requested_total": 1, "open_total": 2, "done": 0, "to_evaluate": 3})
        self.assertEqual(small["follow_ups"]["open"]["words"], "Menos de 5")
        self.assertIn("Asignar líderes a menos de 5 seguimientos abiertos.", small["next_steps"])
        self.assertNotIn("\u2248", str(deck) + str(small))
        one = build_deck(build_payload(event_scope(self.event)), event_name="X", kind_label="Campamento", k=1,
                         released_follow_ups={"requested_total": 1, "open_total": 1, "done": 0, "to_evaluate": 1})
        self.assertIn("Decidir el seguimiento de 1 entrevista.", one["next_steps"])
        self.assertIn("Asignar líderes a 1 seguimiento abierto.", one["next_steps"])
        self.assertEqual(one["follow_ups"]["requested"]["words"], "1")

    def test_the_deck_says_camperos_and_how_totals_are_rounded(self):
        self.login(self.pastor)
        html = self.client.get(self.url("presentation_live")).content.decode()
        self.assertNotIn("\u2248", html)
        from django.conf import settings

        slides = (settings.BASE_DIR / "templates" / "events" / "presentation" / "_slides.html").read_text()
        self.assertIn("La línea horizontal es el total de camperos", slides)  # slide 3 needs completed interviews
        self.assertNotIn("participantes", slides)
        self.assertIn("Los totales se redondean de 5 en 5 y las cantidades menores a 5 no se muestran, para que nadie pueda ser identificado.", html)
        self.assertNotIn("redondeados a múltiplos", html)

    def test_the_pdf_timeline_labels_never_overlap(self):
        """C9. At 22 hourly points the last two labels were 20.0 pt apart and 20.5 pt wide: drawn on top of each other.
        Measured with Figtree's own widths; the first and the last label always stay."""
        from unittest import mock

        from reportlab.pdfbase import pdfmetrics

        from events.services import pdf as pdf_module

        pdf_module._register_fonts()
        for n in (2, 5, 8, 13, 22, 30, 48):
            points = [{"label": f"{h % 24:02d}:00", "cumulative": 4 * (h + 1)} for h in range(n)]
            timeline = pdf_module.Timeline(points, 4 * n + 10, 498)
            timeline.canv = mock.MagicMock()
            timeline.draw()
            drawn = [call.args for call in timeline.canv.drawCentredString.call_args_list]
            self.assertEqual(drawn[0][2], points[0]["label"], n)
            self.assertEqual(drawn[-1][2], points[-1]["label"], n)
            boxes = []
            for x, _, text in drawn:
                width = pdfmetrics.stringWidth(text, "Figtree", 7.5)
                boxes.append((x - width / 2, x + width / 2))
            for (_, a1), (b0, _) in zip(boxes, boxes[1:]):
                self.assertLessEqual(a1 + 4, b0, (n, drawn))

    def test_the_change_since_the_last_cutoff_keeps_the_percentage(self):
        from events.services.pdf import delta_sentence

        self.assertEqual(delta_sentence({"completed": 11, "percent": 3}), "+11 entrevistas y +3\u00a0% de avance desde el corte anterior.")
        self.assertEqual(delta_sentence({"completed": 4, "percent": 0}), "+4 entrevistas desde el corte anterior.")
        # New campers registered and nobody finished: the percentage still moved
        self.assertEqual(
            delta_sentence({"completed": 0, "percent": -5}),
            "Ninguna entrevista nueva, pero el avance cambió \u22125\u00a0% desde el corte anterior.",
        )
        self.assertEqual(delta_sentence({"completed": 0, "percent": None}), "Sin cambios desde el corte anterior.")
