"""Language, accessibility basics, empty and loading states, live fragments."""

import re
from datetime import date
from html.parser import HTMLParser

from django.test import Client
from django.urls import reverse

from core.models import Church, Role
from core.services import assign_role
from events.models import Event, EventMembership, Interview, MembershipRole

from .base import CampFixture, make_user

ENGLISH = re.compile(
    r"\b(Dashboard|Submit|Save|Cancel|Sign (in|out)|Logout|Log out|Search|Delete|Settings|Follow-ups?|Loading|"
    r"Next|Previous|Welcome|Your|Please|Required|This field|Participants?|Questions?|Reports?|Home|Back|Status|"
    r"Completed|Pending|In progress|Not assigned|Edit|View|Download|Export|Import)\b"
)


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.text = []
        self.controls = []  # (tag, attrs)
        self.labels_for = set()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "label" and attrs.get("for"):
            self.labels_for.add(attrs["for"])
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag in ("input", "select", "textarea") and attrs.get("type") not in ("hidden", "submit"):
            self.controls.append((tag, attrs))
        for key in ("placeholder", "aria-label", "title", "alt"):
            if attrs.get(key):
                self.text.append(attrs[key])

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)


def analyse(html):
    parser = TextExtractor()
    parser.feed(html)
    return parser


class SpanishAndAccessibilityTests(CampFixture):
    PAGES = {
        "coord1": ["coord_home", "my_participants", "coord_pending", "coord_progress", "interview_search", "participant_create"],
        "tutor1": ["tutor_home", "coordinators", "interviews", "pending", "reports", "follow_ups"],
        "pastor": ["pastor_home", "progress", "follow_ups", "reports", "team", "presentation_live"],
        "admin": ["admin_home", "participants", "participant_import", "team", "member_add", "assignments", "questionnaires", "settings", "audit", "sessions"],
    }

    def test_pages_are_spanish_and_labelled(self):
        for username, names in self.PAGES.items():
            client = Client()
            self.login(getattr(self, username), client)
            for name in names:
                response = client.get(self.url(name), follow=True)
                self.assertEqual(response.status_code, 200, (username, name))
                html = response.content.decode()
                self.assertIn('lang="es-MX"', html, name)
                parsed = analyse(html)
                text = " ".join(parsed.text)
                match = ENGLISH.search(text)
                self.assertIsNone(match, f"{name}: texto en inglés «{match.group(0) if match else ''}»")
                for tag, attrs in parsed.controls:
                    labelled = attrs.get("id") in parsed.labels_for or attrs.get("aria-label") or tag == "input" and attrs.get("type") in ("radio", "checkbox") and attrs.get("id") in parsed.labels_for
                    if attrs.get("class", "").find("visually-hidden") >= 0:
                        continue
                    self.assertTrue(labelled, f"{name}: control sin etiqueta {tag} {attrs.get('name')}")
                self.assertIn('class="skip-link"', html)

    def test_interview_pages_are_spanish(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        for url in [self.url("interview_step", interview.public_id, n.number) for n in self.steps] + [self.url("interview_review", interview.public_id)]:
            response = self.client.get(url, follow=True)
            text = " ".join(analyse(response.content.decode()).text)
            self.assertIsNone(ENGLISH.search(text), url)

    def test_form_errors_follow_the_error_summary_pattern(self):
        self.login(self.admin)
        response = self.client.post(self.url("member_add"), {"full_name": "", "email": "no-es-correo", "role": ""})
        self.assertContains(response, "Hay un problema")
        self.assertContains(response, "<title>Error: ", html=False)
        self.assertContains(response, "Escribe un correo válido")
        self.assertContains(response, 'href="#id_full_name"')

    def test_live_fragments_are_partial_and_private(self):
        for user, name in [(self.coord1, "coord_home"), (self.tutor1, "tutor_home"), (self.pastor, "pastor_home"), (self.admin, "admin_home"), (self.pastor, "presentation_live")]:
            client = Client()
            self.login(user, client)
            response = client.get(self.url(name), HTTP_X_REQUESTED_WITH="fetch")
            self.assertEqual(response.status_code, 200, name)
            self.assertNotIn(b"<html", response.content, name)
            self.assertIn("no-store", response["Cache-Control"])

    def test_dashboards_poll_with_status_indicator(self):
        self.login(self.pastor)
        response = self.client.get(self.url("pastor_home"))
        self.assertContains(response, 'data-live-url="')
        self.assertContains(response, 'data-live-status="estado-actualizacion"')
        self.assertContains(response, 'aria-live="polite"')

    def test_empty_states(self):
        event = Event.objects.create(church=self.church, name="Retiro vacío", slug="retiro-vacio", kind="retiro", questionnaire_version=self.version)
        coord = make_user("nuevo@prueba.mx", "Nora", "Nueva")
        EventMembership.objects.create(event=event, user=coord, role=MembershipRole.COORDINATOR)
        self.login(coord)
        response = self.client.get(reverse("events:coord_home", args=[event.slug]))
        self.assertContains(response, "Todavía no tienes camperos asignados")
        self.assertContains(response, "Registrar campero")
        response = self.client.get(reverse("events:interview_search", args=[event.slug]), follow=True)
        self.assertEqual(response.status_code, 200)
        self.login(self.admin)
        response = self.client.get(reverse("events:pastor_home", args=[event.slug]))
        self.assertContains(response, "Aún no hay entrevistas completadas")
        self.assertContains(response, "¿Cómo va el retiro?")

    def test_coordinator_has_obvious_logout_and_primary_action(self):
        self.login(self.coord1)
        response = self.client.get(self.url("coord_home"))
        self.assertContains(response, "Cerrar sesión")
        # The sign: one action, the search every interview starts from
        self.assertContains(response, "¿A quién vas a entrevistar?")
        self.assertContains(response, f'action="{self.url("interview_search")}"')

    def test_error_pages_are_spanish(self):
        self.login(self.coord1)
        response = self.client.get(self.url("admin_home"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "No tienes acceso a esta página", status_code=403)


class EventListTests(CampFixture):
    """The event list (base.html, Rótulo): data as sentences, the first camp as the one sign."""

    def test_dates_and_role_read_as_sentences(self):
        Event.objects.filter(pk=self.event.pk).update(starts_on=date(2026, 10, 23), ends_on=date(2026, 10, 25), location="Rancho El Encino")
        Event.objects.filter(pk=self.other_event.pk).update(starts_on=date(2026, 10, 30), ends_on=date(2026, 11, 2), location="")
        self.login(self.admin)
        response = self.client.get(reverse("events:list") + "?todos=1")
        self.assertContains(response, "Campamento del 23 al 25 de octubre de 2026, en Rancho El Encino.")
        self.assertContains(response, "Campamento del 30 de octubre al 2 de noviembre de 2026.")
        self.assertContains(response, "Tu rol: administrador.")
        main = response.content.decode().split("<main", 1)[1].split("</main>", 1)[0]
        self.assertNotIn("·", main)  # data joined with commas, not dots (the <title> comes from base.html)

    def test_creating_the_first_camp_is_the_sign(self):
        church = Church.objects.create(name="Iglesia nueva", slug="iglesia-nueva")
        admin = make_user("admin-nueva@prueba.mx", "Ana", "Nueva")
        assign_role(admin, church, Role.CHURCH_ADMIN)
        self.login(admin)
        response = self.client.get(reverse("events:list") + "?todos=1")
        self.assertContains(response, 'class="sign"', count=1)
        self.assertContains(response, "Todavía no hay eventos")
        self.assertContains(response, f'href="{reverse("events:create")}"', count=1)

    def test_someone_without_events_is_told_who_can_add_them(self):
        self.login(make_user("sin-evento@prueba.mx", "Sara", "Sola"))
        response = self.client.get(reverse("events:list") + "?todos=1")
        self.assertContains(response, "Aún no formas parte de ningún evento")
        self.assertNotContains(response, 'class="sign"')
        self.assertNotContains(response, reverse("events:create"))
