"""Acompañar: the people one person is expected to reach, and the administration's list of them.

Every view is behind the ``community`` module (``groups.views.community_view``: 404 with the module
off or without a person in the church) and fetches inside ``request.church``, so an id of another
church is a 404. The rule is decided by rows, not by roles: the church's administration sees and
arranges every follow-up; its owner sees it and works on it; anybody else gets a 403: a group
guide, a pastor without the administration role and, always, the person it is about (even when
that person is the administration). Hiding a link is not the rule: each view asks again.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Case, F, IntegerField, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from events.models import normalize_name
from groups.views import church_admin_view, community_view
from people.models import Person

from . import services
from .forms import FollowUpForm
from .models import FollowUp, FollowUpStep

Status = FollowUp.Status
LIST_LIMIT = 100
# The administration's views of the list: the word in the address, the words on the screen, the states.
FILTERS = [
    ("", "Sin atender", FollowUp.OPEN),
    ("atendidos", "Atendidos", (Status.DONE,)),
    ("cancelados", "Cancelados", (Status.CANCELLED,)),
]


def _people(request):
    """Who a follow-up can be about, for this administrator: the church's people, except themselves."""
    return Person.objects.filter(church=request.church, merged_into__isnull=True).exclude(pk=request.person.pk)


def _follow_up(request, follow_up_id: int, *, admin_only: bool = False) -> FollowUp:
    """A follow-up of this church the person may open: 404 for another church's, 403 for one that
    is neither theirs nor theirs to administer, and always 403 for the one that is about them."""
    follow_up = get_object_or_404(
        FollowUp.objects.filter(church=request.church).select_related("person", "owner"), pk=follow_up_id
    )
    if follow_up.person_id == request.person.pk:
        raise PermissionDenied
    is_owner = follow_up.owner_id == request.person.pk
    if not request.is_church_admin and (admin_only or not is_owner):
        raise PermissionDenied
    follow_up.is_mine = is_owner
    follow_up.is_overdue = bool(follow_up.is_open and follow_up.due_on and follow_up.due_on < timezone.localdate())
    return follow_up


@community_view
def mine(request):
    return render(request, "care/mine.html", {"follow_ups": services.mine(request.person)})


@church_admin_view
def admin_list(request):
    chosen = request.GET.get("estado", "")
    if chosen not in [key for key, _, _ in FILTERS]:
        raise Http404
    everything = FollowUp.objects.filter(church=request.church).exclude(person=request.person)
    filters = [
        {"key": key, "label": label, "count": everything.filter(status__in=states).count()} for key, label, states in FILTERS
    ]
    states = next(states for key, _, states in FILTERS if key == chosen)
    rows = everything.filter(status__in=states).select_related("person", "owner")
    if chosen:
        rows = rows.order_by("-closed_at", "-id")
    else:
        no_owner = Case(When(owner__isnull=True, then=Value(0)), default=Value(1), output_field=IntegerField())
        rows = rows.alias(no_owner=no_owner).order_by("no_owner", F("due_on").asc(nulls_last=True), "created_at", "id")
    rows, today = list(rows[: LIST_LIMIT + 1]), timezone.localdate()
    for row in rows:
        row.is_overdue = bool(row.is_open and row.due_on and row.due_on < today)
    return render(
        request,
        "care/admin_list.html",
        {"follow_ups": rows[:LIST_LIMIT], "cut": len(rows) > LIST_LIMIT, "limit": LIST_LIMIT, "filters": filters, "chosen": chosen},
    )


@church_admin_view
def create(request):
    chosen = (request.POST.get("persona") if request.method == "POST" else request.GET.get("persona")) or ""
    chosen = chosen.strip()
    if not chosen:
        if request.method == "POST":
            raise Http404
        query, found = request.GET.get("q", "").strip(), []
        if query:
            found = _people(request)
            for token in normalize_name(query).split():
                found = found.filter(normalized_name__contains=token)
            found = found[:10]
        return render(request, "care/choose_person.html", {"query": query, "found": found})
    if not chosen.isdigit():
        raise Http404
    about = get_object_or_404(_people(request), pk=int(chosen))
    form = FollowUpForm(request.POST or None, church=request.church, about=about)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            follow_up = services.create(
                request.church, request.user, person=about, reason=data["reason"], owner=data["owner"], due_on=data["due_on"]
            )
        except services.CareError as error:
            form.add_error(error.code if error.code in form.fields else None, error.message)
        else:
            if follow_up.owner_id:
                messages.success(request, f"Creaste el seguimiento. {follow_up.owner.first_name} lo verá en Acompañar.")
            else:
                messages.success(request, "Creaste el seguimiento. Todavía no tiene responsable.")
            return redirect("care:detail", follow_up.pk)
    return render(request, "care/form.html", {"form": form, "about": about, "follow_up": None, "reasons": FollowUp.Reason.choices})


@community_view
def detail(request, follow_up_id: int):
    follow_up = _follow_up(request, follow_up_id)
    return render(
        request,
        "care/detail.html",
        {
            "follow_up": follow_up,
            "about": follow_up.person,
            "steps": follow_up.steps.select_related("by"),
            "kinds": FollowUpStep.Kind.choices,
            "me": request.person,
            "back_url": reverse("care:mine" if follow_up.is_mine else "care:admin_list"),
            "back_label": "Volver a Personas que acompañas" if follow_up.is_mine else "Volver a todos los seguimientos",
        },
    )


@community_view
def edit(request, follow_up_id: int):
    follow_up = _follow_up(request, follow_up_id, admin_only=True)
    if not follow_up.is_open:
        raise Http404
    form = FollowUpForm(
        request.POST or None, church=request.church, about=follow_up.person, with_reason=False,
        initial={"owner": follow_up.owner_id, "due_on": follow_up.due_on},
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():  # the owner and the date are saved together or not at all
                services.assign(follow_up, request.user, data["owner"])
                services.set_due(follow_up, request.user, data["due_on"])
        except services.CareError as error:
            form.add_error(error.code if error.code in form.fields else None, error.message)
        else:
            messages.success(request, "Guardamos los cambios.")
            return redirect("care:detail", follow_up.pk)
    return render(request, "care/form.html", {"form": form, "about": follow_up.person, "follow_up": follow_up})


@community_view
@require_POST
def step(request, follow_up_id: int):
    follow_up = _follow_up(request, follow_up_id)
    try:
        services.add_step(follow_up, request.user, kind=request.POST.get("paso", ""), by=request.person)
    except services.CareError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, "Guardamos lo que hiciste.")
    return redirect("care:detail", follow_up.pk)


@community_view
@require_POST
def status(request, follow_up_id: int):
    follow_up = _follow_up(request, follow_up_id)
    wanted = request.POST.get("estado", "")
    if wanted not in (Status.DONE, Status.IN_PROCESS):  # cancelling is the administration's, at its own address
        raise Http404
    try:
        services.set_status(follow_up, request.user, wanted)
    except services.CareError as error:
        messages.error(request, error.message)
        return redirect("care:detail", follow_up.pk)
    if wanted == Status.DONE:
        messages.success(request, "Marcaste el seguimiento como atendido.")
        return redirect("care:mine" if follow_up.is_mine else "care:admin_list")
    messages.success(request, "El seguimiento está en proceso.")
    return redirect("care:detail", follow_up.pk)


@community_view
def cancel(request, follow_up_id: int):
    follow_up = _follow_up(request, follow_up_id, admin_only=True)
    if not follow_up.is_open:
        if request.method == "POST":
            messages.error(request, services.CLOSED)
        return redirect("care:detail", follow_up.pk)
    if request.method == "POST":
        try:
            services.set_status(follow_up, request.user, Status.CANCELLED)
        except services.CareError as error:
            messages.error(request, error.message)
            return redirect("care:detail", follow_up.pk)
        messages.success(request, "Cancelaste el seguimiento. Ya no le aparece a nadie como algo por hacer.")
        return redirect("care:admin_list")
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Cancelar el seguimiento de {follow_up.person.full_name}?",
            "text": "Dejará de aparecerle a quien lo tenía. No se borra: lo seguirás encontrando en Cancelados.",
            "action": "Sí, cancelar el seguimiento",
            "back_url": reverse("care:detail", args=[follow_up.pk]),
        },
    )
