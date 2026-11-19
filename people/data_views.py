"""Tags of the people list and its download, for the church's administration only.

Every view: ``people.views.church_admin_required`` (the ``people`` module on for the current church,
404 otherwise; then church ``manage`` through ``core.authz.has_scope``, 403 otherwise). Tags and
people are fetched inside ``request.church``: an id of another church is a 404.
"""

import csv

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from events.services import throttle

from . import export, tags
from .audit import record_church
from .services import PeopleError
from .views import _person, church_admin_required


def _ids(values) -> set[int]:
    return {int(value) for value in values if value.isascii() and value.isdigit() and len(value) < 19}


@church_admin_required
def tag_list(request):
    """"Etiquetas": the church's tags, how many people carry each, and the one-line form that adds one."""
    name = ""
    error = ""
    if request.method == "POST":
        name = request.POST.get("nombre", "")
        try:
            tag = tags.create_tag(request.church, name, request.user)
        except PeopleError as refused:
            error = refused.message
        else:
            messages.success(request, f"Creaste la etiqueta «{tag.name}».")
            return redirect("people:tags")
    rows = list(tags.church_tags(request.church))
    for tag in rows:
        tag.carriers = tags.carriers(tag)
    return render(request, "people/tags.html", {
        "tags": rows, "name": name, "error": error, "max_tags": tags.MAX_TAGS, "max_name": tags.MAX_NAME,
        "full": len(rows) >= tags.MAX_TAGS,
    })


@church_admin_required
def tag_delete(request, tag_id: int):
    """One question before a tag is deleted, with how many people carry it; the POST confirms that number."""
    tag = get_object_or_404(tags.church_tags(request.church), pk=tag_id)
    stale = False
    if request.method == "POST":
        seen = request.POST.get("personas", "")
        try:
            tags.delete_tag(request.church, tag.pk, request.user, seen_count=int(seen) if seen.isascii() and seen.isdigit() else -1)
        except tags.StaleTags:
            stale = True
        except PeopleError as refused:
            messages.error(request, refused.message)
            return redirect("people:tags")
        else:
            messages.success(request, f"Borraste la etiqueta «{tag.name}».")
            return redirect("people:tags")
    return render(request, "people/tag_delete.html", {"tag": tag, "carriers": tags.carriers(tag), "stale": stale})


@church_admin_required
@require_POST
def person_tags(request, person_id: int):
    """The checkboxes of a person's page: leave the person with exactly the tags that are marked."""
    person = _person(request, person_id)
    try:
        tags.set_tags(request.church, person.pk, _ids(request.POST.getlist("etiqueta")), request.user,
                      seen=_ids(request.POST.get("antes", "").split(",")))
    except tags.StaleTags:
        messages.error(request, "Alguien más cambió sus etiquetas mientras las veías. No cambiamos nada: revisa cómo quedaron.")
    except PeopleError as refused:
        messages.error(request, refused.message)
        if refused.code == "merged":
            return redirect("people:list")
    else:
        messages.success(request, "Guardamos sus etiquetas.")
    return redirect("people:detail", person.pk)


# ---- the download ---------------------------------------------------------------------------------
# Per administrator and church: a list is downloaded now and then, not in a loop.
EXPORT_LIMIT = {"limit": 5, "window_seconds": 60 * 60, "block_seconds": 10 * 60}


@church_admin_required
def people_export(request):
    """"Descargar la lista": what the file holds (GET) and the file (POST): one CSV per request, counted
    against a limit, with one audit row that says how many rows went out and nothing about who is in them."""
    context = {"columns": export.HEADER, "total": _people_count(request.church)}
    if request.method != "POST":
        return render(request, "people/export.html", context)
    wait = throttle.consume(f"people-export:{request.church.pk}:{request.user.pk}", **EXPORT_LIMIT)
    if wait:
        context["wait"] = throttle.wait_message(wait).replace("Demasiados intentos.", "Ya descargaste la lista varias veces.")
        return render(request, "people/export.html", context, status=429)
    table = export.rows(request.church)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    response["Content-Disposition"] = f'attachment; filename="personas-{request.church.slug}-{stamp}.csv"'
    response["Cache-Control"] = "no-store"
    response.write("\ufeff")  # Excel opens UTF-8 correctly with a BOM
    writer = csv.writer(response)
    writer.writerow(export.HEADER)
    writer.writerows(table)
    record_church(request.church, request.user, "personas_exportadas", "Descargó la lista de personas de la iglesia (CSV)", None,
                  count=len(table))
    return response


def _people_count(church) -> int:
    from .views import _people

    return _people(church).count()
