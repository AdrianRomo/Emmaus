"""The welcome of each role (core/onboarding.py, events/services/welcome.py, partials/ui/welcome.html).

Someone new reads, on their role's home, where they are, what they can do, what is missing and what to do next, with
"Primeros pasos" taken from the live data. "Entendido" puts it away for that role, for good, server side
(core.OnboardingState); "Mi cuenta" brings it back. People who were already working in Emmaus when it arrived never
meet it for the roles they had (the data step of core/0011).
"""

from __future__ import annotations

import re

from django.apps import apps
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import OnboardingState
from core.onboarding import KEYS, dismiss, is_dismissed
from core.tests import EmmausFixture
from events.models import EventMembership, FollowUp, MembershipRole
from events.tests.base import CampFixture, make_user
from events.tests.test_ui import ENGLISH, analyse

WELCOME = re.compile(r'<section class="welcome" id="bienvenida"[^>]*data-welcome="([^"]+)">(.*?)</section>', re.DOTALL)


def welcome_of(html):
    """(key, html) of the one welcome on the page, or None."""
    found = WELCOME.findall(html)
    assert len(found) <= 1, "one welcome at most"
    return found[0] if found else None


def steps_of(block):
    """[(mark, title)] of "Primeros pasos"."""
    return re.findall(r'<span class="status status--(\w+)">.*?</span>(?:(?!</li>).)*?<p class="welcome__step-title">(.*?)</p>', block, re.DOTALL)


class WelcomeOnEachHomeTests(CampFixture):
    def test_a_new_coordinator_reads_the_welcome_on_mi_inicio_and_not_in_the_live_fragment(self):
        self.login(self.coord1)
        page = self.client.get(self.url("coord_home")).content.decode()
        key, block = welcome_of(page)
        self.assertEqual(key, "rol:coordinador")
        self.assertIn("Te damos la bienvenida, Carla.", block)
        self.assertIn("En Campamento A eres coordinador.", block)
        self.assertIn("Lo que puedes hacer aquí", block)
        self.assertIn("Primeros pasos", block)
        # The welcome frames the sign; it never replaces it
        self.assertIn('<section class="sign" aria-labelledby="que-hago">', page)
        self.assertLess(page.index('id="bienvenida"'), page.index('aria-labelledby="que-hago"'))
        fragment = self.client.get(self.url("coord_home"), HTTP_X_REQUESTED_WITH="fetch").content.decode()
        self.assertIsNone(welcome_of(fragment))

    def test_the_coordinator_steps_come_from_their_own_camperos_only(self):
        self.login(self.coord1)
        self.choose_station()
        _, block = welcome_of(self.client.get(self.url("coord_home")).content.decode())
        steps = steps_of(block)
        self.assertEqual([s[1] for s in steps], [
            "Elige tu computadora", "Ten camperos asignados",
            "Registra la autorización de tus camperos menores de edad", "Completa tu primera entrevista",
        ])
        self.assertEqual([s[0] for s in steps], ["done", "done", "pending", "pending"])
        self.assertIn("1 sin autorización registrada", block)  # Lucía, 13, no record; Mariana, 15, has one
        self.assertIn(f'href="{self.url("my_participants")}?autorizacion=falta"', block)
        self.assertNotIn(self.p4.full_name, block)
        self.assertNotIn(self.p3.full_name, block)
        # The coordinator's screens never name the leaders' screens
        self.assertNotIn("Reportes", block)
        self.assertNotIn("Configuración", block)
        # Coordinator 3 has one minor, with the record: the step is done
        self.login(self.coord3)
        _, block = welcome_of(self.client.get(self.url("coord_home")).content.decode())
        self.assertIn(("done", "Registra la autorización de tus camperos menores de edad"), steps_of(block))

    def test_without_a_computer_the_step_points_at_the_line_above_instead_of_a_second_link(self):
        self.login(self.coord1)
        page = self.client.get(self.url("coord_home")).content.decode()
        _, block = welcome_of(page)
        self.assertIn(("pending", "Elige tu computadora"), steps_of(block))
        self.assertIn("arriba de este recuadro", block)
        self.assertEqual(page.count("Elegir computadora"), 1)

    def test_a_tutor_reads_their_team_and_a_pastor_what_waits(self):
        self.login(self.tutor1)
        _, block = welcome_of(self.client.get(self.url("tutor_home")).content.decode())
        self.assertIn("eres tutor de Equipo Norte", block)
        self.assertIn("Supervisas a 2.", block)
        self.assertEqual([s[0] for s in steps_of(block)], ["done", "done"])
        FollowUp.objects.create(event=self.event, participant=self.p1, kind=FollowUp.Kind.CARE, reason=FollowUp.Reason.TALK_TO_LEADER, created_by=self.coord1)
        self.login(self.pastor)
        _, block = welcome_of(self.client.get(self.url("pastor_home")).content.decode())
        self.assertIn("En Campamento A eres pastor.", block)
        self.assertIn(("pending", "Cada seguimiento tiene responsable"), steps_of(block))
        self.assertIn("1 sin responsable.", block)
        self.assertIn(f'href="{self.url("follow_ups")}?responsable=ninguno"', block)

    def test_the_administration_welcome_frames_preparacion_without_repeating_it(self):
        self.login(self.admin)
        page = self.client.get(self.url("admin_home")).content.decode()
        key, block = welcome_of(page)
        self.assertEqual(key, "rol:administrador")
        self.assertRegex(block, r"“Preparación”, aquí abajo, dice (qué falta: \d de \d pasos listos|que los \d pasos están listos)\.")
        self.assertNotIn("Primeros pasos", block)  # the checklist is "Preparación", once
        self.assertIn('<h2 id="preparacion" class="heading">Preparación</h2>', page)

    def test_an_administrador_reading_the_pastor_summary_gets_no_pastor_welcome(self):
        self.login(self.admin)
        self.assertIsNone(welcome_of(self.client.get(self.url("pastor_home")).content.decode()))

    def test_every_welcome_is_spanish(self):
        for user, name in ((self.coord1, "coord_home"), (self.tutor1, "tutor_home"), (self.pastor, "pastor_home"), (self.admin, "admin_home")):
            self.login(user)
            _, block = welcome_of(self.client.get(self.url(name)).content.decode())
            text = " ".join(analyse(block).text)
            self.assertIsNone(ENGLISH.search(text), name)


class EntendidoTests(CampFixture):
    def test_entendido_puts_the_welcome_away_for_good_across_sessions(self):
        self.login(self.coord1)
        response = self.client.post(reverse("welcome_dismiss"), {"key": "rol:coordinador", "siguiente": self.url("coord_home")})
        self.assertRedirects(response, self.url("coord_home"))
        self.assertIsNone(welcome_of(self.client.get(self.url("coord_home")).content.decode()))
        self.assertTrue(is_dismissed(self.coord1, "rol:coordinador"))
        other = self.login(self.coord1, Client())  # a new session, another day
        self.assertIsNone(welcome_of(other.get(self.url("coord_home")).content.decode()))
        self.assertEqual(OnboardingState.objects.filter(user=self.coord1).count(), 1)

    def test_the_pages_own_fetch_gets_204(self):
        self.login(self.coord1)
        response = self.client.post(reverse("welcome_dismiss"), {"key": "rol:coordinador"}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(response.status_code, 204)
        self.assertTrue(is_dismissed(self.coord1, "rol:coordinador"))

    def test_only_known_keys_and_only_post(self):
        self.login(self.coord1)
        self.assertEqual(self.client.post(reverse("welcome_dismiss"), {"key": "rol:superusuario"}).status_code, 400)
        self.assertEqual(self.client.get(reverse("welcome_dismiss")).status_code, 405)
        self.assertEqual(self.client.post(reverse("welcome_restore"), {"key": "x"}).status_code, 400)
        self.assertEqual(OnboardingState.objects.count(), 0)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("welcome_dismiss"), {"key": "rol:coordinador"}).status_code, 302)

    def test_a_foreign_way_back_falls_back_to_the_dashboard(self):
        self.login(self.coord1)
        response = self.client.post(reverse("welcome_dismiss"), {"key": "rol:coordinador", "siguiente": "https://otro.sitio/x"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("dashboard"))

    def test_mi_cuenta_lists_what_was_put_away_and_brings_it_back(self):
        self.login(self.coord1)
        dismiss(self.coord1, "rol:coordinador")
        page = self.client.get(reverse("account")).content.decode()
        self.assertIn("La bienvenida de coordinador", page)
        self.assertIn('name="key" value="rol:coordinador"', page)
        response = self.client.post(reverse("welcome_restore"), {"key": "rol:coordinador"})
        self.assertRedirects(response, reverse("account"))
        self.assertFalse(is_dismissed(self.coord1, "rol:coordinador"))
        self.assertIsNotNone(welcome_of(self.client.get(self.url("coord_home")).content.decode()))

    def test_each_role_has_its_own_welcome_for_someone_with_several(self):
        """Coordinador in A, tutor in B: putting away the first leaves the second."""
        EventMembership.objects.create(event=self.other_event, user=self.coord1, role=MembershipRole.TUTOR)
        self.login(self.coord1)
        dismiss(self.coord1, "rol:coordinador")
        self.assertIsNone(welcome_of(self.client.get(self.url("coord_home")).content.decode()))
        page = self.client.get(reverse("events:tutor_home", args=[self.other_event.slug])).content.decode()
        self.assertEqual(welcome_of(page)[0], "rol:tutor")
        # A church admin: one welcome on the event list (the church role), another inside an event (administrador)
        self.login(self.admin)
        self.assertEqual(welcome_of(self.client.get(reverse("events:list") + "?todos=1").content.decode())[0], "iglesia:church_admin")
        self.assertEqual(welcome_of(self.client.get(self.url("admin_home")).content.decode())[0], "rol:administrador")
        dismiss(self.admin, "iglesia:church_admin")
        self.assertIsNone(welcome_of(self.client.get(reverse("events:list") + "?todos=1").content.decode()))
        self.assertIsNotNone(welcome_of(self.client.get(self.url("admin_home")).content.decode()))


class NoRoleAndNoNameTests(CampFixture):
    def test_someone_without_a_role_reads_how_to_get_one(self):
        nobody = make_user("nadie@prueba.mx", "Nadia", "Nadie")
        self.login(nobody)
        page = self.client.get(reverse("events:list")).content.decode()
        key, block = welcome_of(page)
        self.assertEqual(key, "sin-rol")
        self.assertIn("Todavía no tienes un rol", block)
        self.assertIn(f'href="{reverse("join")}"', block)
        self.assertIn("Aún no formas parte de ningún evento", page)

    def test_an_account_without_a_name_is_asked_for_it_first(self):
        user = make_user("sin-nombre@prueba.mx", "", "")
        EventMembership.objects.create(event=self.event, user=user, role=MembershipRole.COORDINATOR)
        self.login(user)
        page = self.client.get(self.url("coord_home")).content.decode()
        self.assertIn('<h1 class="voice coord__hello">Hola.</h1>', page)
        _, block = welcome_of(page)
        self.assertIn("Te damos la bienvenida.", block)
        self.assertEqual(steps_of(block)[0], ("pending", "Escribe tu nombre"))
        self.assertIn(f'href="{reverse("account")}"', block)


class DiscipleshipWelcomeTests(EmmausFixture):
    def test_mentor_participant_and_church_roles_read_theirs(self):
        self.client.force_login(self.mentor)
        key, block = welcome_of(self.client.get(reverse("mentor_dashboard")).content.decode())
        self.assertEqual(key, "iglesia:mentor")
        self.assertIn(("done", "Ten participantes asignados"), steps_of(block))
        self.assertIn(("pending", "Responde la primera pregunta"), steps_of(block))
        self.client.force_login(self.participant)
        key, block = welcome_of(self.client.get(reverse("participant_dashboard")).content.decode())
        self.assertEqual(key, "iglesia:participant")
        self.assertIn(("pending", "Di cómo prefieres que te contacten"), steps_of(block))
        self.assertIn(("pending", "Termina tu primera lección"), steps_of(block))
        self.client.force_login(self.pastor)
        self.church.discipleship_enabled = True
        self.church.save()
        self.assertEqual(welcome_of(self.client.get(reverse("church_dashboard")).content.decode())[0], "iglesia:pastor")
        self.client.force_login(self.admin)
        self.assertEqual(welcome_of(self.client.get(reverse("church_dashboard")).content.decode())[0], "iglesia:church_admin")


class ExistingUsersTests(CampFixture):
    def test_the_data_step_puts_away_the_roles_people_already_held(self):
        from importlib import import_module

        step = import_module("core.migrations.0011_onboardingstate").put_away_for_existing_users
        root = make_user("raiz@prueba.mx", "Raúl", "Raíz")
        root.is_superuser = True
        root.save()
        for user in (self.coord1, self.tutor1, self.pastor, self.admin, self.outsider, root):
            user.last_login = timezone.now()
            user.save(update_fields=["last_login"])
        never = make_user("nunca@prueba.mx", "Nunca", "Entró")  # last_login None: shows the welcome when they do
        EventMembership.objects.create(event=self.event, user=never, role=MembershipRole.COORDINATOR)
        fresh = make_user("nueva@prueba.mx", "Nueva", "Cuenta")
        fresh.last_login = timezone.now()
        fresh.save(update_fields=["last_login"])
        step(apps, None)
        self.assertTrue(is_dismissed(self.coord1, "rol:coordinador"))
        self.assertFalse(is_dismissed(self.coord1, "rol:tutor"))  # a role received later shows its welcome once
        self.assertTrue(is_dismissed(self.tutor1, "rol:tutor"))
        self.assertTrue(is_dismissed(self.pastor, "rol:pastor"))
        self.assertTrue(is_dismissed(self.admin, "rol:administrador"))
        self.assertTrue(is_dismissed(self.admin, "iglesia:church_admin"))
        self.assertTrue(is_dismissed(self.outsider, "rol:tutor"))
        self.assertTrue(is_dismissed(root, "rol:administrador"))
        self.assertTrue(is_dismissed(fresh, "sin-rol"))
        self.assertFalse(is_dismissed(never, "rol:coordinador"))
        self.assertTrue(set(OnboardingState.objects.values_list("key", flat=True)) <= KEYS)
