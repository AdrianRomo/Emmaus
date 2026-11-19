"""The two e-mails of an event's subscriptions (issue #96, second part): "confirma tu dirección" and "hay un
aviso nuevo". Written with the discipline of ``core.notify`` and kept apart from it on purpose.

Why not ``core.notify``: its contract is two values (the church's name and one link), a text that sends the
reader to their account, and a sender that logs the mail server's error, which can quote the recipient. These
e-mails go to somebody WITHOUT an account, need a second link (to unsubscribe), the two ``List-Unsubscribe``
headers, and an address that must never reach a log. Widening ``core.notify`` for that would loosen the rule
every other e-mail of Emmaus relies on; so the same rule is applied here to a slightly longer list.

What an e-mail may carry (``ALLOWED``): the church's name, the EVENT's name (the name of an event whose page is
public is public), the link, and for a notice the link to unsubscribe. Never the notice's title or text, never
anything about a person. Any other key is refused before anything renders, and a refusal names keys, not values.
The subject is fixed per kind. A failure is logged by its class name only: never the address, never the error's
message.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from urllib.parse import urlsplit

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from core.notify import FAILED, NOT_CONFIGURED, SENT, Outcome
from events.services.team import email_is_configured

logger = logging.getLogger(__name__)

CONFIRM = "confirmar"
# The same request, when it is the last one this address will get for the event: the text says so.
CONFIRM_LAST = "confirmar_ultimo"
NOTICE = "aviso_nuevo"
# Signing up without an account (``agenda.visitors``, issue #93): "confirma tu inscripción" and "se liberó un lugar".
# The same rule: the church's name, the event's name and one link. Never the visitor's name or what they answered.
VISITOR_CONFIRM = "inscripcion_confirmar"
VISITOR_OFFER = "inscripcion_lugar"
SUBJECTS = {
    CONFIRM: "Emmaus: confirma tu correo para los avisos de un evento",
    CONFIRM_LAST: "Emmaus: confirma tu correo para los avisos de un evento",
    NOTICE: "Emmaus: hay un aviso nuevo de un evento",
    VISITOR_CONFIRM: "Emmaus: confirma tu inscripción a un evento",
    VISITOR_OFFER: "Emmaus: se liberó un lugar en un evento",
}
ALLOWED = {
    CONFIRM: frozenset({"church_name", "event_name", "link"}),
    CONFIRM_LAST: frozenset({"church_name", "event_name", "link"}),
    NOTICE: frozenset({"church_name", "event_name", "link", "unsubscribe_link"}),
    VISITOR_CONFIRM: frozenset({"church_name", "event_name", "link"}),
    VISITOR_OFFER: frozenset({"church_name", "event_name", "link"}),
}
LINKS = ("link", "unsubscribe_link")
NAME_MAX = 200


def _is_web_address(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.hostname) and not any(char.isspace() for char in value)


def render(kind: str, context: Mapping[str, str]) -> tuple[str, str]:
    """The subject and the body, after refusing anything such an e-mail may not carry."""
    if kind not in SUBJECTS:
        raise ValueError(f"Unknown subscription e-mail {kind!r}.")
    allowed = ALLOWED[kind]
    refused = sorted(repr(key) for key in set(context) - allowed)
    if refused:
        raise ValueError(f"A subscription e-mail may not carry {', '.join(refused)}: only {', '.join(sorted(allowed))}.")
    missing = sorted(allowed - set(context))
    if missing:
        raise ValueError(f"A subscription e-mail needs {', '.join(missing)}.")
    for key in sorted(allowed):
        value = context[key]
        if not isinstance(value, str) or not value.strip() or value.splitlines() != [value]:
            raise ValueError(f"{key} must be one non-empty line of text.")
        if key in LINKS and not _is_web_address(value):
            raise ValueError(f"{key} must be an absolute http(s) address.")
        if key not in LINKS and len(value) > NAME_MAX:
            raise ValueError(f"{key} is too long.")
    body = render_to_string(f"agenda/mail/{kind}.txt", {key: context[key] for key in allowed})
    return SUBJECTS[kind], body.strip()


def send(kind: str, to: str, context: Mapping[str, str], connection=None) -> Outcome:
    """Hand one e-mail to the mail server and say whether it took it. One recipient, always."""
    subject, body = render(kind, context)
    if not isinstance(to, str) or not to.strip() or to.splitlines() != [to]:
        raise ValueError("A subscription e-mail goes to one address.")
    if not email_is_configured():
        return NOT_CONFIGURED
    headers = {}
    if kind == NOTICE:
        # RFC 8058: the mail program's own "darse de baja" button posts to the same address the link opens.
        headers = {"List-Unsubscribe": f"<{context['unsubscribe_link']}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    try:
        message = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [to.strip()], headers=headers, connection=connection)
        message.send(fail_silently=False)
    except Exception as error:  # noqa: BLE001 - whatever the mail server says: its message can quote the address
        logger.error("A subscription e-mail could not be sent: %s", type(error).__name__)
        return FAILED
    return SENT
