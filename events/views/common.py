from __future__ import annotations

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from ..access import resolve_access
from ..models import Event, InterviewStation, MembershipRole
from ..services.metrics import local_tz

ADMIN = MembershipRole.ADMIN
PASTOR = MembershipRole.PASTOR
TUTOR = MembershipRole.TUTOR
COORDINATOR = MembershipRole.COORDINATOR
LEADERS = (ADMIN, PASTOR, TUTOR)
EVERYONE = (ADMIN, PASTOR, TUTOR, COORDINATOR)


def event_view(*roles):
    """Resolve the event and the caller's access, enforce roles, activate the event time zone."""

    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, slug, *args, **kwargs):
            # A simple event (camp_tools off) has none of these screens: it is managed in the agenda.
            event = get_object_or_404(Event.objects.select_related("church"), slug=slug, camp_tools=True)
            access = resolve_access(request.user, event)
            if access is None or (roles and access.role not in roles):
                raise PermissionDenied
            request.event = event
            request.access = access
            timezone.activate(local_tz(event))
            if request.session.get("current_event") != event.slug:
                request.session["current_event"] = event.slug
            return view(request, event, access, *args, **kwargs)

        return wrapped

    return decorator


def is_fragment(request) -> bool:
    return request.headers.get("X-Requested-With") == "fetch"


def programme_now(event: Event):
    """"Ahora y después" for a camp home (agenda/now_next.py, events/_now_next.html): what the event's programme says
    for this moment, or None: the church has the community module off, or today has no programme. In the context of
    the full page and of its live fragment alike, so the strip moves on with the page. One query for the church's
    flag and, with the module on, one for today's rows; nothing about a campero."""
    from agenda.now_next import now_and_next  # agenda imports events

    return now_and_next(event)


def role_home_url(event: Event, access) -> str:
    name = {
        ADMIN: "events:admin_home",
        PASTOR: "events:pastor_home",
        TUTOR: "events:tutor_home",
        COORDINATOR: "events:coord_home",
    }[access.role]
    return reverse(name, args=[event.slug])


def nav_items(event: Event, access) -> list[dict]:
    def item(label, name, also=(), short=""):
        # ``short``: the word on a phone's tab, where a long label would wrap (the rail and the bar keep ``label``)
        return {"label": label, "short": short or label, "url": reverse(f"events:{name}", args=[event.slug]), "names": {name, *also}}

    # "Eventos" (the event list) is not an item: every bar links it beside the event's name (partials/ui/bar.html).
    if access.is_admin:
        return [
            item("Inicio", "admin_home"),
            item("Camperos", "participants", ("participant_detail", "participant_edit", "participant_create", "participant_import", "participant_delete",
                                                   "sedes", "sede_add", "sede_edit", "answers_export")),
            item("Equipo", "team", ("member_add", "member_edit", "assignments", "coordinator_detail", "join_code_sign", "team_import")),
            item("Cuestionarios", "questionnaires", ("version_detail", "question_edit", "question_add", "option_add", "section_add",
                                                     "questionnaire_create")),
            item("Reportes", "reports", ("report_detail", "presentation_live")),
            item("Configuración", "settings", ("audit", "sessions", "stations", "confidential_level", "duplicate")),
        ]
    if access.is_pastor:
        return [
            item("Resumen general", "pastor_home", ("confidential_level",), short="Resumen"),
            item(f"Avance {event.of_kind}", "progress", ("coordinator_detail", "interviews"), short="Avance"),
            item("Seguimiento", "follow_ups", ("follow_up_detail",)),
            item("Reportes", "reports", ("report_detail", "presentation_live")),
            item("Equipo", "team", ()),
        ]
    if access.is_tutor:
        return [
            item("Mi equipo", "tutor_home", short="Equipo"),
            item("Coordinadores", "coordinators", ("coordinator_detail",)),
            item("Entrevistas", "interviews", ("participant_detail", "interview_detail")),
            item("Pendientes", "pending", ("follow_up_detail",)),
            item("Reportes", "reports", ("report_detail", "presentation_live")),
        ]
    return [
        item("Mi inicio", "coord_home", short="Inicio"),
        item("Mis camperos", "my_participants", ("participant_detail", "participant_create", "participant_edit"), short="Camperos"),
        item("Entrevistas pendientes", "coord_pending", (), short="Pendientes"),
        item("Mi avance", "coord_progress", short="Avance"),
    ]


def current_station(request, event: Event) -> InterviewStation | None:
    station_id = request.session.get("stations", {}).get(str(event.id))
    if not station_id:
        return None
    return event.stations.filter(pk=station_id, is_active=True).first()


def remember_station(request, event: Event, station: InterviewStation) -> None:
    stations = dict(request.session.get("stations", {}))
    stations[str(event.id)] = station.id
    request.session["stations"] = stations


def initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def safe_next(request, candidate: str | None, fallback: str) -> str:
    """Only same-site redirects (rejects //evil.example, /\\evil.example and absolute URLs to another host)."""
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return candidate
    return fallback


def allowlisted_next(request, candidate: str | None, allowed, fallback: str) -> str:
    """Stricter than ``safe_next``: follow ``candidate`` only if it is exactly one of ``allowed``.

    For a way back that must not open just any page of the site (for example one that would
    claim an interview). ``allowed`` holds paths the view built itself with ``reverse``.
    """
    if candidate and candidate in set(allowed):
        return safe_next(request, candidate, fallback)
    return fallback
