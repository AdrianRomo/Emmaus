from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..forms import ReportScopeForm
from ..models import EventMembership, MembershipRole, ReportSnapshot
from ..services import audit
from ..services.metrics import build_payload, compare, scope_for_access
from ..services.pdf import build_snapshot_pdf
from ..services.presentation import build_deck
from ..services.reports import (
    group_history,
    latest_snapshot,
    manual_cutoff,
    next_slot,
    previous_snapshot,
    released_follow_ups,
    visible_payload,
)
from .common import LEADERS, event_view, is_fragment, safe_next


def _team_public_names(event) -> dict[str, str]:
    return {
        f"equipo:{m.id}": m.team_name
        for m in EventMembership.objects.filter(event=event, role=MembershipRole.TUTOR).exclude(team_name="")
    }


HISTORY_PER_PAGE = 12  # about a day and a half of two-hour cutoffs: one screen, no long scroll
HISTORY_MAX = 400  # fifty days of cutoffs; beyond that the oldest fall off the list (the count line still says how many exist)


@event_view(*LEADERS)
def reports(request, event, access):
    scopes = access.report_scopes()
    scope_key = request.GET.get("alcance") or (scopes[0]["key"] if scopes else "")
    if scope_key not in {s["key"] for s in scopes}:
        scope_key = scopes[0]["key"] if scopes else ""
    history = access.snapshots().filter(scope_key=scope_key).order_by("-cutoff_at")
    latest = history.filter(status=ReportSnapshot.Status.READY).first()
    previous = previous_snapshot(latest) if latest else None
    # Quiet stretches fold into one row (group_history), then twelve rows a page: ?pagina=, scope kept by the pager.
    history_page = Paginator(group_history(history[:HISTORY_MAX]), HISTORY_PER_PAGE).get_page(request.GET.get("pagina"))
    return render(
        request,
        "events/reports/home.html",
        {
            "scopes": scopes,
            "scope_key": scope_key,
            "scope_label": next((s["label"] for s in scopes if s["key"] == scope_key), ""),
            "history_page": history_page,
            "history_total": history.count(),
            "latest": latest,
            "previous": previous,
            "delta": compare(latest.data, previous.data) if latest and previous else {},
            "next_cutoff": next_slot(event),
            "form": ReportScopeForm(initial={"scope": scope_key}, scopes=scopes),
        },
    )


@require_POST
@event_view(*LEADERS)
def report_generate(request, event, access):
    form = ReportScopeForm(request.POST, scopes=access.report_scopes())
    if not form.is_valid():
        messages.error(request, "Elige un alcance válido.")
        return redirect("events:reports", slug=event.slug)
    try:
        snapshot = manual_cutoff(access, form.cleaned_data["scope"])
    except PermissionError as exc:
        raise PermissionDenied(str(exc))
    if snapshot.status == ReportSnapshot.Status.FAILED:
        messages.error(request, "No se pudo generar el corte. Inténtalo otra vez; si sigue igual, avisa a la administración.")
        return redirect("events:reports", slug=event.slug)
    messages.success(request, f"Corte generado con datos de las {timezone.localtime(snapshot.cutoff_at):%H:%M}.")
    return redirect("events:report_detail", slug=event.slug, public_id=snapshot.public_id)


def _snapshot_or_404(access, public_id) -> ReportSnapshot:
    snapshot = get_object_or_404(ReportSnapshot, public_id=public_id, event=access.event)
    if not access.can_view_snapshot(snapshot):
        raise Http404  # access is checked against the viewer's scope *today*
    if snapshot.status != ReportSnapshot.Status.READY:
        raise Http404
    return snapshot


@event_view(*LEADERS)
def report_detail(request, event, access, public_id):
    snapshot = _snapshot_or_404(access, public_id)
    previous = previous_snapshot(snapshot)
    payload = visible_payload(snapshot.data, access)
    return render(
        request,
        "events/reports/detail.html",
        {
            "snapshot": snapshot,
            "payload": payload,
            "progress": payload["progress"],
            "previous": previous,
            "delta": compare(snapshot.data, previous.data) if previous else {},
        },
    )


@event_view(*LEADERS)
def report_pdf(request, event, access, public_id):
    snapshot = _snapshot_or_404(access, public_id)
    previous = previous_snapshot(snapshot)
    payload = visible_payload(snapshot.data, access)
    pdf = build_snapshot_pdf(
        snapshot, payload,
        delta=compare(snapshot.data, previous.data) if previous else None,
        previous_cutoff=previous.cutoff_at if previous else None,
    )
    audit.record(request.user, event, "exportacion", f"Descargó el PDF del corte de las {timezone.localtime(snapshot.cutoff_at):%H:%M} ({snapshot.scope_label})", snapshot)
    stamp = timezone.localtime(snapshot.cutoff_at).strftime("%Y%m%d-%H%M")
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="corte-{event.slug}-{stamp}.pdf"'
    return response


def _deck_context(event, payload, snapshot):
    deck = build_deck(
        payload,
        event_name=event.name,
        kind_label=event.kind_noun,
        k=event.min_group_size,
        team_names=_team_public_names(event),
        released_follow_ups=released_follow_ups(snapshot),
        released_at=snapshot.cutoff_at if snapshot else None,
    )
    return {"deck": deck}


@event_view(*LEADERS)
def report_presentation(request, event, access, public_id):
    snapshot = _snapshot_or_404(access, public_id)
    context = _deck_context(event, snapshot.data, snapshot)
    context.update({"snapshot": snapshot, "live": False})
    return render(request, "events/presentation/deck.html", context)


@event_view(*LEADERS)
def presentation_live(request, event, access):
    scope = scope_for_access(access)
    payload = build_payload(scope)
    context = _deck_context(event, payload, latest_snapshot(event, scope.key))
    context["live"] = True
    # "Salir" goes back to the page that opened the presentation (``?desde=``, the pages that link here say which),
    # and to Reportes otherwise: never to the role's home, which is not where the person was.
    context["exit_url"] = safe_next(request, request.GET.get("desde"), reverse("events:reports", args=[event.slug]))
    if is_fragment(request):
        return render(request, "events/presentation/_slides.html", context)
    return render(request, "events/presentation/deck.html", context)
