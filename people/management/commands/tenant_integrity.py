"""Count the rows that cross churches or events where the schema cannot refuse it (read-only).

The database cannot express "this foreign key points into the same church" or "these two foreign
keys agree", so a bug, a shell session or a hand-written ``QuerySet.update()`` can leave a row that
mixes tenants. This command finds them so drift is visible (roadmap P1-12, audit C-05/C-06).

* It only counts: it never prints an id, a name, an e-mail or an answer, so its output can go to
  CI logs, cron mail or a ticket.
* Every check is a single-valued join compared with ``F()``; the checks of one table share one
  ``COUNT(...) FILTER (...)`` query, so the whole run is one query per table (six).
* Each check also reports how many rows it read: a zero next to zero rows read is not an all-clear.
* Exit status 1 (``CommandError``) when any error-level check finds a row. The ``Person`` check is a
  warning: it is reported and never fails the run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError
from django.db import models
from django.db.models import Count, Exists, F, OuterRef, Q

from core.models import UserChurchRole
from events.models import EventParticipant, EventQuestionnaire, FollowUp, Interview, ReportSnapshot
from people.models import Person

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    model: type[models.Model]
    # The rows the check reads (None: every row of the table) and, among them, the ones that disagree.
    applies: Q | None
    mismatch: Q
    severity: str = ERROR


@dataclass(frozen=True)
class Result:
    check: Check
    checked: int
    mismatched: int


def checks() -> tuple[Check, ...]:
    """The checks in report order. Built per call: the ``Exists`` subquery is not shared between runs."""
    no_church_role = ~Exists(UserChurchRole.objects.filter(user=OuterRef("user"), church=OuterRef("church")))
    return (
        Check("interview_version_church", "Entrevistas con un cuestionario de otra iglesia", Interview, None,
              ~Q(version__questionnaire__church=F("event__church"))),
        Check("event_questionnaire_version_church", "Cuestionarios de un evento que son de otra iglesia",
              EventQuestionnaire, None, ~Q(version__questionnaire__church=F("event__church"))),
        Check("participant_sede_church", "Camperos con una sede de otra iglesia", EventParticipant,
              Q(sede__isnull=False), ~Q(sede__church=F("event__church"))),
        Check("participant_coordinator_event", "Camperos con un coordinador de otro evento", EventParticipant,
              Q(coordinator__isnull=False), ~Q(coordinator__event=F("event"))),
        Check("interview_participant_event", "Entrevistas de un campero de otro evento", Interview, None,
              ~Q(event=F("participant__event"))),
        Check("follow_up_participant_event", "Seguimientos de un campero de otro evento", FollowUp, None,
              ~Q(event=F("participant__event"))),
        Check("snapshot_tutor_event", "Cortes de equipo con un tutor de otro evento", ReportSnapshot,
              Q(tutor__isnull=False), ~Q(tutor__event=F("event"))),
        Check("person_user_without_church_role", "Personas creadas por un rol que ya no tienen rol", Person,
              # Only people created from a church role ("Cuentas existentes"): a person registered by hand or
              # brought from an event has an account and no role by design (they are a member of the church).
              Q(user__isnull=False, created_from=Person.CreatedFrom.BACKFILL), Q(no_church_role), severity=WARNING),
    )


def collect() -> list[Result]:
    """Run every check: one aggregate query per table, rows never leave the database."""
    selected = checks()
    by_model: dict[type[models.Model], list[Check]] = {}
    for check in selected:
        by_model.setdefault(check.model, []).append(check)
    counts: dict[str, int] = {}
    for model, group in by_model.items():
        aggregates = {}
        for check in group:
            mismatch = check.mismatch if check.applies is None else check.applies & check.mismatch
            aggregates[f"{check.key}_checked"] = Count("pk", filter=check.applies)
            aggregates[f"{check.key}_mismatched"] = Count("pk", filter=mismatch)
        # The base manager: a default manager that hides rows would turn into a false all-clear.
        counts.update(model._base_manager.order_by().aggregate(**aggregates))
    return [Result(check, counts[f"{check.key}_checked"], counts[f"{check.key}_mismatched"]) for check in selected]


class Command(BaseCommand):
    help = (
        "Cuenta los registros que mezclan iglesias o eventos y que la base de datos no puede rechazar. "
        "Solo lee y solo muestra conteos (nunca nombres ni identificadores). Termina con error (1) si "
        "encuentra alguno; las personas creadas por un rol que ya no lo tienen son solo un aviso."
    )

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Imprime el resultado en JSON.")

    def handle(self, *args, **opts):
        results = collect()
        errors = [r for r in results if r.check.severity == ERROR and r.mismatched]
        warnings = [r for r in results if r.check.severity == WARNING and r.mismatched]
        error_rows = sum(r.mismatched for r in errors)
        warning_rows = sum(r.mismatched for r in warnings)
        if opts["json"]:
            self._json(results, error_rows, warning_rows)
        else:
            self._table(results, error_rows, warning_rows)
        if errors:
            raise CommandError(
                f"Integridad entre iglesias y eventos: {error_rows} registro(s) con discrepancias en "
                f"{len(errors)} revisión(es). El comando no corrigió nada.",
                returncode=1,
            )

    def _json(self, results, error_rows, warning_rows):
        payload = {
            "ok": not error_rows,
            "errors": error_rows,
            "warnings": warning_rows,
            "checks": {
                r.check.key: {"label": r.check.label, "severity": r.check.severity, "checked": r.checked,
                              "mismatched": r.mismatched}
                for r in results
            },
        }
        self.stdout.write(json.dumps(payload, indent=2))

    def _table(self, results, error_rows, warning_rows):
        def label(result):
            return result.check.label + (" (aviso)" if result.check.severity == WARNING else "")

        def status(result):
            if not result.mismatched:
                return "Correcto"
            return "Aviso" if result.check.severity == WARNING else "Revisar"

        width = max(len("Revisión"), *(len(label(r)) for r in results))
        self.stdout.write("Integridad entre iglesias y eventos (solo lectura, solo conteos)")
        self.stdout.write(f"  {'Revisión':<{width}}  {'Revisados':>9}  {'Discrepancias':>13}  Estado")
        for r in results:
            self.stdout.write(f"  {label(r):<{width}}  {r.checked:>9}  {r.mismatched:>13}  {status(r)}")
        if error_rows:
            self.stdout.write(self.style.ERROR(f"Resultado: {error_rows} registro(s) con discrepancias."))
        else:
            self.stdout.write(self.style.SUCCESS("Resultado: sin discrepancias."))
        if warning_rows:
            self.stdout.write(self.style.WARNING(
                f"Aviso: {warning_rows} persona(s) con cuenta en una iglesia donde esa cuenta no tiene rol. "
                "No cuenta como error."
            ))
