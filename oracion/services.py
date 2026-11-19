"""Every write of the prayer requests, and the church's switch.

What holds whoever calls:

* nothing is stored while the church's switch is off, and only a pastor of the church moves it;
* a request is written by an adult person about themselves, to the pastors named on the form
  (``readers_token``: if who reads changed while the form was open, nothing is saved);
* a reader's action finds its row through ``access.readable`` under a lock, so a pastor who lost the
  role a moment ago, or one the request was not addressed to, finds nothing;
* the words (``body``, ``reply``, the church's own line) are never handed to a logger, an audit
  row, an e-mail or an exception message. An audit row says who of the readers did what to which id;
  writing and deleting a request leave **no** audit row, so nothing outlives the request that says
  who asked for prayer.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core import notify
from people import policy
from people.audit import record_church
from people.models import Person

from . import access
from .models import BODY_MAX, EMERGENCY_LINE_MAX, REPLY_MAX, PrayerPolicy, PrayerRequest, ReaderPreference

# The version of the promises a pastor accepts on the policy screen. Change it when the promises
# change in substance: every church then has to accept again before it can be switched on again.
PROMISES_VERSION = "2026-11"
# How many requests one person may have stored at a time: a guard against a flood, not a count shown to anybody.
MAX_KEPT = 5

# Verified on 2026-11-18 against the Mexican government's own page (see docs/v2/wave10/oracion.md).
EMERGENCY_911 = "Si tú o alguien más está en peligro en este momento, llama al 911."
CRISIS_LINE = (
    "Si pasas por una crisis emocional, la Línea de la Vida te escucha gratis, a cualquier hora, todos los días: "
    "800\u00a0911\u00a02000."  # no-break spaces: the number never breaks across two lines
)

OFF = "Tu iglesia no está recibiendo peticiones de oración."
NO_READER = "Hoy no hay nadie en tu iglesia que pueda leer una petición. No se guardó nada."
READERS_CHANGED = "Cambió quién lee las peticiones mientras escribías. No se guardó nada: revisa los nombres y envíala otra vez."
TOO_MANY = f"Ya tienes {MAX_KEPT} peticiones guardadas. Borra una para escribir otra."
NOT_ACCEPTED = "Para encenderlo, marca que leíste y aceptas lo que tu iglesia promete."
PROMISES_CHANGED = "Cambió quién lee las peticiones mientras leías esta página. No se encendió nada: léela otra vez."
ALREADY_REPLIED = "Esta petición ya tiene una respuesta. Solo se puede responder una vez."

_LINE = re.compile(r"^[\w .,;:¿?¡!()\-–/+«»\"'%#]+$")


class PrayerError(ValidationError):
    """A rule refused the action; the message is ready for the screen and never quotes what was typed."""


class Gone(Exception):
    """The request is not there for this reader (any more): answered as a page that does not exist."""


@dataclass(frozen=True)
class Terms:
    """What a church promises today, as the screens state it."""

    enabled: bool
    retention_days: int
    own_line: str

    @property
    def crisis_line(self) -> str:
        return self.own_line or CRISIS_LINE


# ---- the switch ---------------------------------------------------------------------------------
def terms(church) -> Terms:
    row = PrayerPolicy.objects.filter(church=church).values("enabled", "emergency_line").first() or {}
    return Terms(bool(row.get("enabled")), policy.prayer_retention_days(church), row.get("emergency_line") or "")


def is_on(church) -> bool:
    return PrayerPolicy.objects.filter(church=church, enabled=True).exists()


def readers_token(readers) -> str:
    """A fingerprint of who was named as a reader and of the promises' version. Only the hash travels."""
    stated = "\x1f".join([PROMISES_VERSION, *(str(pk) for pk in sorted(user.pk for user in readers))])
    return hashlib.sha256(stated.encode()).hexdigest()


def clean_line(text: str) -> str:
    """The church's own line of help: one short line of plain words and numbers, or empty."""
    line = " ".join((text or "").split())
    if not line:
        return ""
    if len(line) > EMERGENCY_LINE_MAX:
        raise PrayerError(f"Escribe {EMERGENCY_LINE_MAX} caracteres o menos.", code="emergency_line")
    if not _LINE.match(line) or "www." in line.lower() or "//" in line:
        raise PrayerError("Escribe solo palabras y números, en una línea y sin enlaces.", code="emergency_line")
    return line


def clean_days(days) -> int:
    low, high = policy.PRAYER_RETENTION_DAYS_FLOOR, policy.PRAYER_RETENTION_DAYS
    if isinstance(days, bool) or not isinstance(days, int) or not low <= days <= high:
        raise PrayerError(f"Escribe un número entre {low} y {high}.", code="retention_days")
    return days


def _pastor_or_refuse(church, actor) -> None:
    if not access.is_reader(actor, church):
        raise PrayerError("Solo quien tiene el rol de pastor en esta iglesia puede cambiarlo.", code="role")


def switch_on(church, actor, *, accepted: bool, token: str, emergency_line: str, retention_days: int) -> PrayerPolicy:
    """A pastor accepts the promises that were on the screen and the church starts to receive requests."""
    _pastor_or_refuse(church, actor)
    line, days = clean_line(emergency_line), clean_days(retention_days)
    if not accepted:
        raise PrayerError(NOT_ACCEPTED, code="accepted")
    with transaction.atomic():
        row, _ = PrayerPolicy.objects.select_for_update().get_or_create(church=church)
        if token != readers_token(access.readers(church)):
            raise PrayerError(PROMISES_CHANGED, code="stale")
        if row.enabled:
            return row
        row.enabled, row.accepted_by, row.accepted_at = True, actor, timezone.now()
        row.promises_version, row.emergency_line, row.retention_days = PROMISES_VERSION, line, days
        row.save(update_fields=[
            "enabled", "accepted_by", "accepted_at", "promises_version", "emergency_line", "retention_days", "updated_at",
        ])
        record_church(church, actor, "oracion_politica", "Encendió las peticiones de oración y aceptó lo que la iglesia promete",
                      obj=row, value=True)
    return row


def switch_off(church, actor) -> bool:
    """No new request from now on. What was written stays for its readers until its date."""
    _pastor_or_refuse(church, actor)
    with transaction.atomic():
        row = PrayerPolicy.objects.select_for_update().filter(church=church).first()
        if row is None or not row.enabled:
            return False
        row.enabled = False
        row.save(update_fields=["enabled", "updated_at"])
        record_church(church, actor, "oracion_politica", "Apagó las peticiones de oración", obj=row, value=False)
    return True


def change_terms(church, actor, *, emergency_line: str, retention_days: int) -> bool:
    """While it is on: the church's own line and the days a *new* request is kept."""
    _pastor_or_refuse(church, actor)
    line, days = clean_line(emergency_line), clean_days(retention_days)
    with transaction.atomic():
        row = PrayerPolicy.objects.select_for_update().filter(church=church, enabled=True).first()
        if row is None:
            raise PrayerError(OFF, code="off")
        if (row.emergency_line, row.retention_days) == (line, days):
            return False
        row.emergency_line, row.retention_days = line, days
        row.save(update_fields=["emergency_line", "retention_days", "updated_at"])
        # The line itself is words somebody typed: the row says that it changed, never what it says.
        record_church(church, actor, "oracion_politica", "Cambió la línea de ayuda o los días que se conserva una petición de oración",
                      obj=row, value=days)
    return True


# ---- the writer ---------------------------------------------------------------------------------
def create(church, person: Person, *, body: str, token: str, only_reader_id: int | None = None) -> PrayerRequest:
    """Store one request of ``person``, addressed to the readers the form named (``token``)."""
    body = (body or "").strip()
    if not body:
        raise PrayerError("Escribe tu petición.", code="body")
    if len(body) > BODY_MAX:
        raise PrayerError(f"Escribe {BODY_MAX:,} caracteres o menos.", code="body")
    with transaction.atomic():
        # The person's row is the lock: two tabs cannot both pass the limit, and a merge waits (people.merge).
        person = Person.objects.select_for_update().get(pk=person.pk)
        if person.church_id != church.pk or not access.may_write(person):
            raise PrayerError(OFF, code="off")
        if not is_on(church):
            raise PrayerError(OFF, code="off")
        readers = access.readers(church, apart_from=person.user)
        if not readers:
            raise PrayerError(NO_READER, code="readers")
        if token != readers_token(readers):
            raise PrayerError(READERS_CHANGED, code="stale")
        chosen = None
        if only_reader_id is not None:
            chosen = next((user for user in readers if user.pk == only_reader_id), None)
            if chosen is None:
                raise PrayerError(READERS_CHANGED, code="stale")
        if PrayerRequest.objects.filter(church=church, person=person).count() >= MAX_KEPT:
            raise PrayerError(TOO_MANY, code="limit")
        now = timezone.now()
        return PrayerRequest.objects.create(
            church=church, person=person, body=body, restricted=chosen is not None, only_reader=chosen,
            expires_at=now + timedelta(days=policy.prayer_retention_days(church)),
        )


def delete_own(church, person: Person, prayer_id: int) -> bool:
    """The writer deletes their own request, with its reply. Nothing is kept and nothing is recorded."""
    deleted, _ = PrayerRequest.objects.filter(pk=prayer_id, church=church, person=person).delete()
    return bool(deleted)


def notify_readers(prayer: PrayerRequest, link: str) -> None:
    """One e-mail, with no content, to each reader of ``prayer`` who asked for it. What happens to it
    changes nothing for the writer, who is never told that anybody was notified."""
    wanting = set(
        ReaderPreference.objects.filter(church_id=prayer.church_id, email_new=True).values_list("user_id", flat=True)
    )
    for user in access.reader_of(prayer):
        if user.pk in wanting and user.email:
            notify.notify("peticion_oracion_nueva", user.email, {"church_name": prayer.church.name, "link": link})


# ---- a reader -----------------------------------------------------------------------------------
def unread_count(user, church) -> int:
    """How many requests this reader has not seen marked as read. Zero for anybody who is not a reader."""
    return access.readable(user, church).filter(read_at__isnull=True).count()


def _locked(church, user, prayer_id: int) -> PrayerRequest:
    """The row, locked, read again through today's rule. ``Gone`` for anybody it is not for."""
    if not is_on(church):
        raise Gone  # switched off: what was written is read, not worked on
    prayer = access.readable(user, church).select_for_update(of=("self",)).filter(pk=prayer_id).first()
    if prayer is None:
        raise Gone
    return prayer


def opened(church, user, prayer: PrayerRequest) -> None:
    """A reader opened the words: one audit row with the request's id, never the person's."""
    record_church(church, user, "oracion_abierta", "Abrió una petición de oración", obj=prayer)


def mark_read(church, user, prayer_id: int) -> PrayerRequest:
    with transaction.atomic():
        prayer = _locked(church, user, prayer_id)
        if prayer.read_at is None:
            prayer.read_at = timezone.now()
            prayer.save(update_fields=["read_at"])
            record_church(church, user, "oracion_atendida", "Marcó como leída una petición de oración", obj=prayer, value="leida")
    return prayer


def reply(church, user, prayer_id: int, text: str) -> PrayerRequest:
    """The one short answer the writer will read. A second one is refused: this is not a conversation."""
    text = (text or "").strip()
    if not text:
        raise PrayerError("Escribe tu respuesta.", code="reply")
    if len(text) > REPLY_MAX:
        raise PrayerError(f"Escribe {REPLY_MAX} caracteres o menos.", code="reply")
    with transaction.atomic():
        prayer = _locked(church, user, prayer_id)
        if prayer.replied_at is not None:
            raise PrayerError(ALREADY_REPLIED, code="replied")
        now = timezone.now()
        prayer.reply, prayer.replied_at, prayer.read_at = text, now, prayer.read_at or now
        prayer.save(update_fields=["reply", "replied_at", "read_at"])
        record_church(church, user, "oracion_atendida", "Respondió una petición de oración", obj=prayer, value="respondida")
    return prayer


def mark_talked(church, user, prayer_id: int) -> PrayerRequest:
    with transaction.atomic():
        prayer = _locked(church, user, prayer_id)
        if prayer.talked_at is None:
            now = timezone.now()
            prayer.talked_at, prayer.read_at = now, prayer.read_at or now
            prayer.save(update_fields=["talked_at", "read_at"])
            record_church(church, user, "oracion_atendida", "Marcó que habló en persona sobre una petición de oración",
                          obj=prayer, value="hablada")
    return prayer


def wants_email(church, user) -> bool:
    return ReaderPreference.objects.filter(church=church, user=user, email_new=True).exists()


def set_wants_email(church, user, wanted: bool) -> None:
    """A reader's own choice. Only a reader has one."""
    _pastor_or_refuse(church, user)
    ReaderPreference.objects.update_or_create(church=church, user=user, defaults={"email_new": bool(wanted)})


# ---- retention ----------------------------------------------------------------------------------
def prune(*, apply: bool = True, now=None) -> int:
    """Delete every request whose date passed, with its reply, in every church whatever its switches:
    it only deletes. Also anything older than the platform's 90 days, whatever its row says.
    Returns how many (a number for the operator's log; never by church or by person)."""
    now = now or timezone.now()
    ceiling = now - timedelta(days=policy.PRAYER_RETENTION_DAYS)
    due = PrayerRequest.objects.filter(expires_at__lte=now) | PrayerRequest.objects.filter(created_at__lte=ceiling)
    if not apply:
        return due.count()
    deleted, _ = due.delete()
    return deleted
