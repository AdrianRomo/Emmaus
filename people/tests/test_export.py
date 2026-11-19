""""Descargar la lista": the people of one church as CSV, for its administration, audited and limited."""

import csv
import io
from datetime import date

from django.test import Client
from django.urls import reverse

from events.models import AuthThrottle
from groups.tests.base import CommunityFixture
from people import export, flags, services, tags
from people.models import ChurchAuditEvent, Person


class ExportTests(CommunityFixture):
    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("people:export")

    def table(self, response=None) -> list[dict]:
        response = response or self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        text = response.content.decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))

    def test_the_columns_and_one_row_per_person_of_this_church(self):
        coro = tags.create_tag(self.church, "Coro", self.admin)
        llaves = tags.create_tag(self.church, "Llaves", self.admin)
        tags.set_tags(self.church, self.member.pk, {coro.pk, llaves.pk}, self.admin, seen=set())
        services.set_stage(self.church, self.member.pk, "miembro", self.admin, seen="")
        Person.objects.filter(pk=self.member.pk).update(baptized_on=date(2019, 3, 14), arrived_on=date(2015, 1, 2), member_since=date(2020, 6, 1))
        response = self.client.post(self.url)
        self.assertTrue(response.content.startswith("﻿".encode()))
        self.assertIn(f'filename="personas-{self.church.slug}-', response["Content-Disposition"])
        rows = self.table(response)
        self.assertEqual(list(rows[0]), export.HEADER)
        self.assertEqual(len(rows), Person.objects.filter(church=self.church, merged_into__isnull=True).count())
        diego = next(row for row in rows if row["Nombre"] == "Diego")
        self.assertEqual(
            (diego["Apellidos"], diego["Correo"], diego["Teléfono"], diego["Acceso a Emmaus"], diego["Etapa"], diego["Se bautizó el"],
             diego["Llegó a la iglesia el"], diego["Es miembro desde"], diego["Etiquetas"], diego["Fecha de nacimiento"],
             diego["Edad confirmada por el equipo"]),
            ("Hernández Cruz", "diego@prueba.mx", "55 1111 2222", "Sí", "Miembro", "14/03/2019", "02/01/2015", "01/06/2020", "Coro; Llaves", "", ""),
        )
        nora = next(row for row in rows if row["Nombre"] == "Nora")
        self.assertEqual((nora["Acceso a Emmaus"], nora["Etapa"], nora["Etiquetas"]), ("No", "", ""))
        text = response.content.decode("utf-8-sig")
        for words in ("Bruno", "miembro-b@prueba.mx", "Berta"):  # church B
            self.assertNotIn(words, text)

    def test_only_what_the_administration_already_reads(self):
        """No column of an answer, a note, a follow-up, attendance, reading or an authorization, whatever exists."""
        from agenda.models import EventRegistration, GuardianConsent
        from care.models import FollowUp

        FollowUp.objects.create(church=self.church, person=self.member, reason="visita")
        registration = EventRegistration.objects.create(event=self.other_event, person=self.member)
        GuardianConsent.objects.create(registration=registration, guardian_name="Mamá Secreta", relationship="madre", method="papel")
        text = self.client.post(self.url).content.decode("utf-8-sig")
        for words in ("Mamá Secreta", "visita", "madre", self.event.name, self.other_event.name, self.group.name):
            self.assertNotIn(words, text)
        import people.export as module

        source = open(module.__file__).read()
        for app in ("agenda", "care", "groups", "resources", "serving", "notices", "feedback"):
            self.assertNotIn(f"from {app}", source)
            self.assertNotIn(f"import {app}", source)

    def test_hidden_records_are_left_out_and_minors_have_the_same_columns(self):
        Person.objects.create(church=self.church, first_name="Dora", last_name="Unida", email="dora@prueba.mx", merged_into=self.member)
        minor = Person.objects.create(church=self.church, first_name="Mía", last_name="Menor", birth_date=date(2015, 5, 5))
        rows = self.table()
        self.assertFalse(any(row["Nombre"] == "Dora" for row in rows))
        mia = next(row for row in rows if row["Nombre"] == "Mía")
        self.assertEqual(list(mia), export.HEADER)
        self.assertEqual((mia["Fecha de nacimiento"], mia["Edad confirmada por el equipo"]), ("05/05/2015", "No"))
        services.verify_birth_date(minor, self.admin, "documento")
        self.assertEqual(next(row for row in self.table() if row["Nombre"] == "Mía")["Edad confirmada por el equipo"], "Sí")

    def test_formulas_a_person_typed_are_neutralised_in_every_column(self):
        tag = tags.create_tag(self.church, "=HYPERLINK(1)", self.admin)
        evil = Person.objects.create(church=self.church, first_name="=cmd|' /C calc'!A0", last_name="+SUM(1;1)", email="",
                                     phone="-55 1234 5678")
        other = Person.objects.create(church=self.church, first_name="@import", last_name="\tTab")
        tags.set_tags(self.church, evil.pk, {tag.pk}, self.admin, seen=set())
        response = self.client.post(self.url)
        cells = [cell for row in csv.reader(io.StringIO(response.content.decode("utf-8-sig"))) for cell in row]
        self.assertFalse([cell for cell in cells if cell[:1] in ("=", "+", "-", "@", "\t", "\r")])
        for expected in ("'=cmd|' /C calc'!A0", "'+SUM(1;1)", "'-55 1234 5678", "'@import", "'=HYPERLINK(1)"):
            self.assertIn(expected, cells)
        self.assertTrue(other.pk)
        # The rule is the camp export's own: without it these cells would start with the sign.
        self.assertEqual(export.safe_cell("=1+1"), "'=1+1")

    def test_one_audit_row_with_the_count_and_nothing_else(self):
        rows = self.table()
        row = ChurchAuditEvent.objects.get(action="personas_exportadas")
        self.assertEqual((row.metadata, row.actor), ({"count": len(rows)}, self.admin))
        for person in Person.objects.filter(church=self.church):
            self.assertNotIn(person.first_name, row.summary)
        self.client.get(self.url)  # reading the page is not a download
        self.assertEqual(ChurchAuditEvent.objects.filter(action="personas_exportadas").count(), 1)

    def test_the_page_says_what_it_holds_and_a_get_downloads_nothing(self):
        page = self.client.get(self.url)
        self.assertEqual(page["Content-Type"].split(";")[0], "text/html")
        self.assertContains(page, "Queda registrado que lo descargaste y cuántas filas tenía.")
        self.assertContains(page, "No trae respuestas de cuestionarios")
        self.assertNotContains(page, "diego@prueba.mx")

    def test_it_is_throttled_per_administrator(self):
        for _ in range(5):
            self.assertEqual(self.client.post(self.url)["Content-Type"], "text/csv; charset=utf-8")
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "No se descargó nada.", status_code=429)
        self.assertNotIn(b"diego@prueba.mx", response.content)
        self.assertEqual(ChurchAuditEvent.objects.filter(action="personas_exportadas").count(), 5)
        self.assertTrue(AuthThrottle.objects.filter(key=f"people-export:{self.church.pk}:{self.admin.pk}").exists())
        # Another church's administrator has their own count, and gets their own church.
        self.client.force_login(self.admin_b)
        text = self.client.post(self.url).content.decode("utf-8-sig")
        self.assertIn("Bruno", text)
        self.assertNotIn("Diego", text)

    def test_the_number_of_queries_does_not_grow_with_the_church(self):
        self.client.post(self.url)
        with self.assertNumQueries(2):
            first = export.rows(self.church)
        for number in range(30):
            Person.objects.create(church=self.church, first_name=f"Persona {number}")
        with self.assertNumQueries(2):
            self.assertEqual(len(export.rows(self.church)), len(first) + 30)


class ExportDenialTests(CommunityFixture):
    def test_only_the_administration_and_only_with_the_module_on(self):
        url = reverse("people:export")
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(url).status_code, 302)
        for user in (self.member_user, self.leader_user, self.coord1):
            self.client.force_login(user)
            for method in (self.client.get, self.client.post):
                response = method(url)
                self.assertEqual(response.status_code, 403, user.email)
                self.assertNotIn(b"diego@prueba.mx", response.content)
        flags.set_enabled(self.church, "people", False)
        self.client.force_login(self.admin)
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(url).status_code, 404)
        self.assertFalse(ChurchAuditEvent.objects.filter(action="personas_exportadas").exists())

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(reverse("people:export")).status_code, 403)
        self.assertFalse(ChurchAuditEvent.objects.filter(action="personas_exportadas").exists())
