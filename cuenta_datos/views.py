"""«Mis datos en Emmaus», its download and the request to be removed; the administration's task for
that request; and «Quién puede qué».

Every view: the ``community`` module on for the church of the request (404 otherwise) and a person
of that church (``groups.views.community_view``). The person's own screens read only
``request.person`` and ``request.user``: no id of a person is ever taken from the address. The
administration's screens ask ``church_admin_view``; «Quién puede qué» asks ``overview.access``
(the church roles of administration and pastor of THIS church), and answers 404 to anybody else.
"""

import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.authz import churches_of
from core.nav import safe_next
from events.services import throttle
from formularios.services import church_tz
from groups.views import church_admin_view, community_view
from overview.access import can_see_overview
from people import flags

from . import powers as power_review
from . import registry, services
from .forms import DownloadForm, RemovalForm
from .models import RemovalRequest


def _ctx(request) -> registry.Ctx:
    return registry.Ctx(request.church, request.person, request.user)


@community_view
def home(request):
    return render(request, "cuenta_datos/home.html", {
        "summaries": registry.summaries(_ctx(request)),
        "may_download": services.may_download(request.person),
        "open_request": services.open_request(request.church, request.user),
        "other_churches": len(churches_of(request.user)) > 1,
        "not_included": registry.NOT_INCLUDED,
    })


@community_view
def download(request):
    """GET: what the file holds and the password. POST: the file (JSON, as an attachment) or the same
    content as a page to print. Nothing is stored on the server; each one is counted and audited."""
    church, user = request.church, request.user
    context = {"may_download": services.may_download(request.person), "not_included": registry.NOT_INCLUDED,
               "per_day": services.DOWNLOADS_PER_DAY, "has_password": user.has_usable_password()}
    if not context["may_download"]:
        return render(request, "cuenta_datos/download.html", context, status=403 if request.method == "POST" else 200)
    form = DownloadForm(request.POST or None)
    context["form"] = form
    context["left"] = services.downloads_left(church, user)
    if request.method != "POST" or not form.is_valid():
        return render(request, "cuenta_datos/download.html", context)
    right, wait = services.check_password(user, form.cleaned_data["password"])
    if not right:
        if wait:
            form.add_error("password", throttle.wait_message(wait))
            return render(request, "cuenta_datos/download.html", context, status=429)
        form.add_error("password", "Esa no es tu contraseña. Vuelve a escribirla.")
        return render(request, "cuenta_datos/download.html", context)
    as_page = request.POST.get("formato") == "pagina"
    try:
        content = services.build_download(church, request.person, user, form.texts(), kind="pagina" if as_page else "json")
    except services.TooManyDownloads as error:
        context["again_at"] = timezone.localtime(error.again_at, church_tz(church))
        context["left"] = 0
        return render(request, "cuenta_datos/download.html", context, status=429)
    if as_page:
        response = render(request, "cuenta_datos/print.html", {
            "content": content, "sections": _printable(content), "generated": content["generado"],
            "included": content["textos que pediste incluir"], "not_included": registry.NOT_INCLUDED,
        })
    else:
        response = HttpResponse(json.dumps(content, ensure_ascii=False, indent=2), content_type="application/json; charset=utf-8")
        stamp = timezone.localtime(timezone.now(), church_tz(church)).strftime("%Y%m%d")
        response["Content-Disposition"] = f'attachment; filename="mis-datos-{church.slug}-{stamp}.json"'
    response["Cache-Control"] = "no-store"
    return response


def _printable(content: dict) -> list[dict]:
    """The download's categories as the page draws them: each row a list of (word, value) with the empty ones
    left out, and a value that is itself a list (the days of a plan, the answers of a form) as its own rows."""
    sections = []
    for category in content["categorías"]:
        groups = []
        for label, rows in category["datos"].items():
            drawn = []
            for row in rows:
                facts, nested = [], []
                for word, value in row.items():
                    if isinstance(value, list):
                        nested.append((word, [[(k, v) for k, v in one.items() if v not in ("", None)] for one in value]))
                    elif value not in ("", None):
                        facts.append((word, value))
                drawn.append({"facts": facts, "nested": nested})
            groups.append({"label": label, "rows": drawn})
        sections.append({"title": category["categoría"], "why": category["para qué lo tiene Emmaus"], "groups": groups})
    return sections


@community_view
def ask_removal(request):
    """One fixed choice becomes one task for the administration. Nothing is deleted here, and the page says so."""
    if not services.may_download(request.person):
        raise PermissionDenied  # a known minor asks through the church, with a guardian (the page says so)
    existing = services.open_request(request.church, request.user)
    form = RemovalForm(request.POST or None)
    if request.method == "POST" and existing is None and form.is_valid():
        services.ask_removal(request.church, request.person, request.user, form.cleaned_data["ask"])
        messages.success(request, "Pediste que borren tus datos. La administración de tu iglesia lo verá en sus pendientes.")
        return redirect("cuenta_datos:home")
    if request.method == "POST" and existing is not None:
        return redirect("cuenta_datos:home")
    return render(request, "cuenta_datos/ask_removal.html", {"form": form, "existing": existing})


@community_view
@require_POST
def withdraw(request, request_id: int):
    """Only the account's own open request, in the church of the request: any other id answers 404."""
    if not RemovalRequest.objects.filter(pk=request_id, church=request.church, user=request.user).exists():
        raise Http404
    if services.withdraw(request.church, request.user, request_id) is not None:
        messages.success(request, "Retiraste tu solicitud.")
    else:
        messages.warning(request, "Esa solicitud ya estaba cerrada.")
    # In place on «Mis datos en Emmaus» (form[data-inline]): back to the page that sent it, when it is of this site.
    return redirect(safe_next(request, request.POST.get("siguiente"), reverse("cuenta_datos:home")))


# ---- the administration's task -------------------------------------------------------------------
def _named(church, rows) -> list[RemovalRequest]:
    rows = list(rows)
    for row in rows:
        row.person = services.person_of(church, row.user)
        row.who = (row.person.full_name if row.person else "") or row.user.get_full_name().strip() or row.user.email
    return rows


@church_admin_view
def request_list(request):
    return render(request, "cuenta_datos/requests.html", {
        "requests": _named(request.church, services.open_requests(request.church).order_by("created_at", "id")),
    })


def _request(request, request_id: int) -> RemovalRequest:
    row = RemovalRequest.objects.filter(pk=request_id, church=request.church).select_related("user", "closed_by").first()
    if row is None:
        raise Http404
    return _named(request.church, [row])[0]


@church_admin_view
def request_detail(request, request_id: int):
    """What exists about the person, by category (a count where the administration may already see the rows,
    only the name of the category where it is private to the person), and the tools that already exist."""
    row = _request(request, request_id)
    ctx = registry.Ctx(request.church, row.person, row.user)
    return render(request, "cuenta_datos/request.html", {
        "row": row, "counts": registry.staff_counts(ctx),
        "people_on": flags.enabled(request.church, "people"),
        "person_url": reverse("people:detail", args=[row.person.pk]) if row.person and flags.enabled(request.church, "people") else "",
    })


@church_admin_view
@require_POST
def request_done(request, request_id: int):
    row = _request(request, request_id)
    if services.mark_done(request.church, row.pk, request.user) is not None:
        messages.success(request, "Marcaste la solicitud como atendida.")
    else:
        messages.warning(request, "Esa solicitud ya estaba cerrada.")
    return redirect("cuenta_datos:requests")


# ---- «Quién puede qué» ---------------------------------------------------------------------------
@community_view
def powers(request):
    """Read-only, for the administration and the pastors of this church. Anybody else: 404 (the page does not
    say it exists). Not exported: there is no file of it."""
    if not can_see_overview(request.user, request.church):
        raise Http404
    review = power_review.build(request.church, can_change=request.is_church_admin)
    return render(request, "cuenta_datos/powers.html", {"review": review, "stale_days": power_review.STALE_DAYS})
