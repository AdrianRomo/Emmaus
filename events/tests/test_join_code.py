"""Joining an event team with the printed code: the code, the public pages, the request and its decision.

A request is only a name and an e-mail. Approving it runs the "Agregar persona" path (an account without
a password and a link to create one), so nobody signs in because they typed a code.
"""

import importlib
import re
from datetime import timedelta
from unittest import mock

from django.apps import apps
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Church, Role
from core.services import assign_role
from events.access import resolve_access
from events.models import (
    JOIN_CODE_ALPHABET,
    AccessRequest,
    AuditEvent,
    AuthThrottle,
    Event,
    EventMembership,
    EventParticipant,
    MembershipRole,
    TutorAssignment,
)
from events.services.audit import ACTION_LABELS
from events.services.join import find_event_by_key

from .base import PASSWORD, CampFixture, make_user
from .test_ui import ENGLISH, analyse

SIGN = re.compile(r'class="sign[ "]')
NOT_FOUND = "No encontramos ese evento. Revisa lo que escribiste o pregunta a quien organiza."
ANSWER = ("Listo. La administración de Campamento A revisará tu solicitud. Cuando te den acceso podrás entrar con tu correo; "
          "si es tu primera vez, te darán un enlace para crear tu contraseña.")
CONSOLE = "django.core.mail.backends.console.EmailBackend"
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


class JoinFixture(CampFixture):
    def entry_url(self, event=None):
        return reverse("event_entry", args=[(event or self.event).join_code])

    def ask(self, client=None, event=None, **overrides):
        data = {"full_name": "Raúl Nuevo Voluntario", "email": "Raul.Nuevo@Ejemplo.MX", "requested_role": "tutor", "note": ""}
        data.update(overrides)
        return (client or self.client).post(self.entry_url(event), data)

    def pending(self, email="raul.nuevo@ejemplo.mx", role=MembershipRole.TUTOR, name="Raúl Nuevo Voluntario"):
        return AccessRequest.objects.create(event=self.event, full_name=name, email=email, requested_role=role)


# ---- The code ----------------------------------------------------------------------------------


class JoinCodeModelTests(JoinFixture):
    def test_every_event_gets_a_readable_code_on_save(self):
        codes = [self.event.join_code, self.other_event.join_code]
        for code in codes:
            self.assertEqual(len(code), 6)
            self.assertTrue(set(code) <= set(JOIN_CODE_ALPHABET), code)
        self.assertNotEqual(*codes)
        self.assertEqual(JOIN_CODE_ALPHABET, "ABCDEFGHJKMNPQRSTUVWXYZ23456789")

    def test_a_code_already_taken_is_never_reused(self):
        taken = self.event.join_code
        with mock.patch("events.models.new_join_code", side_effect=[taken, "BBBBBB"]):
            event = Event.objects.create(church=self.church, name="Retiro", slug="retiro-colision")
        self.assertEqual(event.join_code, "BBBBBB")

    def test_a_clash_found_only_by_the_database_is_retried(self):
        """Another worker saved the same code between the check and the insert: the unique index catches it."""
        taken = self.event.join_code
        with mock.patch("events.models.new_join_code", side_effect=[taken, "CCCCCC"]), \
                mock.patch.object(Event, "_join_code_taken", side_effect=[False, True, False]):
            event = Event.objects.create(church=self.church, name="Retiro", slug="retiro-carrera")
        self.assertEqual(event.join_code, "CCCCCC")
        self.assertEqual(Event.objects.get(pk=event.pk).join_code, "CCCCCC")

    def test_another_integrity_error_is_not_swallowed(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Event.objects.create(church=self.church, name="Repetido", slug=self.event.slug)

    def test_the_database_refuses_two_events_with_one_code(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Event.objects.filter(pk=self.other_event.pk).update(join_code=self.event.join_code)

    def test_blank_codes_do_not_clash(self):
        Event.objects.filter(pk__in=[self.event.pk, self.other_event.pk]).update(join_code="")
        self.assertEqual(Event.objects.filter(join_code="").count(), 2)

    def test_saving_some_fields_of_an_event_without_a_code_also_stores_one(self):
        Event.objects.filter(pk=self.event.pk).update(join_code="")
        event = Event.objects.get(pk=self.event.pk)
        event.status = Event.Status.CLOSED
        event.save(update_fields=["status"])
        self.assertRegex(Event.objects.get(pk=self.event.pk).join_code, r"^[A-Z2-9]{6}$")

    def test_regenerating_gives_a_different_code_and_the_old_one_stops_working(self):
        old = self.event.join_code
        with mock.patch("events.models.new_join_code", side_effect=[old, "DDDDDD"]):
            new = self.event.regenerate_join_code()
        self.assertEqual(new, "DDDDDD")
        self.assertEqual(Event.objects.get(pk=self.event.pk).join_code, "DDDDDD")
        self.assertIsNone(find_event_by_key(old))
        self.assertFalse(Event.objects.get(pk=self.event.pk).join_by_name)  # the name was on the old sheet too
        self.assertIsNone(find_event_by_key("campamento-a"))
        self.assertEqual(find_event_by_key("DDDDDD"), self.event)

    def test_migration_backfills_every_event_with_its_own_code(self):
        migration = importlib.import_module("events.migrations.0006_event_join_code")
        for n in range(5):
            Event.objects.create(church=self.church, name=f"Viejo {n}", slug=f"viejo-{n}")
        Event.objects.update(join_code="")
        migration.backfill_join_codes(apps, None)
        codes = list(Event.objects.values_list("join_code", flat=True))
        self.assertEqual(len(codes), 7)
        self.assertEqual(len(set(codes)), 7)
        self.assertTrue(all(re.fullmatch(r"[A-HJKMNP-Z2-9]{6}", c) for c in codes), codes)

    def test_the_code_is_not_editable_in_the_event_form(self):
        from events.forms import EventForm

        self.assertNotIn("join_code", EventForm.base_fields)


class FindEventTests(JoinFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.link = Event.objects.create(church=cls.church, name="Link Camp 2026", slug="link_camp_2026")

    def test_the_code_matches_in_any_case_and_with_spaces_around(self):
        self.assertEqual(find_event_by_key(f"  {self.event.join_code.lower()} "), self.event)

    def test_the_name_matches_the_slug_however_the_separators_were_typed(self):
        for typed in ("Link Camp 2026", "link_camp_2026", "link-camp-2026", "LINK CAMP 2026", "  link  camp 2026 "):
            self.assertEqual(find_event_by_key(typed), self.link, typed)
        self.assertEqual(find_event_by_key("Campamento A"), self.event)
        self.assertEqual(find_event_by_key("campamento_a"), self.event)

    def test_archived_events_are_not_found(self):
        Event.objects.filter(pk=self.link.pk).update(status=Event.Status.ARCHIVED)
        self.assertIsNone(find_event_by_key("link_camp_2026"))
        self.assertIsNone(find_event_by_key(Event.objects.get(pk=self.link.pk).join_code))

    def test_nothing_matches_nonsense_and_nothing_raises(self):
        for typed in ("", "   ", None, "ZZZZZZ", "no existe", "a" * 5000, "abc\x00def", 12345, "%", "_"):
            self.assertIsNone(find_event_by_key(typed), repr(typed)[:40])


# ---- /unirme/ ----------------------------------------------------------------------------------


class JoinPageTests(JoinFixture):
    def test_the_page_asks_for_the_code_or_the_name(self):
        response = self.client.get(reverse("join"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "¿Vas a ayudar en un evento?")
        self.assertContains(response, "Escribe el código o el nombre del evento que te dieron.")
        self.assertContains(response, '<label class="label" for="id_clave">Código o nombre del evento</label>', html=False)
        self.assertContains(response, "Buscar el evento")
        self.assertContains(response, 'class="input search__input"')
        self.assertContains(response, "css/rotulo.css")
        self.assertEqual(response.content.decode().count("<h1"), 1)

    def test_a_code_or_a_name_leads_to_the_event_page(self):
        for typed in (self.event.join_code.lower(), "Campamento A"):
            response = Client().post(reverse("join"), {"clave": typed})
            self.assertRedirects(response, self.entry_url(), fetch_redirect_response=False)

    def test_not_found_says_what_to_do_and_is_the_same_for_codes_and_names(self):
        bodies = []
        for typed in ("ZZZZZZ", "un evento que no existe"):
            response = Client().post(reverse("join"), {"clave": typed})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Hay un problema")
            self.assertContains(response, "<title>Error: ", html=False)
            self.assertContains(response, NOT_FOUND)
            self.assertContains(response, 'href="#id_clave"')
            body = response.content.decode().replace(typed, "")
            bodies.append(re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", body))
        self.assertEqual(bodies[0], bodies[1])

    def test_an_unknown_event_page_sends_back_here_with_the_same_error(self):
        response = self.client.get(reverse("event_entry", args=["ZZZZZZ"]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("join")))
        page = self.client.get(response["Location"])
        self.assertContains(page, NOT_FOUND)
        self.assertContains(page, 'value="ZZZZZZ"')

    def test_searches_are_limited_per_browser_with_a_bounded_key(self):
        client = Client()
        for n in range(10):
            self.assertEqual(client.post(reverse("join"), {"clave": f"NOPE{n}"}).status_code, 200)
        response = client.post(reverse("join"), {"clave": self.event.join_code})
        self.assertEqual(response.status_code, 200)  # blocked: not even a right code is looked up
        self.assertContains(response, "Demasiados intentos")
        self.assertNotContains(response, NOT_FOUND)
        keys = list(AuthThrottle.objects.filter(key__startswith="join:").values_list("key", flat=True))
        self.assertEqual(keys, [f"join:{client.session.session_key}"])
        self.assertLess(len(keys[0]), 200)
        # Another browser is not affected
        self.assertEqual(Client().post(reverse("join"), {"clave": self.event.join_code}).status_code, 302)

    def test_a_typed_text_never_becomes_a_throttle_key(self):
        Client().post(reverse("join"), {"clave": "x" * 150})
        self.assertFalse(AuthThrottle.objects.filter(key__contains="xxxx").exists())


# ---- /e/<code>/ --------------------------------------------------------------------------------


class EventEntryTests(JoinFixture):
    def test_the_event_page_shows_the_event_and_the_request_form(self):
        Event.objects.filter(pk=self.event.pk).update(starts_on="2026-10-23", ends_on="2026-10-25")
        response = self.client.get(self.entry_url())
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('<h1 class="voice page-head__title">Campamento A</h1>', html)
        self.assertContains(response, "Iglesia de prueba, del 23 al 25 de octubre de 2026.")
        self.assertContains(response, "Nombre completo")
        self.assertContains(response, "Correo")
        self.assertContains(response, "¿Qué vas a hacer en el evento?")
        self.assertContains(response, "Tutor: acompaño a un grupo de coordinadores")
        self.assertContains(response, "Coordinador: entrevisto a mis camperos")
        self.assertNotContains(response, 'value="administrador"')
        self.assertNotContains(response, 'value="pastor"')
        self.assertContains(response, 'class="choices')
        self.assertContains(response, "Algo que la administración deba saber")
        self.assertContains(response, "Pedir acceso")
        self.assertContains(response, "¿Ya tienes cuenta?")
        self.assertContains(response, f'href="{reverse("login")}?next={self.entry_url()}"')
        self.assertEqual(html.count("<h1"), 1)
        self.assertEqual(len(SIGN.findall(html)), 0)

    def test_the_slug_opens_the_same_page(self):
        response = self.client.get(reverse("event_entry", args=["campamento-a"]))
        self.assertContains(response, "Campamento A")
        self.assertContains(response, "Pedir acceso")

    def test_opening_an_existing_event_page_changes_nothing(self):
        """A hit is free; only misses count against the search budget (see JoinAbuseTests)."""
        before = (AccessRequest.objects.count(), AuditEvent.objects.count(), AuthThrottle.objects.count(), Session.objects.count())
        for _ in range(15):
            self.client.get(self.entry_url())
        self.client.get(reverse("event_entry", args=["campamento-a"]))
        after = (AccessRequest.objects.count(), AuditEvent.objects.count(), AuthThrottle.objects.count(), Session.objects.count())
        self.assertEqual(before, after)

    def test_someone_with_access_goes_to_their_home(self):
        for user, home in [(self.coord1, "coord_home"), (self.admin, "admin_home"), (self.pastor, "pastor_home")]:
            client = self.login(user, Client())
            response = client.get(self.entry_url())
            self.assertRedirects(response, self.url(home), fetch_redirect_response=False)

    def test_signed_in_without_access_asks_with_the_account_email(self):
        client = self.login(self.outsider, Client())  # a tutor in the other event only
        page = client.get(self.entry_url())
        self.assertContains(page, 'value="Óscar Externo"')
        self.assertContains(page, 'value="externo@prueba.mx"')
        self.assertContains(page, "readonly")
        self.assertNotContains(page, "¿Ya tienes cuenta?")
        self.ask(client, email="otra-persona@ejemplo.mx", full_name="Óscar Externo")
        self.assertEqual(list(AccessRequest.objects.values_list("email", flat=True)), ["externo@prueba.mx"])

    def test_a_removed_member_can_ask_again(self):
        EventMembership.objects.filter(pk=self.m_coord3.pk).update(is_active=False)
        client = self.login(self.coord3, Client())
        self.assertEqual(client.get(self.entry_url()).status_code, 200)

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_asking_creates_one_pending_request_and_says_what_comes_next(self):
        response = self.ask(note="Puedo llegar el sábado.")
        self.assertContains(response, ANSWER)
        request = AccessRequest.objects.get()
        self.assertEqual((request.event, request.email, request.requested_role, request.status), (self.event, "raul.nuevo@ejemplo.mx", "tutor", "pendiente"))
        self.assertEqual(request.note, "Puedo llegar el sábado.")
        self.assertFalse(User.objects.filter(email__iexact="raul.nuevo@ejemplo.mx").exists())  # no account until approved
        self.assertEqual(len(mail.outbox), 0)
        row = AuditEvent.objects.get(action="solicitud_acceso")
        self.assertEqual(row.summary, "Raúl Nuevo Voluntario pidió acceso como tutor")
        self.assertEqual(row.actor_label, "Sistema")
        self.assertNotIn("sábado", row.summary + str(row.metadata))  # never the note

    def test_the_answer_promises_no_email_and_says_nothing_about_the_server(self):
        """True for a new account and an existing one, and the same whether or not SMTP is configured."""
        answers = []
        for backend, email in ((CONSOLE, "uno@ejemplo.mx"), (LOCMEM, "dos@ejemplo.mx")):
            with self.settings(EMAIL_BACKEND=backend):
                html = self.ask(Client(), email=email).content.decode()
            answers.append(html.split('class="confirm__text">', 1)[1].split("</p>", 1)[0])
        self.assertEqual(answers, [ANSWER, ANSWER])
        self.assertNotIn("te llegará un correo", answers[0])

    def test_requests_are_limited_per_browser(self):
        client = Client()
        for n in range(5):
            self.assertContains(self.ask(client, email=f"persona{n}@ejemplo.mx"), ANSWER)
        audits = AuditEvent.objects.count()
        response = self.ask(client, email="sexta@ejemplo.mx")
        self.assertContains(response, "Hay un problema")
        self.assertContains(response, "Demasiados intentos")
        self.assertNotContains(response, ANSWER)
        self.assertEqual(AccessRequest.objects.count(), 5)
        self.assertFalse(AccessRequest.objects.filter(email="sexta@ejemplo.mx").exists())
        self.assertEqual(AuditEvent.objects.count(), audits)
        self.assertTrue(AuthThrottle.objects.filter(key=f"join-request:{client.session.session_key}").exists())
        # Another browser is not affected
        self.assertContains(self.ask(Client(), email="sexta@ejemplo.mx"), ANSWER)
        self.assertEqual(AccessRequest.objects.count(), 6)

    def test_asking_twice_leaves_one_request(self):
        first = self.ask()
        second = self.ask(full_name="Raúl otra vez", email="raul.nuevo@ejemplo.mx")
        self.assertEqual(AccessRequest.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action="solicitud_acceso").count(), 1)
        self.assertContains(second, ANSWER)
        self.assertEqual(first.status_code, second.status_code)

    def test_the_database_keeps_one_pending_request_per_person(self):
        self.pending()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.pending()
        AccessRequest.objects.update(status=AccessRequest.Status.REJECTED)
        self.pending()  # a decided request does not block a new one
        self.assertEqual(AccessRequest.objects.count(), 2)

    def test_only_tutor_or_coordinador_can_be_asked_for(self):
        for role in ("administrador", "pastor", ""):
            response = self.ask(requested_role=role)
            self.assertContains(response, "Elige qué vas a hacer en el evento.")
            self.assertContains(response, 'href="#id_requested_role"')
            self.assertContains(response, 'id="id_requested_role"')
        self.assertFalse(AccessRequest.objects.exists())

    def test_form_errors_follow_the_error_summary_pattern(self):
        response = self.ask(full_name="", email="no-es-correo", note="x" * 301)
        self.assertContains(response, "Hay un problema")
        self.assertContains(response, "<title>Error: ", html=False)
        self.assertContains(response, 'href="#id_full_name"')
        self.assertContains(response, "Escribe un correo válido")
        self.assertContains(response, "Escribe 300 caracteres o menos.")
        self.assertFalse(AccessRequest.objects.exists())

    def test_an_archived_event_has_no_public_page(self):
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.ARCHIVED)
        response = self.client.get(self.entry_url())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("join")))
        self.assertEqual(self.ask().status_code, 302)
        self.assertFalse(AccessRequest.objects.exists())

    def test_public_pages_are_spanish_and_labelled(self):
        for url in (reverse("join"), self.entry_url()):
            html = self.client.get(url).content.decode()
            self.assertIn('lang="es-MX"', html)
            parsed = analyse(html)
            self.assertIsNone(ENGLISH.search(" ".join(parsed.text)), url)
            for tag, attrs in parsed.controls:
                self.assertIn(attrs.get("id"), parsed.labels_for, f"{url}: {tag} {attrs.get('name')}")
            main = html.split("<main", 1)[1].split("</main>", 1)[0]
            self.assertNotIn("·", main)
            self.assertNotIn("<script>", html)


# ---- Equipo: the requests and the code ---------------------------------------------------------


class TeamRequestsTests(JoinFixture):
    def approve(self, request, role=None, client=None):
        return (client or self.client).post(self.url("access_request_approve", request.pk), {"role": role or request.requested_role})

    def test_pending_requests_are_listed_for_the_administration(self):
        request = self.pending()
        self.pending(email="otra@ejemplo.mx", role=MembershipRole.COORDINATOR, name="Otra Persona")
        self.login(self.admin)
        response = self.client.get(self.url("team"))
        self.assertContains(response, 'id="solicitudes"')
        self.assertContains(response, 'Solicitudes de acceso <span class="count">2</span>', html=False)
        self.assertContains(response, '<p class="row__title">Raúl Nuevo Voluntario</p>', html=False)
        self.assertContains(response, "raul.nuevo@ejemplo.mx, pidió ser tutor, hace un momento")
        self.assertContains(response, "otra@ejemplo.mx, pidió ser coordinador, hace un momento")
        self.assertContains(response, f'action="{self.url("access_request_approve", request.pk)}"')
        self.assertContains(response, f'action="{self.url("access_request_reject", request.pk)}"')
        html = response.content.decode()
        select = html.split(f'id="rol-{request.pk}"', 1)[1].split("</select>", 1)[0]
        self.assertEqual(re.findall(r'value="([a-z]+)"', select), ["administrador", "pastor", "tutor", "coordinador"])
        self.assertIn('value="tutor" selected', select)
        self.assertIn("data-confirm=", html.split(f'action="{self.url("access_request_reject", request.pk)}"', 1)[1].split("</form>", 1)[0])
        self.assertIsNone(ENGLISH.search(" ".join(analyse(html).text)))
        parsed = analyse(html)
        for tag, attrs in parsed.controls:
            self.assertIn(attrs.get("id"), parsed.labels_for, f"{tag} {attrs.get('name')}")

    def test_decided_requests_leave_the_list_and_the_empty_line_says_so(self):
        request = self.pending()
        AccessRequest.objects.filter(pk=request.pk).update(status=AccessRequest.Status.REJECTED)
        self.login(self.admin)
        response = self.client.get(self.url("team"))
        self.assertContains(response, "Nadie ha pedido acceso.")
        self.assertNotContains(response, "Raúl Nuevo Voluntario")

    def test_pastors_see_the_team_but_not_the_requests_or_the_code(self):
        self.pending()
        self.login(self.pastor)
        response = self.client.get(self.url("team"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Solicitudes de acceso")
        self.assertNotContains(response, "raul.nuevo@ejemplo.mx")
        self.assertNotContains(response, self.event.join_code)

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_approving_adds_the_person_exactly_like_agregar_persona(self):
        request = self.pending()
        self.login(self.admin)
        response = self.approve(request, role=MembershipRole.COORDINATOR)
        user = User.objects.get(email="raul.nuevo@ejemplo.mx")
        self.assertEqual((user.username, user.first_name, user.last_name), ("raul.nuevo@ejemplo.mx", "Raúl", "Nuevo Voluntario"))
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.church_roles.exists())
        membership = EventMembership.objects.get(event=self.event, user=user)
        self.assertEqual((membership.role, membership.is_active, membership.can_view_sensitive, membership.created_by), ("coordinador", True, False, self.admin))
        request.refresh_from_db()
        self.assertEqual((request.status, request.decided_by, request.membership), ("aprobada", self.admin, membership))
        self.assertIsNotNone(request.decided_at)
        actions = list(AuditEvent.objects.filter(event=self.event).order_by("id").values_list("action", "summary"))
        self.assertIn(("integrante_agregado", "Agregó a Raúl Nuevo Voluntario como coordinador"), actions)
        self.assertIn(("solicitud_aprobada", "Aprobó la solicitud de acceso de Raúl Nuevo Voluntario como coordinador"), actions)
        # The same screen and message as "Agregar persona": the link, by e-mail and on screen
        self.assertTemplateUsed(response, "events/admin/member_created.html")
        self.assertContains(response, "Raúl Nuevo Voluntario ya forma parte del equipo. Le enviamos un correo para crear su contraseña.")
        self.assertEqual(mail.outbox[0].to, ["raul.nuevo@ejemplo.mx"])
        link = response.context["link"]
        self.assertIn(link, mail.outbox[0].body)
        self.assertContains(response, f'value="{link}"')

    def test_when_the_email_is_not_sent_the_link_is_on_screen(self):
        request = self.pending()
        self.login(self.admin)
        with mock.patch("events.views.admin.send_email_safely", return_value=False):
            response = self.approve(request)
        self.assertContains(response, "Raúl Nuevo Voluntario ya forma parte del equipo. Copia el enlace para que cree su contraseña.")
        self.assertContains(response, "/cuenta/contrasena/")

    def test_an_existing_account_is_reused_and_keeps_its_password(self):
        person = make_user("Ya.Tiene@Cuenta.mx", "Yara", "Tiene")
        request = self.pending(email="ya.tiene@cuenta.mx", name="Otro Nombre")
        self.login(self.admin)
        response = self.approve(request)
        self.assertRedirects(response, self.url("team"), fetch_redirect_response=False)
        self.assertEqual(User.objects.filter(email__iexact="ya.tiene@cuenta.mx").count(), 1)
        person.refresh_from_db()
        self.assertTrue(person.has_usable_password())
        self.assertEqual(person.first_name, "Yara")
        self.assertTrue(EventMembership.objects.filter(event=self.event, user=person, role="tutor").exists())
        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertEqual(messages, ["Yara Tiene ya forma parte del equipo. Puede entrar con su contraseña de siempre."])

    def test_a_removed_membership_comes_back_instead_of_a_second_one(self):
        EventMembership.objects.filter(pk=self.m_care.pk).update(is_active=False)  # had confidential access
        request = self.pending(email="cuidado@prueba.mx", name="Celia Cuidado")
        self.login(self.admin)
        self.approve(request, role=MembershipRole.TUTOR)
        memberships = EventMembership.objects.filter(event=self.event, user=self.care)
        self.assertEqual(memberships.count(), 1)
        membership = memberships.get()
        self.assertEqual(membership.pk, self.m_care.pk)
        self.assertEqual((membership.role, membership.is_active, membership.can_view_sensitive), ("tutor", True, False))
        self.assertTrue(AuditEvent.objects.filter(action="acceso_confidencial", summary="Retiró acceso confidencial a Celia Cuidado").exists())
        self.assertEqual(AccessRequest.objects.get(pk=request.pk).membership, membership)

    def test_a_coordinator_brought_back_as_tutor_loses_the_coordinator_links(self):
        EventMembership.objects.filter(pk=self.m_coord1.pk).update(is_active=False)
        request = self.pending(email="coord1@prueba.mx", name="Carla Uno")
        self.login(self.admin)
        self.approve(request, role=MembershipRole.TUTOR)
        self.assertEqual(EventMembership.objects.get(pk=self.m_coord1.pk).role, "tutor")
        self.assertFalse(TutorAssignment.objects.filter(coordinator=self.m_coord1).exists())
        self.assertFalse(EventParticipant.objects.filter(coordinator=self.m_coord1).exists())

    def test_a_second_approval_changes_nothing(self):
        request = self.pending()
        self.login(self.admin)
        self.approve(request)
        audits = AuditEvent.objects.count()
        response = self.approve(request, role=MembershipRole.ADMIN)
        self.assertRedirects(response, self.url("team") + "#solicitudes", fetch_redirect_response=False)
        self.assertEqual([str(m) for m in response.wsgi_request._messages], ["Esta solicitud ya se atendió."])
        self.assertEqual(EventMembership.objects.filter(event=self.event, user__email="raul.nuevo@ejemplo.mx").count(), 1)
        self.assertEqual(EventMembership.objects.get(event=self.event, user__email="raul.nuevo@ejemplo.mx").role, "tutor")
        self.assertEqual(AuditEvent.objects.count(), audits)

    def test_approving_someone_already_on_the_team_is_refused_and_leaves_their_role(self):
        request = self.pending(email="tutor1@prueba.mx", name="Teresa Uno", role=MembershipRole.COORDINATOR)
        self.login(self.admin)
        response = self.approve(request, role=MembershipRole.COORDINATOR)
        self.m_tutor1.refresh_from_db()
        self.assertEqual(self.m_tutor1.role, "tutor")
        request.refresh_from_db()
        self.assertEqual((request.status, request.decision_note, request.membership), ("rechazada", "Ya tenía acceso como tutor.", None))
        self.assertEqual([str(m) for m in response.wsgi_request._messages],
                         ["Teresa Uno ya tiene acceso a este evento como tutor. Si quieres cambiar su rol, hazlo en Equipo, Editar."])

    def test_an_unknown_role_is_refused(self):
        request = self.pending()
        self.login(self.admin)
        response = self.client.post(self.url("access_request_approve", request.pk), {"role": "superusuario"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AccessRequest.objects.get(pk=request.pk).status, "pendiente")
        self.assertFalse(User.objects.filter(email="raul.nuevo@ejemplo.mx").exists())

    def test_rejecting_closes_the_request(self):
        request = self.pending()
        self.login(self.admin)
        response = self.client.post(self.url("access_request_reject", request.pk))
        self.assertEqual([str(m) for m in response.wsgi_request._messages], ["Se rechazó la solicitud de Raúl Nuevo Voluntario."])
        request.refresh_from_db()
        self.assertEqual((request.status, request.decided_by), ("rechazada", self.admin))
        self.assertTrue(AuditEvent.objects.filter(action="solicitud_rechazada").exists())
        self.assertFalse(User.objects.filter(email="raul.nuevo@ejemplo.mx").exists())
        again = self.client.post(self.url("access_request_reject", request.pk))
        self.assertIn("Esta solicitud ya se atendió.", [str(m) for m in again.wsgi_request._messages])
        self.assertEqual(self.approve(request).status_code, 302)
        self.assertFalse(User.objects.filter(email="raul.nuevo@ejemplo.mx").exists())

    def test_a_request_of_another_event_is_not_reachable_from_this_one(self):
        other = AccessRequest.objects.create(event=self.other_event, full_name="Ajena", email="ajena@ejemplo.mx", requested_role="tutor")
        self.login(self.admin)
        self.assertEqual(self.client.post(self.url("access_request_approve", other.pk), {"role": "tutor"}).status_code, 404)
        self.assertEqual(self.client.post(self.url("access_request_reject", other.pk)).status_code, 404)

    def test_only_the_administration_decides(self):
        request = self.pending()
        for user in (self.pastor, self.care, self.tutor1, self.coord1, self.outsider):
            client = self.login(user, Client())
            self.assertEqual(client.post(self.url("access_request_approve", request.pk), {"role": "administrador"}).status_code, 403, user)
            self.assertEqual(client.post(self.url("access_request_reject", request.pk)).status_code, 403, user)
            self.assertEqual(client.post(self.url("join_code_regenerate")).status_code, 403, user)
            self.assertEqual(client.post(self.url("join_by_name_enable")).status_code, 403, user)
            self.assertEqual(client.get(self.url("join_code_sign")).status_code, 403, user)
        anonymous = Client().post(self.url("access_request_approve", request.pk), {"role": "administrador"})
        self.assertEqual(anonymous.status_code, 302)
        self.assertEqual(AccessRequest.objects.get(pk=request.pk).status, "pendiente")
        self.assertEqual(Event.objects.get(pk=self.event.pk).join_code, self.event.join_code)

    def test_the_actions_only_answer_to_post(self):
        request = self.pending()
        self.login(self.admin)
        for name, args in (("access_request_approve", [request.pk]), ("access_request_reject", [request.pk]), ("join_code_regenerate", []),
                           ("join_by_name_enable", [])):
            self.assertEqual(self.client.get(self.url(name, *args)).status_code, 405, name)
        self.assertEqual(AccessRequest.objects.get(pk=request.pk).status, "pendiente")

    def test_the_new_audit_actions_can_be_filtered(self):
        for action in ("solicitud_acceso", "solicitud_aprobada", "solicitud_rechazada"):
            self.assertIn(action, ACTION_LABELS)

    def test_the_code_to_print_is_on_equipo(self):
        self.login(self.admin)
        response = self.client.get(self.url("team"))
        code = self.event.join_code
        self.assertContains(response, f"Para unirse al equipo: entra a http://testserver/unirme/ y escribe el código <strong>{code}</strong> o el nombre corto <strong>campamento-a</strong>.", html=False)
        self.assertContains(response, f'<input class="input" id="enlace-evento" value="http://testserver/e/{code}/" readonly>', html=False)
        self.assertContains(response, 'data-copy="enlace-evento"')
        self.assertContains(response, f'action="{self.url("join_code_regenerate")}"')
        self.assertContains(response, 'data-confirm="El código anterior y el nombre corto dejarán de funcionar."')
        self.assertContains(response, f'href="{self.url("join_code_sign")}"')

    def test_a_new_code_replaces_the_old_one(self):
        old = self.event.join_code
        self.login(self.admin)
        response = self.client.post(self.url("join_code_regenerate"))
        new = Event.objects.get(pk=self.event.pk).join_code
        self.assertNotEqual(new, old)
        self.assertRedirects(response, self.url("team") + "#codigo", fetch_redirect_response=False)
        row = AuditEvent.objects.get(action="evento_configurado")
        self.assertEqual(row.summary, "Generó un código nuevo para unirse")
        self.assertEqual(Client().get(reverse("event_entry", args=[old])).status_code, 302)
        self.assertEqual(Client().get(reverse("event_entry", args=[new])).status_code, 200)

    def test_the_printable_page(self):
        self.login(self.admin)
        response = self.client.get(self.url("join_code_sign"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('<h1 class="voice page-head__title">Campamento A</h1>', html)
        self.assertEqual(html.count("<h1"), 1)
        self.assertIn(f'<p class="voice join-sheet__code">{self.event.join_code}</p>', html)
        self.assertIn('<p class="join-sheet__url">http://testserver/unirme/</p>', html)
        self.assertEqual(html.count("<li>", html.index("join-sheet__steps")), 3)
        self.assertEqual(len(SIGN.findall(html)), 0)
        self.assertIn("data-print", html)
        self.assertIn('aria-current="page"', html.split('href="' + self.url("team") + '"', 1)[1][:40])  # Equipo stays marked


# ---- Admin home: the sign ----------------------------------------------------------------------


class HomeSignTests(JoinFixture):
    def test_pending_requests_take_the_sign_when_nothing_else_blocks(self):
        self.pending()
        self.login(self.admin)
        for response in (self.client.get(self.url("admin_home")), self.client.get(self.url("admin_home"), HTTP_X_REQUESTED_WITH="fetch")):
            html = response.content.decode()
            self.assertEqual(len(SIGN.findall(html)), 1)
            self.assertIn('<h2 id="que-sigue" class="sign__title">1 persona pide acceso al equipo</h2>', html)
            self.assertIn("Revisa quién es y agrégala con su rol.", html)
            self.assertIn(f'href="{self.url("team")}#solicitudes">Ver solicitudes</a>', html)

    def test_the_sign_counts_in_plural(self):
        self.pending()
        self.pending(email="otra@ejemplo.mx")
        self.login(self.admin)
        response = self.client.get(self.url("admin_home"))
        self.assertContains(response, "2 personas piden acceso al equipo")
        self.assertContains(response, "Revisa quiénes son y agrégalas con su rol.")

    def test_decided_requests_do_not_raise_the_sign(self):
        AccessRequest.objects.filter(pk=self.pending().pk).update(status=AccessRequest.Status.APPROVED)
        self.login(self.admin)
        self.assertEqual(len(SIGN.findall(self.client.get(self.url("admin_home")).content.decode())), 0)

    def test_the_sign_goes_after_procedures_and_before_campers_without_a_coordinator(self):
        self.pending()
        EventParticipant.objects.create(event=self.event, number=99, full_name="Rocío Sin Coordinador", age=17, room="9", church_group="Iglesia Centro")
        self.login(self.admin)
        response = self.client.get(self.url("admin_home"))
        self.assertEqual(len(SIGN.findall(response.content.decode())), 1)
        self.assertContains(response, "1 persona pide acceso al equipo")
        self.assertNotContains(response, "Asigna coordinador a")
        self.event.procedures_approved_at = None
        self.event.save(update_fields=["procedures_approved_at"])
        response = self.client.get(self.url("admin_home"))
        self.assertEqual(len(SIGN.findall(response.content.decode())), 1)
        self.assertContains(response, "Confirma los procedimientos de la iglesia")
        self.assertNotContains(response, "pide acceso al equipo")


# ---- Getting there -----------------------------------------------------------------------------


class EntryRedirectTests(JoinFixture):
    def volunteer_in(self, *events):
        user = make_user("voluntaria@prueba.mx", "Vera", "Voluntaria")
        for event in events:
            EventMembership.objects.create(event=event, user=user, role=MembershipRole.COORDINATOR)
        return self.login(user, Client())

    def test_the_only_event_in_course_opens_even_with_closed_ones(self):
        Event.objects.filter(pk=self.other_event.pk).update(status=Event.Status.CLOSED)
        client = self.volunteer_in(self.event, self.other_event)
        response = client.get(reverse("events:list"))
        self.assertRedirects(response, self.url("home"), fetch_redirect_response=False)
        self.assertEqual(client.get(reverse("events:list") + "?todos=1").status_code, 200)

    def test_two_events_in_course_are_listed(self):
        client = self.volunteer_in(self.event, self.other_event)
        response = client.get(reverse("events:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tus eventos")

    def test_a_single_event_in_preparation_still_opens(self):
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.DRAFT)
        client = self.volunteer_in(self.event)
        self.assertRedirects(client.get(reverse("events:list")), self.url("home"), fetch_redirect_response=False)

    def test_sign_in_and_the_empty_list_point_to_the_join_page(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, f'¿Vas a ayudar en un evento? <a href="{reverse("join")}">Únete con el código del evento</a>. Si ya formas parte del equipo, entra con tu correo.', html=False)
        self.login(make_user("sin-evento@prueba.mx", "Sara", "Sola"))
        response = self.client.get(reverse("events:list") + "?todos=1")
        self.assertContains(response, "Aún no formas parte de ningún evento")
        self.assertContains(response, f'href="{reverse("join")}"')
        self.assertNotContains(response, 'class="sign"')


class RequestAgeTests(JoinFixture):
    def test_the_list_says_how_long_ago_someone_asked(self):
        request = self.pending()
        AccessRequest.objects.filter(pk=request.pk).update(created_at=timezone.now() - timedelta(hours=3))
        self.login(self.admin)
        self.assertContains(self.client.get(self.url("team")), "raul.nuevo@ejemplo.mx, pidió ser tutor, hace 3 h")


class MemberAddUnchangedTests(JoinFixture):
    """"Agregar persona" after its shared part moved to services/team.py: same rows, audits, e-mail and messages."""

    def audits(self):
        return list(AuditEvent.objects.filter(event=self.event).order_by("id").values_list("action", "summary"))

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_a_new_person_gets_an_account_without_password_and_the_link(self):
        self.login(self.admin)
        response = self.client.post(self.url("member_add"), {
            "full_name": "Nora Nueva Ruiz", "email": "Nora@Nueva.mx", "role": "coordinador", "tutor": self.m_tutor1.pk, "team_name": "Ignorado",
        })
        user = User.objects.get(email="nora@nueva.mx")
        self.assertEqual((user.username, user.first_name, user.last_name, user.is_staff), ("nora@nueva.mx", "Nora", "Nueva Ruiz", False))
        self.assertFalse(user.has_usable_password())
        membership = EventMembership.objects.get(event=self.event, user=user)
        self.assertEqual((membership.role, membership.team_name, membership.can_view_sensitive, membership.created_by),
                         ("coordinador", "", False, self.admin))
        self.assertEqual(TutorAssignment.objects.get(coordinator=membership).tutor, self.m_tutor1)
        self.assertEqual(self.audits(), [("integrante_agregado", "Agregó a Nora Nueva Ruiz como coordinador")])
        self.assertTemplateUsed(response, "events/admin/member_created.html")
        self.assertContains(response, "Nora Nueva Ruiz ya forma parte del equipo. Le enviamos un correo para crear su contraseña.")
        link = response.context["link"]
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Te invitaron a Campamento A en Emmaus")
        self.assertEqual(mail.outbox[0].body, f"Hola Nora:\n\nTe agregaron al equipo de Campamento A. Crea tu contraseña aquí:\n{link}\n\nSi no esperabas este correo, ignóralo.")

    def test_when_the_email_fails_the_message_says_to_copy_the_link(self):
        self.login(self.admin)
        with mock.patch("events.views.admin.send_email_safely", return_value=False):
            response = self.client.post(self.url("member_add"), {"full_name": "Nora Nueva", "email": "nora@nueva.mx", "role": "tutor"})
        self.assertContains(response, "Nora Nueva ya forma parte del equipo. Copia el enlace para que cree su contraseña.")

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_an_existing_account_keeps_its_password_and_goes_back_to_equipo(self):
        make_user("ya@cuenta.mx", "Yolanda", "Antes")
        self.login(self.admin)
        response = self.client.post(self.url("member_add"), {
            "full_name": "Otro Nombre", "email": "YA@cuenta.mx", "role": "tutor", "team_name": "Equipo Este", "can_view_sensitive": "on",
        })
        self.assertRedirects(response, self.url("team"), fetch_redirect_response=False)
        self.assertEqual([str(m) for m in response.wsgi_request._messages], ["Yolanda Antes ya forma parte del equipo. Puede entrar con su contraseña de siempre."])
        membership = EventMembership.objects.get(event=self.event, user__email="ya@cuenta.mx")
        self.assertEqual((membership.role, membership.team_name, membership.can_view_sensitive), ("tutor", "Equipo Este", True))
        self.assertEqual(self.audits(), [
            ("integrante_agregado", "Agregó a Yolanda Antes como tutor"),
            ("acceso_confidencial", "Otorgó acceso confidencial a Yolanda Antes"),
        ])
        self.assertEqual(len(mail.outbox), 0)


# ---- Refuter round: who the request names, lengths, blank codes, revoking, abuse bounds ----------


class ExistingAccountTests(JoinFixture):
    """A request carries an e-mail anybody could type. An account that already has access is never changed by one."""

    def row(self, request):
        self.login(self.admin)
        html = self.client.get(self.url("team")).content.decode()
        return html.split(f'id="rol-{request.pk}"', 1)[0].rsplit('<li class="row', 1)[1]

    def church_pastor(self):
        pastor = make_user("pastora@prueba.mx", "Paula", "Pastora")
        assign_role(pastor, self.church, Role.PASTOR)
        return pastor

    def test_the_row_says_whose_account_the_email_is_and_what_it_can_do_here(self):
        self.church_pastor()
        request = self.pending(email="Pastora@Prueba.mx", name="Juan Desconocido", role=MembershipRole.COORDINATOR)
        self.assertIn("Ya tiene cuenta: Paula Pastora; acceso actual en este evento: pastor; iglesia: Iglesia de prueba", self.row(request))
        new = self.pending(email="nadie@nuevo.mx", name="Nadie Nuevo")
        self.assertNotIn("Ya tiene cuenta", self.row(new))

    def test_the_row_names_another_church_or_none(self):
        other = Church.objects.create(name="Iglesia Vecina", slug="iglesia-vecina")
        foreign = make_user("vecina@otra.mx", "Vera", "Vecina")
        assign_role(foreign, other, Role.CHURCH_ADMIN)
        loose = make_user("suelto@prueba.mx", "Saúl", "Suelto")
        a = self.pending(email="vecina@otra.mx", name="Vera Vecina")
        b = self.pending(email="suelto@prueba.mx", name="Saúl Suelto")
        self.assertIn("Ya tiene cuenta: Vera Vecina; acceso actual en este evento: ninguno; iglesia: otra iglesia", self.row(a))
        self.assertIn("Ya tiene cuenta: Saúl Suelto; acceso actual en este evento: ninguno; iglesia: ninguna", self.row(b))
        self.assertIsNotNone(loose)

    def assert_refused(self, request, name, role, response):
        request.refresh_from_db()
        self.assertEqual((request.status, request.membership, request.decided_by), ("rechazada", None, self.admin))
        self.assertEqual(request.decision_note, f"Ya tenía acceso como {role}.")
        self.assertEqual([str(m) for m in response.wsgi_request._messages],
                         [f"{name} ya tiene acceso a este evento como {role}. Si quieres cambiar su rol, hazlo en Equipo, Editar."])
        self.assertTrue(AuditEvent.objects.filter(action="solicitud_rechazada", object_id=str(request.pk)).exists())
        self.assertFalse(AuditEvent.objects.filter(action="integrante_agregado").exists())

    def test_a_church_pastor_without_membership_is_not_demoted(self):
        pastor = self.church_pastor()
        request = self.pending(email="pastora@prueba.mx", name="Juan Desconocido", role=MembershipRole.COORDINATOR)
        self.login(self.admin)
        response = self.client.post(self.url("access_request_approve", request.pk), {"role": "coordinador"})
        self.assert_refused(request, "Paula Pastora", "pastor", response)
        self.assertFalse(EventMembership.objects.filter(user=pastor).exists())
        self.assertEqual(resolve_access(pastor, self.event).role, "pastor")

    def test_a_superuser_is_not_touched(self):
        root = User.objects.create_superuser("root@prueba.mx", "root@prueba.mx", PASSWORD, first_name="Raíz", last_name="Total")
        request = self.pending(email="root@prueba.mx", name="Nombre Falso")
        self.login(self.admin)
        response = self.client.post(self.url("access_request_approve", request.pk), {"role": "coordinador"})
        self.assert_refused(request, "Raíz Total", "administrador", response)
        self.assertFalse(EventMembership.objects.filter(user=root).exists())

    def test_a_church_admin_is_not_touched(self):
        request = self.pending(email="admin@prueba.mx", name="Otro Falso")
        self.login(self.admin)
        response = self.client.post(self.url("access_request_approve", request.pk), {"role": "coordinador"})
        self.assert_refused(request, "Ada Admin", "administrador", response)
        self.assertFalse(EventMembership.objects.filter(user=self.admin, event=self.event).exists())

    def test_another_churchs_admin_has_no_access_here_and_can_be_added(self):
        other = Church.objects.create(name="Iglesia Vecina", slug="iglesia-vecina")
        foreign = make_user("vecina@otra.mx", "Vera", "Vecina")
        assign_role(foreign, other, Role.CHURCH_ADMIN)
        request = self.pending(email="vecina@otra.mx", name="Vera Vecina")
        self.assertIn("iglesia: otra iglesia", self.row(request))
        self.client.post(self.url("access_request_approve", request.pk), {"role": "tutor"})
        self.assertEqual(AccessRequest.objects.get(pk=request.pk).status, "aprobada")
        self.assertEqual(resolve_access(foreign, self.event).role, "tutor")

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_an_account_that_never_set_a_password_gets_the_link(self):
        person = User.objects.create_user(username="sin.clave@prueba.mx", email="sin.clave@prueba.mx", first_name="Sina", last_name="Clave")
        person.set_unusable_password()
        person.save()
        request = self.pending(email="sin.clave@prueba.mx", name="Sina Clave")
        self.login(self.admin)
        response = self.client.post(self.url("access_request_approve", request.pk), {"role": "tutor"})
        self.assertTemplateUsed(response, "events/admin/member_created.html")
        self.assertContains(response, "Sina Clave ya forma parte del equipo. Le enviamos un correo para crear su contraseña.")
        self.assertNotContains(response, "contraseña de siempre")
        self.assertEqual(mail.outbox[0].to, ["sin.clave@prueba.mx"])
        self.assertEqual(User.objects.filter(email="sin.clave@prueba.mx").count(), 1)

    def test_a_racing_account_creation_ends_in_one_membership(self):
        """Two approvals (or an approval and "Agregar persona") create the same account at once."""
        winner = User.objects.create_user(username="carrera@prueba.mx", email="carrera@prueba.mx", first_name="Cata", last_name="Carrera")
        winner.set_unusable_password()
        winner.save()
        request = self.pending(email="carrera@prueba.mx", name="Cata Carrera")
        self.login(self.admin)
        clash = IntegrityError('duplicate key value violates unique constraint "auth_user_username_key"')
        with mock.patch("events.services.team.account_for_email", side_effect=[None, None, winner]), \
                mock.patch.object(User.objects, "create_user", side_effect=clash):
            self.client.post(self.url("access_request_approve", request.pk), {"role": "tutor"})
        self.assertEqual(User.objects.filter(email="carrera@prueba.mx").count(), 1)
        self.assertEqual(EventMembership.objects.filter(event=self.event, user=winner).count(), 1)
        self.assertEqual(AccessRequest.objects.get(pk=request.pk).status, "aprobada")


class LengthTests(JoinFixture):
    def test_an_email_or_a_name_too_long_for_an_account_is_a_form_error(self):
        long_email = "a" * 64 + "@" + ".".join(["b" * 60] * 4) + ".mx"
        self.assertEqual(len(long_email), 311)
        response = Client(raise_request_exception=False).post(self.entry_url(), {"full_name": "Lalo Largo", "email": long_email, "requested_role": "tutor", "note": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Escribe un correo de 150 caracteres o menos.")
        response = Client().post(self.entry_url(), {"full_name": "N" * 157, "email": "nombre@largo.mx", "requested_role": "tutor", "note": ""})
        self.assertContains(response, "Escribe un nombre de 150 caracteres o menos.")
        self.assertFalse(AccessRequest.objects.exists())


class BlankCodeTests(JoinFixture):
    def test_an_event_without_a_code_gets_one_before_any_link_is_built(self):
        Event.objects.filter(pk=self.event.pk).update(join_code="")
        self.login(self.admin)
        for name in ("team", "join_code_sign"):
            Event.objects.filter(pk=self.event.pk).update(join_code="")
            response = self.client.get(self.url(name))
            self.assertEqual(response.status_code, 200, name)
            code = Event.objects.get(pk=self.event.pk).join_code
            self.assertRegex(code, r"^[A-Z2-9]{6}$")
            self.assertContains(response, code)

    def test_an_event_without_a_code_is_still_found_by_its_name(self):
        Event.objects.filter(pk=self.event.pk).update(join_code="")
        response = Client().post(reverse("join"), {"clave": "Campamento A"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Client().get(response["Location"]).status_code, 200)


class RevokeNameTests(JoinFixture):
    def test_a_new_code_also_stops_the_name(self):
        self.login(self.admin)
        response = self.client.post(self.url("join_code_regenerate"))
        code = Event.objects.get(pk=self.event.pk).join_code
        self.assertEqual([str(m) for m in response.wsgi_request._messages],
                         [f"El código anterior y el nombre del evento dejan de funcionar. Ahora solo entra el código nuevo: {code}."])
        self.assertEqual(Client().get(reverse("event_entry", args=["campamento-a"])).status_code, 302)
        self.assertContains(Client().post(reverse("join"), {"clave": "Campamento A"}), NOT_FOUND)
        self.assertEqual(Client().get(reverse("event_entry", args=[code])).status_code, 200)
        team = self.client.get(self.url("team"))
        self.assertContains(team, f"Para unirse al equipo: entra a http://testserver/unirme/ y escribe el código <strong>{code}</strong>.", html=False)
        self.assertNotContains(team, "o el nombre <strong>")
        self.assertContains(team, "Volver a permitir el nombre")
        self.assertContains(team, 'data-confirm="El código anterior dejará de funcionar."')
        sheet = self.client.get(self.url("join_code_sign")).content.decode()
        self.assertNotIn("campamento-a.", sheet)
        self.assertIn("<li>Escribe el código.</li>", sheet)

    def test_the_name_can_be_allowed_again(self):
        Event.objects.filter(pk=self.event.pk).update(join_by_name=False)
        self.assertIsNone(find_event_by_key("Campamento A"))
        self.login(self.admin)
        response = self.client.post(self.url("join_by_name_enable"))
        self.assertRedirects(response, self.url("team") + "#codigo", fetch_redirect_response=False)
        self.assertTrue(Event.objects.get(pk=self.event.pk).join_by_name)
        self.assertEqual(find_event_by_key("Campamento A"), self.event)
        self.assertEqual(Client().get(reverse("event_entry", args=["campamento-a"])).status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="evento_configurado", summary="Volvió a permitir unirse con el nombre del evento").exists())
        self.assertNotContains(self.client.get(self.url("team")), "Volver a permitir el nombre")


class LookupNormalizationTests(JoinFixture):
    def test_accents_spaces_and_the_events_real_name(self):
        jov = Event.objects.create(church=self.church, name="Campamento de Jóvenes 2026", slug="jovenes-2026")
        link = Event.objects.create(church=self.church, name="Link Camp 2026", slug="link_camp_2026")
        for typed in ("Jóvenes 2026", "JÓVENES-2026", "Campamento de Jóvenes 2026", "campamento de jovenes 2026"):
            self.assertEqual(find_event_by_key(typed), jov, typed)
        for typed in ("link camp-2026", "link_camp 2026", "Link Camp 2026", "link_camp_2026"):
            self.assertEqual(find_event_by_key(typed), link, typed)

    def test_ties_go_to_the_oldest_event_and_an_exact_slug_wins(self):
        first = Event.objects.create(church=self.church, name="Mi Camp", slug="mi-camp")
        second = Event.objects.create(church=self.church, name="Otro", slug="mi_camp")
        self.assertEqual(find_event_by_key("mi camp"), first)
        self.assertEqual(find_event_by_key("MI CAMP"), first)
        self.assertEqual(find_event_by_key("mi_camp"), second)

    def test_a_code_typed_in_full_width_characters(self):
        wide = "".join(chr(ord(c) + 0xFEE0) for c in self.event.join_code)
        self.assertEqual(find_event_by_key(wide), self.event)


class ClosedEventTests(JoinFixture):
    def test_a_closed_event_takes_no_requests(self):
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.CLOSED)
        page = self.client.get(self.entry_url())
        self.assertContains(page, "Este evento ya terminó.")
        self.assertNotContains(page, "Pedir acceso")
        self.assertNotContains(page, "<form")
        response = self.ask()
        self.assertContains(response, "Este evento ya terminó.")
        self.assertFalse(AccessRequest.objects.exists())
        # /unirme/ finds it, and the page says it is over
        self.assertRedirects(Client().post(reverse("join"), {"clave": self.event.join_code}), self.entry_url(), fetch_redirect_response=False)

    def test_an_event_in_preparation_takes_requests(self):
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.DRAFT)
        self.assertContains(self.ask(), ANSWER)
        self.assertEqual(AccessRequest.objects.count(), 1)


class JoinAbuseTests(JoinFixture):
    def test_the_request_list_has_a_cap(self):
        AccessRequest.objects.bulk_create(
            AccessRequest(event=self.event, full_name=f"P {n}", email=f"p{n}@e.mx", requested_role="tutor") for n in range(50)
        )
        response = self.ask(email="cincuenta-y-uno@e.mx")
        self.assertContains(response, "La lista de solicitudes está llena. Pide a la administración que te agregue directamente.")
        self.assertContains(response, "Hay un problema")
        self.assertEqual(AccessRequest.objects.count(), 50)
        AccessRequest.objects.filter(email="p0@e.mx").update(status=AccessRequest.Status.REJECTED)
        self.assertContains(self.ask(Client(), email="cincuenta-y-uno@e.mx"), ANSWER)  # decided ones free a place

    def test_misses_on_the_event_page_spend_the_search_budget(self):
        client = Client()
        for n in range(10):
            client.get(reverse("event_entry", args=[f"ZZZZ{n:02d}"]))
        self.assertTrue(AuthThrottle.objects.filter(key=f"join:{client.session.session_key}").exists())
        blocked = client.get(reverse("event_entry", args=["ZZZZ99"]), follow=True)
        self.assertContains(blocked, "Demasiados intentos")
        # While blocked, an existing code answers exactly like a missing one: no oracle
        hit = client.get(self.entry_url())
        self.assertEqual(hit.status_code, 302)
        self.assertTrue(hit["Location"].startswith(reverse("join")))
        self.assertContains(client.get(hit["Location"]), "Demasiados intentos")
        self.assertContains(client.post(reverse("join"), {"clave": self.event.join_code}), "Demasiados intentos")
        # Another browser is not affected, and its hits cost nothing
        other = Client()
        self.assertEqual(other.get(self.entry_url()).status_code, 200)
        self.assertEqual(AuthThrottle.objects.count(), 1)

    def test_expired_throttle_rows_are_purged_and_live_ones_kept(self):
        from events.services import throttle

        now = timezone.now()
        old = now - timedelta(hours=2)
        AuthThrottle.objects.bulk_create([
            AuthThrottle(key="viejo", attempts=3, window_started_at=old),
            AuthThrottle(key="viejo-bloqueo-terminado", attempts=9, window_started_at=old, blocked_until=now - timedelta(minutes=1)),
            AuthThrottle(key="bloqueado", attempts=9, window_started_at=old, blocked_until=now + timedelta(minutes=5)),
            AuthThrottle(key="reciente", attempts=2, window_started_at=now - timedelta(minutes=10)),
        ])
        self.assertEqual(throttle.purge_expired(now), 2)
        self.assertEqual(sorted(AuthThrottle.objects.values_list("key", flat=True)), ["bloqueado", "reciente"])

    def test_the_scheduler_purges_old_throttles_and_old_decided_requests(self):
        from django.core.management import call_command

        now = timezone.now()
        AuthThrottle.objects.create(key="join:viejo", attempts=4, window_started_at=now - timedelta(hours=3))
        old = self.pending(email="viejo@e.mx")
        recent = self.pending(email="reciente@e.mx")
        waiting = self.pending(email="esperando@e.mx")
        AccessRequest.objects.filter(pk=old.pk).update(status="rechazada", decided_at=now - timedelta(days=31))
        AccessRequest.objects.filter(pk=recent.pk).update(status="aprobada", decided_at=now - timedelta(days=29))
        AccessRequest.objects.filter(pk=waiting.pk).update(created_at=now - timedelta(days=90))
        call_command("run_report_cutoffs", verbosity=0)
        self.assertFalse(AuthThrottle.objects.filter(key="join:viejo").exists())
        self.assertEqual(sorted(AccessRequest.objects.values_list("email", flat=True)), ["esperando@e.mx", "reciente@e.mx"])
