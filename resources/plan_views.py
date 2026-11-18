"""Planes de lectura: several days of reading that the church prepared, read at each person's pace.

Like the rest of ``resources``: every view is behind the ``community`` module (``community_view``:
404 with the module off or without a person in the church) and fetches inside ``request.church``,
so a plan or a day of another church is a 404. Only the administration writes (``church_admin_view``).

Who opens a plan is answered once, in ``_open``: a published plan, everybody; a retired one, who had
started it; a draft or a retired one that the person never started, only the administration (to
look at it: it cannot be started or marked). Anything else is a 404, by list and by address.

What a person started and read is theirs alone: no view here shows, counts or hints who started a
plan, where anybody is or who finished. The administration's screens read nothing from the
progress tables.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.paging import paginate
from groups.views import church_admin_view, community_view

from . import plans
from .forms import PublishPlanForm, ReadingPlanDayForm, ReadingPlanForm
from .models import ReadingPlan, ReadingPlanDay


def _plan(request, plan_id: int) -> ReadingPlan:
    return get_object_or_404(ReadingPlan, church=request.church, pk=plan_id)


def _open(request, plan_id: int):
    """(plan, this person's progress or None, whether they are only looking as the administration)."""
    plan = _plan(request, plan_id)
    progress = plans.progress_of(plan, request.person)
    if plans.may_read(plan, progress):
        return plan, progress, False
    if request.is_church_admin:
        return plan, None, True
    raise Http404


def _day_words(total: int) -> str:
    return "1 día" if total == 1 else f"{total} días"


# ---- reading ------------------------------------------------------------------------------------
@community_view
def plan_list(request):
    found = plans.plans_for(request.church, request.person)
    for plan in found:
        plan.day_words = _day_words(plan.day_count)
    return render(request, "resources/plan_list.html", {"plans": found})


@community_view
def plan_detail(request, plan_id: int):
    plan, progress, preview = _open(request, plan_id)
    days = plans.days_for(plan, progress)
    next_day = plans.first_unread(days) if progress else None
    return render(
        request,
        "resources/plan_detail.html",
        {
            "plan": plan, "days": days, "day_words": _day_words(len(days)), "started": progress is not None,
            "next_day": next_day, "finished": progress is not None and bool(days) and next_day is None,
            "any_read": any(day.is_read for day in days), "preview": preview,
        },
    )


@community_view
@require_POST
def plan_start(request, plan_id: int):
    plan = _plan(request, plan_id)
    if not plan.is_published:
        raise Http404
    progress = plans.start(plan, request.person)
    day = plans.first_unread(plans.days_for(plan, progress))
    if day is None:  # they had finished it already: starting again changes nothing
        return redirect("resources:plan_detail", plan.pk)
    return redirect("resources:plan_day", plan.pk, day.position)


@community_view
def plan_day(request, plan_id: int, position: int):
    plan, progress, preview = _open(request, plan_id)
    days = plans.days_for(plan, progress)
    day = next((item for item in days if item.position == position), None)
    if day is None:
        raise Http404
    return render(
        request,
        "resources/plan_day.html",
        {
            "plan": plan, "day": day, "total": len(days), "started": progress is not None, "preview": preview,
            "heading": day.title or f"Día {position}", "line": f"{plan.title}, día {position} de {len(days)}.",
            "reading": f"Lectura: {day.reference}. Léela en tu Biblia: aquí va solo la cita.",
            "previous": position - 1 if position > 1 else None, "next": position + 1 if position < len(days) else None,
        },
    )


@community_view
@require_POST
def plan_day_read(request, plan_id: int, position: int):
    plan, progress, _preview = _open(request, plan_id)
    if progress is None:
        raise Http404
    day = get_object_or_404(ReadingPlanDay, plan=plan, position=position)
    day.plan = plan
    plans.mark_day_read(day, request.person)
    if plans.first_unread(plans.days_for(plan, progress)) is None:
        return redirect("resources:plan_detail", plan.pk)  # every day is read: the plan's page says so, quietly
    return redirect("resources:plan_day", plan.pk, position)


@community_view
def plan_leave(request, plan_id: int):
    plan, progress, _preview = _open(request, plan_id)
    if progress is None:
        return redirect("resources:plan_detail", plan.pk)
    if request.method == "POST":
        plans.leave(plan, request.person)
        messages.success(request, f"Dejaste el plan «{plan.title}». Borramos tus marcas.")
        return redirect("resources:plan_list") if plan.is_retired else redirect("resources:plan_detail", plan.pk)
    text = "Se borran tus marcas de lo que ya leíste. "
    text += (
        "Tu iglesia ya retiró este plan: si lo dejas, no vas a poder abrirlo otra vez."
        if plan.is_retired else "Puedes empezarlo otra vez cuando quieras."
    )
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Dejar el plan «{plan.title}»?", "text": text, "action": "Sí, dejar el plan",
            "back_url": reverse("resources:plan_detail", args=[plan.pk]),
        },
    )


# ---- writing ------------------------------------------------------------------------------------
@church_admin_view
def plan_admin(request):
    found = ReadingPlan.objects.filter(church=request.church).order_by("status", "title", "id")
    return render(request, "resources/plan_admin.html", {"plans": paginate(request, found)})


def _plan_form(request, plan):
    form = ReadingPlanForm(request.POST or None, instance=plan)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            saved = plans.save_plan(
                request.church, request.user, title=data["title"], description=data["description"],
                author_line=data["author_line"], plan=plan,
            )
        except plans.ResourceError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, "Guardamos los cambios." if plan else "Guardamos el borrador del plan. Ahora agrega sus días.")
            return redirect("resources:plan_days", saved.pk)
    return render(request, "resources/plan_form.html", {"form": form, "plan": plan})


@church_admin_view
def plan_create(request):
    return _plan_form(request, None)


@church_admin_view
def plan_edit(request, plan_id: int):
    return _plan_form(request, _plan(request, plan_id))


@church_admin_view
def plan_days(request, plan_id: int):
    plan = _plan(request, plan_id)
    days = list(plan.days.order_by("position"))
    return render(request, "resources/plan_days.html", {"plan": plan, "days": days, "day_words": _day_words(len(days))})


def _day_form(request, plan, day):
    form = ReadingPlanDayForm(request.POST or None, instance=day)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            saved = plans.save_day(
                plan, request.user, reference=data["reference"], title=data["title"], reflection=data["reflection"],
                question=data["question"], day=day,
            )
        except plans.ResourceError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, f"Guardamos el día {saved.position}.")
            return redirect("resources:plan_days", plan.pk)
    heading = f"Editar el día {day.position}" if day else f"Agregar el día {plan.days.count() + 1}"
    return render(request, "resources/plan_day_form.html", {"form": form, "plan": plan, "day": day, "heading": heading})


@church_admin_view
def plan_day_create(request, plan_id: int):
    return _day_form(request, _plan(request, plan_id), None)


@church_admin_view
def plan_day_edit(request, plan_id: int, day_id: int):
    plan = _plan(request, plan_id)
    return _day_form(request, plan, get_object_or_404(ReadingPlanDay, plan=plan, pk=day_id))


@church_admin_view
def plan_day_delete(request, plan_id: int, day_id: int):
    plan = _plan(request, plan_id)
    day = get_object_or_404(ReadingPlanDay, plan=plan, pk=day_id)
    if not plan.is_draft:
        raise Http404
    back = reverse("resources:plan_days", args=[plan.pk])
    if request.method == "POST":
        try:
            plans.delete_day(day, request.user)
        except plans.ResourceError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, f"Quitaste el día {day.position}. Los días siguientes se recorrieron.")
        return redirect(back)
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Quitar el día {day.position} del plan?",
            "text": "Se borra lo que escribiste para ese día y los días siguientes se recorren.",
            "action": "Sí, quitar el día", "back_url": back,
        },
    )


@church_admin_view
def plan_publish(request, plan_id: int):
    plan = _plan(request, plan_id)
    if plan.is_published:
        return redirect("resources:plan_days", plan.pk)
    total = plan.days.count()
    form = PublishPlanForm(request.POST if request.method == "POST" else None)  # an empty POST is still an answer: no
    if request.method == "POST" and form.is_valid():
        try:
            plans.publish_plan(plan, request.user, confirmed=form.cleaned_data["confirm"])
        except plans.ResourceError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, "Publicaste el plan. Las personas de tu iglesia ya lo encuentran en Planes de lectura.")
            return redirect("resources:plan_days", plan.pk)
    return render(request, "resources/plan_publish.html", {"form": form, "plan": plan, "total": total, "day_words": _day_words(total)})


@church_admin_view
def plan_retire(request, plan_id: int):
    plan = _plan(request, plan_id)
    back = reverse("resources:plan_days", args=[plan.pk])
    if request.method == "POST":
        plans.retire_plan(plan, request.user)
        messages.success(request, "Retiraste el plan. Ya nadie lo puede empezar.")
        return redirect(back)
    if plan.is_retired:
        return redirect(back)
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Retirar el plan «{plan.title}»?",
            "text": "Deja de verse en Planes de lectura y nadie lo puede empezar. Quien ya lo empezó lo puede seguir "
            "leyendo hasta terminarlo. No se borra: lo puedes volver a publicar.",
            "action": "Sí, retirar el plan", "back_url": back,
        },
    )
