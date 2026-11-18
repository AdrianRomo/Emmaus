"""Issue #50, the defects an independent review found: an account another church created could be taken over
through a link shown on screen; the preview told whether an address had an account; a malformed CSV was a server
error; an alias could outgrow its column; an alias of somebody who stopped coordinating was lost for good.
Every name and address here is invented."""

from __future__ import annotations

import io
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import override_settings

from core.models import Church, Role
from core.services import assign_role
from events.models import AccessRequest, AuditEvent, EventMembership, EventParticipant, MembershipAlias, MembershipRole
from events.services import participants as participant_svc
from events.services import team as team_svc
from events.services import team_import as svc
from events.views.admin import IMPORT_SESSION_KEY, _can_issue_activation_link, _managed_elsewhere
from events.views.team_import import SESSION_KEY
from people.models import Person
from people.services import grant_access

from .base import CampFixture, make_user

HEADER = "Nombre,Correo,Rol,Equipo,Tutor\n"
CAMPER_HEADER = "Nombre,Edad,Cuarto,Iglesia o grupo,Coordinador\n"
LINK = "/cuenta/contrasena/"
CONSOLE = "django.core.mail.backends.console.EmailBackend"


def upload(text: str, name: str = "equipo.csv"):
    file = io.BytesIO(text.encode("utf-8"))
    file.name = name
    return file


class ReviewFixture(CampFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_church = Church.objects.create(name="Otra iglesia", slug="otra-iglesia")
        cls.other_admin = make_user("admin@otra.mx", "Olivia", "Otra")
        assign_role(cls.other_admin, cls.other_church, Role.CHURCH_ADMIN)
        # The exact account of the finding: another church gave a plain member access in Personas. It has no church
        # role, no team anywhere and no password yet.
        person = Person.objects.create(church=cls.other_church, first_name="Marta", last_name="Miembro", email="marta@otra.mx")
        cls.victim, created = grant_access(person, cls.other_admin)
        assert created and not cls.victim.has_usable_password()
        # The same, given by this church to one of its own members
        own = Person.objects.create(church=cls.church, first_name="Nora", last_name="Nuestra", email="nora@prueba.mx")
        cls.own_member, _ = grant_access(own, cls.admin)

    def review(self, text):
        return self.client.post(self.url("team_import"), {"file": upload(text)}, follow=True)

    def confirm(self, *lines):
        return self.client.post(self.url("team_import"), {"action": "confirm", "include": [str(n) for n in lines]})

    def import_victim(self):
        self.login(self.admin)
        self.review(HEADER + "Marta Miembro,marta@otra.mx,coordinador,,\nNadie Nuevo,nadie.nuevo@prueba.mx,coordinador,,\n")
        return self.confirm(2, 3)

    def add_person(self, email, name="Marta Miembro"):
        self.login(self.admin)
        return self.client.post(self.url("member_add"), {"full_name": name, "email": email, "role": "coordinador"})

    def assert_victim_untouched(self):
        self.victim.refresh_from_db()
        self.assertFalse(self.victim.has_usable_password())


class NoLinkForAnAccountThatAlreadyExistedTests(ReviewFixture):
    """Defect 1: the link to create a password, shown to whoever typed the address of somebody else's account."""

    def test_managed_elsewhere_knows_a_person_of_another_church(self):
        self.assertTrue(_managed_elsewhere(self.event, self.victim))
        self.assertFalse(_managed_elsewhere(self.event, self.own_member))

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_import_without_email_shows_no_link_for_it(self):
        response = self.import_victim()
        people = {p["membership"].user.email: p for p in response.context["people"]}
        self.assertEqual((people["marta@otra.mx"]["delivery"], people["marta@otra.mx"]["link"]), ("existing_not_told", ""))
        self.assertIn(LINK, people["nadie.nuevo@prueba.mx"]["link"])  # the account this import created keeps its link
        self.assertEqual(response.content.decode().count(LINK), 1)
        self.assertContains(response, "no pudimos enviarle la invitación")
        self.assert_victim_untouched()

    def test_import_with_a_failed_email_shows_no_link_for_it(self):
        self.login(self.admin)
        self.review(HEADER + "Marta Miembro,marta@otra.mx,coordinador,,\n")
        with patch("core.services.send_mail", side_effect=OSError("smtp down")), self.assertLogs("core.services", level="ERROR"):
            response = self.confirm(2)
        self.assertNotContains(response, LINK)
        self.assertEqual(response.context["people"][0]["delivery"], "existing_not_told")

    def test_import_with_email_tells_the_owner_and_sends_no_link_either(self):
        response = self.import_victim()
        self.assertEqual(response.content.decode().count(LINK), 0)
        sent = {m.to[0]: m.body for m in mail.outbox}
        self.assertNotIn(LINK, sent["marta@otra.mx"])
        self.assertIn("/cuenta/entrar/", sent["marta@otra.mx"])
        self.assertIn(LINK, sent["nadie.nuevo@prueba.mx"])

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_agregar_persona_without_email_shows_no_link_for_it(self):
        response = self.add_person("marta@otra.mx")
        self.assertRedirects(response, self.url("team"))  # not the page with the link
        said = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertEqual(len(said), 1)
        self.assertIn("Ya tiene cuenta en Emmaus, pero no pudimos enviarle la invitación", said[0])
        self.assertNotContains(self.client.get(self.url("team")), LINK)
        self.assertTrue(EventMembership.objects.filter(event=self.event, user=self.victim).exists())
        self.assert_victim_untouched()

    def test_agregar_persona_with_a_failed_email_shows_no_link_for_it(self):
        with patch("core.services.send_mail", side_effect=OSError("smtp down")), self.assertLogs("core.services", level="ERROR"):
            response = self.add_person("marta@otra.mx")
        self.assertRedirects(response, self.url("team"))
        self.assertNotIn(LINK, response.content.decode())

    def test_agregar_persona_with_email_tells_the_owner_without_a_link(self):
        response = self.add_person("marta@otra.mx")
        self.assertRedirects(response, self.url("team"))
        self.assertIn("le avisamos por correo", str(list(get_messages(response.wsgi_request))[0]))
        self.assertEqual([m.to for m in mail.outbox], [["marta@otra.mx"]])
        self.assertNotIn(LINK, mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_member_edit_never_offers_or_makes_a_link_for_it(self):
        self.add_person("marta@otra.mx")
        membership = EventMembership.objects.get(event=self.event, user=self.victim)
        self.assertFalse(_can_issue_activation_link(self.event, self.victim))
        page = self.client.get(self.url("member_edit", membership.id))
        self.assertNotContains(page, "Generar enlace")
        response = self.client.post(self.url("member_edit", membership.id), {"action": "link"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AuditEvent.objects.filter(summary__startswith="Generó un enlace").exists())

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_the_same_for_an_account_this_church_gave_to_one_of_its_members(self):
        """It existed before it joined a team: the event's administration did not create it, so no link either."""
        response = self.add_person("nora@prueba.mx", "Nora Nuestra")
        self.assertRedirects(response, self.url("team"))
        membership = EventMembership.objects.get(event=self.event, user=self.own_member)
        self.assertFalse(_can_issue_activation_link(self.event, self.own_member))
        self.assertEqual(self.client.post(self.url("member_edit", membership.id), {"action": "link"}).status_code, 403)

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_an_account_with_no_tie_anywhere_gets_no_link_either(self):
        loose = User.objects.create_user(username="suelta@otra.mx", email="suelta@otra.mx", first_name="Sara")
        loose.set_unusable_password()
        loose.save()
        response = self.add_person("suelta@otra.mx", "Sara Suelta")
        self.assertRedirects(response, self.url("team"))
        membership = EventMembership.objects.get(event=self.event, user=loose)
        User.objects.filter(pk=loose.pk).update(date_joined=membership.created_at - __import__("datetime").timedelta(days=30))
        loose.refresh_from_db()
        self.assertFalse(_can_issue_activation_link(self.event, loose))

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_an_approved_request_shows_no_link_for_it(self):
        request = AccessRequest.objects.create(event=self.event, full_name="Marta Miembro", email="marta@otra.mx", requested_role="coordinador")
        self.login(self.admin)
        response = self.client.post(self.url("access_request_approve", request.pk), {"role": "coordinador"})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(LINK, response.content.decode())

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_an_account_created_by_agregar_persona_keeps_its_link(self):
        """What must keep working: the person the administrator just added, with no e-mail configured."""
        response = self.add_person("nueva@prueba.mx", "Nuria Nueva")
        self.assertContains(response, LINK)
        user = User.objects.get(email="nueva@prueba.mx")
        membership = EventMembership.objects.get(event=self.event, user=user)
        self.assertTrue(_can_issue_activation_link(self.event, user))
        again = self.client.post(self.url("member_edit", membership.id), {"action": "link"})
        self.assertContains(again, LINK)


class PreviewTellsNothingAboutOtherAccountsTests(ReviewFixture):
    """Defect 2: a review writes nothing, so it may not answer "does this address have an account in Emmaus?"."""

    def rows(self):
        root = User.objects.create_superuser(username="raiz@sistema.mx", email="raiz@sistema.mx", password="clave-de-prueba-123")
        self.assertTrue(root.is_superuser)
        self.login(self.admin)
        text = HEADER + (
            "Nadie Nuevo,nadie.nuevo@prueba.mx,tutor,,\n"  # no account at all
            "Marta Miembro,marta@otra.mx,tutor,,\n"  # another church's member
            "Olivia Otra,admin@otra.mx,tutor,,\n"  # another church's administrator
            "Raíz Sistema,raiz@sistema.mx,tutor,,\n"  # the system's administration
            "Óscar Externo,externo@prueba.mx,tutor,,\n"  # this church's own: a tutor of its other event
        )
        return self.review(text)

    def test_other_accounts_read_exactly_like_a_new_person(self):
        response = self.rows()
        rows = response.context["rows"]
        described = [(r.status, r.notes, r.has_account, r.errors) for r in rows]
        self.assertEqual(described[:4], [("nuevo", [], False, [])] * 4)
        # The church's own team may be told it is the same account
        self.assertEqual(described[4], ("nuevo", [svc.KNOWN_ACCOUNT], True, []))
        self.assertEqual(response.context["summary"], {"total": 5, "new": 5, "unchanged": 0, "errors": 0})
        self.assertNotContains(response, "Ya tiene acceso")
        self.assertContains(response, "Ya tiene cuenta en Emmaus", count=1)

    def test_and_in_the_result_when_the_emails_go_out(self):
        self.rows()
        response = self.confirm(2, 3, 4, 5)
        self.assertEqual([p["delivery"] for p in response.context["people"]], ["sent"] * 4)
        self.assertContains(response, "Le enviamos un correo con su invitación.", count=4)
        self.assertNotContains(response, "Ya tiene cuenta")
        self.assertNotContains(response, LINK)

    def test_a_review_leaves_one_audit_row_without_addresses(self):
        before = AuditEvent.objects.count()
        self.rows()
        self.assertEqual(AuditEvent.objects.count(), before + 1)
        row = AuditEvent.objects.get(event=self.event, action="importacion_equipo_revisada")
        self.assertEqual((row.summary, row.metadata, row.actor), ("Revisó un archivo del equipo con 5 filas", {"filas": 5}, self.admin))
        self.assertNotIn("@", row.summary + str(row.metadata))
        self.client.get(self.url("team_import"))  # looking again at the same review is not another one
        self.assertEqual(AuditEvent.objects.count(), before + 1)
        self.review("Nombre,Rol\nAna,tutor\n")  # a file that was not read leaves none
        self.assertEqual(AuditEvent.objects.count(), before + 1)


class MalformedCsvTests(ReviewFixture):
    """Defect 3: ``_csv.Error`` (a cell over 131,072 characters, which is also what an open quote becomes) was a 500."""

    HUGE = "x" * 131_073

    def test_team_import_says_it_in_words(self):
        self.login(self.admin)
        for text in (HEADER + f"{self.HUGE},ana@prueba.mx,tutor,,\n", HEADER + f'"Ana sin cerrar,ana@prueba.mx,tutor,,\n{self.HUGE}\n'):
            response = self.review(text)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "No pudimos leer el archivo")
            self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(svc.read_file((HEADER.strip() + "," + self.HUGE + "\n").encode()), ([], [participant_svc.UNREADABLE_CSV]))

    def test_campero_import_says_it_in_words(self):
        self.login(self.admin)
        for text in (CAMPER_HEADER + f"{self.HUGE},15,1,Iglesia Centro,\n", CAMPER_HEADER + f'"Ana sin cerrar,15,1,Iglesia Centro,\n{self.HUGE}\n'):
            response = self.client.post(self.url("participant_import"), {"file": upload(text, "camperos.csv")}, follow=True)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "No pudimos leer el archivo")
            self.assertNotIn(IMPORT_SESSION_KEY, self.client.session)
        self.assertFalse(EventParticipant.objects.filter(event=self.event, full_name__startswith="Ana sin").exists())


class AliasLengthTests(ReviewFixture):
    """Defect 4: 150 "ﬃ" fit ``alias`` and compare as 450 letters, which do not fit ``normalized``."""

    LIGATURES = "ﬃ" * 150

    def test_save_alias_refuses_it(self):
        saved, why = participant_svc.save_alias(self.m_coord1, self.LIGATURES, actor=self.admin)
        self.assertEqual((saved, why), (False, "Uno de los nombres es demasiado largo para recordarlo: no se guardó."))
        self.assertFalse(MembershipAlias.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action="alias_guardado").exists())

    def test_the_model_refuses_it(self):
        with self.assertRaises(ValidationError) as caught:
            MembershipAlias(event=self.event, membership=self.m_coord1, alias=self.LIGATURES).full_clean()
        self.assertIn("alias", caught.exception.message_dict)
        MembershipAlias(event=self.event, membership=self.m_coord1, alias="ﬃ" * 50).full_clean()  # 150 letters: fits

    def test_the_campero_import_goes_through_and_says_so(self):
        self.login(self.admin)
        self.client.post(self.url("participant_import"), {"file": upload(CAMPER_HEADER + f"Valeria Ruiz Ortega,16,8,Iglesia Centro,{self.LIGATURES}\n", "c.csv")})
        self.client.post(self.url("participant_import"), {"action": "map", "coordinador_0": self.m_coord1.id, "recordar_0": "1"})
        response = self.client.post(self.url("participant_import"), {"action": "confirm", "include": ["2"]})
        said = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertEqual(said, ["Se importó 1 campero.", "Uno de los nombres es demasiado largo para recordarlo: no se guardó."])
        self.assertEqual(EventParticipant.objects.get(event=self.event, full_name="Valeria Ruiz Ortega").coordinator, self.m_coord1)
        self.assertFalse(MembershipAlias.objects.exists())


class AliasOfSomebodyWhoStoppedCoordinatingTests(ReviewFixture):
    """Defect 5: such an alias matched nobody and could not be given to anybody else."""

    def setUp(self):
        participant_svc.save_alias(self.m_coord2, "Chino", actor=self.admin)

    def stop(self, **changes):
        EventMembership.objects.filter(pk=self.m_coord2.pk).update(**changes)

    def test_it_passes_to_the_coordinator_named_now_and_is_audited(self):
        for changes in ({"is_active": False}, {"role": MembershipRole.TUTOR}):
            with self.subTest(changes=changes):
                MembershipAlias.objects.filter(normalized="chino").update(membership=self.m_coord2)
                EventMembership.objects.filter(pk=self.m_coord2.pk).update(is_active=True, role=MembershipRole.COORDINATOR)
                self.stop(**changes)
                before = AuditEvent.objects.filter(action="alias_guardado").count()
                self.assertEqual(participant_svc.save_alias(self.m_coord1, "CHINO", actor=self.pastor), (True, ""))
                alias = MembershipAlias.objects.get()
                self.assertEqual((alias.membership, alias.alias, alias.created_by), (self.m_coord1, "CHINO", self.pastor))
                row = AuditEvent.objects.filter(action="alias_guardado").order_by("-id").first()
                self.assertEqual(AuditEvent.objects.filter(action="alias_guardado").count(), before + 1)
                self.assertEqual(row.summary, "Pasó «CHINO» de César Dos, que ya no coordina, a Carla Uno para las importaciones")
                self.assertEqual(row.metadata, {"reasignado": True, "antes": self.m_coord2.pk})

    def test_an_active_coordinator_keeps_the_alias(self):
        saved, why = participant_svc.save_alias(self.m_coord1, "chino", actor=self.admin)
        self.assertEqual((saved, why), (False, "«chino» ya es otro nombre de César Dos: no se cambió."))
        self.assertEqual(MembershipAlias.objects.get().membership, self.m_coord2)

    def test_the_import_asks_again_and_remembers_the_new_answer(self):
        self.stop(is_active=False)
        self.login(self.admin)
        text = CAMPER_HEADER + "Valeria Ruiz Ortega,16,8,Iglesia Centro,Chino\n"
        response = self.client.post(self.url("participant_import"), {"file": upload(text, "c.csv")}, follow=True)
        self.assertTrue(response.context["needs_mapping"])
        self.client.post(self.url("participant_import"), {"action": "map", "coordinador_0": self.m_coord3.id, "recordar_0": "1"})
        response = self.client.post(self.url("participant_import"), {"action": "confirm", "include": ["2"]})
        self.assertEqual([str(m) for m in get_messages(response.wsgi_request)],
                         ["Se importó 1 campero. Guardamos 1 nombre para la próxima importación."])
        rows, _ = participant_svc.parse_import(self.event, (CAMPER_HEADER + "Otro Campero,15,1,X,chino\n").encode())
        self.assertEqual(rows[0].coordinator_id, self.m_coord3.id)

    def test_the_member_page_still_removes_it(self):
        self.stop(is_active=False)
        self.login(self.admin)
        alias = MembershipAlias.objects.get()
        self.assertContains(self.client.get(self.url("member_edit", self.m_coord2.id)), "Chino")
        self.client.post(self.url("member_edit", self.m_coord2.id), {"action": "alias_delete", "alias": alias.pk})
        self.assertFalse(MembershipAlias.objects.exists())


class TeamServiceUnchangedTests(ReviewFixture):
    def test_add_member_still_says_whether_the_account_is_new(self):
        added = team_svc.add_member(self.event, self.admin, full_name="Marta Miembro", email="MARTA@otra.mx", role="coordinador")
        self.assertFalse(added.is_new)
        added = team_svc.add_member(self.event, self.admin, full_name="Nadie Nuevo", email="nadie.nuevo@prueba.mx", role="coordinador")
        self.assertTrue(added.is_new)
