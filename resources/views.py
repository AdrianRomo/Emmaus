"""Devocional y Recursos: the devotional of the day and what the church preached, sang or taught.

Every view is behind the ``community`` module (``groups.views.community_view``: 404 with the
module off or without a person in the church) and fetches inside ``request.church``, so an id of
another church is a 404. Every person of the church reads what is published; only the church's
administration writes (``church_admin_view``). Hiding a link is not the rule: each view asks again.

"Ya lo leí" is the person's own: no view here shows who read a devotional or how many did.
Resources open on the site that hosts them, as plain links: nothing is embedded.
"""

from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats, timezone
from django.views.decorators.http import require_POST

from core.paging import paginate
from groups.views import church_admin_view, community_view

from . import services
from .forms import DevotionalForm, MediaItemForm
from .models import Devotional, MediaItem

# The words of the filter are plurals; the model's are the singular a row shows.
KIND_FILTERS = [("predicacion", "Predicaciones"), ("musica", "Música"), ("estudio", "Estudios"), ("otro", "Otros recursos")]


def _day(day) -> str:
    """"martes 17 de noviembre"; with the year when it is not this one."""
    words = formats.date_format(day, r"l j \d\e F").lower()
    return words if day.year == timezone.localdate().year else f"{words} de {day.year}"


def _devotional(request, devotional_id: int) -> Devotional:
    return get_object_or_404(Devotional, church=request.church, pk=devotional_id)


def _media(request, item_id: int) -> MediaItem:
    return get_object_or_404(MediaItem, church=request.church, pk=item_id)


def _reading_page(request, devotional, *, today_page: bool):
    """One devotional as a page to read. ``devotional`` carries ``is_read`` when the person may mark it."""
    line = ""
    if devotional is not None:
        is_today = devotional.date == timezone.localdate()
        line = "Devocional de hoy, " if is_today else "Devocional del "
        line += _day(devotional.date) + (f", por {devotional.author_name}." if devotional.author_name else ".")
    return render(request, "resources/devotional.html", {"devotional": devotional, "today_page": today_page, "line": line})


# ---- the devotional: reading --------------------------------------------------------------------
@community_view
def devotional_today(request):
    return _reading_page(request, services.today_devotional(request.church, request.person), today_page=True)


@community_view
def devotional_list(request):
    devotionals = paginate(request, services.past_devotionals(request.church, request.person))
    return render(request, "resources/devotional_list.html", {"devotionals": devotionals})


@community_view
def devotional_detail(request, devotional_id: int):
    devotional = _devotional(request, devotional_id)
    if not devotional.is_readable and not request.is_church_admin:
        raise Http404
    devotional.is_read = services.is_read(devotional, request.person)
    return _reading_page(request, devotional, today_page=False)


@community_view
@require_POST
def devotional_read(request, devotional_id: int):
    devotional = _devotional(request, devotional_id)
    if not devotional.is_readable:
        raise Http404
    services.mark_read(devotional, request.person)
    shown_today = services.today_devotional(request.church, None)
    if shown_today is not None and shown_today.pk == devotional.pk:
        return redirect("resources:devotional_today")
    return redirect("resources:devotional_detail", devotional.pk)


# ---- the devotional: writing --------------------------------------------------------------------
@church_admin_view
def devotional_admin(request):
    devotionals = Devotional.objects.filter(church=request.church).order_by("-date", "-id")
    return render(request, "resources/devotional_admin.html", {"devotionals": paginate(request, devotionals)})


def _devotional_form(request, devotional):
    initial = None if devotional else {"date": services.first_free_day(request.church)}
    form = DevotionalForm(request.POST or None, instance=devotional, church=request.church, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            saved = services.save_devotional(
                request.church, request.user, date=data["date"], title=data["title"], passage=data["passage"],
                body=data["body"], author_name=data["author_name"], publish=data["publish"], devotional=devotional,
            )
        except services.ResourceError as error:
            form.add_error(None, error.message)
        else:
            if saved.state == "borrador":
                messages.success(
                    request, f"Guardamos el borrador del {_day(saved.date)}. Nadie lo ve todavía: para publicarlo, edítalo y marca «Publicarlo»."
                )
            elif saved.state == "programado":
                messages.success(request, f"Guardamos el devocional. Se publicará el {_day(saved.date)}.")
            else:
                messages.success(request, f"Publicaste el devocional del {_day(saved.date)}.")
            return redirect("resources:devotional_admin")
    return render(request, "resources/devotional_form.html", {"form": form, "devotional": devotional})


@church_admin_view
def devotional_create(request):
    return _devotional_form(request, None)


@church_admin_view
def devotional_edit(request, devotional_id: int):
    return _devotional_form(request, _devotional(request, devotional_id))


# ---- resources: reading -------------------------------------------------------------------------
@community_view
def media_list(request):
    published = services.published_media(request.church)
    counts = dict(published.order_by().values_list("kind").annotate(total=Count("id")))
    kinds = [{"value": value, "label": label, "count": counts[value]} for value, label in KIND_FILTERS if value in counts]
    if len(kinds) < 2:
        kinds = []
    series = " ".join(request.GET.get("serie", "").split())[:120]
    chosen = request.GET.get("tipo", "")
    if series or chosen not in {kind["value"] for kind in kinds}:
        chosen = ""
    query = request.GET.get("q", "").replace("\x00", "").strip()[:160]
    items = published
    if query:
        items = items.filter(Q(title__icontains=query) | Q(speaker__icontains=query) | Q(series__icontains=query))
    if series:
        items = items.filter(series__iexact=series)
    elif chosen:
        items = items.filter(kind=chosen)
    return render(
        request,
        "resources/media_list.html",
        {
            "items": paginate(request, items),
            "query": query,
            "kinds": [] if series else kinds,
            "chosen": chosen,
            "series": series,
            "total": sum(counts.values()),
        },
    )


# ---- resources: writing -------------------------------------------------------------------------
@church_admin_view
def media_admin(request):
    items = MediaItem.objects.filter(church=request.church).order_by("status", "-date", "-id")
    return render(request, "resources/media_admin.html", {"items": paginate(request, items)})


def _media_form(request, item):
    form = MediaItemForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.save_media(
                request.church, request.user, kind=data["kind"], title=data["title"], url=data["url"], date=data["date"],
                speaker=data["speaker"], series=data["series"], description=data["description"], item=item,
            )
        except services.ResourceError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, "Guardamos los cambios." if item else "Publicaste el recurso. Ya se ve en Recursos.")
            return redirect("resources:media_admin")
    return render(request, "resources/media_form.html", {"form": form, "item": item})


@church_admin_view
def media_create(request):
    return _media_form(request, None)


@church_admin_view
def media_edit(request, item_id: int):
    item = _media(request, item_id)
    if item.is_retired:
        raise Http404
    return _media_form(request, item)


@church_admin_view
def media_retire(request, item_id: int):
    item = _media(request, item_id)
    if request.method == "POST":
        services.retire_media(item, request.user)
        messages.success(request, "Retiraste el recurso. Ya nadie lo ve en Recursos.")
        return redirect("resources:media_admin")
    if item.is_retired:
        return redirect("resources:media_admin")
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Retirar el recurso “{item.title}”?",
            "text": "Dejará de verse en Recursos. No se borra: lo seguirás encontrando al administrar los recursos.",
            "action": "Sí, retirar el recurso",
            "back_url": reverse("resources:media_admin"),
        },
    )
