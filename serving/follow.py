"""What a team repeats every week (issue #95): its dates follow a series of events of the church, and
its order of service starts from a template.

**Dates that follow a series.** A team says "servimos en cada fecha de «serie»" (``Team.follows_series``,
an ``agenda.EventSeries`` of the same church). ``sync_team`` then writes one ordinary ``ServiceDate`` for
each date of that series that is on and has not begun: when the team starts following, and on every pass
of the scheduler (``sync_due``, part of the job ``lo_que_se_repite``). The series of events writes its own
dates eight weeks ahead (``agenda.series``); this module only reads them.

* A date is written once: ``ServiceDate`` is unique by team and occurrence. Running twice writes nothing.
* A new date takes the day, the hour, the name and the place of its event, asks for the people each role
  says "por omisión" (``TeamRole.default_count``) and starts with the team's default template, if any.
  From then on it is a date like any other: the coordinator changes it and assigns as always, and the
  rotation, the swaps, the reminders and the adults-only rule work on it exactly as on a date made by hand.
* A date made by hand is never touched, and a day that already has one gets no second date.
* **A day the series calls off cancels the team's date** without anything being written: the date is
  called off while its occurrence is not on (``ServiceDate.called_off``, read every time). Nobody is told,
  and the screens say so; who was asked stays listed so the coordinator knows whom to tell. If the day is
  on again, the date is too.

**Templates.** A coordinator saves a date's order as a named template of the team and starts another
date's order from one. A template holds titles and minutes, never a person; ``MAX_TEMPLATES`` per team.
Like an order, it holds no songs, lyrics, chords, files or links.

Locks: the team, then the date (the order of every writer of Servir, ``serving.services``).
"""

from __future__ import annotations

import logging
from datetime import datetime

from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from agenda import series as event_series_rules
from agenda.models import EventSeries, SeriesOccurrence
from people import flags
from people.audit import record_church

from . import rotas, services
from .models import PlanItem, PlanTemplate, PlanTemplateItem, RoleNeed, ServiceDate, Team, TeamRole
from .services import ARCHIVED, ServingError

logger = logging.getLogger(__name__)

MAX_TEMPLATES = 10
ON = SeriesOccurrence.Status.ACTIVE


# ---- which series a team may follow -------------------------------------------------------------
def series_choices(team: Team, *, is_church_admin: bool):
    """The series of events of the team's church it can be told to follow. The administration reads every
    series; who coordinates only the ones the church already sees (a name of one still in preparation is
    not theirs to read)."""
    rows = EventSeries.objects.filter(church_id=team.church_id, active=True).order_by("name", "id")
    return rows if is_church_admin else rows.filter(published=True)


def follow(team: Team, actor, series: EventSeries | None, *, is_church_admin: bool, default_template: PlanTemplate | None = None,
           now: datetime | None = None) -> int:
    """Say which series the team follows (None: none) and which template its new dates start with.
    Returns how many dates were written now. Dates already written stay, whatever changes here."""
    with transaction.atomic():
        team = Team.objects.select_for_update().get(pk=team.pk)
        if not team.is_active:
            raise ServingError(ARCHIVED, code="archived")
        if series is not None and series.pk != team.follows_series_id:
            if not series_choices(team, is_church_admin=is_church_admin).filter(pk=series.pk).exists():
                raise ServingError("Elige una serie de eventos de esta iglesia.", code="series")
        if default_template is not None and not PlanTemplate.objects.filter(pk=default_template.pk, team=team).exists():
            raise ServingError("Elige una plantilla de este equipo.", code="template")
        wanted = (series.pk if series else None, default_template.pk if default_template else None)
        if wanted != (team.follows_series_id, team.default_template_id):
            changed_series = wanted[0] != team.follows_series_id
            team.follows_series_id, team.default_template_id = wanted
            team.save(update_fields=["follows_series", "default_template", "updated_at"])
            if changed_series:
                record_church(team.church, actor, "equipo_serie", "Cambió la serie de eventos que sigue un equipo.", team,
                              value="sigue" if series else "no_sigue")
            else:
                record_church(team.church, actor, "equipo_plantilla", "Cambió la plantilla con la que empiezan las fechas de un equipo.", team)
    return sync_team(team, actor, now)


# ---- writing the dates --------------------------------------------------------------------------
def sync_team(team: Team, actor=None, now: datetime | None = None) -> int:
    """Write the dates of ``team`` that are missing for the dates of the series it follows. Returns how many.
    A team that follows nothing or is archived, a church that is not active or does not use the module, a
    series of another church: nothing."""
    now = now or timezone.now()
    with transaction.atomic():
        team = Team.objects.select_for_update(of=("self",)).select_related("church").get(pk=team.pk)
        church = team.church
        if not team.is_active or team.follows_series_id is None or not church.active or not flags.enabled(church, "community"):
            return 0
        occurrences = list(
            SeriesOccurrence.objects.filter(series_id=team.follows_series_id, series__church_id=church.pk, status=ON,
                                            event__church_id=church.pk)
            .select_related("event", "event__page").order_by("day", "id")
        )
        occurrences = [row for row in occurrences if row.event.starts_on and not event_series_rules.has_started(row.event, now)]
        if not occurrences:
            return 0
        written_for = set(ServiceDate.objects.filter(team=team, occurrence__in=occurrences).values_list("occurrence_id", flat=True))
        by_hand = set(ServiceDate.objects.filter(team=team, occurrence__isnull=True,
                                                 date__in=[row.event.starts_on for row in occurrences]).values_list("date", flat=True))
        needs = [(role.pk, role.default_count) for role in TeamRole.objects.filter(team=team, active=True, default_count__gt=0)]
        moments = []
        if team.default_template_id is not None:
            moments = list(PlanTemplateItem.objects.filter(template_id=team.default_template_id, template__team=team).order_by("position", "id"))
        written = 0
        for row in occurrences:
            event = row.event
            if row.pk in written_for or event.starts_on in by_hand:
                continue
            page = getattr(event, "page", None)
            try:
                with transaction.atomic():
                    service_date = ServiceDate.objects.create(
                        team=team, date=event.starts_on, starts_at=page.starts_at if page else None, what=event.name[:120],
                        place=event.location[:120], occurrence=row, created_by=actor,
                    )
                    RoleNeed.objects.bulk_create(RoleNeed(service_date=service_date, role_id=role_id, count=count) for role_id, count in needs)
                    PlanItem.objects.bulk_create(
                        PlanItem(service_date=service_date, position=position, title=moment.title, minutes=moment.minutes)
                        for position, moment in enumerate(moments, start=1)
                    )
            except IntegrityError:
                continue  # another pass wrote this date first
            written += 1
        if written:
            record_church(church, actor, "equipo_serie_fechas", "Se escribieron fechas de un equipo que sigue una serie de eventos.",
                          team, count=written)
    return written


def sync_due(now: datetime | None = None) -> dict:
    """The scheduler's pass over every team that follows a series. Counts only; a team that fails never
    stops the next and is logged by its error's class name."""
    now = now or timezone.now()
    teams = (
        Team.objects.filter(status=Team.Status.ACTIVE, follows_series__isnull=False, church__active=True)
        .select_related("church").order_by("pk")
    )
    totals = {"teams": 0, "created": 0, "failed": 0}
    for team in teams:
        if not flags.enabled(team.church, "community"):
            continue
        totals["teams"] += 1
        try:
            totals["created"] += sync_team(team, now=now)
        except Exception as error:  # noqa: BLE001 - the other teams are still written
            totals["failed"] += 1
            logger.error("The dates of a team that follows a series could not be written: %s", type(error).__name__)
    return totals


def drifted(service_date: ServiceDate) -> bool:
    """The event this date was written for is now on another day or at another hour (somebody changed it,
    or the date itself was changed by hand). Nothing follows by itself: the coordinator is told to look.
    Needs ``occurrence__event__page`` loaded."""
    if service_date.occurrence_id is None or service_date.called_off:
        return False
    event = service_date.occurrence.event
    page = getattr(event, "page", None)
    return event.starts_on != service_date.date or (page.starts_at if page else None) != service_date.starts_at


# ---- templates of the order ---------------------------------------------------------------------
def templates_of(team: Team) -> list[PlanTemplate]:
    """The team's templates, each with ``moments`` and ``minutes``."""
    return list(PlanTemplate.objects.filter(team=team).annotate(moments=Count("items"), minutes=Sum("items__minutes")))


def save_template(service_date: ServiceDate, actor, name: str) -> PlanTemplate:
    """Keep the order of ``service_date`` as a template of its team: titles and minutes, in order. Never
    who is in charge."""
    name = rotas._one_line(name, 60, "Escribe el nombre de la plantilla.")
    if any(mark in name.lower() for mark in rotas.LINK_MARKS):
        raise ServingError("El nombre de una plantilla no lleva enlaces.", code="link")
    with transaction.atomic():
        team = rotas._active_team(service_date.team)
        items = list(PlanItem.objects.filter(service_date_id=service_date.pk, service_date__team=team).order_by("position", "id"))
        if not items:
            raise ServingError("Esta fecha todavía no tiene un orden que guardar.", code="empty")
        if PlanTemplate.objects.filter(team=team, name__iexact=name).exists():
            raise ServingError("El equipo ya tiene una plantilla con ese nombre. Usa otro nombre, o quita primero la anterior.", code="duplicate")
        if PlanTemplate.objects.filter(team=team).count() >= MAX_TEMPLATES:
            raise ServingError(f"Un equipo guarda {MAX_TEMPLATES} plantillas como máximo. Quita una antes de guardar otra.", code="too_many")
        template = PlanTemplate.objects.create(team=team, name=name, created_by=actor)
        PlanTemplateItem.objects.bulk_create(
            PlanTemplateItem(template=template, position=position, title=item.title, minutes=item.minutes)
            for position, item in enumerate(items, start=1)
        )
        record_church(team.church, actor, "equipo_plantilla", "Guardó el orden de una fecha como plantilla de un equipo.", template,
                      count=len(items))
    return template


def use_template(service_date: ServiceDate, template: PlanTemplate, actor, today=None) -> int:
    """Start the order of ``service_date`` from ``template``. Only into an empty order, like copying the
    previous one; nobody is put in charge."""
    with transaction.atomic():
        service_date = services._open_date(service_date, today)
        template = PlanTemplate.objects.filter(pk=template.pk, team_id=service_date.team_id).first()
        if template is None:
            raise ServingError("Esa plantilla ya no existe en este equipo.", code="template")
        if PlanItem.objects.filter(service_date=service_date).exists():
            raise ServingError("Esta fecha ya tiene un orden. Para empezar con una plantilla, quita primero sus momentos.", code="not_empty")
        rows = [
            PlanItem(service_date=service_date, position=position, title=item.title, minutes=item.minutes)
            for position, item in enumerate(PlanTemplateItem.objects.filter(template=template).order_by("position", "id"), start=1)
        ]
        PlanItem.objects.bulk_create(rows)
        team = service_date.team
        record_church(team.church, actor, "equipo_plantilla", "Empezó el orden de una fecha con una plantilla de un equipo.", service_date,
                      count=len(rows))
    return len(rows)


def delete_template(template: PlanTemplate, actor) -> None:
    """The template goes. The orders that started from it stay as they are; a team that started its new
    dates with it starts them empty."""
    with transaction.atomic():
        team = Team.objects.select_for_update().get(pk=template.team_id)
        template = PlanTemplate.objects.filter(pk=template.pk, team=team).first()
        if template is None:
            return
        record_church(team.church, actor, "equipo_plantilla", "Quitó una plantilla del orden de un equipo.", template)
        template.delete()
