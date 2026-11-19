"""Página pública de la iglesia: what the administration writes, reviews and publishes.

Every view is behind the ``community`` module and for the church's administration only
(``groups.views.church_admin_view``: 404 with the module off, 403 for anybody else), and works
inside ``request.church``: there is no church in any address, and a time or a resource of another
church is a 404.

Publishing has a review step at one address (``publish``): ``GET`` lists exactly what anybody will
be able to read, ``POST`` asks a pastor of the church to approve it. The administration does not
publish: the pastor's approval does (``resources.approval_views``). The form carries a fingerprint
of what was listed; if what would be public is no longer that (somebody made an event public
meanwhile), nothing is asked and the list is shown again.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from groups.views import church_admin_view
from resources import approvals
from resources.models import MediaItem

from . import public, services
from .forms import PublicHomeForm, ServiceTimeForm
from .models import ServiceTime

STALE = "Lo que se mostraría cambió mientras revisabas. No se pidió nada: revisa otra vez."


def _approval(church, home):
    return approvals.row_of(church, approvals.kind_of(services.HOME_KIND), home)


LEFT = (
    "Guardamos el cambio. La página dejó de ser pública: cambió la bienvenida que un pastor había aprobado. "
    "Revísala y pide la aprobación pastoral otra vez."
)


def _time(request, time_id: int) -> ServiceTime:
    return get_object_or_404(ServiceTime, church=request.church, pk=time_id)


@church_admin_view
def edit(request):
    church = request.church
    home = services.home_of(church)
    if request.method == "POST":
        form = PublicHomeForm(request.POST)
        if form.is_valid():
            try:
                saved = services.save_home(church, request.user, **form.cleaned_data)
            except services.SiteError as error:
                form.add_error(None, error.message)
            else:
                if home.published and not saved.published:
                    messages.warning(request, LEFT)
                elif home.published:
                    messages.success(request, "Guardamos los cambios. Ya se ven en la página pública.")
                else:
                    messages.success(request, "Guardamos los cambios. Nadie los ve todavía: la página no está publicada.")
                return redirect("sitio:edit")
    else:
        form = PublicHomeForm(initial={name: getattr(home, name) for name in ("welcome", "address", "map_url", "contact_phone")})
    return render(
        request, "sitio/edit.html",
        {
            "form": form, "home": home, "live": services.is_live(church), "content": services.page_content(church, home),
            "approval": _approval(church, home), "has_pastor": approvals.has_active_pastor(church),
            "public_url": request.build_absolute_uri(public.public_path(church)),
        },
    )


@church_admin_view
def preview(request):
    """The page as anybody would read it, for the administration, published or not."""
    content = services.page_content(request.church)
    return render(request, "sitio/public.html", public.page_context(content, preview=True, live=services.is_live(request.church)))


@church_admin_view
def publish(request):
    church = request.church
    home = services.home_of(church)
    if home.published:
        return redirect("sitio:edit")
    if request.method == "POST":
        try:
            services.request_publication(church, request.user, request.POST.get("revision", ""))
        except services.StaleReview:
            messages.error(request, STALE)
            return redirect("sitio:publish")
        approvals.tell_asked(request, "Guardamos tu solicitud para publicar la página.")
        return redirect("sitio:edit")
    content = services.page_content(church, home)
    return render(
        request, "sitio/review.html",
        {"content": content, "home": home, "church": church, "revision": content.digest(),
         "approval": _approval(church, home), "has_pastor": approvals.has_active_pastor(church),
         "public_url": request.build_absolute_uri(public.public_path(church))},
    )


@church_admin_view
def unpublish(request):
    if request.method == "POST":
        services.unpublish(request.church, request.user)
        messages.success(request, "Dejaste de publicar la página. Quien abra el enlace ya no la encuentra.")
        return redirect("sitio:edit")
    if not services.home_of(request.church).published:
        return redirect("sitio:edit")
    return render(
        request, "groups/confirm.html",
        {
            "title": "¿Dejar de publicar la página de la iglesia?",
            "text": "Quien abra el enlace ya no encontrará la página. No se borra nada: puedes publicarla otra vez cuando quieras. "
                    "Las páginas públicas de los eventos siguen funcionando.",
            "action": "Sí, dejar de publicarla",
            "back_url": reverse("sitio:edit"),
        },
    )


def _time_form(request, row):
    initial = None
    if row is not None:
        initial = {"weekday": row.weekday, "time": row.time, "name": row.name, "place": row.place, "position": row.position or None}
    form = ServiceTimeForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.save_time(request.church, request.user, weekday=data["weekday"], time=data["time"], name=data["name"],
                               place=data["place"], position=data["position"] or 0, row=row)
        except services.SiteError as error:
            form.add_error(error.code if error.code in form.fields else None, error.message)
        else:
            messages.success(request, "Guardamos el horario." if row else "Agregaste el horario.")
            return redirect("sitio:edit")
    return render(request, "sitio/time_form.html", {"form": form, "row": row})


@church_admin_view
def time_create(request):
    return _time_form(request, None)


@church_admin_view
def time_edit(request, time_id: int):
    return _time_form(request, _time(request, time_id))


@church_admin_view
def time_delete(request, time_id: int):
    row = _time(request, time_id)
    if request.method == "POST":
        services.delete_time(row, request.user)
        messages.success(request, "Quitaste el horario.")
        return redirect("sitio:edit")
    return render(
        request, "groups/confirm.html",
        {
            "title": f"¿Quitar el horario «{row.name}»?",
            "text": "Dejará de verse en la página pública de la iglesia.",
            "action": "Sí, quitar el horario",
            "back_url": reverse("sitio:edit"),
        },
    )


def _media(request, item_id: int) -> MediaItem:
    return get_object_or_404(MediaItem, church=request.church, pk=item_id)


@church_admin_view
@require_POST
def media_show(request, item_id: int):
    item = _media(request, item_id)
    try:
        services.show_media(item, request.user)
    except services.SiteError as error:
        messages.error(request, error.message)
    else:
        if services.is_live(request.church):
            messages.success(request, f"«{item.title}» ya se puede ver en la página pública de la iglesia, sin cuenta.")
        else:
            messages.success(request, f"«{item.title}» se verá en la página pública de la iglesia cuando la publiques.")
    return redirect("resources:media_admin")


@church_admin_view
@require_POST
def media_hide(request, item_id: int):
    item = _media(request, item_id)
    services.hide_media(item, request.user)
    messages.success(request, f"«{item.title}» ya no se ve en la página pública. Sigue en Recursos para las personas de la iglesia.")
    return redirect("resources:media_admin")
