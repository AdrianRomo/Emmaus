"""Django's own messages in tú-voice Mexican Spanish (locale/es_MX overrides Django's "usted" catalogs)."""

import ast
import gettext
import re
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.utils import translation

CATALOG = Path(settings.BASE_DIR) / "locale" / "es_MX" / "LC_MESSAGES"
FORMAL = re.compile(r"\b(Introduzca|Ingrese|Asegúrese|Seleccione|Escoja|Elija|Escriba|Por favor|usted|Su |su |sus |Verifique|Compruebe|Corrija|Envíe|fichero)\b")


def _po_entries(path: Path) -> dict:
    """The catalog's entries: msgid -> msgstr, or (msgid, n) -> msgstr[n] for plurals (single-line strings)."""
    entries, msgid = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(" ")
        if line.startswith("#") or not value.startswith('"'):
            continue  # comments, and the header's continuation lines
        text = ast.literal_eval(value)
        if key == "msgid":
            msgid = text
        elif key == "msgstr":
            entries[msgid] = text
        elif key.startswith("msgstr["):
            entries[(msgid, int(key[7:-1]))] = text
    entries.pop("", None)
    return entries


def _errors(field, value):
    try:
        field.clean(value)
    except ValidationError as error:
        return error.messages
    return []


class LocaleCatalogTests(SimpleTestCase):
    def test_compiled_catalog_matches_the_source(self):
        """Edit the .po, then run `manage.py compilemessages -l es_MX`: a stale .mo would ship the old wording."""
        source = _po_entries(CATALOG / "django.po")
        with open(CATALOG / "django.mo", "rb") as handle:
            compiled = gettext.GNUTranslations(handle)._catalog
        compiled = {key: value for key, value in compiled.items() if key != ""}
        self.assertTrue(source)
        self.assertEqual(source, compiled)

    def test_the_overrides_speak_tu(self):
        for key, text in _po_entries(CATALOG / "django.po").items():
            self.assertIsNone(FORMAL.search(text), f"{key!r}: {text!r}")


class DjangoMessagesInTuTests(SimpleTestCase):
    def setUp(self):
        translation.activate("es-mx")
        self.addCleanup(translation.deactivate)

    def test_form_field_messages(self):
        self.assertEqual(_errors(forms.URLField(), "sitio"), ["Escribe una dirección web válida, por ejemplo: https://ejemplo.com."])
        self.assertEqual(_errors(forms.ChoiceField(choices=[("a", "A")]), "z"), ["Elige una de las opciones de la lista. z no es una de ellas."])
        self.assertEqual(_errors(forms.CharField(min_length=5), "ab"), ["Escribe al menos 5 caracteres (ahora hay 2)."])
        self.assertEqual(_errors(forms.CharField(max_length=1), "ab"), ["Escribe como máximo 1 carácter (ahora hay 2)."])
        self.assertEqual(_errors(forms.DateTimeField(), "ayer"), ["Escribe una fecha y hora válidas."])
        self.assertEqual(_errors(forms.DecimalField(max_digits=3), "12345"), ["Usa como máximo 3 dígitos en total."])

    def test_file_messages_say_archivo(self):
        self.assertEqual(_errors(forms.FileField(), SimpleUploadedFile("camperos.csv", b"")), ["El archivo está vacío."])
        self.assertEqual(_errors(forms.FileField(), None), ["Este campo es obligatorio."])

    def test_password_rules(self):
        with self.assertRaises(ValidationError) as caught:
            validate_password("1234")
        self.assertEqual(
            caught.exception.messages,
            ["Esta contraseña es muy corta. Usa al menos 8 caracteres.", "Esta contraseña es muy común.", "Esta contraseña tiene solo números."],
        )
