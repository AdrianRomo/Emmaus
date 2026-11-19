"""Tags of the people list, for the church's administration only.

Every view: ``people.views.church_admin_required`` (the ``people`` module on for the current church,
404 otherwise; then church ``manage`` through ``core.authz.has_scope``, 403 otherwise). Tags and
people are fetched inside ``request.church``: an id of another church is a 404.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import tags
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
