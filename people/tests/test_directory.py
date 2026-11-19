"""The directory between members (issue #91): off twice by default, per field, read only by who is in it.

High risk: it shows people's contact data to other people. Every denial here would pass if the screen
simply did not exist, so each one is paired with the same request answering for who may.
"""

import re
from datetime import date
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib import admin as django_admin
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from agenda.models import EventRegistration, GuardianConsent
from core.models import Role, UserChurchRole
from core.services import assign_role
from events.models import AuthThrottle
from events.tests.base import make_user
from groups import services as group_services
from groups.models import Group
from groups.tests.base import CommunityFixture
from people import directory, export, flags, merge, minors, services
from people.models import ChurchAuditEvent, DirectoryEntry, DirectoryPolicy, EventParticipantLink, Person
from serving import services as serving
from serving.models import Team

from .merge_base import MergeFixture

LIST = reverse("directorio:list")
MINE = reverse("directorio:mine")
POLICY = reverse("directorio:policy")
PROFILE = reverse("community:profile")
HOME = reverse("community:home")

ANA_PHONE, ANA_MAIL = "55 7000 1111", "ana.directorio@prueba.mx"
BETO_PHONE, BETO_MAIL = "55 7000 2222", "beto.directorio@prueba.mx"
GROUP_NAME, TEAM_NAME = "Jóvenes Roca", "Alabanza del directorio"


def rows_of(response) -> str:
    """The list of the page, raw: everything between the first ``<ul class="rows">`` and the pager or the next section."""
    html = response.content.decode()
    start = html.index('<ul class="rows">')
    return html[start:html.index("</ul>", start)]


class DirectoryFixture(CommunityFixture):
    """Church A: ``ana`` (member: phone, e-mail, a group and a team on record), ``beto`` (attender), ``caro``
    (member), a visitor, somebody with no stage and a minor, each with an account; a pastor who is not the
    administration. Church B: ``member_b`` as a member and its own pastor. Nobody has chosen anything."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        def person(church, email, first, last, stage, **extra):
            user = make_user(email, first, last)
            return cls._person(church, user, stage=stage, **extra)

        cls.ana = person(cls.church, ANA_MAIL, "Ana", "Directorio Uno", "miembro", phone=ANA_PHONE,
                         baptized_on=date(2011, 3, 4), arrived_on=date(2009, 5, 6), member_since=date(2012, 7, 8))
        cls.beto = person(cls.church, BETO_MAIL, "Beto", "Directorio Dos", "asistente", phone=BETO_PHONE)
        cls.caro = person(cls.church, "caro.directorio@prueba.mx", "Caro", "Directorio Tres", "miembro", phone="55 7000 3333")
        cls.visitor = person(cls.church, "visita.directorio@prueba.mx", "Vera", "Visita", "visita")
        cls.unstaged = person(cls.church, "sinetapa.directorio@prueba.mx", "Simón", "Sin Etapa", "")
        today = timezone.localdate()
        cls.minor = person(cls.church, "menor.directorio@prueba.mx", "Mía", "Menor", "miembro", birth_date=date(today.year - 15, 1, 1))
        group_services.set_membership(cls.group, cls.ana, cls.admin)
        cls.team = serving.save_team(cls.church, cls.admin, name=TEAM_NAME)
        serving.set_member(cls.team, cls.ana, cls.admin)
        cls.pastor = make_user("pastor.directorio@prueba.mx", "Pablo", "Pastor")
        assign_role(cls.pastor, cls.church, Role.PASTOR)
        cls.pastor_person = cls._person(cls.church, cls.pastor)
        cls.pastor_b = make_user("pastor-b.directorio@prueba.mx", "Benito", "Pastor Be")
        assign_role(cls.pastor_b, cls.church_b, Role.PASTOR)
        cls._person(cls.church_b, cls.pastor_b)
        Person.objects.filter(pk=cls.member_b.pk).update(stage="miembro", phone="55 7999 0000")
        cls.member_b.refresh_from_db()

    def turn_on(self, church=None, actor=None):
        return directory.switch_on(church or self.church, actor or self.pastor, accepted=True, wording=directory.WORDING_VERSION)

    def join(self, person, *fields):
        person.refresh_from_db()
        token = directory.preview_token(person, fields, directory.preview(person.church, person, fields))
        return directory.opt_in(person.church, person, person.user, fields=fields, token=token)

    def as_(self, person_or_user):
        self.client.force_login(getattr(person_or_user, "user", person_or_user))
        return self.client

    def status(self, who, url=LIST):
        return self.as_(who).get(url).status_code

    def choices(self) -> list:
        fields = ("person_id", "listed", "show_phone", "show_email", "show_groups", "show_teams", "accepted_at", "wording_version")
        return list(DirectoryEntry.objects.order_by("person_id").values_list(*fields))


class OffByDefaultTests(DirectoryFixture):
    def test_nothing_answers_until_a_pastor_switches_it_on(self):
        self.assertFalse(DirectoryPolicy.objects.exists())
        for who in (self.ana, self.admin, self.pastor, self.caro):
            self.assertEqual(self.status(who), 404, who)
        for who in (self.ana, self.admin):
            self.assertEqual(self.status(who, MINE), 404)
            self.assertEqual(self.status(who, POLICY), 404)
        self.assertEqual(self.status(self.pastor, POLICY), 200)
        self.assertNotContains(self.as_(self.ana).get(PROFILE), "Aparecer en el directorio")

    def test_signed_out_is_sent_to_sign_in(self):
        self.turn_on()
        for url in (LIST, MINE, POLICY):
            response = Client().get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("login"), response["Location"])

    def test_switching_it_on_makes_nobody_appear(self):
        self.turn_on()
        self.assertFalse(DirectoryEntry.objects.exists())
        self.assertEqual(directory.visible_entries(self.church).count(), 0)
        page = self.as_(self.admin).get(LIST)
        self.assertContains(page, "Todavía nadie aparece")
        self.assertNotContains(page, "Ana Directorio")

    def test_with_the_community_module_off_every_address_is_a_404(self):
        self.turn_on()
        self.join(self.ana)
        flags.set_enabled(self.church, "community", False)
        for who, url in ((self.ana, LIST), (self.ana, MINE), (self.pastor, POLICY), (self.admin, LIST)):
            self.assertEqual(self.status(who, url), 404)

    def test_the_models_are_not_in_the_django_admin(self):
        registered = {model.__name__ for model in django_admin.site._registry}
        self.assertFalse({"DirectoryEntry", "DirectoryPolicy"} & registered)


class SwitchTests(DirectoryFixture):
    def test_a_pastor_must_accept_and_the_acceptance_is_recorded_with_ids_only(self):
        client = self.as_(self.pastor)
        refused = client.post(POLICY, {"paso": "encender", "texto": directory.WORDING_VERSION})
        self.assertContains(refused, "marca que leíste y aceptas")
        self.assertFalse(directory.is_on(self.church))
        done = client.post(POLICY, {"paso": "encender", "texto": directory.WORDING_VERSION, "accepted": "on"})
        self.assertRedirects(done, POLICY)
        row = DirectoryPolicy.objects.get(church=self.church)
        self.assertEqual((row.enabled, row.accepted_by, row.wording_version), (True, self.pastor, directory.WORDING_VERSION))
        self.assertIsNotNone(row.accepted_at)
        event = ChurchAuditEvent.objects.get(action="directorio_politica")
        self.assertEqual((event.church, event.actor, event.metadata), (self.church, self.pastor, {"value": True}))
        self.assertFalse(directory.is_on(self.church_b))

    def test_a_page_read_under_an_older_wording_switches_nothing_on(self):
        response = self.as_(self.pastor).post(POLICY, {"paso": "encender", "texto": "2020-01", "accepted": "on"})
        self.assertContains(response, "cambió mientras la leías")
        self.assertFalse(DirectoryPolicy.objects.filter(enabled=True).exists())

    def test_the_administration_cannot_open_or_post_the_switch(self):
        for who in (self.admin, self.ana, self.leader):
            client = self.as_(who)
            self.assertEqual(client.get(POLICY).status_code, 404, who)
            self.assertEqual(client.post(POLICY, {"paso": "encender", "texto": directory.WORDING_VERSION, "accepted": "on"}).status_code, 404)
        self.assertFalse(DirectoryPolicy.objects.exists())
        # A pastor of another church switches their own church, never this one.
        self.as_(self.pastor_b).post(POLICY, {"paso": "encender", "texto": directory.WORDING_VERSION, "accepted": "on"})
        self.assertEqual(list(DirectoryPolicy.objects.values_list("church_id", flat=True)), [self.church_b.pk])
        self.assertFalse(directory.is_on(self.church))
        for actor in (self.admin, self.ana.user, self.pastor_b):
            with self.assertRaises(directory.DirectoryError):
                directory.switch_on(self.church, actor, accepted=True, wording=directory.WORDING_VERSION)
        self.turn_on()
        self.assertEqual(self.as_(self.admin).post(POLICY, {"paso": "apagar"}).status_code, 404)
        with self.assertRaises(directory.DirectoryError):
            directory.switch_off(self.church, self.admin)
        self.assertTrue(directory.is_on(self.church))

    def test_a_pastor_who_lost_the_role_cannot_switch(self):
        UserChurchRole.objects.filter(user=self.pastor, church=self.church).delete()
        self.assertEqual(self.status(self.pastor, POLICY), 404)

    def test_posts_need_the_csrf_token(self):
        self.turn_on()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.pastor)
        self.assertEqual(client.post(POLICY, {"paso": "apagar"}).status_code, 403)
        client.force_login(self.ana.user)
        self.assertEqual(client.post(MINE, {"paso": "ver"}).status_code, 403)
        self.assertEqual(client.post(MINE, {"paso": "salir"}).status_code, 403)
        self.assertTrue(directory.is_on(self.church))

    def test_switching_off_hides_it_for_everybody_and_keeps_every_choice(self):
        self.turn_on()
        self.join(self.ana, "phone", "groups")
        self.join(self.beto)
        before = self.choices()
        self.assertEqual(self.status(self.ana), 200)
        self.assertRedirects(self.as_(self.pastor).post(POLICY, {"paso": "apagar"}), POLICY)
        for who in (self.ana, self.beto, self.admin, self.pastor):
            self.assertEqual(self.status(who), 404, who)
        self.assertEqual(self.choices(), before)
        self.assertEqual(directory.visible_entries(self.church).count(), 0)
        # The person still reaches their own choice, to take it back; nothing new can be chosen.
        page = self.as_(self.ana).get(MINE)
        self.assertContains(page, "Tu iglesia apagó el directorio")
        self.assertNotContains(page, 'name="mostrar"')
        self.assertEqual(self.as_(self.ana).post(MINE, {"paso": "ver", "mostrar": ["email"]}).status_code, 404)
        self.assertEqual(self.status(self.caro, MINE), 404)
        # On again: exactly who had chosen, with exactly what they chose. Nobody else.
        self.turn_on()
        self.assertEqual(self.choices(), before)
        html = rows_of(self.as_(self.beto).get(LIST))
        self.assertIn(ANA_PHONE, html)
        self.assertNotIn(ANA_MAIL, html)
        self.assertNotIn("Caro", html)
        self.assertEqual(ChurchAuditEvent.objects.filter(action="directorio_politica").count(), 3)

    def test_the_policies_page_of_the_administration_says_it_and_cannot_change_it(self):
        page = self.as_(self.admin).get(reverse("church_policies"))
        self.assertContains(page, "Directorio entre miembros: apagado")
        self.turn_on()
        self.assertContains(self.as_(self.admin).get(reverse("church_policies")), "Directorio entre miembros: encendido")


class ChoiceTests(DirectoryFixture):
    def setUp(self):
        self.turn_on()

    def test_the_two_steps_and_the_audit_row(self):
        client = self.as_(self.ana)
        page = client.get(MINE)
        self.assertContains(page, "Qué quieres mostrar")
        self.assertNotContains(page, "checked")
        self.assertContains(client.get(PROFILE), "Aparecer en el directorio")
        preview = client.post(MINE, {"paso": "ver", "mostrar": ["phone"]})
        self.assertContains(preview, "Así te verán")
        self.assertContains(preview, ANA_PHONE)
        self.assertNotContains(preview, ANA_MAIL)
        self.assertNotContains(preview, GROUP_NAME)
        self.assertContains(preview, "Lo que aceptas al aparecer")
        self.assertFalse(DirectoryEntry.objects.exists())  # a preview saves nothing
        token = re.search(r'name="visto" value="([0-9a-f]{64})"', preview.content.decode()).group(1)
        done = client.post(MINE, {"paso": "confirmar", "mostrar": ["phone"], "visto": token})
        self.assertRedirects(done, MINE)
        entry = DirectoryEntry.objects.get(person=self.ana)
        self.assertEqual((entry.listed, entry.show_phone, entry.show_email, entry.show_groups, entry.show_teams), (True, True, False, False, False))
        self.assertEqual(entry.wording_version, directory.WORDING_VERSION)
        self.assertIsNotNone(entry.accepted_at)
        event = ChurchAuditEvent.objects.get(action="directorio_eleccion")
        self.assertEqual(event.metadata, {"person_id": self.ana.pk, "value": True, "fields": ["phone"]})
        self.assertEqual(event.actor, self.ana.user)
        stored = f"{event.summary} {event.metadata} {event.object_id}"
        for secret in (ANA_PHONE, ANA_MAIL, "Ana", "5570001111"):
            self.assertNotIn(secret, stored)
        page = client.get(MINE)
        self.assertContains(page, "Así te ven hoy")
        self.assertContains(page, 'value="phone" checked')
        self.assertContains(client.get(PROFILE), "Hoy apareces")
        self.assertContains(client.get(PROFILE), "quienes ven el directorio de tu iglesia")

    def test_changing_the_choice_goes_back_to_the_form_with_what_was_marked(self):
        page = self.as_(self.ana).post(MINE, {"paso": "cambiar", "mostrar": ["email", "nada-que-exista"]})
        self.assertContains(page, 'value="email" checked')
        self.assertNotContains(page, 'value="phone" checked')
        self.assertFalse(DirectoryEntry.objects.exists())

    def test_a_confirmation_whose_preview_is_stale_saves_nothing_and_shows_the_preview_again(self):
        client = self.as_(self.ana)
        preview = client.post(MINE, {"paso": "ver", "mostrar": ["phone", "groups"]})
        token = re.search(r'name="visto" value="([0-9a-f]{64})"', preview.content.decode()).group(1)
        Person.objects.filter(pk=self.ana.pk).update(phone="55 7000 9999")
        again = client.post(MINE, {"paso": "confirmar", "mostrar": ["phone", "groups"], "visto": token})
        self.assertContains(again, "cambió mientras decidías")
        self.assertContains(again, "55 7000 9999")
        self.assertFalse(DirectoryEntry.objects.exists())
        # The token also covers which fields were shown: the same values with one more field is another preview.
        token = re.search(r'name="visto" value="([0-9a-f]{64})"', again.content.decode()).group(1)
        more = client.post(MINE, {"paso": "confirmar", "mostrar": ["phone", "groups", "email"], "visto": token})
        self.assertContains(more, "cambió mientras decidías")
        self.assertFalse(DirectoryEntry.objects.exists())
        # And a group joined meanwhile.
        token = re.search(r'name="visto" value="([0-9a-f]{64})"', more.content.decode()).group(1)
        group_services.set_membership(self.other_group, self.ana, self.admin)
        self.assertContains(client.post(MINE, {"paso": "confirmar", "mostrar": ["phone", "groups", "email"], "visto": token}), "cambió mientras decidías")
        self.assertFalse(DirectoryEntry.objects.exists())

    def test_a_stale_copy_of_the_person_cannot_choose_after_the_stage_changed(self):
        stale = Person.objects.get(pk=self.ana.pk)
        token = directory.preview_token(stale, ("phone",), directory.preview(self.church, stale, ("phone",)))
        services.set_stage(self.church, self.ana.pk, "ya_no_asiste", self.admin, seen="miembro")
        self.assertEqual(stale.stage, "miembro")  # what the open tab still believes
        with self.assertRaises(directory.DirectoryError):
            directory.opt_in(self.church, stale, stale.user, fields=("phone",), token=token)
        self.assertFalse(DirectoryEntry.objects.exists())

    def test_the_administration_cannot_choose_for_somebody(self):
        client = self.as_(self.admin)
        Person.objects.filter(pk=self.admin_person.pk).update(stage="miembro")
        token = directory.preview_token(self.caro, ("phone",), directory.preview(self.church, self.caro, ("phone",)))
        # A hand-made POST that names another person in every way a form could: only the account's own person is read.
        payload = {"paso": "confirmar", "mostrar": ["phone"], "visto": token, "person": self.caro.pk, "persona": self.caro.pk,
                   "person_id": self.caro.pk, "id": self.caro.pk}
        client.post(MINE, payload)
        client.post(f"{MINE}?person={self.caro.pk}&persona={self.caro.pk}", payload)
        self.assertFalse(DirectoryEntry.objects.filter(person=self.caro).exists())
        self.assertFalse(DirectoryEntry.objects.exists())
        for actor in (self.admin, self.pastor, self.ana.user, None):
            with self.assertRaises(directory.DirectoryError) as refused:
                directory.opt_in(self.church, self.caro, actor, fields=("phone",), token=token)
            self.assertEqual(refused.exception.code, "not_yours")
        self.join(self.caro, "phone")
        with self.assertRaises(directory.DirectoryError):
            directory.opt_out(self.church, self.caro, self.admin)
        self.assertTrue(DirectoryEntry.objects.get(person=self.caro).listed)
        self.assertFalse(DirectoryEntry.objects.exclude(person=self.caro).exists())

    def test_no_address_of_the_directory_names_a_person(self):
        from people import directory_urls

        self.assertEqual([str(pattern.pattern) for pattern in directory_urls.urlpatterns], ["", "aparecer/", "politica/"])

    def test_a_field_cannot_be_stored_for_somebody_who_does_not_appear(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            DirectoryEntry.objects.create(person=self.caro, listed=False, show_phone=True)

    def test_leaving_is_immediate_and_total(self):
        self.join(self.ana, "phone", "email", "groups", "teams")
        self.join(self.beto)
        self.assertIn("Ana Directorio", rows_of(self.as_(self.beto).get(LIST)))
        response = self.as_(self.ana).post(MINE, {"paso": "salir"})
        self.assertRedirects(response, MINE)
        entry = DirectoryEntry.objects.get(person=self.ana)
        self.assertEqual((entry.listed, entry.show_phone, entry.show_email, entry.show_groups, entry.show_teams), (False,) * 5)
        self.assertEqual(self.status(self.ana), 404)  # reciprocity: who is not in it does not read it
        html = rows_of(self.as_(self.beto).get(LIST))
        self.assertNotIn("Ana Directorio", html)
        self.assertNotIn(ANA_PHONE, html)
        left = ChurchAuditEvent.objects.filter(action="directorio_eleccion").first()
        self.assertEqual(left.metadata, {"person_id": self.ana.pk, "value": False, "fields": []})
        # A second press changes nothing and writes no second row.
        self.as_(self.ana).post(MINE, {"paso": "salir"})
        self.assertEqual(ChurchAuditEvent.objects.filter(action="directorio_eleccion", metadata__value=False).count(), 1)

    def test_who_is_not_offered_the_choice_gets_a_404_and_no_row_in_mis_datos(self):
        for who in (self.visitor, self.unstaged, self.minor):
            client = self.as_(who)
            self.assertEqual(client.get(MINE).status_code, 404, who)
            self.assertEqual(client.post(MINE, {"paso": "ver", "mostrar": ["phone"]}).status_code, 404, who)
            self.assertEqual(client.post(MINE, {"paso": "confirmar", "mostrar": ["phone"], "visto": "x"}).status_code, 404, who)
            self.assertNotContains(client.get(PROFILE), "Aparecer en el directorio")
            self.assertNotContains(client.get(PROFILE), MINE)
            with self.assertRaises(directory.DirectoryError):
                self.join(who, "phone")
        self.assertFalse(DirectoryEntry.objects.exists())

    def test_an_unknown_step_is_a_404(self):
        self.assertEqual(self.as_(self.ana).post(MINE, {"paso": "todos"}).status_code, 404)
        self.assertEqual(self.as_(self.ana).post(MINE, {}).status_code, 404)


class ReadingTests(DirectoryFixture):
    def setUp(self):
        self.turn_on()
        self.join(self.ana, "phone", "email", "groups", "teams")
        self.join(self.beto)

    def test_a_row_shows_the_name_and_only_what_the_person_chose(self):
        response = self.as_(self.beto).get(LIST)
        html = rows_of(response)
        self.assertIn("Ana Directorio Uno", html)
        self.assertIn('href="tel:5570001111"', html)
        self.assertIn(f'href="mailto:{ANA_MAIL}"', html)
        self.assertIn(GROUP_NAME, html)
        self.assertIn(TEAM_NAME, html)
        self.assertIn("Beto Directorio Dos", html)
        raw = response.content.decode()
        for hidden in (BETO_PHONE, "5570002222", BETO_MAIL):
            self.assertNotIn(hidden, raw)
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(html.count("mailto:"), 1)
        self.assertNotIn(",", re.search(r'href="mailto:([^"]*)"', html).group(1))
        self.assertNotIn("csv", raw.lower())
        self.assertNotIn("Descargar", raw)

    def test_each_field_that_was_not_chosen_is_absent_from_the_raw_response(self):
        present = {"phone": (ANA_PHONE, "5570001111"), "email": (ANA_MAIL,), "groups": (GROUP_NAME,), "teams": (TEAM_NAME,)}
        for left_out in directory.FIELDS:
            with self.subTest(left_out=left_out):
                self.join(self.ana, *[name for name in directory.FIELDS if name != left_out])
                for reader in (self.beto, self.admin, self.pastor):
                    raw = self.as_(reader).get(LIST).content.decode()
                    self.assertIn("Ana Directorio Uno", raw)
                    for value in present[left_out]:
                        self.assertNotIn(value, raw, (left_out, reader))
                    for name, values in present.items():
                        if name != left_out:
                            self.assertIn(values[0], raw)

    def test_nothing_else_of_a_person_is_ever_in_it(self):
        Person.objects.filter(pk=self.ana.pk).update(birth_date=date(1990, 2, 3))
        from people.tags import create_tag, set_tags

        tag = create_tag(self.church, "Etiqueta-secreta-88", self.admin)
        set_tags(self.church, self.ana.pk, {tag.pk}, self.admin, seen=set())
        raw = self.as_(self.beto).get(LIST).content.decode()
        for hidden in ("1990", "2011", "2009", "2012", "Etiqueta-secreta-88", "Miembro", "Asistente", "años", "Se bautizó"):
            self.assertNotIn(hidden, rows_of(self.client.get(LIST)), hidden)
        self.assertNotIn("Etiqueta-secreta-88", raw)
        self.assertNotIn("03/02/1990", raw)

    def test_who_may_not_read_gets_a_404(self):
        self.join(self.caro)
        directory.opt_out(self.church, self.caro, self.caro.user)
        cases = {
            "did not choose to appear": self.caro, "a visitor": self.visitor, "nobody wrote a stage": self.unstaged, "a minor": self.minor,
            "a guide with no stage": self.leader, "a member with no stage": self.member, "another church's member": self.member_b,
            "another church's pastor": self.pastor_b, "another church's administration": self.admin_b,
        }
        for why, who in cases.items():
            self.assertEqual(self.status(who), 404, why)
        for who in (self.ana, self.beto, self.admin, self.pastor):
            self.assertEqual(self.status(who), 200, who)

    def test_a_row_that_says_yes_is_not_enough_for_a_visitor_a_person_with_no_stage_or_a_minor(self):
        """The rule is asked at read time: a row written while the person qualified (or by hand) shows nobody."""
        for person in (self.visitor, self.unstaged, self.minor, self.no_account):
            DirectoryEntry.objects.create(person=person, listed=True, show_phone=True, wording_version=directory.WORDING_VERSION,
                                          accepted_at=timezone.now())
        for who in (self.visitor, self.unstaged, self.minor):
            self.assertEqual(self.status(who), 404, who)
        html = rows_of(self.as_(self.admin).get(LIST))
        for name in ("Vera", "Simón", "Mía", "Nora"):
            self.assertNotIn(name, html)
        self.assertEqual(directory.visible_entries(self.church).count(), 2)

    def test_ya_no_asiste_disappears_and_stops_reading(self):
        self.assertEqual(self.status(self.ana), 200)
        services.set_stage(self.church, self.ana.pk, "ya_no_asiste", self.admin, seen="miembro")
        self.assertEqual(self.status(self.ana), 404)
        self.assertNotIn("Ana Directorio", rows_of(self.as_(self.beto).get(LIST)))
        self.assertNotContains(self.as_(self.ana).get(HOME), f'href="{LIST}"')
        # Their choice is still theirs to take back, and nothing else is offered.
        page = self.as_(self.ana).get(MINE)
        self.assertContains(page, "Hoy no apareces en el directorio")
        self.assertNotContains(page, 'name="mostrar"')
        self.as_(self.ana).post(MINE, {"paso": "salir"})
        self.assertFalse(DirectoryEntry.objects.get(person=self.ana).listed)
        self.assertEqual(self.status(self.ana, MINE), 404)

    def test_a_person_who_becomes_known_as_a_minor_disappears_at_read_time(self):
        before = self.choices()
        today = timezone.localdate()
        Person.objects.filter(pk=self.ana.pk).update(birth_date=date(today.year - 16, 1, 1))
        self.assertEqual(self.choices(), before)  # nothing was saved about the directory
        self.assertNotIn("Ana Directorio", rows_of(self.as_(self.beto).get(LIST)))
        self.assertNotIn("Ana Directorio", rows_of(self.as_(self.admin).get(LIST)))
        self.assertEqual(self.status(self.ana), 404)
        page = self.as_(self.ana).get(MINE)  # only the way out
        self.assertNotContains(page, 'name="mostrar"')
        self.assertContains(page, 'value="salir"')

    def test_a_deactivated_or_removed_account_disappears(self):
        self.ana.user.is_active = False
        self.ana.user.save(update_fields=["is_active"])
        self.assertNotIn("Ana Directorio", rows_of(self.as_(self.beto).get(LIST)))
        self.ana.user.is_active = True
        self.ana.user.save(update_fields=["is_active"])
        self.assertIn("Ana Directorio", rows_of(self.as_(self.beto).get(LIST)))
        Person.objects.filter(pk=self.ana.pk).update(user=None)
        self.assertNotIn("Ana Directorio", rows_of(self.as_(self.beto).get(LIST)))

    def test_a_record_joined_into_another_never_appears_even_with_its_account(self):
        """A merge takes the account off the absorbed record; the rule does not lean on that."""
        Person.objects.filter(pk=self.ana.pk).update(merged_into=self.caro)
        self.assertNotIn("Ana Directorio", rows_of(self.as_(self.beto).get(LIST)))
        self.assertEqual(directory.visible_entries(self.church).count(), 1)

    def test_an_archived_group_or_team_and_a_membership_that_ended_are_not_shown(self):
        Group.objects.filter(pk=self.group.pk).update(status=Group.Status.ARCHIVED)
        Team.objects.filter(pk=self.team.pk).update(status=Team.Status.ARCHIVED)
        html = rows_of(self.as_(self.beto).get(LIST))
        self.assertNotIn(GROUP_NAME, html)
        self.assertNotIn(TEAM_NAME, html)

    def test_the_search_is_by_name_only(self):
        client = self.as_(self.beto)
        for query in (ANA_PHONE, "5570001111", "7000", ANA_MAIL, "ana.directorio", "@prueba.mx", "prueba", GROUP_NAME, TEAM_NAME, "Roca"):
            page = client.get(LIST, {"q": query})
            self.assertContains(page, "Nadie que aparezca se llama así")
            self.assertNotContains(page, "Ana Directorio Uno")
            self.assertNotContains(page, 'href="tel:')
        for query in ("ana", "ANA uno", "directorio"):
            self.assertIn("Ana Directorio Uno", rows_of(client.get(LIST, {"q": query})))
        self.assertNotIn("Beto", rows_of(client.get(LIST, {"q": "ana"})))
        # Somebody who does not appear is not found by name either.
        self.assertContains(client.get(LIST, {"q": "Caro"}), "Nadie que aparezca se llama así")

    def test_what_a_person_or_the_administration_typed_is_escaped(self):
        Person.objects.filter(pk=self.ana.pk).update(first_name="<script>alert(1)</script>", phone='55"><b>1', email="a<b>@prueba.mx")
        self.ana.refresh_from_db()
        self.ana.save()  # the normalized name follows
        Group.objects.filter(pk=self.group.pk).update(name="<img src=x onerror=1>")
        Team.objects.filter(pk=self.team.pk).update(name="<b>Equipo</b>")
        raw = self.as_(self.beto).get(LIST).content.decode()
        for unsafe in ("<script>alert(1)", "<img src=x", "<b>Equipo", '"><b>1', "a<b>@"):
            self.assertNotIn(unsafe, raw)
        self.assertIn("&lt;script&gt;alert(1)", raw)
        self.assertIn("&lt;img src=x", raw)
        preview = self.as_(self.ana).post(MINE, {"paso": "ver", "mostrar": list(directory.FIELDS)}).content.decode()
        for unsafe in ("<script>alert(1)", "<img src=x", "<b>Equipo", '"><b>1'):
            self.assertNotIn(unsafe, preview)
        self.assertNotIn("<script>alert(1)", self.as_(self.beto).get(LIST, {"q": "<script>alert(1)</script>"}).content.decode())

    def test_the_list_is_paged_and_costs_the_same_queries_whatever_its_length(self):
        with self.assertNumQueries(4):  # the switch, the entries with their person, the groups, the teams
            directory.rows_for(self.church, directory.search(self.church, ""))
        for number in range(directory.PAGE_SIZE + 3):
            user = make_user(f"fila{number}@prueba.mx", "Fila", f"Número {number:02d}")
            self.join(self._person(self.church, user, stage="miembro", phone=f"55 6000 {number:04d}"), "phone", "groups")
        with self.assertNumQueries(4):
            directory.rows_for(self.church, directory.search(self.church, "")[: directory.PAGE_SIZE])
        client = self.as_(self.beto)
        first = client.get(LIST)
        self.assertEqual(rows_of(first).count('class="row row--lead"'), directory.PAGE_SIZE)
        self.assertContains(first, "Página 1 de 2")
        second = client.get(LIST, {"pagina": 2})
        self.assertEqual(rows_of(second).count('class="row row--lead"'), 5)

    def test_every_view_counts_against_the_account_and_the_block_shows_nobody(self):
        client = self.as_(self.beto)
        for _ in range(directory.VIEW_LIMIT["limit"]):
            self.assertEqual(client.get(LIST, {"q": "ana"}).status_code, 200)
        blocked = client.get(LIST)
        self.assertEqual(blocked.status_code, 429)
        self.assertContains(blocked, "Abriste el directorio muchas veces", status_code=429)
        self.assertNotContains(blocked, "Ana Directorio", status_code=429)
        self.assertNotContains(blocked, ANA_PHONE, status_code=429)
        self.assertEqual(client.get(LIST, {"q": "ana"}).status_code, 429)
        self.assertTrue(AuthThrottle.objects.filter(key=f"directory:{self.church.pk}:{self.beto.user_id}").exists())
        # Another account, and somebody who may not read at all (their 404 counts nothing), are untouched.
        self.assertEqual(self.status(self.ana), 200)
        self.assertEqual(self.status(self.caro), 404)
        self.assertFalse(AuthThrottle.objects.filter(key=f"directory:{self.church.pk}:{self.caro.user_id}").exists())
        # The administration is counted like everybody.
        for _ in range(directory.VIEW_LIMIT["limit"]):
            self.assertEqual(self.as_(self.admin).get(LIST).status_code, 200)
        self.assertEqual(self.as_(self.admin).get(LIST).status_code, 429)

    def test_the_navigation_lists_it_only_for_who_may_read(self):
        link = f'href="{LIST}"'
        for who in (self.ana, self.beto, self.admin, self.pastor):
            self.assertContains(self.as_(who).get(HOME), link, msg_prefix=str(who))
        for who in (self.caro, self.visitor, self.unstaged, self.minor, self.leader):
            self.assertNotContains(self.as_(who).get(HOME), link, msg_prefix=str(who))
        directory.switch_off(self.church, self.pastor)
        for who in (self.ana, self.admin, self.pastor):
            self.assertNotContains(self.as_(who).get(HOME), link)
        # Off: only a pastor gets a door, and it is the switch's.
        self.assertContains(self.as_(self.pastor).get(HOME), f'href="{POLICY}"')
        self.assertNotContains(self.as_(self.admin).get(HOME), f'href="{POLICY}"')
        self.assertNotContains(self.as_(self.ana).get(HOME), f'href="{POLICY}"')

    def test_the_administration_and_a_pastor_read_without_appearing(self):
        html = rows_of(self.as_(self.pastor).get(LIST))
        self.assertIn("Ana Directorio", html)
        self.assertNotIn("Pablo Pastor", html)
        self.assertNotIn(self.admin_person.full_name, rows_of(self.as_(self.admin).get(LIST)))


class WordingTests(DirectoryFixture):
    def test_a_new_wording_hides_everybody_until_the_pastor_and_then_each_person_accept_again(self):
        self.turn_on()
        self.join(self.ana, "phone", "groups")
        self.join(self.beto)
        before = self.choices()
        with mock.patch.object(directory, "WORDING_VERSION", "2099-01"):
            self.assertFalse(directory.is_on(self.church))
            self.assertEqual(directory.state(self.church), directory.State(False, True))
            for who in (self.ana, self.admin, self.pastor):
                self.assertEqual(self.status(who), 404)
            self.assertContains(self.as_(self.pastor).get(POLICY), "Cambió lo que Emmaus ofrece")
            self.turn_on()
            # The church accepted; the people have not: nobody appears, and their choices are still there.
            self.assertEqual(directory.visible_entries(self.church).count(), 0)
            self.assertEqual(self.choices(), before)
            self.assertEqual(self.status(self.ana), 404)
            self.assertNotIn("Ana Directorio", self.as_(self.admin).get(LIST).content.decode())
            page = self.as_(self.ana).get(MINE)
            self.assertContains(page, "Cambió lo que aceptas al aparecer")
            self.assertContains(page, 'value="phone" checked')
            self.assertContains(page, 'value="groups" checked')
            self.assertNotContains(page, 'value="email" checked')
            self.assertNotContains(page, "Así te ven hoy")
            self.assertContains(self.as_(self.ana).get(PROFILE), "Hoy no apareces")
            self.join(self.ana, "phone", "groups")
            self.assertEqual(self.status(self.ana), 200)
            html = rows_of(self.as_(self.ana).get(LIST))
            self.assertIn("Ana Directorio", html)
            self.assertNotIn("Beto", html)
            self.assertEqual(DirectoryEntry.objects.get(person=self.ana).wording_version, "2099-01")


class ChurchesTests(DirectoryFixture):
    def setUp(self):
        self.turn_on()
        self.turn_on(self.church_b, self.pastor_b)
        self.join(self.ana, "phone")
        self.join(self.member_b, "phone")

    def test_each_church_reads_only_its_own(self):
        html = rows_of(self.as_(self.member_b).get(LIST))
        self.assertIn("Bruno", html)
        self.assertNotIn("Ana", html)
        self.assertNotIn(ANA_PHONE, html)
        for who in (self.admin_b, self.pastor_b):
            raw = self.as_(who).get(LIST).content.decode()
            self.assertNotIn("Ana Directorio", raw)
            self.assertNotIn(ANA_PHONE, raw)
        raw = self.as_(self.admin).get(LIST, {"q": "Bruno"}).content.decode()
        self.assertNotIn("55 7999 0000", raw)
        self.assertNotIn("Bruno Miembro", raw)

    def test_one_church_switched_off_does_not_touch_the_other(self):
        directory.switch_off(self.church_b, self.pastor_b)
        self.assertEqual(self.status(self.member_b), 404)
        self.assertEqual(self.status(self.ana), 200)

    def test_an_account_of_two_churches_chooses_in_each_one_apart_and_reads_where_it_chose(self):
        both = self.ana.user
        there = self._person(self.church_b, both, stage="miembro", phone="55 7888 1111")
        assign_role(both, self.church, Role.MENTOR)  # a role in each: the account chooses where it works
        assign_role(both, self.church_b, Role.MENTOR)
        client = self.as_(both)
        client.post(reverse("switch_church"), {"church": self.church.pk})
        self.assertIn("Ana Directorio", rows_of(client.get(LIST)))  # church A, where the account chose to appear
        client.post(reverse("switch_church"), {"church": self.church_b.pk})
        # Mid-session, in the other church: the choice of church A gives nothing here.
        self.assertEqual(client.get(LIST).status_code, 404)
        self.assertFalse(DirectoryEntry.objects.filter(person=there).exists())
        self.assertNotIn("55 7888 1111", self.as_(self.admin_b).get(LIST).content.decode())
        client = self.as_(both)
        client.post(reverse("switch_church"), {"church": self.church_b.pk})
        preview = client.post(MINE, {"paso": "ver", "mostrar": ["email"]})
        self.assertNotContains(preview, ANA_PHONE)
        token = re.search(r'name="visto" value="([0-9a-f]{64})"', preview.content.decode()).group(1)
        client.post(MINE, {"paso": "confirmar", "mostrar": ["email"], "visto": token})
        here, other = DirectoryEntry.objects.get(person=self.ana), DirectoryEntry.objects.get(person=there)
        self.assertEqual((here.show_phone, here.show_email), (True, False))
        self.assertEqual((other.show_phone, other.show_email), (False, True))
        html = rows_of(client.get(LIST))
        self.assertIn("Bruno", html)
        self.assertNotIn(ANA_PHONE, html)
        self.assertNotIn("55 7888 1111", html)
        # Leaving in one church leaves the other as it was.
        client.post(MINE, {"paso": "salir"})
        self.assertFalse(DirectoryEntry.objects.get(person=there).listed)
        self.assertTrue(DirectoryEntry.objects.get(person=self.ana).listed)
        # A person of another church cannot be chosen for through this church.
        with self.assertRaises(directory.DirectoryError):
            directory.opt_in(self.church, there, both, fields=(), token="x")


class MinorRuleTests(DirectoryFixture):
    def test_the_list_filter_and_the_single_rule_agree_for_every_way_of_being_a_known_minor(self):
        today = timezone.localdate()

        def make(name, **extra):
            return Person.objects.create(church=self.church, first_name=name, last_name="Regla", **extra)

        eighteen_today = today.replace(year=today.year - 18) if (today.month, today.day) != (2, 29) else today.replace(year=today.year - 18, day=28)
        cases = {
            "nothing known": make("Nada"),
            "adult by date": make("Adulta", birth_date=date(today.year - 30, 1, 1)),
            "18 today": make("Hoy", birth_date=eighteen_today),
            "minor by date": make("Menor", birth_date=date(today.year - 17, 12, 31) if (today.month, today.day) != (12, 31) else date(today.year - 16, 1, 1)),
            "camp under the adult age": make("Campera"),
            "camp as an adult": make("Campero"),
            "door said minor": make("Puerta"),
            "guardian consent": make("Tutor"),
            "adult by date, minor at a camp years ago": make("Creció", birth_date=date(today.year - 25, 1, 1)),
        }
        EventParticipantLink.objects.create(participant=self.p1, person=cases["camp under the adult age"], event=self.event)  # 15
        EventParticipantLink.objects.filter(participant=self.p2).delete()
        EventParticipantLink.objects.create(participant=self.p2, person=cases["camp as an adult"], event=self.event)  # 19
        from events.models import Event

        day_event = Event.objects.create(church=self.church, name="Convivio", slug="convivio-directorio", starts_on=today, ends_on=today)
        EventRegistration.objects.create(event=day_event, person=cases["door said minor"], guest_is_minor=True)
        registration = EventRegistration.objects.create(event=day_event, person=cases["guardian consent"])
        GuardianConsent.objects.create(registration=registration, guardian_name="Tutora de prueba", method="papel", recorded_by=self.admin)
        expected = {
            "nothing known": False, "adult by date": False, "18 today": False, "minor by date": True, "camp under the adult age": True,
            "camp as an adult": False, "door said minor": True, "guardian consent": True, "adult by date, minor at a camp years ago": False,
        }
        adults = set(minors.exclude_known_minors(Person.objects.filter(last_name="Regla"), "").values_list("pk", flat=True))
        for why, person in cases.items():
            self.assertEqual(minors.known_minor(person), expected[why], why)
            self.assertEqual(person.pk not in adults, expected[why], why)

    def test_the_rule_moved_and_the_old_import_still_works(self):
        from formularios.services import is_known_minor
        from oracion import access

        self.assertIs(access.known_minor, minors.known_minor)
        self.assertTrue(is_known_minor(self.minor))


class MergeTests(MergeFixture):
    """Consent is the record's: the survivor keeps its own, the absorbed record's is dropped (never unioned), and
    the undo gives the absorbed record back what it had."""

    def setUp(self):
        self.pastor = make_user("pastor.union@prueba.mx", "Pablo", "Pastor")
        assign_role(self.pastor, self.church, Role.PASTOR)
        directory.switch_on(self.church, self.pastor, accepted=True, wording=directory.WORDING_VERSION)
        self.account = make_user("lia.cuenta@prueba.mx", "Lía", "Luna")
        self.reader = make_user("lector.union@prueba.mx", "Leo", "Lector")
        reader = self._person(self.church, self.reader, stage="miembro")
        directory.opt_in(self.church, reader, self.reader, fields=(), token=directory.preview_token(reader, (), directory.preview(self.church, reader, ())))

    def entry(self, person, **shown):
        return DirectoryEntry.objects.create(person=person, listed=True, wording_version=directory.WORDING_VERSION, accepted_at=timezone.now(), **shown)

    def listed(self) -> str:
        self.client.force_login(self.reader)
        AuthThrottle.objects.all().delete()
        return rows_of(self.client.get(LIST))

    def test_the_inventory_names_the_relation_and_nothing_moves(self):
        self.assertIn(("people.DirectoryEntry", "person"), merge.NOTHING_MOVES)
        self.assertEqual(merge.undecided_relations(), [])
        with_none = merge.build_preview(self.church, self.keep, self.dup).token
        self.entry(self.dup, show_email=True)
        self.assertEqual(merge.build_preview(self.church, self.keep, self.dup).token, with_none)  # a preview cannot tell

    def test_the_survivor_keeps_its_own_choice_and_never_gains_the_other_records(self):
        Person.objects.filter(pk=self.keep.pk).update(user=self.account, stage="miembro")
        Person.objects.filter(pk=self.dup.pk).update(stage="miembro")
        mine = self.entry(self.keep, show_phone=True)
        theirs = self.entry(self.dup, show_email=True, show_groups=True)
        record = self.do_merge()
        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual((mine.person_id, mine.show_phone, mine.show_email, mine.show_groups), (self.keep.pk, True, False, False))
        self.assertEqual((theirs.person_id, theirs.show_email), (self.dup.pk, True))  # kept for the undo, read by nothing
        self.assertEqual(directory.visible_entries(self.church).filter(person__in=[self.keep, self.dup]).count(), 1)
        html = self.listed()
        self.assertEqual(html.count('<p class="row__title">Lía Luna</p>'), 1)
        self.assertIn("55 3333 4444", html)
        self.assertNotIn("lia@prueba.mx", html.lower())  # the e-mail the absorbed record had chosen to show is not shown
        self.assertNotIn("Lia Luna Ríos", html)
        merge.undo(self.church, record.pk, self.admin)
        mine.refresh_from_db()
        self.assertEqual((mine.show_phone, mine.show_email), (True, False))
        self.assertTrue(DirectoryEntry.objects.get(person=self.dup).show_email)

    def test_the_absorbed_records_choice_is_dropped_with_it_and_the_undo_restores_it(self):
        Person.objects.filter(pk=self.dup.pk).update(user=self.account, stage="miembro", phone="55 4444 5555")
        Person.objects.filter(pk=self.keep.pk).update(stage="miembro")
        self.dup.refresh_from_db()
        token = directory.preview_token(self.dup, ("phone", "email"), directory.preview(self.church, self.dup, ("phone", "email")))
        directory.opt_in(self.church, self.dup, self.account, fields=("phone", "email"), token=token)
        self.assertIn("Lia Luna Ríos", self.listed())
        self.client.force_login(self.account)
        self.assertEqual(self.client.get(LIST).status_code, 200)

        record = self.do_merge(phone=self.keep.pk)
        self.keep.refresh_from_db()
        self.assertEqual(self.keep.user, self.account)  # the account moved to the survivor…
        self.assertFalse(DirectoryEntry.objects.filter(person=self.keep).exists())  # …and the consent did not
        html = self.listed()
        self.assertNotIn("Luna", html)
        self.assertNotIn("55 4444 5555", html)
        self.assertNotIn("55 3333 4444", html)
        self.client.force_login(self.account)
        self.assertEqual(self.client.get(LIST).status_code, 404)  # not in it any more: does not read it
        self.assertNotContains(self.client.get(MINE), "Así te ven hoy")

        merge.undo(self.church, record.pk, self.admin)
        entry = DirectoryEntry.objects.get(person=self.dup)
        self.assertEqual((entry.listed, entry.show_phone, entry.show_email, entry.show_groups), (True, True, True, False))
        html = self.listed()
        self.assertIn("Lia Luna Ríos", html)
        self.assertIn("55 4444 5555", html)
        self.client.force_login(self.account)
        self.assertEqual(self.client.get(LIST).status_code, 200)


class DataExchangeTests(DirectoryFixture):
    def test_the_export_has_no_directory_column_and_the_import_never_writes_a_choice(self):
        self.turn_on()
        self.join(self.ana, "phone")
        header = " ".join(export.HEADER).lower()
        self.assertNotIn("directorio", header)
        self.assertEqual(len(export.HEADER), 15)
        self.assertTrue(all(len(row) == 15 for row in export.rows(self.church)))
        root = Path(__file__).resolve().parents[1]
        for name in ("export.py", "imports.py", "import_views.py", "data_views.py"):
            source = (root / name).read_text()
            self.assertNotIn("Directory", source, name)
            self.assertNotIn("directory", source, name)

    def test_an_import_creates_people_with_no_choice(self):
        from people import imports

        text = "Nombre,Apellidos,Correo,Teléfono,Etapa\nImportada,Sin Elección,importada@prueba.mx,55 7000 4444,Miembro\n"
        raw, problems = imports.read_file(text.encode())
        self.assertEqual(problems, [])
        imports.apply(self.church, raw, self.admin, token=imports.plan(self.church, raw).token)
        person = Person.objects.get(email="importada@prueba.mx")
        self.assertFalse(DirectoryEntry.objects.filter(person=person).exists())
        self.turn_on()
        self.assertFalse(directory.visible_entries(self.church).exists())


class SeedTests(DirectoryFixture):
    def test_the_plain_seed_leaves_it_off_and_the_flag_adds_synthetic_adults_who_chose(self):
        with self.settings(DEBUG=True):
            call_command("seed_community_demo", "--password", "Clave-de-prueba-1", stdout=StringIO())
            self.assertFalse(DirectoryPolicy.objects.exists())
            self.assertFalse(DirectoryEntry.objects.exists())
            for _ in range(2):  # twice: the second run adds nothing
                call_command("seed_community_demo", "--password", "Clave-de-prueba-1", "--directorio", stdout=StringIO())
        policy = DirectoryPolicy.objects.get()
        self.assertTrue(policy.enabled)
        self.assertEqual(policy.accepted_by.email, "pastor.iglesia@demo.invalid")
        shown = directory.visible_entries(policy.church)
        self.assertEqual(shown.count(), 4)
        self.assertEqual(DirectoryEntry.objects.count(), 4)
        self.assertTrue(all(entry.person.email.endswith("@demo.invalid") for entry in shown.select_related("person")))
        self.assertEqual(len({(e.show_phone, e.show_email, e.show_groups, e.show_teams) for e in shown}), 4)
        self.assertFalse(any(minors.known_minor(entry.person) for entry in shown.select_related("person")))
