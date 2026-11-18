"""Count the rows that cross churches or events where the schema cannot refuse it (read-only).

The database cannot express "this foreign key points into the same church" or "these two foreign
keys agree", so a bug, a shell session or a hand-written ``QuerySet.update()`` can leave a row that
mixes tenants. This command finds them so drift is visible (roadmap P1-12, audit C-05/C-06).

* It only counts: it never prints a name, an e-mail or an answer, so its output can go to CI logs,
  cron mail or a ticket. ``--ids`` adds the primary keys of the rows that disagree (at most
  ``ID_LIMIT`` per check) so somebody can go and look; nothing else about a row is ever printed.
* Every check is a single-valued join compared with ``F()`` (or one ``EXISTS``); the checks of one
  table share one ``COUNT(...) FILTER (...)`` query, so the whole run is one query per table.
  ``--ids`` adds one query for each check that found something.
* The camp's tables come first (``camp_checks``), then the church modules added after the camp
  (``community_checks``): people, groups, notices, events of the church, resources, serving, care,
  opinions, the public home and the roles by scope. ``policies.ChurchPolicy``,
  ``sitio.PublicHome`` and ``sitio.ServiceTime`` hold one church and nothing else: there is
  nothing in them that could cross.
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

from access.models import RoleAssignment
from agenda.models import EventRegistration, GuardianConsent
from care.models import FollowUp as CareFollowUp
from care.models import FollowUpStep
from core.models import UserChurchRole
from events.models import Event, EventParticipant, EventQuestionnaire, FollowUp, Interview, ReportSnapshot
from feedback.models import FeedbackGiven
from groups.models import Group, GroupAttendance, GroupJoinRequest, GroupMembership
from notices.models import Announcement, AnnouncementRead, NoticeEmail
from people.models import CampusAffiliation, EventParticipantLink, Person
from resources.models import DevotionalRead, ReadingPlanDayRead, ReadingPlanProgress
from serving.models import Assignment, Team, TeamMembership
from sitio.models import PublicMedia

ERROR = "error"
WARNING = "warning"
ID_LIMIT = 50  # per check, with --ids


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
    checked: int | None  # None: a private table whose size is not printed
    mismatched: int
    ids: tuple = ()  # primary keys of the first rows that disagree, only with --ids


def checks() -> tuple[Check, ...]:
    """Every check in report order. Built per call: the ``Exists`` subqueries are not shared between runs."""
    return camp_checks() + community_checks()


def community_checks() -> tuple[Check, ...]:
    """The church modules added after the camp: a person, a group, a team, an event or a notice of
    one church tied to a row of another."""

    def scope_missing(model) -> Q:
        # The scope's row does not exist in the church the role says (another church's, or deleted).
        return ~Exists(model._base_manager.filter(pk=OuterRef("scope_id"), church_id=OuterRef("church_id")))

    scope_elsewhere = (
        (Q(scope_type="church") & ~Q(scope_id=F("church_id")))
        | (Q(scope_type="event") & scope_missing(Event))
        | (Q(scope_type="group") & scope_missing(Group))
        | (Q(scope_type="ministry") & scope_missing(Team))
    )
    return (
        # people
        Check("person_merged_into_church", "Personas unidas a una persona de otra iglesia", Person,
              Q(merged_into__isnull=False), ~Q(merged_into__church=F("church"))),
        Check("campus_affiliation_sede_church", "Personas con una sede de otra iglesia", CampusAffiliation, None,
              ~Q(sede__church=F("person__church"))),
        Check("participant_link_person_church", "Vínculos de un campero con una persona de otra iglesia",
              EventParticipantLink, None, ~Q(person__church=F("event__church"))),
        Check("participant_link_participant_event", "Vínculos de un campero de otro evento", EventParticipantLink, None,
              ~Q(participant__event=F("event"))),
        # groups
        Check("group_sede_church", "Grupos con una sede de otra iglesia", Group, Q(sede__isnull=False),
              ~Q(sede__church=F("church"))),
        Check("group_membership_person_church", "Integrantes de un grupo de otra iglesia", GroupMembership, None,
              ~Q(person__church=F("group__church"))),
        Check("group_join_request_person_church", "Solicitudes para un grupo de otra iglesia", GroupJoinRequest, None,
              ~Q(person__church=F("group__church"))),
        Check("group_attendance_person_church", "Asistencias a un grupo de otra iglesia", GroupAttendance, None,
              ~Q(person__church=F("meeting__group__church"))),
        # notices
        Check("announcement_group_church", "Grupos de otra iglesia en un aviso", Announcement, Q(group__isnull=False),
              ~Q(group__church=F("church"))),
        Check("announcement_read_person_church", "Lecturas de un aviso de otra iglesia", AnnouncementRead, None,
              ~Q(person__church=F("announcement__church"))),
        Check("notice_email_person_church", "Correos de un aviso de otra iglesia", NoticeEmail, None,
              ~Q(person__church=F("announcement__church"))),
        # the church's events
        Check("registration_person_church", "Registros a un evento de otra iglesia", EventRegistration,
              Q(person__isnull=False), ~Q(person__church=F("event__church"))),
        Check("consent_registration_church", "Autorizaciones de un registro a un evento de otra iglesia", GuardianConsent,
              Q(registration__person__isnull=False), ~Q(registration__person__church=F("registration__event__church"))),
        Check("feedback_given_person_church", "Opiniones dadas en un evento de otra iglesia", FeedbackGiven,
              Q(person__isnull=False), ~Q(person__church=F("event__church"))),
        # resources
        Check("devotional_read_person_church", "Lecturas de un devocional de otra iglesia", DevotionalRead, None,
              ~Q(person__church=F("devotional__church"))),
        Check("reading_plan_progress_person_church", "Planes de lectura empezados por una persona de otra iglesia",
              ReadingPlanProgress, None, ~Q(person__church=F("plan__church"))),
        Check("reading_plan_day_read_plan", "Días leídos de un plan distinto al que la persona empezó", ReadingPlanDayRead, None,
              ~Q(day__plan=F("progress__plan"))),
        # the public home: a resource marked as public by a church it does not belong to
        Check("public_media_item_church", "Recursos públicos marcados por otra iglesia", PublicMedia, None,
              ~Q(item__church=F("church"))),
        # serving
        Check("team_membership_person_church", "Integrantes de un equipo de otra iglesia", TeamMembership, None,
              ~Q(person__church=F("team__church"))),
        Check("assignment_person_church", "Fechas de servicio en un equipo de otra iglesia", Assignment, None,
              ~Q(person__church=F("service_date__team__church"))),
        # care
        Check("care_follow_up_person_church", "Acompañamientos de una persona de otra iglesia", CareFollowUp, None,
              ~Q(person__church=F("church"))),
        Check("care_follow_up_owner_church", "Acompañamientos a cargo de una persona de otra iglesia", CareFollowUp,
              Q(owner__isnull=False), ~Q(owner__church=F("church"))),
        Check("care_step_by_church", "Pasos de un acompañamiento dados por una persona de otra iglesia", FollowUpStep,
              Q(by__isnull=False), ~Q(by__church=F("follow_up__church"))),
        # roles by scope
        Check("role_assignment_scope_church", "Roles por ámbito que apuntan fuera de su iglesia", RoleAssignment, None,
              scope_elsewhere),
    )


def camp_checks() -> tuple[Check, ...]:
    """The camp's tables and the people created from a church role."""
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


def collect(with_ids: bool = False) -> list[Result]:
    """Run every check: one aggregate query per table. Rows never leave the database; with
    ``with_ids`` the primary keys of the ones that disagree do, and nothing else."""
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
    results = []
    for check in selected:
        mismatched, ids = counts[f"{check.key}_mismatched"], ()
        if with_ids and mismatched:
            mismatch = check.mismatch if check.applies is None else check.applies & check.mismatch
            ids = tuple(check.model._base_manager.filter(mismatch).order_by("pk").values_list("pk", flat=True)[:ID_LIMIT])
        # What a person reads is theirs alone: the size of those tables is not printed, even to who operates Emmaus.
        checked = None if check.key in PRIVATE_SIZES else counts[f"{check.key}_checked"]
        results.append(Result(check, checked, mismatched, ids))
    return results


PRIVATE_SIZES = frozenset({"devotional_read_person_church", "reading_plan_progress_person_church", "reading_plan_day_read_plan"})


class Command(BaseCommand):
    help = (
        "Cuenta los registros que mezclan iglesias o eventos y que la base de datos no puede rechazar. "
        "Solo lee y solo muestra conteos (nunca nombres); con --ids agrega los números de los registros "
        "con discrepancias. Termina con error (1) si encuentra alguno; las personas creadas por un rol "
        "que ya no lo tienen son solo un aviso."
    )

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Imprime el resultado en JSON.")
        parser.add_argument(
            "--ids", action="store_true",
            help=f"Agrega los números (llave primaria) de los registros con discrepancias, hasta {ID_LIMIT} por revisión.",
        )

    def handle(self, *args, **opts):
        self.with_ids = opts["ids"]
        results = collect(with_ids=self.with_ids)
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
                              "mismatched": r.mismatched, **({"ids": list(r.ids)} if self.with_ids else {})}
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
            self.stdout.write(f"  {label(r):<{width}}  {"—" if r.checked is None else r.checked:>9}  {r.mismatched:>13}  {status(r)}")
            if r.ids:
                more = "" if r.mismatched <= len(r.ids) else f" (los primeros {len(r.ids)})"
                table = r.check.model._meta.label
                self.stdout.write(f"    {table}, id{more}: {', '.join(str(pk) for pk in r.ids)}")
        if error_rows:
            self.stdout.write(self.style.ERROR(f"Resultado: {error_rows} registro(s) con discrepancias."))
        else:
            self.stdout.write(self.style.SUCCESS("Resultado: sin discrepancias."))
        if warning_rows:
            self.stdout.write(self.style.WARNING(
                f"Aviso: {warning_rows} persona(s) con cuenta en una iglesia donde esa cuenta no tiene rol. "
                "No cuenta como error."
            ))
