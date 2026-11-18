"""Issue #50: the team CSV import. Every name and address here is invented (@prueba.mx, @ejemplo.invalid)."""

from __future__ import annotations

import io
import re
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import mail
from django.test import override_settings

from core.models import Church, Role, UserChurchRole
from core.services import assign_role
from events.models import AuditEvent, EventMembership, MembershipRole, TutorAssignment
from events.services import team_import as svc
from events.views.team_import import SESSION_KEY

from .base import CampFixture, make_user

HEADER = "Nombre,Correo,Rol,Equipo,Tutor\n"
TEAM_CSV = HEADER + (
    "Tania Vega Soto,Tania.Vega@Prueba.MX,tutor,Equipo Oriente,\n"
    "Carmen Ríos Paz,carmen.rios@prueba.mx,Coordinadora,,tania.vega@prueba.mx\n"
    "Camilo Peña Luz,camilo.pena@prueba.mx,coordinador,Equipo Oriente,\n"
    "Claudia Mora Gil,claudia.mora@prueba.mx,coordinador,,Teresa Uno\n"
    "Pedro Lara Rey,pedro.lara@prueba.mx,pastor,,\n"
)
NEW_EMAILS = ["tania.vega@prueba.mx", "carmen.rios@prueba.mx", "camilo.pena@prueba.mx", "claudia.mora@prueba.mx", "pedro.lara@prueba.mx"]


def upload(text: str, name: str = "equipo.csv", encoding: str = "utf-8"):
    file = io.BytesIO(text.encode(encoding))
    file.name = name
    return file


class TeamImportFixture(CampFixture):
    def review(self, text=TEAM_CSV, **kwargs):
        return self.client.post(self.url("team_import"), {"file": upload(text, **kwargs)}, follow=True)

    def confirm(self, *lines):
        return self.client.post(self.url("team_import"), {"action": "confirm", "include": [str(n) for n in lines]})

    def snapshot(self):
        return {
            "users": sorted(User.objects.values_list("email", "first_name", "last_name", "password")),
            "memberships": sorted(EventMembership.objects.values_list("event_id", "user_id", "role", "team_name", "can_view_sensitive", "is_active")),
            "assignments": sorted(TutorAssignment.objects.values_list("event_id", "tutor_id", "coordinator_id")),
            "church_roles": sorted(UserChurchRole.objects.values_list("user_id", "church_id", "role")),
            "audit": AuditEvent.objects.count(),
            "mail": len(mail.outbox),
        }

    def member(self, email):
        return EventMembership.objects.select_related("user").get(event=self.event, user__email=email)


class AccessTests(TeamImportFixture):
    def test_only_an_administrator_of_this_event_reaches_the_import(self):
        other_church = Church.objects.create(name="Otra iglesia", slug="otra-iglesia")
        other_admin = make_user("admin@otra.mx", "Otra", "Admin")
        assign_role(other_admin, other_church, Role.CHURCH_ADMIN)
        before = self.snapshot()
        for user in (other_admin, self.pastor, self.care, self.tutor1, self.coord1, self.outsider):
            self.login(user)
            for name in ("team_import", "team_import_sample"):
                self.assertEqual(self.client.get(self.url(name)).status_code, 403, f"{user.email} {name}")
            self.assertEqual(self.client.post(self.url("team_import"), {"file": upload(TEAM_CSV)}).status_code, 403)
            self.assertEqual(self.confirm(2, 3, 4, 5, 6).status_code, 403)
            self.client.logout()
        self.assertEqual(self.snapshot(), before)

    def test_anonymous_goes_to_sign_in(self):
        response = self.client.post(self.url("team_import"), {"file": upload(TEAM_CSV)})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/entrar", response["Location"])

    def test_an_event_administrator_without_a_church_role_can_import(self):
        boss = make_user("jefa@prueba.mx", "Julia", "Jefa")
        EventMembership.objects.create(event=self.event, user=boss, role=MembershipRole.ADMIN)
        self.login(boss)
        self.assertEqual(self.client.get(self.url("team_import")).status_code, 200)
        # ...and only in her event
        self.assertEqual(self.client.get(self.url("team_import").replace(self.event.slug, self.other_event.slug)).status_code, 403)

    def test_a_preview_of_another_event_is_never_confirmed_here(self):
        self.login(self.admin)
        self.review()
        session = self.client.session
        preview = session[SESSION_KEY]
        preview["event"] = self.other_event.id
        session[SESSION_KEY] = preview
        session.save()
        before = self.snapshot()
        self.confirm(2, 3, 4, 5, 6)
        self.assertEqual(self.snapshot(), before)

    def test_the_team_page_links_the_import_for_administrators_only(self):
        self.login(self.admin)
        self.assertContains(self.client.get(self.url("team")), self.url("team_import"))
        self.login(self.pastor)
        self.assertNotContains(self.client.get(self.url("team")), self.url("team_import"))


class PreviewTests(TeamImportFixture):
    def test_the_preview_writes_nothing_and_shows_what_will_be_created(self):
        self.login(self.admin)
        before = self.snapshot()
        response = self.review()
        self.assertEqual(self.snapshot(), before)
        self.assertContains(response, "Nada se guarda ni se envía hasta que confirmes")
        rows = response.context["rows"]
        self.assertEqual([(r.email, r.role, r.status) for r in rows],
                         [(NEW_EMAILS[0], "tutor", "nuevo"), (NEW_EMAILS[1], "coordinador", "nuevo"), (NEW_EMAILS[2], "coordinador", "nuevo"),
                          (NEW_EMAILS[3], "coordinador", "nuevo"), (NEW_EMAILS[4], "pastor", "nuevo")])
        self.assertEqual([r.tutor_label for r in rows], ["", "Tania Vega Soto", "Tania Vega Soto", "Teresa Uno", ""])
        self.assertEqual(response.context["summary"], {"total": 5, "new": 5, "unchanged": 0, "errors": 0})
        # GET again, still nothing
        self.client.get(self.url("team_import"))
        self.assertEqual(self.snapshot(), before)

    def test_row_errors_are_said_per_row_and_those_rows_cannot_be_marked(self):
        self.login(self.admin)
        text = HEADER + (
            ",sin.nombre@prueba.mx,tutor,,\n"
            "Rita Sin Correo,,tutor,,\n"
            "Raúl Mal Correo,raul(arroba)prueba.mx,tutor,,\n"
            "Rosa Sin Rol,rosa@prueba.mx,,,\n"
            "Rubén Rol Raro,ruben@prueba.mx,chofer,,\n"
            "Ramón Jefe,ramon@prueba.mx,Administrador,,\n"
            "Regina Uno,regina@prueba.mx,tutor,,\n"
            "Regina Dos,REGINA@prueba.mx,tutor,,\n"
            "Renata Sin Tutor,renata@prueba.mx,coordinador,,nadie@prueba.mx\n"
            "Rocío Sin Equipo,rocio@prueba.mx,coordinador,Equipo Fantasma,\n"
            "=HYPERLINK(\"http://x\"),formula@prueba.mx,tutor,,\n"
        )
        response = self.review(text)
        errors = {r.line: r.errors for r in response.context["rows"]}
        self.assertEqual(errors[2], ["Falta el nombre."])
        self.assertEqual(errors[3], ["Falta el correo."])
        self.assertIn("no es un correo válido", errors[4][0])
        self.assertEqual(errors[5], ["Falta el rol: coordinador, tutor o pastor."])
        self.assertEqual(errors[6], ["No conocemos el rol «chofer». Usa coordinador, tutor o pastor."])
        self.assertIn("no puede dar el rol de administrador", errors[7][0])
        self.assertEqual(errors[8], [])
        self.assertEqual(errors[9], ["El correo está repetido: ya aparece en la fila 8."])
        self.assertIn("No encontramos al tutor «nadie@prueba.mx»", errors[10][0])
        self.assertIn("No encontramos el equipo «Equipo Fantasma»", errors[11][0])
        self.assertEqual(errors[12], ["El nombre no puede empezar con =, +, - o @."])
        self.assertEqual(response.context["summary"], {"total": 11, "new": 1, "unchanged": 0, "errors": 10})
        self.assertEqual(response.content.decode().count('name="include"'), 1)
        # Marking them by hand does not import them either
        self.confirm(*range(2, 13))
        self.assertEqual(set(EventMembership.objects.filter(event=self.event, user__email__in=[
            "sin.nombre@prueba.mx", "ramon@prueba.mx", "formula@prueba.mx", "renata@prueba.mx", "regina@prueba.mx",
        ]).values_list("user__email", flat=True)), {"regina@prueba.mx"})
        self.assertFalse(User.objects.filter(email="ramon@prueba.mx").exists())

    def test_an_ambiguous_tutor_is_never_guessed(self):
        EventMembership.objects.filter(pk=self.m_tutor2.pk).update(team_name="Equipo Norte")
        self.login(self.admin)
        response = self.review(HEADER + "Cata Duda,cata@prueba.mx,coordinador,Equipo Norte,\n")
        self.assertIn("coincide con más de un tutor (Teresa Uno, Tomás Dos)", response.context["rows"][0].errors[0])

    def test_file_problems_stop_the_file_and_keep_nothing(self):
        self.login(self.admin)
        cases = [
            ("Nombre,Rol\nAna,tutor\n", "Faltan estas columnas: Correo."),
            (HEADER, "El archivo no tiene filas con datos."),
            (HEADER + "".join(f"Persona {n},p{n}@prueba.mx,tutor,,\n" for n in range(svc.MAX_TEAM_ROWS + 1)),
             f"El archivo tiene más de {svc.MAX_TEAM_ROWS} personas. Divídelo en partes y súbelas una por una."),
        ]
        for text, problem in cases:
            response = self.review(text)
            self.assertContains(response, problem)
            self.assertNotIn(SESSION_KEY, self.client.session)
        # exactly the cap is accepted
        response = self.review(HEADER + "".join(f"Persona {n},p{n}@prueba.mx,tutor,,\n" for n in range(svc.MAX_TEAM_ROWS)))
        self.assertEqual(response.context["summary"]["new"], svc.MAX_TEAM_ROWS)

    def test_a_file_too_large_or_of_another_kind_is_refused_with_a_clear_message(self):
        self.login(self.admin)
        big = HEADER + "x" * (svc.MAX_TEAM_BYTES + 1)
        self.assertContains(self.review(big), "El archivo es muy grande. El máximo es 1 MB.")
        self.assertContains(self.review(TEAM_CSV, name="equipo.xlsx"), "El archivo debe ser .csv.")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_excel_in_spanish_semicolons_and_latin_1(self):
        self.login(self.admin)
        response = self.review("Nombre;Correo electrónico;Función\nJosé Núñez;jose.nunez@prueba.mx;Tutor\n", encoding="latin-1")
        row = response.context["rows"][0]
        self.assertEqual((row.full_name, row.email, row.role, row.errors), ("José Núñez", "jose.nunez@prueba.mx", "tutor", []))

    def test_the_example_file_is_one_the_import_accepts(self):
        self.login(self.admin)
        sample = self.client.get(self.url("team_import_sample"))
        self.assertEqual(sample["Content-Type"], "text/csv; charset=utf-8")
        raw, problems = svc.read_file(sample.content)
        self.assertEqual(problems, [])
        rows = svc.plan(self.event, raw)
        self.assertEqual([r.errors for r in rows], [[], [], []])
        self.assertEqual([r.tutor_label for r in rows], ["", "Teresa Molina Vega", "Teresa Molina Vega"])
        self.assertTrue(all(r.email.endswith("@ejemplo.invalid") for r in rows))  # no real address in the example


class ConfirmTests(TeamImportFixture):
    def test_confirming_creates_memberships_assignments_and_invitations(self):
        self.login(self.admin)
        self.review()
        response = self.confirm(2, 3, 4, 5, 6)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "5 personas ya forman parte del equipo")
        self.assertContains(response, "A 5 les enviamos un correo")
        self.assertNotContains(response, "/cuenta/contrasena/")  # an e-mail went out: the link is not also put on screen
        tania = self.member("tania.vega@prueba.mx")  # the address was normalised
        self.assertEqual((tania.role, tania.team_name, tania.user.first_name, tania.user.last_name), ("tutor", "Equipo Oriente", "Tania", "Vega Soto"))
        self.assertFalse(tania.user.has_usable_password())
        self.assertEqual(self.member("pedro.lara@prueba.mx").role, "pastor")
        tutor_of = lambda email: self.member(email).tutor_assignment.tutor  # noqa: E731
        self.assertEqual(tutor_of("carmen.rios@prueba.mx"), tania)  # by e-mail, a tutor of the same file
        self.assertEqual(tutor_of("camilo.pena@prueba.mx"), tania)  # by team name
        self.assertEqual(tutor_of("claudia.mora@prueba.mx"), self.m_tutor1)  # by name, a tutor already in the team
        self.assertEqual(self.member("camilo.pena@prueba.mx").team_name, "")  # a team name belongs to tutors
        # audited: one row per person (the path of "Agregar persona") and one for the file
        self.assertEqual(AuditEvent.objects.filter(event=self.event, action="integrante_agregado").count(), 5)
        self.assertEqual(AuditEvent.objects.get(event=self.event, action="importacion_equipo").summary,
                         "Importó 5 integrantes del equipo desde un archivo")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_each_invitation_carries_its_own_working_link(self):
        self.login(self.admin)
        self.review()
        self.confirm(2, 3, 4, 5, 6)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), sorted(NEW_EMAILS))
        links = [re.search(r"http://testserver(\S+)", m.body).group(1) for m in mail.outbox]
        self.assertEqual(len(set(links)), 5)
        self.client.logout()
        for message, link in zip(mail.outbox, links):
            self.assertEqual(message.subject, f"Te invitaron a {self.event.name} en Emmaus")
            opened = self.client.get(link, follow=True)
            self.assertEqual(opened.status_code, 200)
            self.assertTrue(opened.context["validlink"], link)

    def test_reimporting_the_same_file_changes_nothing(self):
        """Acceptance criterion 1."""
        self.login(self.admin)
        self.review()
        self.confirm(2, 3, 4, 5, 6)
        before = self.snapshot()
        response = self.review()
        self.assertEqual(response.context["summary"], {"total": 5, "new": 0, "unchanged": 5, "errors": 0})
        self.assertNotContains(response, 'name="include"')
        self.assertContains(response, "Este archivo no agrega a nadie")
        response = self.confirm(2, 3, 4, 5, 6)  # even if somebody posts the confirmation by hand
        self.assertRedirects(response, self.url("team"))
        self.assertIn("No se agregó a nadie", str(list(get_messages(response.wsgi_request))[0]))
        self.assertEqual(self.snapshot(), before)  # no account, membership, assignment, audit row or e-mail

    def test_a_reimport_never_changes_a_role_a_team_or_a_tutor(self):
        self.login(self.admin)
        before = self.snapshot()
        text = HEADER + (
            "Carla Uno,COORD1@prueba.mx,tutor,Equipo Nuevo,\n"  # a coordinator the file calls a tutor
            "Teresa Uno,tutor1@prueba.mx,tutor,Otro Nombre,\n"
            "César Dos,coord2@prueba.mx,coordinador,,tutor2@prueba.mx\n"  # another tutor than the one he has
        )
        response = self.review(text)
        rows = response.context["rows"]
        self.assertEqual([r.status for r in rows], ["sin_cambios"] * 3)
        self.assertIn("El archivo dice tutor: si quieres cambiarlo, hazlo en Equipo, Editar.", rows[0].notes)
        self.confirm(2, 3, 4)
        self.assertEqual(self.snapshot(), before)

    def test_a_member_whose_access_was_removed_is_not_brought_back(self):
        EventMembership.objects.filter(pk=self.m_coord3.pk).update(is_active=False)
        self.login(self.admin)
        response = self.review(HEADER + "Cora Tres,coord3@prueba.mx,coordinador,,\n")
        self.assertIn("Se le retiró el acceso a este evento", response.context["rows"][0].notes[0])
        self.confirm(2)
        self.m_coord3.refresh_from_db()
        self.assertFalse(self.m_coord3.is_active)

    def test_a_church_role_is_left_alone_and_never_created(self):
        church_pastor = make_user("pastora@prueba.mx", "Paula", "Iglesia")
        assign_role(church_pastor, self.church, Role.PASTOR)
        self.login(self.admin)
        before = self.snapshot()
        text = HEADER + "Paula Iglesia,pastora@prueba.mx,coordinador,,\nAda Admin,admin@prueba.mx,tutor,,\n"
        response = self.review(text)
        self.assertEqual([r.notes for r in response.context["rows"]],
                         [["Ya tiene acceso a este evento como pastor."], ["Ya tiene acceso a este evento como administrador."]])
        self.confirm(2, 3)
        self.assertEqual(self.snapshot(), before)

    def test_no_column_grants_confidential_access_or_a_church_role(self):
        self.login(self.admin)
        roles_before = UserChurchRole.objects.count()
        text = ("Nombre,Correo,Rol,Equipo,Tutor,Puede leer información confidencial,can_view_sensitive,Rol en la iglesia,is_staff,is_superuser\n"
                "Pía Cuidado,pia@prueba.mx,pastor,,,sí,True,church_admin,1,1\n")
        self.review(text)
        self.confirm(2)
        pia = self.member("pia@prueba.mx")
        self.assertEqual(pia.role, "pastor")
        self.assertFalse(pia.can_view_sensitive)
        self.assertFalse(pia.user.is_staff or pia.user.is_superuser)
        self.assertEqual(UserChurchRole.objects.count(), roles_before)
        self.assertFalse(AuditEvent.objects.filter(event=self.event, action="acceso_confidencial").exists())
        self.assertNotIn(MembershipRole.ADMIN, svc.ROLE_WORDS.values())

    def test_an_existing_account_is_reused_by_address_whatever_its_case(self):
        account = make_user("Vero.Luna@Prueba.MX", "Verónica", "Luna")
        self.login(self.admin)
        response = self.review(HEADER + "Otro Nombre,vero.luna@prueba.mx,tutor,Equipo Luna,\n")
        self.assertEqual(response.context["rows"][0].notes, ["Ya tiene cuenta en Emmaus: se usa esa cuenta y su nombre no cambia."])
        response = self.confirm(2)
        self.assertEqual(User.objects.filter(email__iexact="vero.luna@prueba.mx").count(), 1)
        account.refresh_from_db()
        self.assertEqual(account.first_name, "Verónica")  # a file never renames an account
        self.assertEqual(EventMembership.objects.get(event=self.event, user=account).role, "tutor")
        self.assertContains(response, "Puede entrar con su contraseña de siempre.")
        self.assertEqual(mail.outbox, [])  # it has a password: no link to create one is issued

    def test_an_account_of_another_church_gets_only_this_event_and_no_link(self):
        other_church = Church.objects.create(name="Otra iglesia", slug="otra-iglesia")
        foreign = User.objects.create_user(username="lejos@otra.mx", email="lejos@otra.mx", first_name="Lía", last_name="Lejos")
        foreign.set_unusable_password()
        foreign.save()
        assign_role(foreign, other_church, Role.PASTOR)
        self.login(self.admin)
        self.review(HEADER + "Lía Lejos,lejos@otra.mx,coordinador,,\n")
        response = self.confirm(2)
        self.assertContains(response, "la administra otra iglesia o el sistema")
        self.assertNotContains(response, "/cuenta/contrasena/")
        self.assertEqual(mail.outbox, [])
        self.assertEqual(list(UserChurchRole.objects.filter(user=foreign).values_list("church_id", "role")), [(other_church.id, Role.PASTOR)])
        self.assertEqual(list(EventMembership.objects.filter(user=foreign).values_list("event_id", "role")), [(self.event.id, "coordinador")])

    def test_only_marked_rows_are_imported_and_a_coordinator_without_its_tutor_is_said(self):
        self.login(self.admin)
        self.review()
        response = self.confirm(3, 6)  # Carmen (her tutor, row 2, is not marked) and Pedro
        self.assertContains(response, "2 personas ya forman parte del equipo")
        self.assertContains(response, "Sin tutor, porque no importaste la fila de su tutor: Carmen Ríos Paz.")
        self.assertFalse(User.objects.filter(email="tania.vega@prueba.mx").exists())
        self.assertFalse(TutorAssignment.objects.filter(coordinator=self.member("carmen.rios@prueba.mx")).exists())

    def test_the_confirmation_reads_the_team_again(self):
        """Somebody was added by hand between the preview and the confirmation: not added twice, role untouched."""
        self.login(self.admin)
        self.review()
        taken = make_user("pedro.lara@prueba.mx", "Pedro", "Lara")
        EventMembership.objects.create(event=self.event, user=taken, role=MembershipRole.COORDINATOR)
        response = self.confirm(2, 3, 4, 5, 6)
        self.assertContains(response, "4 personas ya forman parte del equipo")
        self.assertEqual(self.member("pedro.lara@prueba.mx").role, "coordinador")
        self.assertEqual(User.objects.filter(email="pedro.lara@prueba.mx").count(), 1)

    def test_a_tampered_session_cannot_do_more_than_a_file(self):
        self.login(self.admin)
        self.review()
        session = self.client.session
        preview = session[SESSION_KEY]
        preview["rows"] = [
            {"line": 2, "full_name": "Mala Fe", "email": "mala@prueba.mx", "role": "administrador", "team": "", "tutor": "",
             "can_view_sensitive": True},
            {"line": 3, "full_name": "Buena Fe", "email": "buena@prueba.mx", "role": "pastor", "team": "", "tutor": "",
             "can_view_sensitive": True},
        ]
        session[SESSION_KEY] = preview
        session.save()
        self.confirm(2, 3)
        self.assertFalse(User.objects.filter(email="mala@prueba.mx").exists())
        self.assertFalse(self.member("buena@prueba.mx").can_view_sensitive)


class DeliveryTests(TeamImportFixture):
    ONE = HEADER + "Tania Vega Soto,tania.vega@prueba.mx,tutor,,\nPedro Lara Rey,pedro.lara@prueba.mx,pastor,,\n"

    def test_a_failed_email_is_said_and_the_link_is_given_instead(self):
        self.login(self.admin)
        self.review(self.ONE)
        calls = []

        def flaky(subject, message, sender, recipients, **kwargs):
            calls.append(recipients[0])
            if recipients[0] == "tania.vega@prueba.mx":
                raise OSError("smtp down")
            return 1

        with patch("core.services.send_mail", side_effect=flaky), self.assertLogs("core.services", level="ERROR"):
            response = self.confirm(2, 3)
        self.assertEqual(calls, ["tania.vega@prueba.mx", "pedro.lara@prueba.mx"])
        self.assertContains(response, "A 1 le enviamos un correo")
        self.assertContains(response, "1 correo no se pudo enviar")
        people = {p["membership"].user.email: p for p in response.context["people"]}
        self.assertEqual(people["tania.vega@prueba.mx"]["delivery"], "failed")
        self.assertIn("/cuenta/contrasena/", people["tania.vega@prueba.mx"]["link"])
        self.assertEqual((people["pedro.lara@prueba.mx"]["delivery"], people["pedro.lara@prueba.mx"]["link"]), ("sent", ""))
        self.assertContains(response, "No pudimos enviarle el correo.", count=1)
        # The people are in the team all the same
        self.assertEqual(EventMembership.objects.filter(event=self.event, user__email__in=["tania.vega@prueba.mx", "pedro.lara@prueba.mx"]).count(), 2)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_without_email_configured_nothing_claims_an_email(self):
        self.login(self.admin)
        preview = self.review(self.ONE)
        self.assertContains(preview, "No hay correo configurado: al confirmar verás el enlace de cada persona")
        with patch("core.services.send_mail") as send:
            response = self.confirm(2, 3)
        send.assert_not_called()  # a password link is never written to the server log
        self.assertNotContains(response, "enviamos un correo")
        self.assertContains(response, "No hay correo configurado: copia el enlace", count=2)
        self.assertEqual(response.content.decode().count("data-copy="), 2)

    def test_the_link_page_is_not_kept_anywhere(self):
        self.login(self.admin)
        self.review(self.ONE)
        with patch("core.services.send_mail", side_effect=OSError("smtp down")), self.assertLogs("core.services", level="ERROR"):
            self.confirm(2, 3)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertNotContains(self.client.get(self.url("team_import")), "/cuenta/contrasena/")
        self.assertFalse(AuditEvent.objects.filter(summary__contains="contrasena/").exists())


class FormulaTests(TeamImportFixture):
    def test_formula_cells_are_refused_at_the_door(self):
        self.login(self.admin)
        text = HEADER + "+52 55 1234,mas@prueba.mx,tutor,,\n@SUM(A1),arroba@prueba.mx,tutor,,\nTina Bien,tina@prueba.mx,tutor,=1+1,\n-Guion,guion@prueba.mx,pastor,,\n"
        response = self.review(text)
        self.assertEqual([bool(r.errors) for r in response.context["rows"]], [True, True, True, True])

    def test_a_team_name_that_looks_like_a_formula_is_neutralised_where_names_are_exported(self):
        """Names reach a spreadsheet through the campero list ("Coordinador" column). Whatever is in the database,
        from this import or from before it, leaves through ``safe_cell``."""
        User.objects.filter(pk=self.coord1.pk).update(first_name="=HYPERLINK(\"http://x\")", last_name="")
        self.login(self.admin)
        export = self.client.get(self.url("participant_export")).content.decode("utf-8-sig")
        self.assertIn("'=HYPERLINK", export)
        self.assertNotIn(",=HYPERLINK", export)
        self.assertNotIn(',"=HYPERLINK', export)
