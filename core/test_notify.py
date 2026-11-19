"""core.notify: new e-mail notices carry their kind's fixed text, the church name and a link, and nothing else.

Like ``core/tests.py::NotificationPrivacyTests``, but enforced before anything renders: a context with a person's
name, a question or any other free-text field is refused (AGENTS.md invariant 7).
"""

from __future__ import annotations

import re
from pathlib import Path
from smtplib import SMTPException
from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.test import SimpleTestCase, override_settings

from core import notify as notify_module
from core.notify import ALLOWED_CONTEXT, FAILED, NOT_CONFIGURED, SENT, SUBJECTS, notify, render
from core.test_locale import FORMAL
from events.services.team import UNDELIVERED_EMAIL_BACKENDS

TEMPLATES = Path(settings.BASE_DIR) / "templates" / "notify"
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
CONSOLE = "django.core.mail.backends.console.EmailBackend"

KINDS = {
    "solicitud_grupo_recibida", "solicitud_grupo_decidida", "solicitud_contacto_recibida", "seguimientos_pendientes",
    "aviso_publicado", "aviso_urgente", "lugar_ofrecido", "recordatorio_servir",
    "peticion_oracion_nueva",
}
LINK = "https://emmaus.example.org/grupos/solicitudes/"
CONTEXT = {"church_name": "Iglesia Fe & Esperanza", "link": LINK}

# The whole mail, pinned: the only parts that come from the caller are the church name and the link.
EXPECTED_BODIES = {
    "solicitud_grupo_recibida": (
        "Hola:\n\n"
        "Una persona pidió unirse a tu grupo en Iglesia Fe & Esperanza. "
        "Entra a Emmaus para ver la solicitud y responderla:\n\n"
        f"{LINK}\n\n"
        "Por privacidad, este correo no incluye el nombre de la persona ni lo que escribió."
    ),
    "solicitud_grupo_decidida": (
        "Hola:\n\n"
        "Ya hay respuesta a tu solicitud para unirte a un grupo en Iglesia Fe & Esperanza. "
        "Entra a Emmaus para verla:\n\n"
        f"{LINK}\n\n"
        "Por privacidad, la respuesta solo se ve en Emmaus."
    ),
    "solicitud_contacto_recibida": (
        "Hola:\n\n"
        "Una persona pidió que alguien de Iglesia Fe & Esperanza se comunique con ella. "
        "Entra a Emmaus para ver la solicitud y darle seguimiento:\n\n"
        f"{LINK}\n\n"
        "Por privacidad, este correo no incluye el nombre de la persona, cómo contactarla ni lo que escribió."
    ),
    "seguimientos_pendientes": (
        "Hola:\n\n"
        "Tienes seguimientos abiertos a tu cargo en Iglesia Fe & Esperanza. "
        "Entra a Emmaus para ver cuáles son y atenderlos:\n\n"
        f"{LINK}\n\n"
        "Por privacidad, este correo no incluye nombres, motivos ni respuestas."
    ),
    "aviso_publicado": (
        "Hola:\n\n"
        "Hay un aviso nuevo para ti en Iglesia Fe & Esperanza. "
        "Entra a Emmaus con tu cuenta para leerlo:\n\n"
        f"{LINK}\n\n"
        "Por privacidad, este correo no incluye el aviso: solo se lee en Emmaus. "
        "En «Mis datos» eliges qué avisos te llegan por correo."
    ),
    "aviso_urgente": (
        "Hola:\n\n"
        "Hay un aviso urgente para ti en Iglesia Fe & Esperanza. "
        "Entra a Emmaus con tu cuenta para leerlo y responder «Enterado»:\n\n"
        f"{LINK}\n\n"
        "Por privacidad, este correo no incluye el aviso: solo se lee en Emmaus. "
        "En «Mis datos» eliges qué avisos te llegan por correo."
    ),
    "lugar_ofrecido": (
        "Hola:\n\n"
        "Se liberó un lugar en un evento de Iglesia Fe & Esperanza en el que estás en lista de espera. "
        "Entra a Emmaus con tu cuenta para aceptarlo o dejarlo pasar:\n\n"
        f"{LINK}\n\n"
        "El lugar se guarda para ti por tiempo limitado: en Emmaus ves hasta cuándo. "
        "Por privacidad, este correo no incluye más detalles."
    ),
    "recordatorio_servir": (
        "Hola:\n\n"
        "Tienes un servicio próximo en Iglesia Fe & Esperanza. "
        "Entra a Emmaus con tu cuenta para ver el día y la hora, y para decir si puedes:\n\n"
        f"{LINK}\n\n"
        "Por privacidad, este correo no incluye más detalles. "
        "Si ya no quieres estos recordatorios, puedes apagarlos en «Servir»."
    ),
    "peticion_oracion_nueva": (
        "Hola:\n\n"
        "Hay una petición de oración nueva en Emmaus, en Iglesia Fe & Esperanza. "
        "Entra a Emmaus con tu cuenta para leerla:\n\n"
        f"{LINK}\n\n"
        "Por privacidad, este correo no dice quién la escribió ni qué dice: solo se lee en Emmaus. "
        "En «Peticiones de oración» eliges si te llega este correo."
    ),
}

# What a careless caller could hand over: a person's name, a question, an answer, free text, the decision.
PRIVATE_FIELDS = {
    "name": "Mariana López",
    "first_name": "Mariana",
    "email": "mariana.lopez@demo.invalid",
    "question": "¿Puedo hablar con alguien sobre mi familia?",
    "answer": "Me siento solo desde que llegué a la ciudad.",
    "message": "Llámenme después de las seis, por favor.",
    "group_name": "Jóvenes de Satélite",
    "decision": "aprobada",
    "user": object(),
}


class FailingBackend(BaseEmailBackend):
    """A configured backend whose server refuses the message."""

    def send_messages(self, email_messages):
        raise SMTPException("550 refused")


class RenderTests(SimpleTestCase):
    def test_each_kind_has_a_template_and_each_template_a_kind(self):
        self.assertEqual(set(SUBJECTS), KINDS)
        self.assertEqual({path.stem for path in TEMPLATES.glob("*.txt")}, KINDS)

    def test_each_kind_renders_its_fixed_text_in_spanish_tu_voice(self):
        for kind in sorted(KINDS):
            with self.subTest(kind):
                subject, body = render(kind, CONTEXT)
                self.assertEqual(subject, SUBJECTS[kind])
                self.assertEqual(body, EXPECTED_BODIES[kind])
                self.assertTrue(subject.startswith("Emmaus: "), subject)
                self.assertIn("Entra a Emmaus", body)
                self.assertIsNone(FORMAL.search(subject), subject)
                self.assertIsNone(FORMAL.search(body), body)
                self.assertNotIn("&amp;", body)  # plain text: the church name is not HTML-escaped

    def test_the_decision_mail_does_not_say_which_way(self):
        subject, body = render("solicitud_grupo_decidida", CONTEXT)
        for word in ("aprob", "acept", "rechaz", "negad", "bienvenid"):
            self.assertNotIn(word, f"{subject}\n{body}".lower(), word)

    def test_a_notice_mail_says_only_that_there_is_one_and_only_the_urgent_one_says_its_level(self):
        subject, body = render("aviso_publicado", CONTEXT)
        for word in ("urgente", "importante", "grupo", "título"):
            self.assertNotIn(word, f"{subject}\n{body}".replace(LINK, "").lower(), word)
        subject, body = render("aviso_urgente", CONTEXT)
        self.assertIn("urgente", subject)
        for word in ("importante", "grupo", "título"):
            self.assertNotIn(word, f"{subject}\n{body}".replace(LINK, "").lower(), word)

    def test_templates_use_only_the_church_name_and_the_link(self):
        for path in sorted(TEMPLATES.glob("*.txt")):
            with self.subTest(path.name):
                source = path.read_text(encoding="utf-8")
                variables = {match.strip() for match in re.findall(r"{{(.*?)}}", source)}
                self.assertEqual(variables, ALLOWED_CONTEXT)  # no filters, no attributes, nothing else
                tags = set(re.findall(r"{%\s*(\w+)", source))
                self.assertEqual(tags, {"autoescape", "endautoescape"})
                self.assertNotIn("{#", source)

    def test_the_body_varies_only_by_the_church_name_and_the_link(self):
        other = {"church_name": "Comunidad Cristiana Monte Sión", "link": "http://192.168.0.10:8000/x/9/"}
        for kind in sorted(KINDS):
            with self.subTest(kind):
                first = render(kind, CONTEXT)[1].replace(CONTEXT["church_name"], "<IGLESIA>").replace(LINK, "<LINK>")
                second = render(kind, other)[1].replace(other["church_name"], "<IGLESIA>").replace(other["link"], "<LINK>")
                self.assertEqual(first, second)
                self.assertEqual(render(kind, CONTEXT)[0], render(kind, other)[0])  # the subject is fixed

    def test_the_allowlist_is_the_church_name_and_the_link(self):
        self.assertEqual(ALLOWED_CONTEXT, frozenset({"church_name", "link"}))

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_a_name_or_free_text_in_the_context_is_refused_and_nothing_is_sent(self):
        for key, value in PRIVATE_FIELDS.items():
            with self.subTest(key):
                with self.assertRaises(ValueError) as caught:
                    notify("solicitud_grupo_recibida", ["lider@demo.invalid"], {**CONTEXT, key: value})
                self.assertIn(repr(key), str(caught.exception))
                self.assertNotIn(str(value), str(caught.exception))  # the refusal never repeats what it refused
                with self.assertRaises(ValueError):
                    render("solicitud_grupo_recibida", {**CONTEXT, key: value})
        self.assertEqual(mail.outbox, [])

    def test_both_keys_are_required(self):
        for missing in sorted(ALLOWED_CONTEXT):
            with self.subTest(missing):
                context = {key: value for key, value in CONTEXT.items() if key != missing}
                with self.assertRaisesRegex(ValueError, missing):
                    render("solicitud_contacto_recibida", context)

    def test_values_are_one_line_of_text_and_the_link_an_absolute_web_address(self):
        bad = [
            {"church_name": ""},
            {"church_name": "   "},
            {"church_name": None},
            {"church_name": object()},
            {"church_name": "Iglesia Central\nMariana López pidió unirse"},
            {"church_name": "Iglesia Central\r\nBcc: otro@demo.invalid"},
            {"link": "Mariana López pidió unirse a Jóvenes"},
            {"link": "/grupos/solicitudes/"},
            {"link": "javascript:alert(1)"},
            {"link": "mailto:mariana.lopez@demo.invalid"},
            {"link": "https://emmaus.example.org/grupos/ Mariana"},
            {"link": "https:///sin-servidor/"},
        ]
        for override in bad:
            with self.subTest(override):
                with self.assertRaises(ValueError):
                    render("solicitud_grupo_decidida", {**CONTEXT, **override})

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Unknown notice kind"):
            render("respuesta_del_cuestionario", CONTEXT)


class DeliveryTests(SimpleTestCase):
    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_the_console_backend_is_not_configured_and_nothing_is_handed_to_it(self):
        with mock.patch.object(notify_module, "send_email_safely") as send:
            for kind in sorted(KINDS):
                with self.subTest(kind):
                    self.assertEqual(notify(kind, ["lider@demo.invalid"], CONTEXT), NOT_CONFIGURED)
        send.assert_not_called()  # so the caller shows "No hay correo configurado" and nothing lands in a log
        self.assertEqual(NOT_CONFIGURED, "not_configured")

    def test_every_backend_that_delivers_nothing_is_not_configured(self):
        for backend in sorted(UNDELIVERED_EMAIL_BACKENDS):
            with self.subTest(backend), override_settings(EMAIL_BACKEND=backend):
                with mock.patch.object(notify_module, "send_email_safely") as send:
                    self.assertEqual(notify("solicitud_contacto_recibida", "bienvenida@demo.invalid", CONTEXT),
                                     NOT_CONFIGURED)
                send.assert_not_called()

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_the_refusal_comes_first_even_without_email(self):
        # Development runs on the console backend: a caller's leak must fail there, not only in production.
        with self.assertRaises(ValueError):
            notify("solicitud_grupo_recibida", ["lider@demo.invalid"], {**CONTEXT, "name": "Mariana López"})

    @override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL="Emmaus <no-reply@demo.invalid>")
    def test_a_configured_backend_gets_one_mail_with_the_fixed_text(self):
        for kind in sorted(KINDS):
            with self.subTest(kind):
                mail.outbox = []
                self.assertEqual(notify(kind, ["lider@demo.invalid"], CONTEXT), SENT)
                self.assertEqual(len(mail.outbox), 1)
                message = mail.outbox[0]
                self.assertEqual(message.subject, SUBJECTS[kind])
                self.assertEqual(message.body, EXPECTED_BODIES[kind])
                self.assertEqual(message.to, ["lider@demo.invalid"])
                self.assertEqual(message.from_email, "Emmaus <no-reply@demo.invalid>")
                for value in PRIVATE_FIELDS.values():
                    self.assertNotIn(str(value), message.subject + message.body)
        self.assertEqual(SENT, "sent")

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_recipients_are_one_address_or_several(self):
        self.assertEqual(notify("solicitud_grupo_decidida", "persona@demo.invalid", CONTEXT), SENT)
        self.assertEqual(mail.outbox[-1].to, ["persona@demo.invalid"])
        team = ["b@demo.invalid", " a@demo.invalid ", "", "b@demo.invalid"]
        self.assertEqual(notify("solicitud_contacto_recibida", team, CONTEXT), SENT)
        self.assertEqual(mail.outbox[-1].to, ["a@demo.invalid", "b@demo.invalid"])
        for nobody in ([], [""], "", ["  "]):
            with self.subTest(nobody), self.assertRaisesRegex(ValueError, "recipient"):
                notify("solicitud_contacto_recibida", nobody, CONTEXT)
        with self.assertRaises(ValueError):
            notify("solicitud_contacto_recibida", [None], CONTEXT)
        self.assertEqual(len(mail.outbox), 2)

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_a_caller_may_hand_over_an_open_connection_and_the_mail_is_the_same(self):
        connection = mail.get_connection()
        with mock.patch.object(connection, "send_messages", wraps=connection.send_messages) as send:
            self.assertEqual(notify("aviso_urgente", "persona@demo.invalid", CONTEXT, connection=connection), SENT)
        send.assert_called_once()
        self.assertEqual(mail.outbox[-1].body, EXPECTED_BODIES["aviso_urgente"])

    def test_a_mail_server_that_hangs_is_given_up_on(self):
        self.assertEqual(settings.EMAIL_TIMEOUT, 10)

    @override_settings(EMAIL_BACKEND="core.test_notify.FailingBackend")
    def test_a_backend_that_refuses_the_message_is_failed_not_sent(self):
        with self.assertLogs("core.services", "ERROR") as logs:
            self.assertEqual(notify("solicitud_grupo_recibida", ["lider@demo.invalid"], CONTEXT), FAILED)
        self.assertIn(SUBJECTS["solicitud_grupo_recibida"], logs.output[0])
        self.assertEqual(FAILED, "failed")
