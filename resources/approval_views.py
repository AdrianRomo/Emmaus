"""Aprobación pastoral: the list of what waits for a pastor, the preview, and the two decisions.

Like the rest of ``resources``: every view is behind the ``community`` module (``community_view``: 404
with the module off or without a person in the church) and works inside ``request.church``, so a kind
that does not exist and an object of another church are the same 404.

Who:

* the list and the preview: a pastor of the church, and its administration (which prepared the content
  and reads here what waits and why something came back). Anybody else: 403;
* approving and returning: a pastor of the church only (``approvals.can_approve``, asked here and again
  inside ``approvals.approve`` and ``approvals.send_back``). The administration cannot, by form or by address.

Nothing here reads, counts or hints who read a devotional or started a plan.
"""

from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.paging import paginate
from groups.views import community_view

from . import approvals
from .forms import ApproveForm, ReturnForm


def approvals_view(view_func):
    """A pastor of the church or its administration. ``request.can_approve`` says which."""

    @community_view
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        request.can_approve = approvals.can_approve(request.user, request.church)
        if not (request.can_approve or request.is_church_admin):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


def pastor_view(view_func):
    """Only a pastor of the church."""

    @approvals_view
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.can_approve:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


def _found(request, kind_key: str, object_id: int):
    """(kind, object, row) inside the request's church, when there is something to decide or a decision
    to read. A draft nobody asked about is not here: 404, like an id of another church."""
    kind = approvals.kind_of(kind_key)
    obj = kind.get(request.church, object_id) if kind is not None else None
    if obj is None:
        raise Http404
    row = approvals.row_of(request.church, kind, obj)
    if row is None and not approvals.is_waiting(kind, obj, row):
        raise Http404
    return kind, obj, row


def _page(request, kind, obj, row, *, approve_form=None, return_form=None, status=200):
    waiting = approvals.is_waiting(kind, obj, row)
    revision = kind.revision(obj)
    context = {
        "kind": kind, "obj": obj, "row": row, "title": kind.title(obj), "line": kind.line(obj),
        "waiting": waiting, "published": kind.is_published(obj), "can_approve": request.can_approve,
        "can_return": waiting and row is not None and row.is_pending,
        "not_ready": kind.not_ready(obj) if waiting else "",
        "approve_form": approve_form or ApproveForm(initial={"revision": revision}),
        "return_form": return_form or ReturnForm(),
        "revision": revision, "edit_url": kind.edit_url(obj) if request.is_church_admin else "",
        "has_pastor": approvals.has_active_pastor(request.church),
        **kind.preview(obj),
    }
    return render(request, "resources/approval_review.html", context, status=status)


@approvals_view
def queue(request):
    church = request.church
    return render(
        request, "resources/approval_queue.html",
        {
            "pending": approvals.pending(church), "returned": approvals.returned(church),
            "unrecorded": paginate(request, approvals.unrecorded(church)),
            "can_approve": request.can_approve, "has_pastor": approvals.has_active_pastor(church),
        },
    )


@approvals_view
def review(request, kind: str, object_id: int):
    return _page(request, *_found(request, kind, object_id))


@pastor_view
@require_POST
def approve(request, kind: str, object_id: int):
    found, obj, row = _found(request, kind, object_id)
    form = ApproveForm(request.POST)
    if not form.is_valid():
        return _page(request, found, obj, row, approve_form=form)
    data = form.cleaned_data
    try:
        approvals.approve(
            request.church, found.key, obj.pk, request.user, revision=data["revision"],
            permitted_use=data["permitted_use"], source_note=data["source_note"],
        )
    except approvals.StaleContent:
        messages.error(request, approvals.STALE)
        return redirect("resources:approval_review", found.key, obj.pk)
    except approvals.ApprovalError as error:
        messages.error(request, error.message)
        return redirect("resources:approval_queue") if error.code == "missing" else redirect("resources:approval_review", found.key, obj.pk)
    obj = found.get(request.church, obj.pk) or obj  # as it is now: published
    messages.success(request, f"Aprobaste «{found.title(obj)}». {found.published_note(obj)}")
    return redirect("resources:approval_queue")


@pastor_view
@require_POST
def send_back(request, kind: str, object_id: int):
    found, obj, row = _found(request, kind, object_id)
    form = ReturnForm(request.POST)
    if not form.is_valid():
        return _page(request, found, obj, row, return_form=form)
    try:
        approvals.send_back(request.church, found.key, obj.pk, request.user, reason=form.cleaned_data["reason"])
    except approvals.ApprovalError as error:
        messages.error(request, error.message)
        return redirect("resources:approval_queue") if error.code == "missing" else redirect("resources:approval_review", found.key, obj.pk)
    messages.success(request, f"Devolviste «{found.title(obj)}». No se publicó: la administración verá el motivo y lo podrá corregir.")
    return redirect("resources:approval_queue")
