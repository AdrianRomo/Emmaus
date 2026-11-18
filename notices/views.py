"""Avisos: what the church and a person's groups want them to know, read in "Mi iglesia".

Every view is behind the ``community`` module (``groups.views.community_view``: 404 with the
module off or without a person in the church) and fetches inside ``request.church``, so an id of
another church is a 404. Who writes: the administration to the whole church; who can manage a
group (its guides, the administration) to that group. Who reads: ``services.visible_to``. Hiding
a link is not the rule: each view asks again. No e-mail is sent.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats, timezone

from core.authz import Action, has_scope
from groups.access import church_groups, managed_group_ids
from groups.models import Group
from groups.views import community_view

from . import services
from .forms import WHOLE_CHURCH, NoticeForm
from .models import Announcement


def _can_write_to_group(request, group: Group) -> bool:
    return request.is_church_admin or has_scope(request.user, request.church, "group", group.pk, Action.MANAGE)


def _writable_groups(request) -> list[Group]:
    """The active groups of this church the person may publish to."""
    groups = church_groups(request.church)
    if not request.is_church_admin:
        groups = groups.filter(pk__in=managed_group_ids(request.user, request.church))
    return list(groups.order_by("name", "id"))


def _can_edit(request, notice: Announcement) -> bool:
    """The administration, or the author while they still may write to that group."""
    if request.is_church_admin:
        return True
    if notice.created_by_id != request.user.pk or notice.group_id is None:
        return False
    return notice.group.is_active and _can_write_to_group(request, notice.group)


def _notice(request, notice_id: int) -> Announcement:
    return get_object_or_404(Announcement.objects.filter(church=request.church).select_related("group"), pk=notice_id)


def _editable(request, notice_id: int) -> Announcement:
    """A notice the person may change. One they may only read is a 403; one they may not even read is a 404."""
    notice = _notice(request, notice_id)
    if _can_edit(request, notice):
        return notice
    if services.visible_to(request.person, is_admin=False).filter(pk=notice.pk).exists():
        raise PermissionDenied
    raise Http404


def _day(day) -> str:
    return formats.date_format(day, r"j \d\e F \d\e Y").lower()


@community_view
def notice_list(request):
    can_write = request.is_church_admin or bool(_writable_groups(request))
    return render(
        request,
        "notices/list.html",
        {
            "notices": services.for_home(request.person, is_admin=request.is_church_admin, limit=50),
            "can_write": can_write,
            "published": services.published_by(request.user, request.church) if can_write else [],
        },
    )


@community_view
def notice_detail(request, notice_id: int):
    notice = _notice(request, notice_id)
    can_edit = _can_edit(request, notice)
    readable = services.visible_to(request.person, is_admin=request.is_church_admin).filter(pk=notice.pk).exists()
    if not readable and not can_edit:
        raise Http404
    if readable:
        services.mark_read(notice, request.person)
    return render(request, "notices/detail.html", {"notice": notice, "can_edit": can_edit})


@community_view
def notice_create(request):
    groups = _writable_groups(request)
    if not request.is_church_admin and not groups:
        raise PermissionDenied
    # Who it is for is answered here, before the form: a value the person may not write to is refused, not corrected.
    chosen = (request.POST.get("para") if request.method == "POST" else request.GET.get("grupo")) or ""
    chosen = chosen.strip()
    group = None
    if chosen == WHOLE_CHURCH:
        if not request.is_church_admin:
            raise PermissionDenied
    elif chosen:
        if not chosen.isdigit():
            raise Http404
        group = get_object_or_404(church_groups(request.church), pk=int(chosen))
        if not _can_write_to_group(request, group):
            raise PermissionDenied
    audiences = ([(WHOLE_CHURCH, "Toda la iglesia")] if request.is_church_admin else []) + [
        (str(item.pk), item.name) for item in groups
    ]
    initial = {"para": chosen or audiences[0][0]}
    form = NoticeForm(request.POST or None, audiences=audiences, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            notice = services.publish(
                request.church, request.user, group=group, title=data["title"], body=data["body"],
                publish_on=data["publish_on"], expires_on=data["expires_on"],
            )
        except services.NoticeError as error:
            form.add_error(None, error.message)
        else:
            if notice.publish_on > timezone.localdate():
                messages.success(request, f"Guardamos el aviso. Se publicará el {_day(notice.publish_on)}.")
            else:
                messages.success(request, f"Publicaste el aviso p{notice.audience[1:]}.")
            return redirect("notices:detail", notice.pk)
    return render(request, "notices/form.html", {"form": form, "notice": None})


@community_view
def notice_edit(request, notice_id: int):
    notice = _editable(request, notice_id)
    if notice.is_retired:
        raise Http404
    form = NoticeForm(request.POST or None, instance=notice)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.update(
                notice, request.user, title=data["title"], body=data["body"],
                publish_on=data["publish_on"], expires_on=data["expires_on"],
            )
        except services.NoticeError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, "Guardamos los cambios.")
            return redirect("notices:detail", notice.pk)
    return render(request, "notices/form.html", {"form": form, "notice": notice})


@community_view
def notice_retire(request, notice_id: int):
    notice = _editable(request, notice_id)
    if request.method == "POST":
        services.retire(notice, request.user)
        messages.success(request, "Retiraste el aviso. Ya nadie lo ve en Mi iglesia.")
        return redirect("notices:list")
    if notice.is_retired:
        return redirect("notices:detail", notice.pk)
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Retirar el aviso “{notice.title}”?",
            "text": "Dejará de verse en Mi iglesia. No se borra: lo seguirás encontrando en los avisos que publicaste.",
            "action": "Sí, retirar el aviso",
            "back_url": reverse("notices:detail", args=[notice.pk]),
        },
    )
