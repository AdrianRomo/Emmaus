"""Avisos: what the church and a person's groups want them to know, read in "Mi iglesia".

Every view is behind the ``community`` module (``groups.views.community_view``: 404 with the
module off or without a person in the church) and fetches inside ``request.church``, so an id of
another church is a 404. Who writes: the administration to the whole church; who can manage a
group (its guides, the administration) to that group. Who makes one urgent: who holds a church role
the church's policy names (``services.may_publish_urgent``; today the administration). Who reads: ``services.visible_to``. Hiding
a link is not the rule: each view asks again. No e-mail is sent.

"Compartir por WhatsApp" hands the person a message to send themselves. It carries the church's
name and the address of the notice's page, which asks for the account and answers by the same rules
as here; it never carries the title or the body, because a message can be forwarded to anybody.
"""

from urllib.parse import quote


from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats, timezone

from core.authz import Action, has_scope
from groups.access import church_groups, managed_group_ids
from groups.models import Group
from groups.views import community_view
from people import policy

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
    notices = Announcement.objects.filter(church=request.church).select_related("group", "created_by")
    return get_object_or_404(notices, pk=notice_id)


def _editable(request, notice_id: int) -> Announcement:
    """A notice the person may change. One they may only read is a 403; one they may not even read is a 404."""
    notice = _notice(request, notice_id)
    if _can_edit(request, notice):
        return notice
    if services.visible_to(request.person, is_admin=False).filter(pk=notice.pk).exists():
        raise PermissionDenied
    raise Http404


def _published_by(request, notice: Announcement) -> str:
    """Who speaks, without a name: the church, or whoever guides the group. Only a guide or the
    administration writes to a group, so an author who is not the administration is a guide."""
    author = notice.created_by
    if notice.group_id and author is not None:
        if not has_scope(author, request.church, "church", request.church.pk, Action.MANAGE):
            return f"Publicado por quien guía {notice.group.name}"
    return "Publicado por la iglesia"


def _share_url(request, notice: Announcement) -> str:
    """A WhatsApp message the person sends: the church, that there is a notice, and where to read
    it after signing in. Nothing of the notice itself and nothing that opens it without an account."""
    link = request.build_absolute_uri(reverse("notices:detail", args=[notice.pk]))
    text = f"{request.church.name}: hay un aviso nuevo en Emmaus. Entra con tu cuenta para leerlo: {link}"
    return "https://wa.me/?text=" + quote(text, safe="")


def _acknowledged_line(notice: Announcement) -> tuple[str, bool]:
    """What who may change the notice reads about "Enterado", and whether it gives the count."""
    done, total = services.acknowledgement_count(notice)
    smallest = policy.min_reporting_size(notice.church)
    if total < smallest:  # with so few readers the number would say who
        return f"Este aviso es para menos de {smallest} personas con cuenta: aquí no se cuenta cuántas dijeron «Enterado».", False
    # A number that leaves fewer than the minimum on either side says who: "5 de 6" names the one who has not.
    if 0 < done < smallest:
        return f"Menos de {smallest} de {total} personas han dicho «Enterado».", True
    if 0 < total - done < smallest:
        return f"Casi todas las {total} personas dijeron «Enterado».", True
    return f"{done} de {total} personas dijeron «Enterado».", True


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
    context = {"notice": notice, "can_edit": can_edit, "published_by": _published_by(request, notice)}
    if notice.state == "publicado":
        context["share_url"] = _share_url(request, notice)
    if notice.is_urgent:
        if readable and notice.created_by_id != request.user.pk:
            answered = services.acknowledged_at(notice, request.person)
            context["can_acknowledge"] = answered is None
            context["acknowledged_on"] = timezone.localdate(answered) if answered else None
            if answered is None and services.read_before_it_changed(notice, request.person):
                context["changed_on"] = timezone.localdate(notice.changed_at)
        if can_edit:
            context["acknowledged_line"], context["count_given"] = _acknowledged_line(notice)
            context["asked_again_on"] = timezone.localdate(notice.changed_at) if notice.changed_at else None
    return render(request, "notices/detail.html", context)


@community_view
def notice_acknowledge(request, notice_id: int):
    """"Enterado" on an urgent notice the person may read now. Anything else does not exist."""
    notice = _notice(request, notice_id)
    readable = services.visible_to(request.person, is_admin=request.is_church_admin).filter(pk=notice.pk).exists()
    if not readable or not notice.is_urgent:
        raise Http404
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    # The page says which version it showed. If the notice changed since, the answer would be to
    # words the person has not read. A page without it (opened before versions existed) is of version 1.
    seen = request.POST.get("version", "1")
    try:
        if services.acknowledge(notice, request.person, version=int(seen) if seen.isdigit() else 0):
            messages.success(request, "Listo, anotamos tu «Enterado».")
    except services.NoticeError as error:
        messages.error(request, error.message)
    return redirect("notices:detail", notice.pk)


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
    may_urgent = services.may_publish_urgent(request.user, request.church)
    form = NoticeForm(request.POST or None, audiences=audiences, initial=initial, allow_urgent=may_urgent)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            notice = services.publish(
                request.church, request.user, group=group, title=data["title"], body=data["body"],
                publish_on=data["publish_on"], expires_on=data["expires_on"],
                priority=data["priority"],
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
    version = notice.version
    may_urgent = services.may_publish_urgent(request.user, request.church)
    form = NoticeForm(request.POST or None, instance=notice, allow_urgent=may_urgent)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            saved = services.update(
                notice, request.user, title=data["title"], body=data["body"],
                publish_on=data["publish_on"], expires_on=data["expires_on"],
                priority=data.get("priority"), material=form.material_answer,
            )
        except services.NoticeError as error:
            form.add_error(None, error.message)
        else:
            if saved.version != version:
                messages.success(request, "Guardamos los cambios y pedimos «Enterado» otra vez a todas las personas a quienes va el aviso.")
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
