"""One helper for every new e-mail notice: ``notify(kind, to, context)``.

A notice says that something waits in Emmaus and where, never who or what (AGENTS.md invariant 7). Its body is the
kind's fixed text in ``templates/notify/<kind>.txt`` with the church name and a link; its subject is fixed per kind
(``SUBJECTS``). ``context`` holds exactly ``ALLOWED_CONTEXT``: any other key is refused with ``ValueError`` before
anything renders, so a caller cannot hand a person's name, a question or a free-text field to a template, not even by
mistake. A refusal names the keys, never their values, so it cannot leak through a log either.

The result says what happened, so a screen never claims an e-mail that did not go out:

* ``"sent"``: the backend accepted the message.
* ``"not_configured"``: the backend delivers nothing (console, file or dummy; ``email_is_configured``) and nothing was
  handed to it. The caller says "No hay correo configurado" instead of "Te enviamos un correo".
* ``"failed"``: the backend raised; ``send_email_safely`` logged the subject and the error.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal
from urllib.parse import urlsplit

from django.template.loader import render_to_string

from .services import send_email_safely

Outcome = Literal["sent", "not_configured", "failed"]
SENT: Outcome = "sent"
NOT_CONFIGURED: Outcome = "not_configured"
FAILED: Outcome = "failed"

# kind -> subject; each kind's body is templates/notify/<kind>.txt.
SUBJECTS: dict[str, str] = {
    # To a group leader: somebody asked to join their group.
    "solicitud_grupo_recibida": "Emmaus: hay una solicitud nueva para tu grupo",
    # To the person who asked to join: the request was decided. The mail never says which way.
    "solicitud_grupo_decidida": "Emmaus: ya hay respuesta a tu solicitud",
    # To the church's welcome team: somebody asked to be contacted.
    "solicitud_contacto_recibida": "Emmaus: hay una solicitud de contacto nueva",
    # To whoever attends open follow-ups of an event (remind_follow_up_owners). Never how many, whose or why.
    "seguimientos_pendientes": "Emmaus: tienes seguimientos por atender",
    # To a person a notice of their church is for, if they chose it (notices.email). Never its title, its
    # text, its group or its author; an important notice uses the same words as a normal one.
    "aviso_publicado": "Emmaus: hay un aviso nuevo de tu iglesia",
    # The same for an urgent notice, and again when what it says changed. "Urgente" is all it tells.
    "aviso_urgente": "Emmaus: hay un aviso urgente de tu iglesia",
    # To somebody on an event's waiting list: a place was freed and is held for them (agenda.services). Never
    # which event beyond the link to its page, never until when, never who left the place.
    "lugar_ofrecido": "Emmaus: se liberó un lugar en un evento",
}

# Everything a notice carries besides its fixed text. Both are required.
ALLOWED_CONTEXT = frozenset({"church_name", "link"})


def render(kind: str, context: Mapping[str, str]) -> tuple[str, str]:
    """The subject and body of a notice, after refusing anything a notice may not carry."""
    if kind not in SUBJECTS:
        raise ValueError(f"Unknown notice kind {kind!r}; known kinds: {', '.join(sorted(SUBJECTS))}.")
    refused = sorted(repr(key) for key in set(context) - ALLOWED_CONTEXT)
    if refused:
        raise ValueError(
            f"A notice may not carry {', '.join(refused)}: only {', '.join(sorted(ALLOWED_CONTEXT))} are allowed, "
            "never a name, an answer or free text."
        )
    missing = sorted(ALLOWED_CONTEXT - set(context))
    if missing:
        raise ValueError(f"A notice needs {', '.join(missing)}.")
    for key in sorted(ALLOWED_CONTEXT):
        value = context[key]
        if not isinstance(value, str) or not value.strip() or value.splitlines() != [value]:
            raise ValueError(f"{key} must be one non-empty line of text.")
    link = context["link"]
    parts = urlsplit(link)
    if parts.scheme not in ("http", "https") or not parts.hostname or any(char.isspace() for char in link):
        raise ValueError("link must be an absolute http(s) address of a page in Emmaus (request.build_absolute_uri).")
    body = render_to_string(f"notify/{kind}.txt", {key: context[key] for key in ALLOWED_CONTEXT})
    return SUBJECTS[kind], body.strip()


def notify(kind: str, to: str | Iterable[str], context: Mapping[str, str]) -> Outcome:
    """Send the ``kind`` notice to ``to`` (one address or several) and say whether it went; see the module docstring."""
    subject, body = render(kind, context)
    recipients = _recipients(to)
    # Imported here: events imports core, so a module-level import would make core.notify unusable from core.services.
    from events.services.team import email_is_configured

    if not email_is_configured():
        return NOT_CONFIGURED
    return SENT if send_email_safely(subject, body, recipients) else FAILED


def _recipients(to: str | Iterable[str]) -> list[str]:
    addresses = [to] if isinstance(to, str) else list(to)
    if not all(isinstance(address, str) for address in addresses):
        raise ValueError("Recipients must be e-mail addresses.")
    recipients = sorted({address.strip() for address in addresses if address.strip()})
    if not recipients:
        raise ValueError("A notice needs at least one recipient address.")
    return recipients
