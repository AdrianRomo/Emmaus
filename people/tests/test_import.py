""""Importar personas": a CSV reviewed before anything is written, confirmed with a token, undone within 24 hours."""

import re
from datetime import date, timedelta
from html import unescape

from django.contrib.auth.models import User
from django.core import mail, signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from groups import services as groups
from groups.tests.base import CommunityFixture
from people import flags, imports, tags
from people.models import ChurchAuditEvent, PeopleImport, Person, PersonTag

HEAD = "Nombre,Apellidos,Correo,Teléfono,Fecha de nacimiento,Etapa,Etiquetas\n"


def upload(text, name="personas.csv", encoding="utf-8"):
    data = text if isinstance(text, bytes) else text.encode(encoding)
    return SimpleUploadedFile(name, data, content_type="text/csv")


class ImportFixture(CommunityFixture):
    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("people:import")
        self.before = Person.objects.count()

    def review(self, text, client=None, **kwargs):
        return (client or self.client).post(self.url, {"file": upload(text, **kwargs)})

    @staticmethod
    def fields(response) -> dict:
        html = response.content.decode()
        found = {name: unescape(value) for name, value in re.findall(r'<input type="hidden" name="(revision|datos)" value="([^"]*)"', html)}
        return {"accion": "confirmar", **found}

    def confirm(self, response, client=None, **changes):
        return (client or self.client).post(self.url, {**self.fields(response), **changes})

    def do_import(self, text) -> PeopleImport:
        response = self.confirm(self.review(text))
        self.assertEqual(response.status_code, 302, response.content.decode()[:300])
        return PeopleImport.objects.latest("pk")

    def created(self):
        return Person.objects.filter(church=self.church, imported_in__isnull=False)


class PreviewAndConfirmTests(ImportFixture):
    def test_nothing_is_written_until_the_confirmation_and_then_exactly_the_new_rows(self):
        coro = tags.create_tag(self.church, "Coro", self.admin)
        text = HEAD + (
            "Rosa,Peña Gil,ROSA@prueba.mx,55 9000 0001,14/03/1990,Miembro,coro\n"
            "Luis,Sosa,,55 9000 0002,,,\n"
            "Diego,Otro Apellido,diego@prueba.mx,,,,\n"  # the e-mail of a person the church already has
            ",Sin Nombre,,,,,\n"
        )
        response = self.review(text)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        for words in ("Importar 2 personas", "Tu archivo tiene 4 filas con datos", "Fila 4: Diego Otro Apellido", "el mismo correo",
                      "Fila 5: Sin Nombre", "Falta el nombre.", "Todavía no se guarda nada."):
            self.assertContains(response, words)
        self.assertContains(response, f'href="{reverse("people:detail", args=[self.member.pk])}"')
        self.assertEqual(Person.objects.count(), self.before)
        self.assertFalse(PeopleImport.objects.exists())
        self.assertFalse(ChurchAuditEvent.objects.filter(action__startswith="personas_import").exists())

        confirmed = self.confirm(response)
        record = PeopleImport.objects.get()
        self.assertRedirects(confirmed, reverse("people:import_detail", args=[record.pk]))
        self.assertEqual((record.church, record.imported_by, record.created_count), (self.church, self.admin, 2))
        rosa = Person.objects.get(email="rosa@prueba.mx")
        self.assertEqual(
            (rosa.full_name, rosa.phone, rosa.birth_date, rosa.stage, rosa.stage_changed_on, rosa.created_from, rosa.imported_in, rosa.user),
            ("Rosa Peña Gil", "55 9000 0001", date(1990, 3, 14), "miembro", timezone.localdate(), "import", record, None),
        )
        self.assertEqual(list(PersonTag.objects.filter(person=rosa).values_list("tag_id", flat=True)), [coro.pk])
        luis = Person.objects.get(first_name="Luis")
        self.assertEqual((luis.preferred_contact_method, luis.stage, luis.stage_changed_on), ("phone", "", None))
        self.assertEqual(Person.objects.count(), self.before + 2)
        # The person it matched was not touched.
        diego = Person.objects.get(pk=self.member.pk)
        self.assertEqual((diego.last_name, diego.phone), ("Hernández Cruz", "55 1111 2222"))
        # One audit row, with counts and nothing a person typed.
        row = ChurchAuditEvent.objects.get(action="personas_importadas")
        self.assertEqual(row.metadata, {"import_id": record.pk, "count": 2})
        self.assertNotIn("Rosa", row.summary)
        self.assertFalse(ChurchAuditEvent.objects.filter(action="persona_creada").exists())

    def test_the_preview_reads_top_to_bottom_counts_then_rows_then_the_confirmation(self):
        tags.create_tag(self.church, "Coro", self.admin)
        text = HEAD + "Rosa,Peña,rosa@prueba.mx,,,,\nRosita,Peña,rosa@prueba.mx,,,,\nDiego,X,diego@prueba.mx,,,,\n,Sin Nombre,,,,,\n"
        html = self.review(text).content.decode()
        order = ["Tu archivo tiene 4 filas con datos", 'id="se-registran"', 'id="ya-existen"', 'id="repetidas"', 'id="con-errores"',
                 'id="confirmar"', "Al confirmar se registra", "Importar 1 persona</button>", "Volver sin importar"]
        places = [html.index(words) for words in order]
        self.assertEqual(places, sorted(places))
        self.assertEqual(html.count("<button class=\"btn btn--primary\""), 1)

    def test_it_never_creates_an_account_sends_mail_or_verifies_an_age(self):
        users = User.objects.count()
        self.do_import(HEAD + "Rosa,Peña,rosa@prueba.mx,,14/03/2012,,\n")
        rosa = Person.objects.get(email="rosa@prueba.mx")
        self.assertEqual((User.objects.count(), len(mail.outbox), rosa.user_id), (users, 0, None))
        self.assertFalse(User.objects.filter(email__iexact="rosa@prueba.mx").exists())
        self.assertEqual((rosa.birth_date_verified, rosa.birth_date_verified_at, rosa.birth_date_verified_method), (False, None, ""))

    def test_a_minor_is_said_in_the_preview_and_stands_where_one_registered_by_hand_stands(self):
        born = timezone.localdate() - timedelta(days=365 * 10)
        response = self.review(HEAD + f"Mía,Menor,mia@prueba.mx,,{born.strftime('%d/%m/%Y')},,\n")
        self.assertContains(response, "Una es menor de edad")
        self.assertContains(response, "es menor de edad")
        self.assertContains(response, "queda sin confirmar")
        self.confirm(response)
        mia = Person.objects.get(email="mia@prueba.mx")
        self.assertEqual((mia.birth_date, mia.birth_date_verified, mia.user_id), (born, False, None))
        # The rule of the manual form's own next step still holds: no account without the guardian's yes.
        response = self.client.post(reverse("people:grant_access", args=[mia.pk]), follow=True)
        self.assertContains(response, "Confirma que su mamá, papá o tutor está de acuerdo")
        self.assertIsNone(Person.objects.get(pk=mia.pk).user_id)

    def test_the_file_as_spreadsheets_save_it(self):
        cases = {
            "bom": ("﻿" + HEAD + "José,Núñez,jose@prueba.mx,,,,\n").encode("utf-8"),
            "semicolons": "Nombre;Apellidos;Correo;Teléfono\nJosé;Núñez;jose@prueba.mx;55 9000 0003\n".encode("utf-8"),
            "latin-1": (HEAD + "José,Núñez,jose@prueba.mx,,,,\n").encode("latin-1"),
            "lenient headers": "  NOMBRE ,apellido,E-mail,CELULAR\nJosé,Núñez,jose@prueba.mx,55 9000 0003\n".encode("utf-8"),
            "one name column": "Nombre completo,Correo\nJosé Núñez,jose@prueba.mx\n".encode("utf-8"),
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.do_import(data)
                jose = Person.objects.get(email="jose@prueba.mx")
                self.assertEqual(jose.full_name, "José Núñez")
                jose.delete()

    def test_limits_and_files_that_cannot_be_read(self):
        many = HEAD + "".join(f"Persona {n},Apellido,,,,,\n" for n in range(2001))
        cases = [
            (many, "más de 2 000 filas"), ("Correo,Teléfono\na@prueba.mx,1\n", "Falta la columna Nombre"),
            (HEAD, "no tiene filas con datos"), (HEAD + "Rosa," + "x" * 140000 + "\n", "No pudimos leer el archivo"),
        ]
        for text, words in cases:
            response = self.review(text)
            self.assertContains(response, words, status_code=400)
            self.assertNotContains(response, 'name="datos"', status_code=400)
        response = self.client.post(self.url, {"file": upload(b"x" * (1024 * 1024 + 1))})
        self.assertContains(response, "El máximo es 1 MB.")
        self.assertContains(self.client.post(self.url, {"file": upload(HEAD, name="personas.xlsx")}), "El archivo debe ser .csv")
        self.assertContains(self.client.post(self.url, {}), "Elige un archivo CSV.")
        self.assertEqual(Person.objects.count(), self.before)
        # Exactly 2,000 rows is a file like any other, and its undo deletes them all.
        exact = HEAD + "".join(f"Persona {n},Apellido,p{n}@prueba.mx,,,,\n" for n in range(2000))
        record = self.do_import(exact)
        self.assertEqual((record.created_count, self.created().count()), (2000, 2000))
        imports.undo(self.church, record.pk, self.admin, token=imports.undo_plan(record).token)
        self.assertEqual(Person.objects.count(), self.before)

    def test_rows_with_errors_say_the_line_and_the_reason_and_nothing_typed_is_run(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).strftime("%d/%m/%Y")
        text = HEAD + (
            "<script>alert(1)</script>,Peña,no-es-correo,,,,\n"
            "=SUM(1),Fórmula,,,,,\n"
            f"Futura,Fecha,,,{tomorrow},,\n"
            "Mala,Fecha,,,31/02/2001,,\n"
            "Otra,Etapa,,,,líder,\n"
            "Sin,Etiqueta,,,,,Inexistente\n"
            f"{'x' * 121},Largo,,,,,\n"
            "Buena,Fila,,=55,,,\n"
        )
        response = self.review(text)
        html = response.content.decode()
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        for words in ("Fila 2", "El correo no es válido", "Fila 3", "no pueden empezar con =", "Fila 4", "Revisa la fecha de nacimiento",
                      "Fila 5", "no se entiende", "Fila 6", "No conocemos esa etapa", "Fila 7", "una etiqueta que tu iglesia no ha creado",
                      "Fila 8", "son muy largos", "Fila 9", "El teléfono no es válido", "Este archivo no registra a nadie"):
            self.assertIn(words, html)
        self.assertNotIn('name="datos"', html)
        self.assertEqual(Person.objects.count(), self.before)
        self.assertFalse(PersonTag.objects.exists())  # an unknown tag never becomes a tag
        self.assertEqual(tags.church_tags(self.church).count(), 0)

    def test_rows_repeated_inside_the_file_are_created_once(self):
        text = HEAD + (
            "Rosa,Peña,rosa@prueba.mx,,,,\n"
            "Rosita,Peña,ROSA@prueba.mx,,,,\n"            # same e-mail
            "Luis,Sosa,,+52 55 9000 0002,,,\n"
            "Luisa,Sosa,,5590000002,,,\n"                  # same phone
            "Ana,Ríos,,,01/02/1980,,\n"
            "ANA,rios,,,1980-02-01,,\n"                    # same name and date of birth
            "Solo,Nombre,,,,,\n"
            "Solo,Nombre,,,,,\n"                           # the same row, with nothing else to compare
            "Ana,Ríos,,,03/04/1999,,\n"                    # the same name and another date: another person
        )
        response = self.review(text)
        for words in ("Importar 5 personas", "Fila 3: Rosita Peña", "Tiene el mismo correo que la fila 2", "Fila 5: Luisa Sosa",
                      "Tiene el mismo teléfono que la fila 4", "Fila 7: ANA rios", "el mismo nombre y la misma fecha de nacimiento que la fila 6",
                      "Fila 9: Solo Nombre", "el mismo nombre y ningún otro dato que la fila 8"):
            self.assertContains(response, words)
        self.confirm(response)
        self.assertEqual(sorted(self.created().values_list("first_name", flat=True)), ["Ana", "Ana", "Luis", "Rosa", "Solo"])

    def test_each_signal_matches_a_person_the_church_has_and_only_those(self):
        Person.objects.filter(pk=self.no_account.pk).update(birth_date=date(1980, 2, 1))
        Person.objects.create(church=self.church, first_name="Dora", last_name="Unida", email="dora@prueba.mx", merged_into=self.member)
        text = HEAD + (
            "X,Y,DIEGO@prueba.mx,,,,\n"
            "X,Z,,+52 55 1111 2222,,,\n"
            "nora,SIN CUENTA,,,01/02/1980,,\n"
            "Nora,Sin Cuenta,,,02/02/1980,,\n"   # same name, another date of birth: not a match
            "Nora,Sin Cuenta,,,,,\n"             # same name alone: not a signal
            "Dora,Unida,dora@prueba.mx,,,,\n"    # the e-mail of a record that was merged away: not a person of the list
        )
        plan = imports.plan(self.church, imports.read_file(text.encode())[0])
        self.assertEqual([row.status for row in plan.rows], ["ya_existe", "ya_existe", "ya_existe", "nueva", "nueva", "nueva"])
        self.assertEqual([row.match.pk if row.match else None for row in plan.rows[:3]], [self.member.pk, self.member.pk, self.no_account.pk])
        with self.assertNumQueries(3):
            imports.plan(self.church, imports.read_file(text.encode())[0])

    def test_a_person_of_another_church_is_never_revealed_or_touched(self):
        Person.objects.filter(pk=self.member_b.pk).update(phone="55 7000 0007", birth_date=date(1991, 1, 1))
        text = HEAD + "Bruno,Miembro,miembro-b@prueba.mx,55 7000 0007,01/01/1991,,\n"
        response = self.review(text)
        self.assertContains(response, "Importar 1 persona")
        html = response.content.decode()
        for words in ("Ya existen: no se crean</h2>", "Iglesia B", reverse("people:detail", args=[self.member_b.pk])):
            self.assertNotIn(words, html)
        # Byte for byte what the same row says when no church has that person.
        Person.objects.filter(pk=self.member_b.pk).update(email="", phone="", birth_date=None, first_name="Otro")
        clean = self.review(text).content.decode()
        strip = lambda page: re.sub(r'name="(csrfmiddlewaretoken|datos)" value="[^"]+"', "", page)  # noqa: E731
        self.assertEqual(strip(html), strip(clean))
        self.confirm(response)
        mine = Person.objects.get(church=self.church, email="miembro-b@prueba.mx")
        self.assertEqual((mine.church, mine.user), (self.church, None))
        self.assertEqual(Person.objects.filter(church=self.church_b).count(), 2)
        self.assertEqual(Person.objects.get(pk=self.member_b.pk).church, self.church_b)


class CellsThatCannotBeStoredTests(ImportFixture):
    """What the independent review reproduced: a cell is refused in the preview, never cleaned, never cut."""

    def assert_refused(self, cell_row: str, words: str):
        response = self.review(HEAD + cell_row + "Buena,Fila,buena@prueba.mx,,,,\n")
        self.assertContains(response, "Fila 2")
        self.assertContains(response, words)
        self.assertContains(response, "Importar 1 persona")
        self.confirm(response)
        self.assertEqual(list(self.created().values_list("first_name", flat=True)), ["Buena"])
        self.created().delete()

    def test_a_control_character_is_an_error_of_its_row(self):
        for bad in ("\x00", "\x01", "\x1f", "\x7f", "\x85", "\u202e", "\u2066", "\u200f", "\u061c", "\u2028", "\u2029", "\t"):
            for row in (f"Ro{bad}sa,Peña,,,,,\n", f"Rosa,Peña,ro{bad}sa@prueba.mx,,,,\n", f"Rosa,Peña,,55 1{bad}2,,,\n"):
                with self.subTest(repr(bad), row=row[:12]):
                    self.assert_refused(row, "caracteres que no se ven")
        for person in Person.objects.all():
            self.assertFalse(any(ord(ch) < 32 for ch in person.first_name + person.email + person.phone))
        rows, _ = imports.read_file((HEAD + "Ro\x00sa,Peña,,,,,\n").encode())
        self.assertEqual((rows[0]["first_name"], rows[0]["unfit"]), ("", ["control"]))  # not even carried to the confirmation

    def test_a_cell_that_is_too_long_is_an_error_and_is_never_cut(self):
        tags.create_tag(self.church, "Coro", self.admin)
        long_tags = "Coro;" + ";".join(["Inexistente"] * 20)  # cut at 200 it read as «Coro» alone
        long_mail = "a" * 191 + "@prueba.mx.invalid"  # cut at 200 it was another, valid address
        self.assertGreater(len(long_mail), 200)
        for row in (f"Rosa,Peña,,,,,{long_tags}\n", f"Rosa,Peña,{long_mail},,,,\n", f"Rosa,{'x' * 201},,,,,\n"):
            self.assert_refused(row, "Una celda es demasiado larga")
        self.assertFalse(PersonTag.objects.exists())
        self.assertFalse(Person.objects.filter(email__startswith="aaaa").exists())
        # 200 characters is not too long for the cell; the field's own limit then speaks.
        self.assertContains(self.review(HEAD + f"Rosa,{'x' * 200},,,,,\n"), "son muy largos")

    def test_a_name_that_grows_when_normalised_is_an_error_not_a_failed_import(self):
        grows = "\ufb03" * 100  # 100 characters, 300 once normalised: more than ``normalized_name`` holds
        self.assert_refused(f"{grows},Peña,,,,,\n", "letras especiales que ocupan más")
        # The manual form says the same instead of failing on PostgreSQL.
        response = self.client.post(reverse("people:create"), {"first_name": grows, "preferred_contact_method": "email"})
        self.assertContains(response, "letras especiales que ocupan más")
        response = self.client.post(reverse("people:create"), {"first_name": "Ro\u202esa", "preferred_contact_method": "email"})
        self.assertContains(response, "caracteres que no se ven")
        self.assertEqual(Person.objects.count(), self.before)

    def test_a_value_the_database_refuses_is_said_in_words(self):
        from unittest import mock

        from django.db import DataError

        response = self.review(HEAD + "Rosa,Peña,,,,,\n")
        with mock.patch.object(Person, "save", side_effect=DataError("value too long")):
            answer = self.confirm(response, follow=True) if False else self.client.post(self.url, self.fields(response), follow=True)
        self.assertContains(answer, "No se registró a nadie.")
        self.assertEqual((Person.objects.count(), PeopleImport.objects.count()), (self.before, 0))


class ConfirmationTests(ImportFixture):
    TEXT = HEAD + "Rosa,Peña,rosa@prueba.mx,,,,\nLuis,Sosa,luis@prueba.mx,,,,\n"

    def test_confirming_twice_creates_everybody_once(self):
        response = self.review(self.TEXT)
        self.assertEqual(self.confirm(response).status_code, 302)
        again = self.confirm(response)
        self.assertEqual(again.status_code, 200)
        for words in ("No registramos a nadie.", "Este archivo no registra a nadie", "Fila 2: Rosa Peña"):
            self.assertContains(again, words)
        self.assertEqual((self.created().count(), PeopleImport.objects.count()), (2, 1))
        self.assertEqual(ChurchAuditEvent.objects.filter(action="personas_importadas").count(), 1)

    def test_the_church_changed_between_the_preview_and_the_confirmation(self):
        response = self.review(self.TEXT)
        Person.objects.create(church=self.church, first_name="Rosa", last_name="Ya Estaba", email="rosa@prueba.mx")
        again = self.confirm(response)
        for words in ("No registramos a nadie.", "Importar 1 persona", "Fila 2: Rosa Peña"):
            self.assertContains(again, words)
        self.assertEqual((self.created().count(), PeopleImport.objects.count()), (0, 0))
        self.assertEqual(self.confirm(again).status_code, 302)  # the new preview is the one that confirms
        self.assertEqual(list(self.created().values_list("first_name", flat=True)), ["Luis"])

    def test_a_changed_file_is_not_what_was_reviewed(self):
        response = self.review(self.TEXT)
        fields = self.fields(response)
        # The cells were altered on their way back: the signature says so, and nothing is written.
        tampered = fields["datos"][:40] + ("A" if fields["datos"][40] != "A" else "B") + fields["datos"][41:]
        for datos in (tampered, "", "no-es-una-firma", signing.dumps({"church": self.church.pk, "user": self.admin.pk, "rows": []}, salt="otra")):
            answer = self.client.post(self.url, {**fields, "datos": datos})
            self.assertContains(answer, "la revisión ya no es válida", status_code=400)
        # Another file's cells, properly signed, under the first file's token: shown again, not imported.
        other = self.fields(self.review(HEAD + "Intrusa,Nueva,intrusa@prueba.mx,,,,\n"))
        answer = self.client.post(self.url, {**fields, "datos": other["datos"]})
        self.assertContains(answer, "No registramos a nadie.")
        self.assertContains(answer, "Fila 2: Intrusa Nueva")
        # No token at all.
        self.assertContains(self.client.post(self.url, {"accion": "confirmar", "datos": fields["datos"]}), "No registramos a nadie.")
        self.assertEqual((self.created().count(), PeopleImport.objects.count()), (0, 0))

    def test_a_preview_signed_for_another_church_or_account_or_too_old_is_refused(self):
        rows, _ = imports.read_file(self.TEXT.encode())
        token = imports.plan(self.church, rows).token
        for packed in (imports.pack(self.church_b, self.admin, rows), imports.pack(self.church, self.admin_b, rows)):
            answer = self.client.post(self.url, {"accion": "confirmar", "datos": packed, "revision": token})
            self.assertContains(answer, "la revisión ya no es válida", status_code=400)
        packed = imports.pack(self.church, self.admin, rows)
        with self.settings():
            old, imports.PACK_MAX_AGE = imports.PACK_MAX_AGE, -1
            try:
                answer = self.client.post(self.url, {"accion": "confirmar", "datos": packed, "revision": token})
            finally:
                imports.PACK_MAX_AGE = old
        self.assertContains(answer, "la revisión ya no es válida", status_code=400)
        self.assertEqual(self.created().count(), 0)
        # The other church's administrator, with their own preview, imports into their own church only.
        client_b = Client()
        client_b.force_login(self.admin_b)
        self.assertEqual(self.confirm(self.review(self.TEXT, client=client_b), client=client_b).status_code, 302)
        self.assertEqual(Person.objects.filter(church=self.church_b, imported_in__isnull=False).count(), 2)
        self.assertEqual(self.created().count(), 0)

    def test_the_service_refuses_a_token_that_is_not_the_plans(self):
        rows, _ = imports.read_file(self.TEXT.encode())
        with self.assertRaises(imports.StalePreview):
            imports.apply(self.church, rows, self.admin, token="")
        with self.assertRaises(imports.StalePreview):  # the token of church B's plan of the same file
            imports.apply(self.church, rows, self.admin, token=imports.plan(self.church_b, rows).token)
        self.assertEqual(Person.objects.count(), self.before)


class UndoTests(ImportFixture):
    TEXT = HEAD + "Rosa,Peña,rosa@prueba.mx,,,,\nLuis,Sosa,luis@prueba.mx,,,,\nEva,Lara,eva@prueba.mx,,,,\n"

    def setUp(self):
        super().setUp()
        coro = tags.create_tag(self.church, "Coro", self.admin)
        self.record = self.do_import(self.TEXT.replace("eva@prueba.mx,,,,", "eva@prueba.mx,,,,Coro"))
        self.detail = reverse("people:import_detail", args=[self.record.pk])
        self.undo_url = reverse("people:import_undo", args=[self.record.pk])
        self.coro = coro

    def token(self):
        return imports.undo_plan(self.record).token

    def test_undo_deletes_exactly_the_rows_it_created(self):
        page = self.client.get(self.detail)
        self.assertContains(page, "se borran 3 personas de las que registró")
        response = self.client.post(self.undo_url, {"revision": self.token()}, follow=True)
        self.assertContains(response, "se borraron 3 personas")
        self.assertEqual((Person.objects.count(), PersonTag.objects.count()), (self.before, 0))
        self.assertTrue(tags.church_tags(self.church).filter(pk=self.coro.pk).exists())  # the tag itself stays
        self.record.refresh_from_db()
        self.assertEqual((self.record.undone_by, self.record.undo_report), (self.admin, {"deleted": 3, "kept": 0}))
        row = ChurchAuditEvent.objects.get(action="personas_importacion_deshecha")
        self.assertEqual(row.metadata, {"import_id": self.record.pk, "count": 3, "kept": 0})
        # Once: a second undo says so and deletes nobody.
        response = self.client.post(self.undo_url, {"revision": self.token()}, follow=True)
        self.assertContains(response, "ya se había deshecho")
        self.assertEqual(Person.objects.count(), self.before)

    def test_a_person_who_joined_a_group_since_stays_and_is_reported(self):
        rosa = Person.objects.get(email="rosa@prueba.mx")
        groups.set_membership(self.group, rosa, self.admin)
        page = self.client.get(self.detail)
        self.assertContains(page, "se borran 2 personas de las que registró")
        self.assertContains(page, "1 persona se queda")
        self.assertContains(page, "Rosa Peña")
        response = self.client.post(self.undo_url, {"revision": self.token()}, follow=True)
        self.assertContains(response, "Se borraron 2 personas; 1 se quedó en la lista")
        self.assertEqual(list(self.created().values_list("first_name", flat=True)), ["Rosa"])
        self.assertTrue(self.group.memberships.filter(person=rosa, status="activa").exists())
        self.record.refresh_from_db()
        self.assertEqual(self.record.undo_report, {"deleted": 2, "kept": 1})

    def test_whatever_a_person_gained_keeps_them(self):
        from agenda.models import EventRegistration
        from care.models import FollowUp
        from people import merge

        rosa, luis, eva = (Person.objects.get(email=f"{name}@prueba.mx") for name in ("rosa", "luis", "eva"))
        Person.objects.filter(pk=rosa.pk).update(user=User.objects.create_user("rosa-cuenta", "rosa@prueba.mx"))
        EventRegistration.objects.create(event=self.other_event, person=luis)
        self.assertEqual(imports.undo_plan(self.record).delete, [eva.pk])
        FollowUp.objects.create(church=self.church, person=eva, reason="visita")
        self.assertEqual(imports.undo_plan(self.record).delete, [])
        FollowUp.objects.all().delete()
        self.assertEqual(imports.undo_plan(self.record).delete, [eva.pk])
        # A relation of wave 10 that this module never names: a place in a team's rotation.
        from serving import services as serving
        from serving.models import RotationEntry, TeamRole

        team = serving.save_team(self.church, self.admin, name="Sonido")
        entry = RotationEntry.objects.create(role=TeamRole.objects.create(team=team, name="Consola"), person=eva, position=1)
        self.assertEqual(imports.undo_plan(self.record).delete, [])
        entry.delete()
        self.assertEqual(imports.undo_plan(self.record).delete, [eva.pk])
        # A record that received a merge, and the one that was merged away, both stay.
        twin = Person.objects.create(church=self.church, first_name="Eva", last_name="Lara B", email="EVA@prueba.mx")
        token = merge.build_preview(self.church, eva, twin).token
        merge.merge(self.church, eva.pk, twin.pk, self.admin, choices={}, token=token)
        self.assertEqual(imports.undo_plan(self.record).delete, [])
        self.assertNotContains(self.client.get(self.detail), 'action="' + self.undo_url)
        # Only a tag (the import's own, or one added since) does not keep anybody.
        self.assertEqual(imports.IGNORED_RELATIONS, {("people.PersonTag", "person")})

    def test_the_page_says_everything_the_undo_deletes(self):
        page = self.client.get(self.detail)
        for words in ("sus etiquetas", "su etapa", "sus fechas clave", "la confirmación de su edad", "cualquier corrección que les hayas hecho a mano"):
            self.assertContains(page, words)

    def test_the_confirmation_is_of_what_the_page_said(self):
        token = self.token()
        groups.set_membership(self.group, Person.objects.get(email="luis@prueba.mx"), self.admin)
        response = self.client.post(self.undo_url, {"revision": token}, follow=True)
        self.assertContains(response, "No deshicimos nada")
        self.assertContains(response, "se borran 2 personas de las que registró")
        self.assertEqual(self.created().count(), 3)
        self.assertEqual(self.client.post(self.undo_url, {}).status_code, 302)  # no token: nothing
        self.assertEqual(self.created().count(), 3)

    def test_after_24_hours_it_cannot_be_undone(self):
        PeopleImport.objects.filter(pk=self.record.pk).update(imported_at=timezone.now() - timedelta(hours=24, minutes=1))
        page = self.client.get(self.detail)
        self.assertContains(page, "ya no se puede deshacer")
        self.assertNotContains(page, 'name="revision"')
        response = self.client.post(self.undo_url, {"revision": self.token()}, follow=True)
        self.assertContains(response, "Pasaron más de 24 horas")
        self.assertEqual(self.created().count(), 3)
        PeopleImport.objects.filter(pk=self.record.pk).update(imported_at=timezone.now() - timedelta(hours=23, minutes=59))
        self.assertTrue(imports.can_undo(PeopleImport.objects.get(pk=self.record.pk)))

    def test_people_the_church_had_before_and_other_imports_are_never_deleted(self):
        other = self.do_import(HEAD + "Otra,Importación,otra@prueba.mx,,,,\n")
        self.client.post(self.undo_url, {"revision": self.token()})
        self.assertEqual(list(self.created().values_list("imported_in_id", flat=True)), [other.pk])
        self.assertEqual(Person.objects.count(), self.before + 1)
        self.assertTrue(Person.objects.filter(pk=self.member.pk).exists())


class ImportDenialTests(ImportFixture):
    def setUp(self):
        super().setUp()
        self.record = self.do_import(HEAD + "Rosa,Peña,rosa@prueba.mx,,,,\n")
        self.client.logout()

    def pages(self):
        return [
            ("get", self.url, None), ("get", reverse("people:import_sample"), None),
            ("get", reverse("people:import_detail", args=[self.record.pk]), None),
            ("post", self.url, {"file": upload(HEAD + "Nueva,Persona,,,,,\n")}),
            ("post", self.url, {"accion": "confirmar", "datos": "x", "revision": "x"}),
            ("post", reverse("people:import_undo", args=[self.record.pk]), {"revision": imports.undo_plan(self.record).token}),
        ]

    def ask(self, method, url, data):
        if isinstance(data, dict) and "file" in data:
            data = {"file": upload(HEAD + "Nueva,Persona,,,,,\n")}
        return getattr(self.client, method)(url, data or {})

    def test_only_the_administration_of_this_church(self):
        for page in self.pages():
            self.assertEqual(self.ask(*page).status_code, 302)
        for user in (self.member_user, self.leader_user, self.coord1):
            self.client.force_login(user)
            for page in self.pages():
                named = any(part.isdigit() for part in page[1].split("/"))  # an import by its id: one answer, 404
                self.assertEqual(self.ask(*page).status_code, 404 if named else 403, (user.email, page[1]))
        # Church B's administration: the import of church A does not exist, by its guessed id.
        self.client.force_login(self.admin_b)
        self.assertEqual(self.client.get(reverse("people:import_detail", args=[self.record.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("people:import_undo", args=[self.record.pk]), {"revision": "x"}).status_code, 404)
        self.assertNotContains(self.client.get(self.url), "Importaciones recientes")
        with self.assertRaises(imports.PeopleError):
            imports.undo(self.church_b, self.record.pk, self.admin_b, token=imports.undo_plan(self.record).token)
        self.assertEqual(self.created().count(), 1)
        flags.set_enabled(self.church, "people", False)
        self.client.force_login(self.admin)
        for page in self.pages():
            self.assertEqual(self.ask(*page).status_code, 404, page[1])
        self.assertEqual((self.created().count(), PeopleImport.objects.count()), (1, 1))

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        for method, url, data in self.pages():
            if method == "post":
                if "file" in data:
                    data = {"file": upload(HEAD + "Nueva,Persona,,,,,\n")}
                self.assertEqual(client.post(url, data).status_code, 403, url)
        self.assertEqual((self.created().count(), PeopleImport.objects.count()), (1, 1))

    def test_the_sample_file_is_accepted_by_the_import(self):
        self.client.force_login(self.admin)
        sample = self.client.get(reverse("people:import_sample")).content
        rows, problems = imports.read_file(sample)
        self.assertEqual(problems, [])
        self.assertEqual([row.status for row in imports.plan(self.church, rows).rows], ["nueva", "nueva"])

    def test_an_export_comes_back_as_people_who_already_exist(self):
        """The round trip: the list downloaded from Emmaus, uploaded again, creates nobody (phones the export protected included)."""
        self.client.force_login(self.admin)
        Person.objects.filter(pk=self.no_account.pk).update(phone="+52 55 3000 0003")
        exported = self.client.post(reverse("people:export")).content
        rows, problems = imports.read_file(exported)
        self.assertEqual(problems, [])
        plan = imports.plan(self.church, rows)
        nora = next(row for row in plan.rows if row.first_name == "Nora")
        self.assertEqual((nora.phone, nora.status, nora.match.pk), ("+52 55 3000 0003", "ya_existe", self.no_account.pk))
