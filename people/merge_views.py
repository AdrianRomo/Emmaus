"""Duplicate review and merges, for the church's administration only.

Every view: the ``people`` module on for the current church (404 otherwise), then church ``manage``
(``people.views.church_admin_required``, which asks ``core.authz.has_scope``). Every person, merge
and dismissal is fetched inside ``request.church``, and a person only while it is active: the id of
another church, and the id of a record that was merged away, are a 404 here as on every other
people screen (one answer everywhere: a merged-away id does not exist, for anybody).
"""

from datetime import timedelta

from django.contrib import messages
from django.db import OperationalError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.nav import safe_next
from core.paging import paginate

from . import duplicates, merge
from .models import DuplicateDismissal, Person, PersonMerge
from .views import _people, church_admin_required


BUSY = "Alguien más estaba cambiando estos datos en ese momento. No se cambió nada: inténtalo de nuevo."


def _pair(request, first_id, second_id) -> tuple[Person, Person]:
    """Two different active people of the request's church, or a 404."""
    people = {person.pk: person for person in _people(request.church).filter(pk__in=[first_id, second_id]).select_related("user")}
    if first_id == second_id or len(people) != 2:
        raise Http404
    return people[first_id], people[second_id]


def _int(value) -> int:
    value = str(value or "")
    return int(value) if value.isdigit() and len(value) < 19 else 0


@church_admin_required
def duplicate_list(request):
    church = request.church
    since = timezone.now() - timedelta(days=merge.UNDO_DAYS)
    recent = list(
        PersonMerge.objects.filter(church=church, merged_at__gte=since).select_related("survivor", "absorbed")[:50]
    )
    for record in recent:
        record.can_undo = merge.can_undo(record)
    dismissed = (
        DuplicateDismissal.objects.filter(church=church, person_a__merged_into__isnull=True, person_b__merged_into__isnull=True)
        .select_related("person_a", "person_b")
    )
    return render(request, "people/duplicates.html", {
        "page": paginate(request, duplicates.suggestions(church)),
        "recent": recent,
        "dismissed": paginate(request, dismissed, param="descartados"),
        "undo_days": merge.UNDO_DAYS,
    })


def _back_to_the_review(request) -> str:
    """Where a dismissal or its reopening returns: the page of the review it was pressed on (``siguiente``, so the list
    changes in place and keeps its page), and only that screen; anything else is the review's first page."""
    review = reverse("people:duplicates")
    back = safe_next(request, request.POST.get("siguiente"), review)
    return back if back.split("?")[0] == review else review


@church_admin_required
@require_POST
def duplicate_dismiss(request):
    first, second = _pair(request, _int(request.POST.get("a")), _int(request.POST.get("b")))
    try:
        merge.dismiss(request.church, first.pk, second.pk, request.user)
    except merge.MergeRefused as error:
        messages.error(request, error.message)
    else:
        messages.success(request, "Listo: ya no te mostraremos este par como posible duplicado.")
    return redirect(_back_to_the_review(request))


@church_admin_required
@require_POST
def duplicate_reopen(request, dismissal_id: int):
    get_object_or_404(DuplicateDismissal, pk=dismissal_id, church=request.church)
    try:
        merge.reopen(request.church, dismissal_id, request.user)
    except merge.MergeRefused as error:
        messages.error(request, error.message)
    else:
        messages.success(request, "Volvieron a la revisión. Si todavía comparten un dato, aparecen otra vez en la lista.")
    return redirect(_back_to_the_review(request))


@church_admin_required
def merge_preview(request, survivor_id: int, absorbed_id: int):
    """What would happen, field by field and count by count (GET); the merge itself (POST)."""
    survivor, absorbed = _pair(request, survivor_id, absorbed_id)
    stale = False
    if request.method == "POST":
        choices = {name: _int(request.POST.get(f"elige_{name}")) for name in merge.CHOICE_FIELDS}
        try:
            record = merge.merge(
                request.church, survivor.pk, absorbed.pk, request.user, choices=choices, token=request.POST.get("revision", ""),
                confirmed_siblings=request.POST.get("no_son_hermanos") == "si",
            )
        except merge.StalePreview:
            stale = True
        except (merge.Busy, OperationalError):  # somebody was writing the same rows (or the database broke a deadlock)
            messages.error(request, BUSY)
            return redirect("people:merge_preview", survivor_id, absorbed_id)
        except merge.MergeRefused as error:
            if _people(request.church).filter(pk__in=[survivor_id, absorbed_id]).count() != 2:
                messages.error(request, error.message)  # somebody else merged one of them meanwhile
                return redirect("people:duplicates")
        else:
            messages.success(request, f"Uniste los dos registros. Puedes deshacerlo durante {merge.UNDO_DAYS} días.")
            return redirect("people:merge_detail", record.pk)
        survivor, absorbed = _pair(request, survivor_id, absorbed_id)
    preview = merge.build_preview(request.church, survivor, absorbed)
    return render(request, "people/merge_preview.html", {
        "preview": preview, "survivor": survivor, "absorbed": absorbed, "stale": stale, "undo_days": merge.UNDO_DAYS,
        "reasons": [duplicates.SIGNAL_LABELS[signal] for signal in preview.signals],
    })


def _merge(request, merge_id: int) -> PersonMerge:
    return get_object_or_404(PersonMerge.objects.select_related("survivor", "absorbed", "merged_by", "undone_by"), pk=merge_id, church=request.church)


def _moved_lines(record: PersonMerge) -> list[str]:
    counts = {key: len(ids) for key, ids in (record.manifest.get("moved") or {}).items()}
    counts[merge.CAMP_KEY] = len(record.manifest.get("camp_participants") or [])
    return merge.Preview._counted(counts)


@church_admin_required
def merge_detail(request, merge_id: int):
    record = _merge(request, merge_id)
    return render(request, "people/merge_detail.html", {
        "record": record,
        "moved": _moved_lines(record),
        "can_undo": merge.can_undo(record),
        "until": merge.undoable_until(record),
        "not_restored": merge.undo_report_lines(record) if record.is_undone else [],
        "changed": merge.undo_change_lines(record) if record.is_undone else [],
        "survivor_open": record.survivor.merged_into_id is None,
        "absorbed_open": record.absorbed.merged_into_id is None,
        "undo_days": merge.UNDO_DAYS,
    })


@church_admin_required
@require_POST
def merge_undo(request, merge_id: int):
    record = _merge(request, merge_id)
    try:
        merge.undo(request.church, record.pk, request.user)
    except merge.MergeRefused as error:
        messages.error(request, error.message)
    except (merge.Busy, OperationalError):
        messages.error(request, BUSY)
    else:
        messages.success(request, "Deshiciste la unión: vuelven a ser dos registros.")
    return redirect("people:merge_detail", record.pk)
