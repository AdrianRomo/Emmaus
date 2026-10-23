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
            event = get_object_or_404(Event.objects.select_related("church", "questionnaire_version"), slug=slug)
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


def role_home_url(event: Event, access) -> str:
    name = {
        ADMIN: "events:admin_home",
        PASTOR: "events:pastor_home",
        TUTOR: "events:tutor_home",
        COORDINATOR: "events:coord_home",
    }[access.role]
    return reverse(name, args=[event.slug])


def nav_items(event: Event, access) -> list[dict]:
    def item(label, name, also=()):
        return {"label": label, "url": reverse(f"events:{name}", args=[event.slug]), "names": {name, *also}}

    if access.is_admin:
        return [
            item("Inicio", "admin_home"),
            {"label": "Campamentos", "url": reverse("events:list"), "names": {"list", "create"}},
            item("Participantes", "participants", ("participant_detail", "participant_edit", "participant_create", "participant_import", "participant_delete")),
            item("Equipo", "team", ("member_add", "member_edit", "assignments", "coordinator_detail")),
            item("Cuestionarios", "questionnaires", ("version_detail", "question_edit", "question_add", "option_add", "section_add")),
            item("Reportes", "reports", ("report_detail", "presentation_live")),
            item("Configuración", "settings", ("audit", "sessions", "stations")),
        ]
    if access.is_pastor:
        return [
            item("Resumen general", "pastor_home"),
            item(f"Avance {event.of_kind}", "progress", ("coordinator_detail", "interviews")),
            item("Seguimiento", "follow_ups", ("follow_up_detail",)),
            item("Reportes", "reports", ("report_detail", "presentation_live")),
            item("Equipo", "team", ()),
        ]
    if access.is_tutor:
        return [
            item("Mi equipo", "tutor_home"),
            item("Coordinadores", "coordinators", ("coordinator_detail",)),
            item("Entrevistas", "interviews", ("participant_detail", "interview_detail")),
            item("Pendientes", "pending", ("follow_up_detail",)),
            item("Reportes", "reports", ("report_detail", "presentation_live")),
        ]
    return [
        item("Mi inicio", "coord_home"),
        item("Mis camperos", "my_participants", ("participant_detail", "participant_create", "participant_edit")),
        item("Entrevistas pendientes", "coord_pending", ()),
        item("Mi avance", "coord_progress"),
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
    """Only same-site redirects (rejects //evil.example, /\\evil.example, absolute URLs)."""
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return candidate
    return fallback
