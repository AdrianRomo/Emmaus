"""Camp flow fixes: the right baseline for the headline, one sentence for the change since a cutoff,
an empty event that says so, and counts that agree with their nouns."""

import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.test import SimpleTestCase
from django.utils import timezone

from events.models import (
    AuditEvent,
    Event,
    EventMembership,
    EventParticipant,
    InterviewStatus,
    MembershipRole,
    TutorAssignment,
)
from events.services.metrics import (
    build_payload,
    delta_sentence,
    event_scope,
    recommendations,
    team_scope,
)
from events.services.presentation import build_deck
from events.services.reports import generate_snapshot

from .base import CampFixture

EMPTY = "Aún no hay camperos registrados."


def hhmm(moment):
    return f"{timezone.localtime(moment):%H:%M}"


class DeltaSentenceTests(SimpleTestCase):
    """The one sentence the screens and the PDF use for the change since the previous cutoff."""

    def setUp(self):
        self.since = timezone.now().replace(microsecond=0)
        self.at = hhmm(self.since)

    def test_interviews_and_percentage(self):
        self.assertEqual(
            delta_sentence({"completed": 11, "percent": 3, "percent_before": 40, "percent_now": 43}, self.since),
            f"11 entrevistas más que en el corte de las {self.at}. El avance pasó de 40\u00a0% a 43\u00a0%.",
        )

    def test_interviews_only(self):
        self.assertEqual(delta_sentence({"completed": 11, "percent": 0}, self.since), f"11 entrevistas más que en el corte de las {self.at}.")
        self.assertEqual(delta_sentence({"completed": 1, "percent": None}, self.since), f"1 entrevista más que en el corte de las {self.at}.")

    def test_percentage_only_when_new_camperos_registered(self):
        self.assertEqual(
            delta_sentence({"completed": 0, "percent": -5, "percent_before": 25, "percent_now": 20}, self.since),
            f"Ninguna entrevista nueva desde el corte de las {self.at}; el avance pasó de 25\u00a0% a 20\u00a0%.",
        )

    def test_nothing_changed(self):
        self.assertEqual(delta_sentence({"completed": 0, "percent": 0}, self.since), f"Sin cambios desde el corte de las {self.at}.")
        self.assertEqual(delta_sentence({"completed": 0, "percent": None}, self.since), f"Sin cambios desde el corte de las {self.at}.")

    def test_camperos_registered_or_removed_while_nobody_finished(self):
        """compare() keeps the change in participants under "eligible"; with no interview finished it is the news."""
        at = self.at
        self.assertEqual(
            delta_sentence({"completed": 0, "percent": None, "eligible": 5}, self.since),
            f"Ninguna entrevista nueva desde el corte de las {at}, pero se registraron 5 camperos más.",
        )
        self.assertEqual(
            delta_sentence({"completed": 0, "percent": 0, "eligible": 1}, self.since),
            f"Ninguna entrevista nueva desde el corte de las {at}, pero se registró 1 campero más.",
        )
        self.assertEqual(
            delta_sentence({"completed": 0, "percent": -5, "eligible": 1, "percent_before": 25, "percent_now": 20}, self.since),
            f"Ninguna entrevista nueva desde el corte de las {at}; el avance pasó de 25\u00a0% a 20\u00a0% porque se registró 1 campero más.",
        )
        self.assertEqual(
            delta_sentence({"completed": 0, "percent": None, "eligible": -5}, self.since),
            f"Ninguna entrevista nueva desde el corte de las {at}, pero hay 5 camperos menos.",
        )
        self.assertEqual(
            delta_sentence({"completed": 0, "percent": 4, "eligible": -1, "percent_before": 20, "percent_now": 24}),
            "Ninguna entrevista nueva desde el corte anterior; el avance pasó de 20\u00a0% a 24\u00a0% porque hay 1 campero menos.",
        )
        # Interviews finished: the interviews lead, as before.
        self.assertEqual(
            delta_sentence({"completed": 2, "percent": 10, "eligible": 3, "percent_before": 30, "percent_now": 40}, self.since),
            f"2 entrevistas más que en el corte de las {at}. El avance pasó de 30\u00a0% a 40\u00a0%.",
        )

    def test_without_a_time_it_says_the_previous_cutoff(self):
        """The PDF's framing: the same facts, anchored to "el corte anterior"."""
        self.assertEqual(
            delta_sentence({"completed": -2, "percent": -55, "percent_before": 80, "percent_now": 25}),
            "2 entrevistas menos que en el corte anterior. El avance pasó de 80\u00a0% a 25\u00a0%.",
        )
        self.assertEqual(
            delta_sentence({"completed": 0, "percent": 4, "percent_before": 20, "percent_now": 24}),
            "Ninguna entrevista nueva desde el corte anterior; el avance pasó de 20\u00a0% a 24\u00a0%.",
        )

    def test_the_pdf_uses_the_same_sentence(self):
        from events.services import pdf

        self.assertIs(pdf.delta_sentence, delta_sentence)


class CampFlowFixture(CampFixture):
    def set_status(self, participants, status):
        EventParticipant.objects.filter(pk__in=[p.pk for p in participants]).update(interview_status=status)

    def set_active(self, participants, active):
        EventParticipant.objects.filter(pk__in=[p.pk for p in participants]).update(is_active=active)


class HeadlineBaselineTests(CampFlowFixture):
    """A1: the pastor's and the administration's headline compare the whole event with a whole-event cutoff."""

    def setUp(self):
        now = timezone.now().replace(microsecond=0)
        # 20:00-style cutoff of the whole event: nobody had finished (0 of 5).
        self.event_cut = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=now - timedelta(hours=2))
        # A newer cutoff of one team only, with very different numbers (3 of its 4).
        self.set_status([self.p1, self.p2, self.p3], InterviewStatus.COMPLETED)
        self.team_cut = generate_snapshot(self.event, team_scope(self.event, self.m_tutor1), trigger="automatico", cutoff_at=now - timedelta(hours=1))
        # Now: 1 of 5 for the whole event.
        self.set_status([self.p2, self.p3], InterviewStatus.PENDING)

    def test_pastor_home_compares_with_the_whole_event_cutoff(self):
        self.login(self.pastor)
        response = self.client.get(self.url("pastor_home"))
        self.assertEqual(response.context["last_snapshot"], self.event_cut)
        self.assertContains(response, f"1 entrevista más que en el corte de las {hhmm(self.event_cut.cutoff_at)}. El avance pasó de 0\u00a0% a 20\u00a0%.")
        self.assertNotContains(response, "2 entrevistas menos")  # the team's numbers
        self.assertNotContains(response, f"desde el corte de las {hhmm(self.team_cut.cutoff_at)}")

    def test_admin_home_uses_the_whole_event_cutoff(self):
        self.login(self.admin)
        response = self.client.get(self.url("admin_home"))
        self.assertEqual(response.context["last_snapshot"], self.event_cut)

    def test_tutor_home_keeps_the_team_cutoff(self):
        self.login(self.tutor1)
        response = self.client.get(self.url("tutor_home"))
        self.assertEqual(response.context["last_snapshot"], self.team_cut)


class PercentOnlyChangeTests(CampFlowFixture):
    """A2: new camperos registered and nobody finished: the screens say the percentage moved, like the PDF."""

    SENTENCE = "Ninguna entrevista nueva desde el corte de las {}; el avance pasó de 25\u00a0% a 20\u00a0% porque se registró 1 campero más."

    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.set_status([self.p1], InterviewStatus.COMPLETED)
        self.set_active([self.p2], False)  # 1 of 4: 25 %
        self.previous = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=self.now - timedelta(hours=1))
        self.set_active([self.p2], True)  # a campero joins: 1 of 5, 20 %
        self.login(self.pastor)

    def cut_now(self):
        return generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=self.now)

    def assert_says_the_percentage_moved(self, response):
        self.assertNotContains(response, "Sin cambios")
        self.assertContains(response, "de 25\u00a0% a 20\u00a0%")
        self.assertContains(response, self.SENTENCE.format(hhmm(self.previous.cutoff_at)))
        self.assertNotContains(response, "puntos desde")

    def test_cutoff_detail(self):
        latest = self.cut_now()
        self.assert_says_the_percentage_moved(self.client.get(self.url("report_detail", latest.public_id)))

    def test_reports_home(self):
        self.cut_now()
        self.assert_says_the_percentage_moved(self.client.get(self.url("reports")))

    def test_pastor_home(self):
        # Live: 1 of 5 now against the cutoff's 1 of 4.
        self.assert_says_the_percentage_moved(self.client.get(self.url("pastor_home")))

    def test_pdf_states_the_same_change(self):
        from events.services.metrics import compare

        latest = self.cut_now()
        delta = compare(latest.data, self.previous.data)
        self.assertEqual(
            delta_sentence(delta),
            "Ninguna entrevista nueva desde el corte anterior; el avance pasó de 25\u00a0% a 20\u00a0% porque se registró 1 campero más.",
        )
        pdf = self.client.get(self.url("report_pdf", latest.public_id))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_pdf_names_the_time_of_the_previous_cutoff(self):
        """Like the screens: "desde el corte de las 20:00", not "desde el corte anterior"."""
        latest = self.cut_now()
        with patch("events.services.pdf.delta_sentence", wraps=delta_sentence) as sentence:
            self.assertEqual(self.client.get(self.url("report_pdf", latest.public_id)).status_code, 200)
        self.assertEqual(sentence.call_count, 1)
        delta, since = sentence.call_args.args
        self.assertEqual(since, self.previous.cutoff_at)
        self.assertEqual(delta_sentence(delta, since), self.SENTENCE.format(hhmm(self.previous.cutoff_at)))

    def test_avance_figures_say_percent_not_points(self):
        latest = self.cut_now()
        reports = self.client.get(self.url("reports"))
        self.assertContains(reports, "De 25\u00a0% a 20\u00a0% desde el corte anterior")  # from-to, never a signed percentage
        self.assertNotContains(reports, "\u22125&nbsp;%")
        self.assertNotContains(reports, "puntos")
        detail = self.client.get(self.url("report_detail", latest.public_id))
        self.assertContains(detail, '<th scope="row">Avance</th>')
        self.assertContains(detail, "de 25\u00a0% a 20\u00a0%</td>")
        self.assertNotContains(detail, "\u22125&nbsp;%")
        self.assertNotContains(detail, "puntos")


class CamperosRegisteredTests(CampFlowFixture):
    """A2: the cutoff had no camperos; five registered since and nobody finished. Never "Sin cambios"."""

    SENTENCE = "Ninguna entrevista nueva desde el corte de las {}, pero se registraron 5 camperos más."

    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        EventParticipant.objects.filter(event=self.event).update(is_active=False)
        self.previous = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=self.now - timedelta(hours=1))
        EventParticipant.objects.filter(event=self.event).update(is_active=True)
        self.login(self.pastor)

    def assert_says_who_registered(self, response):
        self.assertNotContains(response, "Sin cambios")
        self.assertContains(response, self.SENTENCE.format(hhmm(self.previous.cutoff_at)))

    def test_pastor_home(self):
        self.assert_says_who_registered(self.client.get(self.url("pastor_home")))

    def test_cutoff_detail_and_reports_home(self):
        latest = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=self.now)
        self.assert_says_who_registered(self.client.get(self.url("report_detail", latest.public_id)))
        self.assert_says_who_registered(self.client.get(self.url("reports")))


class AvanceUnchangedTests(CampFlowFixture):
    def test_the_avance_note_when_nothing_moved(self):
        now = timezone.now().replace(microsecond=0)
        generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=now - timedelta(hours=1))
        generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=now)
        self.login(self.pastor)
        response = self.client.get(self.url("reports"))
        self.assertContains(response, '<dd class="figures__note">Igual que en el corte anterior</dd>')
        self.assertNotContains(response, "sin cambio puntos")


class EmptyEventTests(CampFlowFixture):
    """A5: an event without camperos says so in every section, without zeros, charts or team rows."""

    def setUp(self):
        self.empty = Event.objects.create(
            church=self.church,
            name="Campamento vacío",
            slug="campamento-vacio",
            questionnaire_version=self.version,
            procedures_approved_at=timezone.now(),
            starts_on=timezone.localdate(),
            ends_on=timezone.localdate(),
        )
        EventMembership.objects.create(event=self.empty, user=self.pastor, role=MembershipRole.PASTOR)
        tutor = EventMembership.objects.create(event=self.empty, user=self.tutor1, role=MembershipRole.TUTOR, team_name="Equipo Norte")
        coordinator = EventMembership.objects.create(event=self.empty, user=self.coord1, role=MembershipRole.COORDINATOR)
        TutorAssignment.objects.create(event=self.empty, tutor=tutor, coordinator=coordinator)

    def test_pastor_home_on_an_event_without_camperos(self):
        from django.urls import reverse

        self.login(self.pastor)
        response = self.client.get(reverse("events:pastor_home", args=[self.empty.slug]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(html.count(EMPTY), 5, "one sentence in each of the five sections")
        self.assertNotIn("ya terminamos", html)
        self.assertNotIn("Ritmo", html)
        self.assertNotIn("0 de 0", html)
        self.assertNotIn("Todavía no hay participantes registrados.", html)
        self.assertNotIn('class="teams"', html)
        self.assertNotIn('class="figures"', html)
        self.assertNotIn("pastor-acumulado", html)


class PluralTests(CampFlowFixture):
    """A7: '1 campero', '1 coordinador'; never '1 camperos' or '1 coordinadores'."""

    def problems(self):
        self.login(self.admin)
        return [text for text, _ in self.client.get(self.url("admin_home")).context["problems"]]

    def test_coordinators_without_tutor(self):
        TutorAssignment.objects.filter(coordinator=self.m_coord3).delete()
        self.assertIn("1 coordinador no tiene tutor.", self.problems())
        TutorAssignment.objects.filter(coordinator=self.m_coord2).delete()
        self.assertIn("2 coordinadores no tienen tutor.", self.problems())

    def test_camperos_without_coordinator(self):
        EventParticipant.objects.filter(pk=self.p4.pk).update(coordinator=None)
        self.assertIn("1 campero no tiene coordinador.", self.problems())
        EventParticipant.objects.filter(pk=self.p3.pk).update(coordinator=None)
        self.assertIn("2 camperos no tienen coordinador.", self.problems())

    def test_interviews_still_to_do(self):
        def texts(payload):
            return [item["text"].split(" Al ritmo")[0] for item in recommendations(payload)]

        def steps(payload):
            return build_deck(payload, event_name="X", kind_label="Campamento", k=5)["next_steps"]

        self.set_status([self.p1, self.p2, self.p3, self.p4], InterviewStatus.COMPLETED)
        payload = build_payload(event_scope(self.event))
        self.assertIn("Falta 1 entrevista de 5.", texts(payload))
        self.assertIn("Completar 1 entrevista pendiente.", steps(payload))
        self.set_status([self.p4], InterviewStatus.PENDING)
        payload = build_payload(event_scope(self.event))
        self.assertIn("Faltan 2 entrevistas de 5.", texts(payload))
        self.assertIn("Completar 2 entrevistas pendientes.", steps(payload))

    def import_audit(self):
        return AuditEvent.objects.filter(event=self.event, action="importacion").values_list("summary", flat=True).get()

    def import_rows(self, *names):
        rows = "".join(f"{name},16,8,Iglesia Centro,,sí\n" for name in names)
        upload = io.BytesIO(("Nombre,Edad,Cuarto,Iglesia o grupo,Coordinador,Autorización\n" + rows).encode("utf-8"))
        upload.name = "camperos.csv"
        self.login(self.admin)
        self.client.post(self.url("participant_import"), {"file": upload})
        lines = [str(line) for line in range(2, 2 + len(names))]
        response = self.client.post(self.url("participant_import"), {"action": "confirm", "include": lines})
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_import_one_campero(self):
        self.assertEqual(self.import_rows("Valeria Ruiz Ortega"), ["Se importó 1 campero."])
        self.assertEqual(self.import_audit(), "Importó 1 campero desde un archivo")

    def test_import_two_camperos(self):
        self.assertEqual(self.import_rows("Valeria Ruiz Ortega", "Marco Díaz Luna"), ["Se importaron 2 camperos."])
        self.assertEqual(self.import_audit(), "Importó 2 camperos desde un archivo")
