"""Issue #50: coordinators named by nickname in the campero import, and the aliases that remember them.
Every name here is invented."""

from __future__ import annotations

import io

from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction

from events.models import AuditEvent, EventMembership, EventParticipant, MembershipAlias, MembershipRole
from events.services import duplicate as dup
from events.services import participants as svc
from events.views.admin import IMPORT_SESSION_KEY

from .base import CampFixture, make_user

HEADER = "Nombre,Edad,Cuarto,Iglesia o grupo,Coordinador\n"
NICKNAMES = HEADER + (
    "Valeria Ruiz Ortega,16,8,Iglesia Centro,Carlita\n"
    "Marco Díaz Luna,15,8,Iglesia Centro,CARLITA\n"
    "Elena Soto Paz,14,9,Iglesia Centro,El Chino\n"
    "Bruno Gil Mora,17,9,Iglesia Centro,coord3@prueba.mx\n"
    "Nadia Cruz Lima,16,9,Iglesia Centro,\n"
)


def upload(text: str):
    file = io.BytesIO(text.encode("utf-8"))
    file.name = "camperos.csv"
    return file


class AliasFixture(CampFixture):
    def review(self, text=NICKNAMES):
        return self.client.post(self.url("participant_import"), {"file": upload(text)}, follow=True)

    def answer_mapping(self, data):
        return self.client.post(self.url("participant_import"), {"action": "map", **data}, follow=True)

    def confirm(self, *lines):
        response = self.client.post(self.url("participant_import"), {"action": "confirm", "include": [str(n) for n in lines]})
        return response, [str(m) for m in get_messages(response.wsgi_request)]

    def camper(self, name):
        return EventParticipant.objects.get(event=self.event, full_name=name)

    def state(self):
        return (EventParticipant.objects.count(), MembershipAlias.objects.count(), AuditEvent.objects.count())


class MappingTests(AliasFixture):
    def test_nicknames_succeed_after_mapping_and_match_on_the_next_import(self):
        """Acceptance criterion 2, end to end."""
        self.login(self.admin)
        before = self.state()
        response = self.review()
        # The mapping step: each distinct value once, with how many camperos carry it; no review table yet
        self.assertTrue(response.context["needs_mapping"])
        self.assertEqual([(u["value"], u["count"]) for u in response.context["unmatched"]], [("Carlita", 2), ("El Chino", 1)])
        self.assertContains(response, "¿Quién es cada coordinador?")
        self.assertNotContains(response, "Importar filas marcadas")
        self.assertEqual({c.id for c in response.context["coordinators"]}, {self.m_coord1.id, self.m_coord2.id, self.m_coord3.id})
        self.assertEqual(self.state(), before)

        response = self.answer_mapping({"coordinador_0": self.m_coord1.id, "recordar_0": "1", "coordinador_1": self.m_coord2.id, "recordar_1": "1"})
        self.assertEqual(self.state(), before)  # the answers live in the session: still nothing written
        self.assertFalse(response.context["needs_mapping"])
        self.assertEqual([r["coordinator_label"] for r in response.context["preview"]["rows"]],
                         ["Carla Uno", "Carla Uno", "César Dos", "Cora Tres", ""])
        self.assertContains(response, "«Carlita»: Carla Uno. Se guardará para la próxima importación.")
        self.assertEqual(response.context["summary"]["no_coordinator"], 1)

        response, said = self.confirm(2, 3, 4, 5, 6)
        self.assertEqual(self.camper("Valeria Ruiz Ortega").coordinator, self.m_coord1)
        self.assertEqual(self.camper("Marco Díaz Luna").coordinator, self.m_coord1)
        self.assertEqual(self.camper("Elena Soto Paz").coordinator, self.m_coord2)
        self.assertEqual(self.camper("Bruno Gil Mora").coordinator, self.m_coord3)
        self.assertIsNone(self.camper("Nadia Cruz Lima").coordinator)
        self.assertEqual(said[0], "Se importaron 5 camperos. Guardamos 2 nombres para la próxima importación.")
        self.assertEqual(sorted(MembershipAlias.objects.values_list("membership_id", "alias", "normalized", "event_id", "created_by_id")),
                         sorted([(self.m_coord1.id, "Carlita", "carlita", self.event.id, self.admin.id),
                                 (self.m_coord2.id, "El Chino", "el chino", self.event.id, self.admin.id)]))
        self.assertEqual(sorted(AuditEvent.objects.filter(event=self.event, action="alias_guardado").values_list("summary", "actor_id")),
                         [("Guardó «Carlita» como otro nombre de Carla Uno para las importaciones", self.admin.id),
                          ("Guardó «El Chino» como otro nombre de César Dos para las importaciones", self.admin.id)])

        # The next import: the same nicknames, written with other case and accents, match without asking
        again = HEADER + "Íker Vidal Rey,15,3,Iglesia Centro,cárlita\nOlga Marín Sáez,16,3,Iglesia Centro,  EL   CHINO \n"
        response = self.review(again)
        self.assertFalse(response.context["needs_mapping"])
        self.assertEqual([(r["coordinator_id"], r["coordinator_raw"], r["errors"]) for r in response.context["preview"]["rows"]],
                         [(self.m_coord1.id, "", []), (self.m_coord2.id, "", [])])
        self.confirm(2, 3)
        self.assertEqual(self.camper("Íker Vidal Rey").coordinator, self.m_coord1)
        self.assertEqual(self.camper("Olga Marín Sáez").coordinator, self.m_coord2)
        self.assertEqual(MembershipAlias.objects.count(), 2)

    def test_nothing_is_imported_while_the_mapping_is_unanswered(self):
        self.login(self.admin)
        self.review()
        before = self.state()
        response, said = self.confirm(2, 3, 4, 5, 6)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "¿Quién es cada coordinador?")
        self.assertEqual(self.state(), before)

    def test_nobody_chosen_imports_without_coordinator_and_reports_it(self):
        """The report at the end of the import: who has no coordinator, and the list filtered to them."""
        self.login(self.admin)
        self.review()
        response = self.answer_mapping({"coordinador_0": "", "recordar_0": "1", "coordinador_1": self.m_coord2.id})
        self.assertContains(response, "«Carlita»: sin coordinador.")
        self.assertEqual(response.context["summary"]["no_coordinator"], 3)
        response, said = self.confirm(2, 3, 4, 5, 6)
        self.assertEqual(said, ["Se importaron 5 camperos.",
                                "3 camperos quedaron sin coordinador. Aquí los ves: asígnales uno desde su ficha."])
        self.assertEqual(response["Location"], self.url("participants") + "?coordinador=ninguno")
        listed = self.client.get(response["Location"])
        for name in ("Valeria Ruiz Ortega", "Marco Díaz Luna", "Nadia Cruz Lima"):
            self.assertContains(listed, name)
        self.assertNotContains(listed, "Elena Soto Paz")
        # "Recordar" without a coordinator saves nothing; unticked saves nothing either
        self.assertFalse(MembershipAlias.objects.exists())
        self.assertEqual(AuditEvent.objects.get(event=self.event, action="importacion").metadata["sin_coordinador"], 3)

    def test_a_file_whose_coordinators_all_match_reports_nothing(self):
        self.login(self.admin)
        response = self.review(HEADER + "Valeria Ruiz Ortega,16,8,Iglesia Centro,Carla Uno\n")
        self.assertFalse(response.context["needs_mapping"])
        response, said = self.confirm(2)
        self.assertEqual(said, ["Se importó 1 campero."])
        self.assertEqual(response["Location"], self.url("participants"))

    def test_only_an_active_coordinator_of_this_event_can_be_chosen(self):
        self.login(self.admin)
        for chosen in (self.m_other.id, self.m_tutor1.id, 999999, "²", "1 OR 1=1"):
            self.review()
            response = self.answer_mapping({"coordinador_0": chosen, "recordar_0": "1", "coordinador_1": ""})
            self.assertEqual([r["coordinator_id"] for r in response.context["preview"]["rows"]][:2], [None, None], chosen)
            self.confirm(2)
            self.assertFalse(MembershipAlias.objects.exists())
        self.assertFalse(EventParticipant.objects.filter(coordinator=self.m_other, event=self.event).exists())

    def test_the_answers_can_be_changed_before_confirming(self):
        self.login(self.admin)
        self.review()
        self.answer_mapping({"coordinador_0": self.m_coord1.id, "coordinador_1": self.m_coord2.id})
        response = self.client.post(self.url("participant_import"), {"action": "remap"}, follow=True)
        self.assertTrue(response.context["needs_mapping"])
        self.answer_mapping({"coordinador_0": self.m_coord3.id, "coordinador_1": ""})
        self.confirm(2, 4)
        self.assertEqual(self.camper("Valeria Ruiz Ortega").coordinator, self.m_coord3)
        self.assertIsNone(self.camper("Elena Soto Paz").coordinator)
        self.assertFalse(MembershipAlias.objects.exists())  # "Recordar" was not ticked

    def test_a_row_with_errors_does_not_ask_for_its_coordinator(self):
        self.login(self.admin)
        response = self.review(HEADER + "Sin Edad,,8,Iglesia Centro,Apodo Raro\nValeria Ruiz Ortega,16,8,Iglesia Centro,Carla Uno\n")
        self.assertFalse(response.context["needs_mapping"])

    def test_other_roles_cannot_post_a_mapping(self):
        for user in (self.pastor, self.tutor1, self.coord1, self.outsider):
            self.login(user)
            response = self.client.post(self.url("participant_import"), {"action": "map", "coordinador_0": self.m_coord1.id})
            self.assertEqual(response.status_code, 403)


class AliasRuleTests(AliasFixture):
    def test_an_alias_is_unique_within_an_event_whatever_its_case_or_accents(self):
        self.assertEqual(svc.save_alias(self.m_coord1, "Chuy", actor=self.admin), (True, ""))
        saved, why = svc.save_alias(self.m_coord2, "  CHÚY ", actor=self.admin)
        self.assertFalse(saved)
        self.assertEqual(why, "«CHÚY» ya es otro nombre de Carla Uno: no se cambió.")
        self.assertEqual(list(MembershipAlias.objects.values_list("membership_id", "alias")), [(self.m_coord1.id, "Chuy")])
        with self.assertRaises(IntegrityError), transaction.atomic():  # the database says so too
            MembershipAlias.objects.create(membership=self.m_coord2, alias="chuy")
        # Another event is another list: the same nickname is free there
        self.assertEqual(svc.save_alias(self.m_other, "Chuy", actor=self.admin), (True, ""))
        self.assertEqual(MembershipAlias.objects.get(membership=self.m_other).event_id, self.other_event.id)

    def test_saving_the_same_alias_twice_is_one_row_and_one_audit_row(self):
        svc.save_alias(self.m_coord1, "Chuy", actor=self.admin)
        self.assertEqual(svc.save_alias(self.m_coord1, "chuy", actor=self.admin), (True, ""))
        self.assertEqual(MembershipAlias.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action="alias_guardado").count(), 1)

    def test_what_is_not_saved(self):
        self.assertEqual(svc.save_alias(self.m_coord1, "   ", actor=self.admin), (False, ""))
        self.assertFalse(svc.save_alias(self.m_coord1, "=1+1", actor=self.admin)[0])
        self.assertFalse(svc.save_alias(self.m_tutor1, "Tere", actor=self.admin)[0])
        EventMembership.objects.filter(pk=self.m_coord2.pk).update(is_active=False)
        self.m_coord2.refresh_from_db()
        self.assertFalse(svc.save_alias(self.m_coord2, "Chino", actor=self.admin)[0])
        self.assertFalse(MembershipAlias.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action="alias_guardado").exists())

    def test_an_alias_never_outranks_an_email_or_a_full_name(self):
        MembershipAlias.objects.create(membership=self.m_coord2, alias="Carla Uno")
        MembershipAlias.objects.create(membership=self.m_coord2, alias="coord3@prueba.mx")
        rows, problems = svc.parse_import(self.event, (HEADER + "A B,15,1,X,carla uno\nC D,15,1,X,COORD3@prueba.mx\n").encode())
        self.assertEqual([r.coordinator_id for r in rows], [self.m_coord1.id, self.m_coord3.id])

    def test_an_alias_of_another_event_or_of_somebody_without_access_does_not_match(self):
        MembershipAlias.objects.create(membership=self.m_other, alias="Olguita")
        MembershipAlias.objects.create(membership=self.m_coord2, alias="Chino")
        EventMembership.objects.filter(pk=self.m_coord2.pk).update(is_active=False)
        rows, _ = svc.parse_import(self.event, (HEADER + "A B,15,1,X,Olguita\nC D,15,1,X,Chino\n").encode())
        self.assertEqual([(r.coordinator_id, r.coordinator_raw, r.errors) for r in rows], [(None, "Olguita", []), (None, "Chino", [])])

    def test_a_conflict_at_confirmation_is_said_and_nothing_is_overwritten(self):
        self.login(self.admin)
        self.review()
        self.answer_mapping({"coordinador_0": self.m_coord1.id, "recordar_0": "1", "coordinador_1": ""})
        MembershipAlias.objects.create(membership=self.m_coord3, alias="carlita")  # saved by another administrator meanwhile
        response, said = self.confirm(2)
        self.assertIn("«Carlita» ya es otro nombre de Cora Tres: no se cambió.", said)
        self.assertEqual(self.camper("Valeria Ruiz Ortega").coordinator, self.m_coord1)  # this import keeps the answer given
        self.assertEqual(MembershipAlias.objects.get().membership, self.m_coord3)

    def test_the_alias_follows_the_team_to_a_duplicated_event_and_only_with_the_team(self):
        svc.save_alias(self.m_coord1, "Carlita", actor=self.admin)
        with_team = dup.duplicate_camp(self.event, self.admin, name="Campamento C", slug="campamento-c", copy_team=True).event
        alias = MembershipAlias.objects.get(event=with_team)
        self.assertEqual((alias.alias, alias.membership.user, alias.membership.event), ("Carlita", self.coord1, with_team))
        rows, _ = svc.parse_import(with_team, (HEADER + "A B,15,1,X,carlita\n").encode())
        self.assertEqual(rows[0].coordinator_id, alias.membership_id)
        without = dup.duplicate_camp(self.event, self.admin, name="Campamento D", slug="campamento-d").event
        self.assertFalse(MembershipAlias.objects.filter(event=without).exists())


class AliasOnTheMemberPageTests(AliasFixture):
    def test_the_administrator_sees_and_removes_an_alias(self):
        svc.save_alias(self.m_coord1, "Carlita", actor=self.admin)
        alias = MembershipAlias.objects.get()
        self.login(self.admin)
        page = self.client.get(self.url("member_edit", self.m_coord1.id))
        self.assertContains(page, "Otros nombres en las listas")
        self.assertContains(page, "Carlita")
        self.assertNotContains(self.client.get(self.url("member_edit", self.m_coord2.id)), "Otros nombres en las listas")
        # Through somebody else's page, or another event's alias: not found, nothing removed
        self.assertEqual(self.client.post(self.url("member_edit", self.m_coord2.id), {"action": "alias_delete", "alias": alias.pk}).status_code, 404)
        self.assertEqual(self.client.post(self.url("member_edit", self.m_coord1.id), {"action": "alias_delete", "alias": "²"}).status_code, 404)
        self.assertTrue(MembershipAlias.objects.exists())
        response = self.client.post(self.url("member_edit", self.m_coord1.id), {"action": "alias_delete", "alias": alias.pk})
        self.assertRedirects(response, self.url("member_edit", self.m_coord1.id))
        self.assertFalse(MembershipAlias.objects.exists())
        self.assertEqual(AuditEvent.objects.get(action="alias_eliminado").summary, "Quitó «Carlita» como otro nombre de Carla Uno")
        self.assertEqual(self.m_coord1.role, MembershipRole.COORDINATOR)

    def test_a_non_administrator_cannot_remove_one(self):
        svc.save_alias(self.m_coord1, "Carlita", actor=self.admin)
        boss = make_user("otra.jefa@prueba.mx")
        EventMembership.objects.create(event=self.other_event, user=boss, role=MembershipRole.ADMIN)
        for user in (self.pastor, self.coord1, boss):
            self.login(user)
            response = self.client.post(self.url("member_edit", self.m_coord1.id), {"action": "alias_delete", "alias": MembershipAlias.objects.get().pk})
            self.assertEqual(response.status_code, 403)
        self.assertTrue(MembershipAlias.objects.exists())


class OldPreviewTests(AliasFixture):
    def test_a_preview_stored_before_this_change_still_confirms(self):
        """A review left open across the deploy has no "mapping" key and no "coordinator_raw" on its rows."""
        self.login(self.admin)
        self.review(HEADER + "Valeria Ruiz Ortega,16,8,Iglesia Centro,Carla Uno\n")
        session = self.client.session
        preview = session[IMPORT_SESSION_KEY]
        del preview["mapping"]
        for row in preview["rows"]:
            del row["coordinator_raw"]
        session[IMPORT_SESSION_KEY] = preview
        session.save()
        self.assertEqual(self.client.get(self.url("participant_import")).status_code, 200)
        self.confirm(2)
        self.assertEqual(self.camper("Valeria Ruiz Ortega").coordinator, self.m_coord1)
