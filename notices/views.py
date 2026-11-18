"""Avisos: what the church and a person's groups want them to know, read in "Mi iglesia".

Every view is behind the ``community`` module (``groups.views.community_view``: 404 with the
module off or without a person in the church) and fetches inside ``request.church``, so an id of
another church is a 404. Who writes: the administration to the whole church; who can manage a
group (its guides, the administration) to that group. Who makes one urgent: who holds a church role
the church's policy names (``services.may_publish_urgent``; today the administration). Who reads: ``services.visible_to``. Hiding
a link is not the rule: each view asks again.

E-mail (``notices.email``): who chose it on "Avisos por correo" is told that there is a notice and
where, never what it says. Publishing hands the address of this Emmaus down from the request; who may
change a notice reads how many e-mails went out, as a number under the church's minimum reporting size.
A notice kept for a later day is mailed by the scheduler's pass: the form and the notice's page say that
no e-mail will go out that day while that pass is not running (``core.scheduler.notice_mail_problem``).

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
from core.paging import paginate
from core.scheduler import notice_mail_problem
from groups.access import church_groups, managed_group_ids
from groups.models import Group
from groups.views import community_view
from people import policy

from events.services.team import email_is_configured

from . import email, services
from .forms import WHOLE_CHURCH, EmailPreferenceForm, NoticeForm
from .models import Announcement


# The scheduler's pass is what mails a notice kept for a later day (``core.scheduler``). While it is not
# running, or the server lacks its public address, nothing here says that an e-mail will go out that day.
NOT_SENDING = "Ahora mismo Emmaus no está enviando los correos de los avisos programados."
NOT_SENDING_THIS = (
    "Si eso no cambia, el día que se publique este aviso se verá en Emmaus, pero no saldrá ningún correo."
    " Avísale a quien administra el servidor de Emmaus."
)
NOT_SENDING_NEW = (
    "Si eliges un día posterior para publicarlo, ese día se verá en Emmaus, pero no saldrá ningún correo."
    " Si lo publicas hoy, el correo sí sale."
)


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


def _emailed_lines(notice: Announcement) -> list[str]:
    """What who may change the notice reads about its e-mails: how many went out for the notice as
    it is now, never to whom, and that sent is not read. Nothing pretends an e-mail that did not go."""
    sent, total, failed = email.sent_count(notice)
    smallest = policy.min_reporting_size(notice.church)
    if not sent and not email_is_configured():
        return ["No hay correo configurado: este aviso solo se ve en Emmaus."]
    if not sent and notice.state != "publicado":
        if notice.state != "programado":
            return []
        # Kept for a later day: only the scheduler's pass can mail it then. Promise it only while that pass runs.
        if notice_mail_problem():
            return ["No ha salido ningún correo por este aviso.", f"{NOT_SENDING} {NOT_SENDING_THIS}"]
        return ["No ha salido ningún correo por este aviso. El día que se publique, Emmaus le enviará el correo a quien lo eligió."]
    if not sent and email.will_not_be_mailed(notice):
        # Published while the server had no e-mail, or before e-mails existed, and its days have passed.
        return [
            f"No salió ningún correo por este aviso y ya no saldrá ninguno: los correos solo salen en los {email.WINDOW_DAYS}"
            " días siguientes a su publicación. Este aviso solo se ve en Emmaus."
        ]
    if total < smallest:  # with so few readers the number would say who chose what
        lines = [f"Este aviso es para menos de {smallest} personas con cuenta: aquí no se cuenta cuántos correos salieron."]
    elif not sent:
        lines = ["Hasta ahora no ha salido ningún correo por este aviso."]
    elif sent < smallest:
        lines = [f"Salieron menos de {smallest} correos por este aviso."]
    elif 0 < total - sent < smallest:
        lines = [f"Salió un correo a casi todas las {total} personas con cuenta a quienes va este aviso."]
    elif sent > total:  # people left the group since
        lines = [f"Salieron {sent} correos por este aviso."]
    else:
        lines = [f"Salieron {sent} correos: a {sent} de las {total} personas con cuenta a quienes va este aviso."]
    if notice.version > 1 and total >= smallest:
        lines[0] += " Cuenta desde el último cambio que pidió «Enterado» otra vez."
    if failed:
        lines.append("Algunos correos no se pudieron enviar.")
    lines.append(
        "Cada persona elige qué avisos le llegan por correo. El correo solo dice que hay un aviso y lleva el enlace a"
        " esta página. Un correo enviado no es un correo leído."
    )
    return lines


def _scheduled_mail_warning(notice: Announcement | None) -> str:
    """What the form says when a notice kept for a later day would not be e-mailed on its day: the
    server has e-mail (a notice published today is mailed from here) and nothing is sending the rest.
    Empty when the pass runs, when there is no e-mail at all (the form already says so) and when
    the notice being corrected is not waiting for its day."""
    if notice is not None and notice.state != "programado":
        return ""
    if not email_is_configured() or not notice_mail_problem():
        return ""
    return f"{NOT_SENDING} {NOT_SENDING_THIS if notice is not None else NOT_SENDING_NEW}"


def _base_url(request) -> str:
    """The address of this Emmaus as the person reached it: what an e-mail's link starts with."""
    return request.build_absolute_uri("/")


def _day(day) -> str:
    return formats.date_format(day, r"j \d\e F \d\e Y").lower()


@community_view
def notice_list(request):
    can_write = request.is_church_admin or bool(_writable_groups(request))
    # Two lists on one screen, each with its own page in the address (?pagina= and ?publicados=).
    published = services.published_by(request.user, request.church) if can_write else Announcement.objects.none()
    return render(
        request,
        "notices/list.html",
        {
            "notices": paginate(request, services.readable(request.person, is_admin=request.is_church_admin)),
            "can_write": can_write,
            "published": paginate(request, published, param="publicados"),
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
    if can_edit:
        context["emailed_lines"] = _emailed_lines(notice)
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
                priority=data["priority"], base_url=_base_url(request),
            )
        except services.NoticeError as error:
            form.add_error(None, error.message)
        else:
            if notice.publish_on > timezone.localdate():
                messages.success(request, f"Guardamos el aviso. Se publicará el {_day(notice.publish_on)}.")
            else:
                messages.success(request, f"Publicaste el aviso p{notice.audience[1:]}.")
            return redirect("notices:detail", notice.pk)
    return render(
        request,
        "notices/form.html",
        {"form": form, "notice": None, "email_configured": email_is_configured(), "scheduled_mail_warning": _scheduled_mail_warning(None)},
    )


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
                priority=data.get("priority"), material=form.material_answer, base_url=_base_url(request),
            )
        except services.NoticeError as error:
            form.add_error(None, error.message)
        else:
            if saved.version != version:
                messages.success(request, "Guardamos los cambios y pedimos «Enterado» otra vez a todas las personas a quienes va el aviso.")
            else:
                messages.success(request, "Guardamos los cambios.")
            return redirect("notices:detail", notice.pk)
    return render(request, "notices/form.html", {"form": form, "notice": notice, "scheduled_mail_warning": _scheduled_mail_warning(notice)})


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


@community_view
def email_preferences(request):
    """"Avisos por correo": the person's own choice of which notices they hear about by e-mail."""
    person = request.person
    # ``data`` even when empty: a form sent with nothing chosen is answered with the error, not shown again as new.
    data = request.POST if request.method == "POST" else None
    form = EmailPreferenceForm(data, initial={"choice": email.preference(person)})
    if request.method == "POST" and form.is_valid():
        email.set_preference(person, form.cleaned_data["choice"])
        messages.success(request, "Guardamos tu elección.")
        return redirect("notices:email_preferences")
    return render(
        request,
        "notices/email_preferences.html",
        {"form": form, "address": person.email, "email_configured": email_is_configured()},
    )
