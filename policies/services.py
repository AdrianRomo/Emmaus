"""What a church chose, what a form asks to change, and the one write path (``save``)."""

from dataclasses import dataclass

from django.db import transaction

from people import policy
from people.audit import record_church

from .models import ChurchPolicy

# The order of the screen. A change to anything but these goes through the review page first.
FIELDS = (
    "min_reporting_size", "feedback_open_days", "urgent_notice_pastors", "minors_need_consent_by_default",
    "attendance_identifiable_months", "attendance_aggregate_months",
)
SENSITIVE = frozenset(FIELDS) - {"feedback_open_days"}
# How each value reads in the review page and in the audit summary: the name, then the value in words.
LABELS = {
    "min_reporting_size": "Mínimo de personas para mostrar un total",
    "feedback_open_days": "Días para dar una opinión después de un evento",
    "urgent_notice_pastors": "Quién puede publicar un aviso urgente",
    "minors_need_consent_by_default": "Autorización para menores de edad en los eventos nuevos",
    "attendance_identifiable_months": "Tiempo que se conserva quién asistió a cada reunión",
    "attendance_aggregate_months": "Tiempo que se conservan los totales de cada reunión",
}
UNITS = {
    "min_reporting_size": "personas", "feedback_open_days": "días",
    "attendance_identifiable_months": "meses", "attendance_aggregate_months": "meses",
}
WORDS = {
    "urgent_notice_pastors": {
        False: "Solo la administración de la iglesia", True: "La administración de la iglesia y los pastores",
    },
    "minors_need_consent_by_default": {False: "Apagada al crear el evento", True: "Encendida al crear el evento"},
}


class PolicyError(ValueError):
    """A value outside what the platform allows reached the write path."""


@dataclass(frozen=True)
class Change:
    field: str
    label: str
    old: int | bool
    new: int | bool
    old_words: str
    new_words: str
    sensitive: bool


def in_words(field: str, value) -> str:
    if field in WORDS:
        return WORDS[field][bool(value)]
    return f"{value} {UNITS[field]}"


def current(church) -> dict:
    """The six values in force for ``church``: its own choices inside the bounds, else the defaults."""
    retention = policy.attendance_retention(church)
    return {
        "min_reporting_size": policy.min_reporting_size(church),
        "feedback_open_days": policy.feedback_open_days(church),
        "urgent_notice_pastors": "pastor" in policy.urgent_notice_roles(church),
        "minors_need_consent_by_default": policy.minors_consent_default(church),
        "attendance_identifiable_months": retention.identifiable_months,
        "attendance_aggregate_months": retention.aggregate_months,
    }


def check(wanted: dict) -> None:
    """Refuse anything a form would refuse: this is the write path's own floor."""
    if set(wanted) != set(FIELDS):
        raise PolicyError("Faltan o sobran políticas.")
    for field in FIELDS:
        value = wanted[field]
        if field in policy.BOUNDS:
            low, high = policy.BOUNDS[field]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise PolicyError(f"{LABELS[field]}: fuera de lo que permite la plataforma.")
        elif not isinstance(value, bool):
            raise PolicyError(f"{LABELS[field]}: no es sí o no.")
    if wanted["attendance_aggregate_months"] < wanted["attendance_identifiable_months"]:
        raise PolicyError("Los totales no se conservan menos tiempo que los nombres.")


def changes(church, wanted: dict) -> list[Change]:
    """What saving ``wanted`` would change, in the order of the screen."""
    now = current(church)
    return [
        Change(field, LABELS[field], now[field], wanted[field], in_words(field, now[field]),
               in_words(field, wanted[field]), field in SENSITIVE)
        for field in FIELDS
        if wanted[field] != now[field]
    ]


def save(church, actor, wanted: dict) -> list[Change]:
    """Store what changed and leave one audit row per value changed. Values that stay as they are
    are not written: an empty column keeps following the product default."""
    check(wanted)
    with transaction.atomic():
        row, _ = ChurchPolicy.objects.select_for_update().get_or_create(church=church)
        policy.forget(church)
        changed = changes(church, wanted)
        for change in changed:
            setattr(row, change.field, change.new)
        if changed:
            row.save()
        for change in changed:
            record_church(
                church, actor, "politica_cambiada",
                f"{change.label}: cambió de «{change.old_words}» a «{change.new_words}».",
                obj=row, policy=change.field, previous=change.old, value=change.new,
            )
    policy.forget(church)
    return changed
