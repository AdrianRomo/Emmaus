"""Sedes: the church's campuses, captured for every campero at registration and filterable afterwards."""

import csv
import io
import re
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, ProtectedError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Church
from events.models import AuditEvent, Event, EventMembership, EventParticipant, MembershipRole, Sede
from events.services import participants as participant_svc

from .base import CampFixture, make_user


def csv_upload(text, name="camperos.csv"):
    upload = io.BytesIO(text.encode("utf-8"))
    upload.name = name
    return upload


class SedeFixture(CampFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        sede = lambda name, slug, order, **kw: Sede.objects.create(church=cls.church, name=name, slug=slug, order=order, **kw)  # noqa: E731
        cls.satelite = sede("Satélite", "satelite", 1, city="Naucalpan")
        cls.esmeralda = sede("Zona Esmeralda", "esmeralda", 2)  # an identifier that is not the name
        cls.merida = sede("Mérida", "merida", 3, city="Mérida")
        cls.antigua = sede("Sede Antigua", "antigua", 4, active=False)
        cls.p1.sede = cls.merida
        cls.p1.save()
        cls.p3.sede = cls.merida
        cls.p3.save()
        cls.p4.sede = cls.satelite
        cls.p4.save()
        cls.p_other_event.sede = cls.merida  # same church, another event: never counted here
        cls.p_other_event.save()

    def options(self, html, name="sede"):
        select = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
        self.assertIsNotNone(select, f"no hay <select name={name}>")
        return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', select.group(1))


class SedeModelTests(SedeFixture):
    def test_slug_is_unique_per_church(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Sede.objects.create(church=self.church, name="Mérida 2", slug="merida")
        other = Church.objects.create(name="Otra iglesia", slug="otra-iglesia")
        Sede.objects.create(church=other, name="Mérida", slug="merida")  # another church may use the same slug
        self.assertEqual(Sede.objects.filter(slug="merida").count(), 2)

    def test_a_sede_with_camperos_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.merida.delete()
        self.assertTrue(Sede.objects.filter(pk=self.merida.pk).exists())

    def test_the_database_fills_city_order_and_active_by_itself(self):
        """docs/deployment.md: a non-null column carries a database default, so an INSERT that names only the
        required columns (as code that does not know a column yet would write it) still works."""
        q = connection.ops.quote_name
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {q(Sede._meta.db_table)} ({q('church_id')}, {q('name')}, {q('slug')}) VALUES (%s, %s, %s)",
                [self.church.pk, "CDMX", "cdmx"],
            )
        sede = Sede.objects.get(church=self.church, slug="cdmx")
        self.assertEqual((sede.city, sede.order, sede.active), ("", 0, True))

    def test_allowed_sedes_are_the_events_or_every_active_one(self):
        self.assertEqual(list(self.event.allowed_sedes()), [self.satelite, self.esmeralda, self.merida])
        self.event.sedes.set([self.merida, self.antigua])
        self.assertEqual(list(self.event.allowed_sedes()), [self.merida])

    def test_when_none_of_the_events_sedes_is_active_every_active_sede_is_allowed(self):
        """Deactivating (or deleting) every sede the event lists must not leave registration without sedes."""
        self.event.sedes.set([self.antigua])
        self.assertEqual(list(self.event.allowed_sedes()), [self.satelite, self.esmeralda, self.merida])


class SedeCaptureTests(SedeFixture):
    def test_register_form_has_no_sede_without_active_sedes(self):
        Sede.objects.update(active=False)
        self.login(self.coord1)
        response = self.client.get(self.url("participant_create"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="sede"')

    def test_register_form_offers_the_active_sedes_of_the_church(self):
        self.login(self.coord1)
        html = self.client.get(self.url("participant_create")).content.decode()
        self.assertIn('<label class="label" for="id_sede">Sede', html)
        self.assertEqual(
            self.options(html),
            [("", "Sin sede"), (str(self.satelite.pk), "Satélite"), (str(self.esmeralda.pk), "Zona Esmeralda"), (str(self.merida.pk), "Mérida")],
        )

    def test_register_form_limits_to_the_events_sedes(self):
        self.event.sedes.set([self.merida])
        self.login(self.coord1)
        html = self.client.get(self.url("participant_create")).content.decode()
        self.assertEqual(self.options(html), [("", "Sin sede"), (str(self.merida.pk), "Mérida")])

    def test_register_form_offers_every_active_sede_when_the_events_are_all_inactive(self):
        self.event.sedes.set([self.antigua])
        self.login(self.coord1)
        html = self.client.get(self.url("participant_create")).content.decode()
        self.assertEqual(
            self.options(html),
            [("", "Sin sede"), (str(self.satelite.pk), "Satélite"), (str(self.esmeralda.pk), "Zona Esmeralda"), (str(self.merida.pk), "Mérida")],
        )

    def _register(self, client_user, sede, **extra):
        self.login(client_user)
        data = {"full_name": "Renata Flores Gil", "age": 19, "room": "7", "church_group": "Iglesia Centro", "then": "lista", **extra}
        if sede is not None:
            data["sede"] = sede.pk
        return self.client.post(self.url("participant_create"), data)

    def test_coordinator_registers_a_campero_with_a_sede(self):
        response = self._register(self.coord1, self.esmeralda)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EventParticipant.objects.get(full_name="Renata Flores Gil").sede, self.esmeralda)

    def test_admin_registers_a_campero_with_a_sede(self):
        self._register(self.admin, self.satelite, coordinator=self.m_coord2.pk)
        self.assertEqual(EventParticipant.objects.get(full_name="Renata Flores Gil").sede, self.satelite)

    def test_a_sede_outside_the_event_is_refused(self):
        self.event.sedes.set([self.merida])
        response = self._register(self.coord1, self.satelite)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(EventParticipant.objects.filter(full_name="Renata Flores Gil").exists())

    def test_edit_changes_the_sede_and_logs_it(self):
        self.login(self.coord1)
        data = {"full_name": self.p2.full_name, "age": self.p2.age, "room": self.p2.room, "church_group": self.p2.church_group, "sede": self.merida.pk}
        self.client.post(self.url("participant_edit", self.p2.public_id), data)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.sede, self.merida)
        self.assertTrue(AuditEvent.objects.filter(action="campero_modificado", summary__startswith="Cambió sede").exists())

    def test_admin_corrects_the_sede_and_can_leave_it_empty(self):
        self.login(self.admin)
        url = self.url("participant_edit", self.p1.public_id)
        self.assertIn(f'<option value="{self.merida.pk}" selected>Mérida</option>', self.client.get(url).content.decode())
        data = {"full_name": self.p1.full_name, "age": self.p1.age, "room": self.p1.room, "church_group": self.p1.church_group,
                "coordinator": self.m_coord1.pk, "is_active": "on"}
        self.client.post(url, {**data, "sede": self.satelite.pk})
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.sede, self.satelite)
        self.client.post(url, {**data, "sede": ""})  # "Sin sede"
        self.p1.refresh_from_db()
        self.assertIsNone(self.p1.sede)

    def test_edit_keeps_a_sede_deactivated_since(self):
        self.p2.sede = self.antigua
        self.p2.save()
        self.login(self.coord1)
        html = self.client.get(self.url("participant_edit", self.p2.public_id)).content.decode()
        self.assertIn((str(self.antigua.pk), "Sede Antigua"), self.options(html))
        data = {"full_name": self.p2.full_name, "age": 20, "room": self.p2.room, "church_group": self.p2.church_group, "sede": self.antigua.pk}
        self.client.post(self.url("participant_edit", self.p2.public_id), data)
        self.p2.refresh_from_db()
        self.assertEqual((self.p2.age, self.p2.sede), (20, self.antigua))


class SedeImportTests(SedeFixture):
    HEADER = "Nombre,Edad,Cuarto,Iglesia o grupo,Sede\n"

    def test_import_matches_name_or_slug_without_case_or_accents(self):
        text = self.HEADER + (
            "Ana Uno Pérez,15,1,Centro,merida\n"
            "Ana Dos Pérez,15,1,Centro,MÉRIDA\n"
            "Ana Tres Pérez,15,1,Centro,esmeralda\n"
            "Ana Cuatro Pérez,15,1,Centro,zona esmeralda\n"
            "Ana Cinco Pérez,15,1,Centro,Satelite\n"
            "Ana Seis Pérez,15,1,Centro,\n"
        )
        rows, problems = participant_svc.parse_import(self.event, text.encode("utf-8"))
        self.assertEqual(problems, [])
        self.assertEqual([r.errors for r in rows], [[]] * 6)
        self.assertEqual([r.sede_id for r in rows], [self.merida.pk, self.merida.pk, self.esmeralda.pk, self.esmeralda.pk, self.satelite.pk, None])
        result = participant_svc.apply_import(self.event, [r.as_session() for r in rows], actor=self.admin, include_lines={r.line for r in rows})
        self.assertEqual((result.created, result.sede_unavailable), (6, 0))
        self.assertEqual(EventParticipant.objects.get(full_name="Ana Dos Pérez").sede, self.merida)
        self.assertIsNone(EventParticipant.objects.get(full_name="Ana Seis Pérez").sede)

    def test_unknown_sede_is_a_row_error(self):
        text = self.HEADER + "Ana Uno Pérez,15,1,Centro,Guadalajara\nAna Dos Pérez,15,1,Centro,antigua\n"
        rows, _ = participant_svc.parse_import(self.event, text.encode("utf-8"))
        expected = "Sede desconocida: «Guadalajara». Usa una de: Satélite, Zona Esmeralda, Mérida."
        self.assertEqual(rows[0].errors, [expected])
        self.assertEqual(rows[1].errors, ["Sede desconocida: «antigua». Usa una de: Satélite, Zona Esmeralda, Mérida."])  # inactive
        result = participant_svc.apply_import(self.event, [r.as_session() for r in rows], actor=self.admin, include_lines={2, 3})
        self.assertEqual(result.created, 0)

    def test_a_cell_without_letters_never_matches(self):
        """A sede whose name has no letters or digits (made outside the form) is not what a "-" in the file means."""
        Sede.objects.create(church=self.church, name="¿?", slug="signos", order=9)
        rows, _ = participant_svc.parse_import(self.event, (self.HEADER + "Ana Uno Pérez,15,1,Centro,-\n").encode("utf-8"))
        self.assertEqual(rows[0].sede_id, None)
        self.assertTrue(rows[0].errors[0].startswith("Sede desconocida: «-»."), rows[0].errors)

    def test_import_only_takes_the_events_sedes(self):
        self.event.sedes.set([self.merida])
        rows, _ = participant_svc.parse_import(self.event, (self.HEADER + "Ana Uno Pérez,15,1,Centro,Satélite\n").encode("utf-8"))
        self.assertEqual(rows[0].errors, ["Sede desconocida: «Satélite». Usa una de: Mérida."])

    def test_a_sede_in_the_file_when_the_church_has_no_active_one(self):
        Sede.objects.update(active=False)
        rows, _ = participant_svc.parse_import(self.event, (self.HEADER + "Ana Uno Pérez,15,1,Centro,Mérida\n").encode("utf-8"))
        self.assertEqual(rows[0].errors, ["Sede desconocida: «Mérida». La iglesia no tiene sedes activas: deja la columna vacía."])

    def test_confirm_takes_only_a_sede_of_this_church(self):
        """The preview waits in the session: on confirm, a sede that is not this church's (or a preview stored
        before sedes existed, with no sede at all) imports the campero without one."""
        other = Church.objects.create(name="Otra iglesia", slug="otra-iglesia")
        foreign = Sede.objects.create(church=other, name="Lejos", slug="lejos")
        text = self.HEADER + "Ana Uno Pérez,15,1,Centro,merida\nAna Dos Pérez,15,1,Centro,merida\n"
        stored = [r.as_session() for r in participant_svc.parse_import(self.event, text.encode("utf-8"))[0]]
        stored[0]["sede_id"] = foreign.pk
        del stored[1]["sede_id"]
        result = participant_svc.apply_import(self.event, stored, actor=self.admin, include_lines={2, 3})
        self.assertEqual((result.created, result.sede_unavailable), (2, 1))  # the row with no sede at all is not counted
        names = ["Ana Uno Pérez", "Ana Dos Pérez"]
        self.assertEqual(list(EventParticipant.objects.filter(full_name__in=names).values_list("sede", flat=True)), [None, None])

    def test_a_sede_gone_between_preview_and_confirm_is_counted_in_the_message(self):
        temporal = Sede.objects.create(church=self.church, name="Temporal", slug="temporal", order=10)
        text = self.HEADER + (
            "Ana Uno Pérez,15,1,Centro,Temporal\nAna Dos Pérez,15,1,Centro,Mérida\n"
            "Ana Tres Pérez,15,1,Centro,Satélite\nAna Cuatro Pérez,15,1,Centro,\n"
        )
        self.login(self.admin)
        self.client.post(self.url("participant_import"), {"file": csv_upload(text)})
        temporal.delete()  # no camperos, so it may go
        Sede.objects.filter(pk=self.merida.pk).update(active=False)
        response = self.client.post(self.url("participant_import"), {"action": "confirm", "include": ["2", "3", "4", "5"]}, follow=True)
        self.assertContains(response, "Se importaron 4 camperos. 2 camperos quedaron sin sede porque la sede ya no estaba disponible.")
        sedes = dict(EventParticipant.objects.filter(full_name__startswith="Ana ").values_list("full_name", "sede__slug"))
        self.assertEqual(sedes, {"Ana Uno Pérez": None, "Ana Dos Pérez": None, "Ana Tres Pérez": "satelite", "Ana Cuatro Pérez": None})

    def test_a_reading_that_leads_to_two_sedes_is_an_error(self):
        Sede.objects.create(church=self.church, name="Merida", slug="merida-yucatan", order=0)  # made outside the form
        text = self.HEADER + "Ana Uno Pérez,15,1,Centro,merida\nAna Dos Pérez,15,1,Centro,Mérida\nAna Tres Pérez,15,1,Centro,merida-yucatan\n"
        rows, _ = participant_svc.parse_import(self.event, text.encode("utf-8"))
        self.assertTrue(rows[0].errors and rows[0].errors[0].startswith("Sede ambigua: «merida»."), rows[0].errors)
        self.assertTrue(rows[1].errors and rows[1].errors[0].startswith("Sede ambigua: «Mérida»."), rows[1].errors)
        self.assertEqual((rows[2].errors, rows[2].sede_label), ([], "Merida"))
        self.assertEqual([r.sede_id for r in rows[:2]], [None, None])

    def test_import_review_shows_the_sede_and_the_error(self):
        self.login(self.admin)
        text = self.HEADER + "Ana Uno Pérez,15,1,Centro,merida\nAna Dos Pérez,15,1,Centro,Guadalajara\n"
        self.client.post(self.url("participant_import"), {"file": csv_upload(text)})
        response = self.client.get(self.url("participant_import"))
        self.assertContains(response, "<th>Sede</th>", html=True)
        self.assertContains(response, "<td>Mérida</td>", html=True)
        self.assertContains(response, "Sede desconocida: «Guadalajara».")

    def test_import_page_documents_the_column_and_links_the_example(self):
        self.login(self.admin)
        response = self.client.get(self.url("participant_import"))
        self.assertContains(response, "“Coordinador”, “Autorización” y “Sede” son opcionales.")
        self.assertContains(response, "Satélite, Zona Esmeralda, Mérida.")
        self.assertContains(response, f'href="{self.url("participant_import_sample")}"')
        self.assertContains(response, "Descargar archivo de ejemplo")

    def test_import_page_lists_every_active_sede_when_the_events_are_all_inactive(self):
        self.event.sedes.set([self.antigua])
        self.login(self.admin)
        response = self.client.get(self.url("participant_import"))
        self.assertContains(response, "Satélite, Zona Esmeralda, Mérida.")
        self.assertNotContains(response, "no tiene sedes activas")

    def test_import_page_without_sedes_says_to_leave_the_column_empty(self):
        Sede.objects.update(active=False)
        self.login(self.admin)
        response = self.client.get(self.url("participant_import"))
        self.assertContains(response, "La iglesia no tiene sedes activas: deja “Sede” vacía.")
        self.assertContains(response, f'href="{self.url("sedes")}"')
        self.assertContains(response, "Descargar archivo de ejemplo")


class SampleCsvTests(SedeFixture):
    def test_sample_csv_has_bom_headers_and_two_rows(self):
        self.login(self.admin)
        response = self.client.get(self.url("participant_import_sample"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", response["Content-Disposition"])
        body = response.content.decode("utf-8")
        self.assertTrue(body.startswith("﻿"))
        rows = list(csv.reader(io.StringIO(body.lstrip("﻿"))))
        self.assertEqual(rows[0], ["Nombre", "Edad", "Cuarto", "Iglesia o grupo", "Coordinador", "Autorización", "Sede"])
        self.assertEqual(len(rows), 3)
        self.assertEqual([r[6] for r in rows[1:]], ["Satélite", "Zona Esmeralda"])

    def test_the_sample_imports_without_errors(self):
        self.login(self.admin)
        body = self.client.get(self.url("participant_import_sample")).content
        rows, problems = participant_svc.parse_import(self.event, body)
        self.assertEqual(problems, [])
        self.assertEqual([r.errors for r in rows], [[], []])
        self.assertEqual([r.sede_id for r in rows], [self.satelite.pk, self.esmeralda.pk])
        self.assertIsNotNone(rows[0].coordinator_id)


class SedeListTests(SedeFixture):
    def test_filter_by_sede_with_counts(self):
        self.login(self.admin)
        html = self.client.get(self.url("participants")).content.decode()
        self.assertIn('<label class="label" for="f-sede">Sede</label>', html)
        self.assertEqual(
            self.options(html.replace('<select id="f-sede" name="sede">', '<select name="sede">')),
            [("", "Todas"), (str(self.satelite.pk), "Satélite (1)"), (str(self.esmeralda.pk), "Zona Esmeralda (0)"),
             (str(self.merida.pk), "Mérida (2)"), ("ninguna", "Sin sede (2)")],
        )
        filtered = self.client.get(self.url("participants"), {"sede": self.merida.pk})
        self.assertEqual({p.pk for p in filtered.context["page"].object_list}, {self.p1.pk, self.p3.pk})
        none = self.client.get(self.url("participants"), {"sede": "ninguna"})
        self.assertEqual({p.pk for p in none.context["page"].object_list}, {self.p2.pk, self.p_minor_no_auth.pk})

    def test_a_sede_no_longer_offered_stays_in_the_filter_while_camperos_have_it(self):
        EventParticipant.objects.filter(pk=self.p2.pk).update(sede=self.antigua)  # deactivated after registering him
        self.login(self.admin)
        self.assertContains(self.client.get(self.url("participants")), f'<option value="{self.antigua.pk}">Sede Antigua (1)</option>')
        # Chosen, with other filters that leave it at zero: still in the list, so the select shows what is applied.
        response = self.client.get(self.url("participants"), {"sede": self.antigua.pk, "estado": "completada"})
        self.assertContains(response, f'<option value="{self.antigua.pk}" selected>Sede Antigua (0)</option>')

    def test_each_count_is_the_rows_that_choosing_it_shows(self):
        """The counts use the same filters as the rows (participation first among them)."""
        EventParticipant.objects.filter(pk=self.p3.pk).update(is_active=False)  # one of Mérida's two
        self.login(self.admin)
        for params, expected in (({"participa": "si"}, 1), ({"participa": "no"}, 1), ({"participa": "todos"}, 2),
                                 ({"participa": "todos", "coordinador": self.m_coord3.pk}, 0)):
            response = self.client.get(self.url("participants"), params)
            labels = {o["value"]: o["label"] for o in response.context["sede_options"]}
            self.assertEqual(labels[str(self.merida.pk)], f"Mérida ({expected})", params)
            rows = self.client.get(self.url("participants"), {**params, "sede": self.merida.pk})
            self.assertEqual(rows.context["page"].paginator.count, expected, params)

    def test_digits_that_are_not_plain_numbers_do_not_break_the_list(self):
        self.login(self.admin)
        for params in ({"sede": "²"}, {"coordinador": "²"}, {"q": "²"}, {"sede": "1²"}):
            self.assertEqual(self.client.get(self.url("participants"), params).status_code, 200, params)

    def test_a_tutor_counts_only_their_team(self):
        self.login(self.tutor2)  # supervises coord3, whose only campero (p4) is from Satélite
        html = self.client.get(self.url("participants")).content.decode()
        self.assertIn(f'<option value="{self.satelite.pk}">Satélite (1)</option>', html)
        self.assertIn(f'<option value="{self.merida.pk}">Mérida (0)</option>', html)

    def test_no_sede_filter_or_column_without_sedes(self):
        EventParticipant.objects.update(sede=None)
        Sede.objects.all().delete()
        self.login(self.admin)
        response = self.client.get(self.url("participants"))
        self.assertNotContains(response, 'name="sede"')
        self.assertNotContains(response, "orden=sede")  # no "Sede" column header (each header sorts by its column)

    def test_table_column_and_phone_sentence(self):
        self.login(self.admin)
        response = self.client.get(self.url("participants"))
        sede_header = '<th scope="col"><a href="?orden=sede&amp;dir=asc">Sede<span class="visually-hidden">, ordenar</span></a></th>'
        self.assertContains(response, sede_header, html=True)
        self.assertContains(response, "<td>Mérida</td>", html=True)
        html = response.content.decode()
        self.assertIn('<p class="row__meta">Cuarto 1, 15 años, Mérida</p>', html)
        self.assertIn('<p class="row__meta">Cuarto 2, 19 años</p>', html)  # no sede: nothing in its place
        self.assertIn(f'<p class="row__meta">#{self.p1.code}, Iglesia Centro,', html)
        self.assertIn(f'<p class="row__meta">#{self.p2.code}, Iglesia Centro,', html)

    def test_csv_export_has_the_sede(self):
        self.login(self.admin)
        response = self.client.get(self.url("participant_export"))
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8").lstrip("﻿"))))
        sede_at = rows[0].index("Sede")
        self.assertEqual(rows[0][sede_at - 1], "Iglesia o grupo")
        by_code = {r[0]: r[sede_at] for r in rows[1:]}
        self.assertEqual(by_code[self.p1.code], "Mérida")
        self.assertEqual(by_code[self.p2.code], "")

    def test_my_participants_shows_the_sede(self):
        self.login(self.coord1)
        html = self.client.get(self.url("my_participants")).content.decode()
        # Two meta lines, at most three data on the first: where to find them, then who they are
        self.assertIn('<p class="row__meta">Cuarto 1, 15 años, Mérida</p>', html)
        self.assertIn(f'<p class="row__meta">#{self.p1.code}, Iglesia Centro</p>', html)
        self.assertIn('<p class="row__meta">Cuarto 2, 19 años</p>', html)  # no sede: nothing in its place
        self.assertIn(f'<p class="row__meta">#{self.p2.code}, Iglesia Centro</p>', html)


class SedeAdminPageTests(SedeFixture):
    def test_page_lists_the_sedes_on_rotulo(self):
        self.login(self.admin)
        response = self.client.get(self.url("sedes"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("css/rotulo.css", html)
        self.assertEqual(len(re.findall(r"<h1\b", html)), 1)
        self.assertIn('<ul class="rows admin-rows">', html)
        self.assertIn("Naucalpan, activa, 1 campero en este evento", html)
        self.assertIn("Mérida, activa, 2 camperos en este evento", html)
        self.assertIn("Activa, 0 camperos en este evento", html)  # no city: the sentence starts with a capital
        self.assertIn("Inactiva, 0 camperos en este evento", html)
        self.assertIn(f'href="{self.url("sede_edit", self.merida.pk)}"', html)
        self.assertIn('class="choice choice--multi"', html)
        self.assertIn("<legend class=\"legend\">Sedes que participan</legend>", html)
        self.assertNotIn("Las sedes las administra la iglesia.", html)  # a church administrator edits them here

    def test_the_count_is_of_camperos_taking_part_in_this_event(self):
        EventParticipant.objects.filter(pk=self.p3.pk).update(is_active=False)
        self.login(self.admin)
        self.assertContains(self.client.get(self.url("sedes")), "Mérida, activa, 1 campero en este evento")

    def test_without_sedes_the_page_offers_to_add_one_and_nothing_to_choose(self):
        EventParticipant.objects.update(sede=None)
        Sede.objects.all().delete()
        self.login(self.admin)
        response = self.client.get(self.url("sedes"))
        self.assertContains(response, "La iglesia todavía no tiene sedes.")
        self.assertContains(response, f'href="{self.url("sede_add")}"')
        self.assertNotContains(response, "Sedes que participan")  # nothing to choose: no empty form either

    def test_an_inactive_sede_of_the_event_stays_listed_to_unmark_it(self):
        self.login(self.admin)
        self.assertNotContains(self.client.get(self.url("sedes")), "<span>Sede Antigua")  # not the event's: not offered
        self.event.sedes.set([self.antigua, self.merida])
        html = self.client.get(self.url("sedes")).content.decode()
        self.assertIn("<span>Sede Antigua, inactiva</span>", html)
        self.assertRegex(html, rf'<input type="checkbox" name="sedes" value="{self.antigua.pk}" id="[^"]+" checked>')

    def test_the_page_warns_when_a_chosen_sede_is_inactive(self):
        self.login(self.admin)
        self.event.sedes.set([self.antigua, self.merida])
        html = self.client.get(self.url("sedes")).content.decode()
        self.assertIn("Participan estas sedes: Mérida. Al registrar un campero solo se puede elegir una de ellas.", html)
        self.assertIn("Sede Antigua está inactiva: no se ofrece al registrar ni al importar.", html)
        self.event.sedes.set([self.antigua])
        html = self.client.get(self.url("sedes")).content.decode()
        self.assertIn("Participan todas las sedes activas de la iglesia.", html)
        self.assertIn("Ninguna de las sedes marcadas está activa: al registrar y al importar se ofrecen todas las sedes activas de la iglesia.", html)
        self.event.sedes.set([self.merida])
        self.assertNotContains(self.client.get(self.url("sedes")), "inactiva:")

    def test_sedes_pages_are_spanish_and_every_control_is_labelled(self):
        from .test_ui import ENGLISH, analyse

        self.login(self.admin)
        for url in (self.url("sedes"), self.url("sede_add"), self.url("sede_edit", self.esmeralda.pk)):
            html = self.client.get(url).content.decode()
            parsed = analyse(html)
            self.assertIsNone(ENGLISH.search(" ".join(parsed.text)), url)
            for tag, attrs in parsed.controls:
                self.assertIn(attrs.get("id"), parsed.labels_for, (url, tag, attrs.get("name")))
            self.assertEqual(len(re.findall(r"<h1\b", html)), 1, url)
            self.assertIn("css/rotulo.css", html, url)

    def test_add_edit_and_delete_are_in_the_activity_log(self):
        self.login(self.admin)
        self.client.post(self.url("sede_add"), {"name": "Lomas Verdes", "order": 5, "active": "on"})
        lomas = Sede.objects.get(church=self.church, slug="lomas-verdes")
        self.client.post(self.url("sede_edit", lomas.pk), {"name": "Lomas Verdes", "slug": "lomas-verdes", "order": 6})
        self.client.post(self.url("sede_delete", lomas.pk))
        summaries = AuditEvent.objects.filter(event=self.event, action="evento_configurado").order_by("id").values_list("summary", flat=True)
        self.assertEqual(list(summaries), ["Agregó la sede «Lomas Verdes»", "Modificó la sede «Lomas Verdes»", "Borró la sede «Lomas Verdes»"])

    def test_an_administrator_of_one_event_only_chooses_sedes_and_never_edits_them(self):
        """Sedes belong to the church. An Administrador who is only a member of one event (no church role) must not
        rename, deactivate, add or delete the sedes other events' camperos have; choosing participation stays theirs."""
        volunteer = make_user("voluntaria@prueba.mx", "Vera", "Voluntaria")
        EventMembership.objects.create(event=self.other_event, user=volunteer, role=MembershipRole.ADMIN)
        client = self.login(volunteer, Client())
        u = lambda name, *a: reverse(f"events:{name}", args=[self.other_event.slug, *a])  # noqa: E731
        state = lambda: list(Sede.objects.order_by("pk").values_list("pk", "name", "slug", "active"))  # noqa: E731
        before = state()
        for method, url, data in (
            ("get", u("sede_add"), None), ("get", u("sede_edit", self.merida.pk), None),
            ("post", u("sede_add"), {"name": "Intrusa", "order": 0, "active": "on"}),
            ("post", u("sede_edit", self.merida.pk), {"name": "Tijuana", "slug": "tijuana", "order": 0}),
            ("post", u("sede_delete", self.esmeralda.pk), {}),
        ):
            self.assertEqual(getattr(client, method)(url, data or {}).status_code, 403, (method, url))
        self.assertEqual(state(), before)
        page = client.get(u("sedes"))
        self.assertContains(page, "Las sedes las administra la iglesia.")
        self.assertNotContains(page, f'href="{u("sede_add")}"')
        self.assertNotContains(page, f'href="{u("sede_edit", self.merida.pk)}"')
        self.assertRedirects(client.post(u("sedes"), {"sedes": [self.merida.pk]}), u("sedes"))
        self.assertEqual(list(self.other_event.sedes.all()), [self.merida])
        # A superuser administers every church.
        root = make_user("raiz@prueba.mx", "Raúl", "Raíz")
        root.is_superuser = True
        root.save()
        self.assertEqual(self.login(root, Client()).get(u("sede_edit", self.merida.pk)).status_code, 200)

    def test_the_participants_tab_stays_marked_on_the_sedes_pages(self):
        self.login(self.admin)
        marked = f'<a href="{self.url("participants")}" aria-current="page">Camperos</a>'
        for url in (self.url("sedes"), self.url("sede_add"), self.url("sede_edit", self.merida.pk)):
            self.assertContains(self.client.get(url), marked, msg_prefix=url)

    def test_the_participants_page_links_here(self):
        self.login(self.admin)
        self.assertContains(self.client.get(self.url("participants")), f'href="{self.url("sedes")}"')

    def test_add_generates_the_slug_from_the_name(self):
        self.login(self.admin)
        response = self.client.post(self.url("sede_add"), {"name": "  Lomas   Verdes ", "slug": "", "city": "Naucalpan", "order": 5, "active": "on"})
        self.assertRedirects(response, self.url("sedes"))
        sede = Sede.objects.get(church=self.church, slug="lomas-verdes")
        self.assertEqual((sede.name, sede.city, sede.order, sede.active), ("Lomas Verdes", "Naucalpan", 5, True))

    def test_the_slug_can_be_written(self):
        self.login(self.admin)
        self.client.post(self.url("sede_add"), {"name": "Ciudad de México", "slug": "cdmx", "order": 0, "active": "on"})
        self.assertTrue(Sede.objects.filter(church=self.church, slug="cdmx", name="Ciudad de México").exists())

    def test_add_form_page(self):
        self.login(self.admin)
        response = self.client.get(self.url("sede_add"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().count("<h1"), 1)
        for name in ("name", "slug", "city", "order", "active"):
            self.assertContains(response, f'name="{name}"')

    def test_repeated_slug_or_name_is_refused(self):
        self.login(self.admin)
        response = self.client.post(self.url("sede_add"), {"name": "Otra", "slug": "merida", "order": 0})
        self.assertContains(response, "Ya hay una sede con ese identificador.")
        response = self.client.post(self.url("sede_add"), {"name": "MERIDA", "slug": "merida-2", "order": 0})
        self.assertContains(response, "Ya hay una sede con ese nombre.")
        self.assertEqual(Sede.objects.filter(church=self.church).count(), 4)

    def test_a_name_or_identifier_that_reads_as_another_sede_is_refused(self):
        """The import finds a sede by its name or its identifier: each value it reads must lead to one sede."""
        self.login(self.admin)
        # "esmeralda" is Zona Esmeralda's identifier: a sede named "Esmeralda" would take its rows.
        response = self.client.post(self.url("sede_add"), {"name": "Esmeralda", "slug": "esmeralda-norte", "order": 0})
        self.assertContains(response, "Ese nombre es el identificador de la sede «Zona Esmeralda».")
        # "zona-esmeralda" is how the import reads the name "Zona Esmeralda".
        response = self.client.post(self.url("sede_add"), {"name": "Esmeralda Norte", "slug": "zona-esmeralda", "order": 0})
        self.assertContains(response, "Ese identificador se lee como el nombre de la sede «Zona Esmeralda».")
        # Nothing the import could read: a cell with only "-" would land on it.
        response = self.client.post(self.url("sede_add"), {"name": "¿?", "slug": "signos", "order": 0})
        self.assertContains(response, "Escribe un nombre con letras o números.")
        self.assertEqual(Sede.objects.filter(church=self.church).count(), 4)

    def test_a_long_name_cut_to_an_identifier_never_ends_in_a_separator(self):
        """slugify() drops a trailing "_" or "-": an identifier cut at 40 must not keep one, or it would read as
        the name of another sede."""
        self.login(self.admin)
        self.client.post(self.url("sede_add"), {"name": "a" * 39 + "_b", "slug": "", "order": 0, "active": "on"})
        self.assertTrue(Sede.objects.filter(church=self.church, slug="a" * 39).exists())
        response = self.client.post(self.url("sede_add"), {"name": "a" * 39, "slug": "otra", "order": 0})
        self.assertContains(response, "Ese nombre es el identificador de la sede")

    def test_an_identifier_made_outside_the_form_is_compared_as_the_import_reads_it(self):
        Sede.objects.create(church=self.church, name="Mérida Centro", slug="centro_", order=8)  # an older row
        self.login(self.admin)
        response = self.client.post(self.url("sede_add"), {"name": "Centro", "slug": "centro-2", "order": 0})
        self.assertContains(response, "Ese nombre es el identificador de la sede «Mérida Centro».")
        response = self.client.post(self.url("sede_add"), {"name": "Norte", "slug": "centro", "order": 0})
        self.assertContains(response, "Ya hay una sede con ese identificador.")

    def test_edit_deactivates(self):
        self.login(self.admin)
        response = self.client.post(self.url("sede_edit", self.merida.pk), {"name": "Mérida", "slug": "merida", "city": "Mérida", "order": 3})
        self.assertRedirects(response, self.url("sedes"))
        self.merida.refresh_from_db()
        self.assertFalse(self.merida.active)
        self.assertEqual(EventParticipant.objects.get(pk=self.p1.pk).sede, self.merida)  # its camperos keep it

    def test_a_sede_with_camperos_offers_deactivation_not_deletion(self):
        self.login(self.admin)
        response = self.client.get(self.url("sede_edit", self.merida.pk))
        self.assertContains(response, "no se puede borrar")
        self.assertNotContains(response, self.url("sede_delete", self.merida.pk))
        response = self.client.post(self.url("sede_delete", self.merida.pk), follow=True)
        self.assertContains(response, "tiene camperos registrados y no se puede borrar")
        self.assertTrue(Sede.objects.filter(pk=self.merida.pk).exists())

    def test_a_sede_without_camperos_can_be_deleted(self):
        self.login(self.admin)
        response = self.client.get(self.url("sede_edit", self.esmeralda.pk))
        self.assertContains(response, f'action="{self.url("sede_delete", self.esmeralda.pk)}"')
        self.assertContains(response, 'class="btn btn--danger"')
        self.assertEqual(self.client.get(self.url("sede_delete", self.esmeralda.pk)).status_code, 405)  # a link never deletes
        self.assertRedirects(self.client.post(self.url("sede_delete", self.esmeralda.pk)), self.url("sedes"))
        self.assertFalse(Sede.objects.filter(pk=self.esmeralda.pk).exists())

    def test_choose_the_sedes_that_take_part(self):
        self.login(self.admin)
        response = self.client.post(self.url("sedes"), {"sedes": [self.merida.pk, self.satelite.pk]})
        self.assertRedirects(response, self.url("sedes"))
        self.assertEqual(set(self.event.sedes.all()), {self.merida, self.satelite})
        self.assertTrue(AuditEvent.objects.filter(action="evento_configurado", summary__startswith="Cambió las sedes").exists())
        self.client.post(self.url("sedes"), {})
        self.assertEqual(self.event.sedes.count(), 0)  # none marked: the whole church again

    def test_another_churchs_sede_is_not_found(self):
        other = Church.objects.create(name="Otra iglesia", slug="otra-iglesia")
        foreign = Sede.objects.create(church=other, name="Lejos", slug="lejos")
        self.login(self.admin)
        self.assertEqual(self.client.get(self.url("sede_edit", foreign.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url("sede_delete", foreign.pk)).status_code, 404)
        self.client.post(self.url("sedes"), {"sedes": [foreign.pk]})
        self.assertEqual(self.event.sedes.count(), 0)

    def test_only_administrators(self):
        for user in (self.coord1, self.tutor1, self.pastor):
            client = Client()
            self.login(user, client)
            for name, args in (("sedes", ()), ("sede_add", ()), ("sede_edit", (self.merida.pk,)), ("participant_import_sample", ())):
                self.assertEqual(client.get(self.url(name, *args)).status_code, 403, (user.email, name))
            self.assertEqual(client.post(self.url("sede_delete", self.esmeralda.pk)).status_code, 403, user.email)
            self.assertEqual(client.post(self.url("sedes"), {"sedes": [self.merida.pk]}).status_code, 403, user.email)
        self.assertTrue(Sede.objects.filter(pk=self.esmeralda.pk).exists())
        self.assertEqual(self.event.sedes.count(), 0)


class SedePurgeTests(SedeFixture):
    def test_the_retention_purge_deletes_the_camperos_and_their_sede_goes_with_them(self):
        """purge_expired_event_data deletes each EventParticipant (it does not blank fields), so the sede needs nothing
        of its own; PROTECT never blocks it, and the church's list of sedes stays."""
        Event.objects.filter(pk=self.event.pk).update(ends_on=timezone.localdate() - timedelta(days=400), retention_days=30)
        call_command("purge_expired_event_data", "--apply", stdout=StringIO())
        self.assertFalse(EventParticipant.objects.filter(event=self.event).exists())
        self.assertEqual(Sede.objects.filter(church=self.church).count(), 4)
        self.assertEqual(EventParticipant.objects.get(pk=self.p_other_event.pk).sede, self.merida)
        self.satelite.delete()  # its only campero went with the purge: nothing protects it any more
        self.assertFalse(Sede.objects.filter(pk=self.satelite.pk).exists())


class SeedSedesTests(TestCase):
    def _seed(self):
        with override_settings(DEBUG=True):
            call_command("seed_camp_demo", "--reset", "--participants", "40", "--hours", "1", password="x", stdout=StringIO())
        campers = EventParticipant.objects.filter(event__slug="jovenes-2026")
        return dict(campers.values_list("sede__slug").annotate(n=Count("id")).order_by())

    def test_seed_creates_five_sedes_and_gives_one_to_every_campero(self):
        counts = self._seed()
        church = Church.objects.get(slug="iglesia-demo")
        self.assertEqual(
            list(church.sedes.values_list("name", "slug")),
            [("Satélite", "satelite"), ("Zona Esmeralda", "zona-esmeralda"), ("Lomas Verdes", "lomas-verdes"), ("CDMX", "cdmx"), ("Mérida", "merida")],
        )
        self.assertNotIn(None, counts)
        self.assertEqual(sum(counts.values()), 40)
        self.assertEqual(self._seed(), counts)  # the same seed, the same counts; --reset keeps five sedes
        self.assertEqual(church.sedes.count(), 5)

    def test_purge_demo_data_reports_the_sedes_it_takes(self):
        self._seed()
        out = StringIO()
        call_command("purge_demo_data", stdout=out)  # a dry run: nothing is deleted
        self.assertRegex(out.getvalue(), r"\n  Sedes\s+5\n")
        self.assertEqual(Sede.objects.count(), 5)
