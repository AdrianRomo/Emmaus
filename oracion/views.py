"""Peticiones de oración: the writer's pages, the pastors' pages and the church's switch.

Every view is behind the ``community`` module (``groups.views.community_view``: 404 with the module
off or without a person in the church) and works inside ``request.church``. Two rules, asked again
on every request and never taken from a link that was or was not shown:

* **who writes**: an adult person of the church, while a pastor has the church's switch on
  (``_writer_or_404``). With the switch off the form is a 404; a person keeps the page of what they
  already wrote, to read the answer and to delete it.
* **who reads**: who holds the church role of pastor in this church now (``_reader_or_404``), and
  each row again through ``access.readable``. Anybody else gets the same 404 as for an id that does
  not exist: the administration, a guide, the owner of a follow-up, a superuser, a pastor of another
  church, a pastor the request was not addressed to.

The words never go into a message of the messages framework, the session or a log; the views that
receive them mark them as sensitive, so an error report of a failed request does not carry them.
"""

import logging
from datetime import timedelta

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from core.paging import paginate
from events.services.team import email_is_configured
from groups.views import community_view

from . import access, services
from .forms import PrayerForm, ReplyForm, TermsForm
from .models import PrayerRequest

log = logging.getLogger(__name__)


def _own(request):
    return PrayerRequest.objects.filter(church=request.church, person=request.person)


def _writer_or_404(request) -> None:
    if not services.is_on(request.church) or not access.may_write(request.person):
        raise Http404


def _reader_or_404(request) -> None:
    if not access.is_reader(request.user, request.church):
        raise Http404


# ---- who writes ---------------------------------------------------------------------------------
@community_view
def mine(request):
    """What this person wrote, with its state and its answer. Nobody else's, ever."""
    own = list(_own(request))
    may_write = services.is_on(request.church) and access.may_write(request.person)
    if not own and not may_write:
        raise Http404
    readers = {user.pk: user for user in access.readers(request.church, apart_from=request.user)}
    for prayer in own:
        chosen = readers.get(prayer.only_reader_id) if prayer.restricted else None
        prayer.reader_name = chosen.reader_name if chosen else ""
        prayer.nobody_reads = not readers or (prayer.restricted and chosen is None)
    return render(request, "oracion/mine.html", {
        "prayers": own, "may_write": may_write, "full": len(own) >= services.MAX_KEPT, "limit": services.MAX_KEPT,
    })


@sensitive_post_parameters("body")
@community_view
def new(request):
    _writer_or_404(request)
    church = request.church
    readers = access.readers(church, apart_from=request.user)
    terms = services.terms(church)
    context = {
        "readers": readers, "terms": terms, "emergency": services.EMERGENCY_911,
        "expires_on": timezone.now() + timedelta(days=terms.retention_days),
    }
    if not readers:
        return render(request, "oracion/unavailable.html", context)
    token = services.readers_token(readers)
    form = PrayerForm(request.POST or None, readers=readers)
    if request.method == "POST" and form.is_valid():
        try:
            prayer = services.create(
                church, request.person, body=form.cleaned_data["body"], token=request.POST.get("lectores", ""),
                only_reader_id=form.only_reader_id,
            )
        except services.PrayerError as error:
            if error.code == "off":
                raise Http404 from None
            form.add_error("body" if error.code == "body" else None, error.message)
        else:
            try:
                services.notify_readers(prayer, request.build_absolute_uri(reverse("oracion:inbox")))
            except Exception as error:  # noqa: BLE001 - an e-mail that fails changes nothing for the writer
                log.error("A notice of a new prayer request could not be sent: %s", type(error).__name__)
            messages.success(request, "Guardamos tu petición. Aquí vas a ver cuando la lean.")
            return redirect("oracion:mine")
    return render(request, "oracion/form.html", {**context, "form": form, "token": token, "several": len(readers) > 1})


@community_view
def delete(request, prayer_id: int):
    prayer = get_object_or_404(_own(request), pk=prayer_id)
    if request.method == "POST":
        services.delete_own(request.church, request.person, prayer.pk)
        messages.success(request, "Borramos tu petición y su respuesta. Ya no la puede leer nadie.")
        still = _own(request).exists() or (services.is_on(request.church) and access.may_write(request.person))
        return redirect("oracion:mine" if still else "community:home")
    return render(request, "groups/confirm.html", {
        "title": "¿Borrar esta petición de oración?",
        "text": "Se borra de Emmaus junto con su respuesta, y quien la leía deja de verla. No se puede recuperar.",
        "action": "Sí, borrarla",
        "back_url": reverse("oracion:mine"),
    })


# ---- who reads ----------------------------------------------------------------------------------
@community_view
def inbox(request):
    """The requests this pastor may read: who and when, never the words (those are one page each)."""
    _reader_or_404(request)
    rows = access.readable(request.user, request.church).select_related("person")
    return render(request, "oracion/inbox.html", {
        "prayers": paginate(request, rows), "terms": services.terms(request.church),
        "wants_email": services.wants_email(request.church, request.user),
        "email_configured": email_is_configured(), "address": request.user.email,
    })


def _detail(request, prayer, form=None, status=200):
    return render(request, "oracion/read.html", {
        "prayer": prayer, "form": form or ReplyForm(), "on": services.is_on(request.church),
    }, status=status)


@community_view
def read(request, prayer_id: int):
    _reader_or_404(request)
    prayer = get_object_or_404(access.readable(request.user, request.church).select_related("person"), pk=prayer_id)
    services.opened(request.church, request.user, prayer)
    return _detail(request, prayer)


@community_view
@require_POST
def mark_read(request, prayer_id: int):
    _reader_or_404(request)
    try:
        services.mark_read(request.church, request.user, prayer_id)
    except services.Gone:
        raise Http404 from None
    messages.success(request, "La marcaste como leída. Quien la escribió verá «Leída» con la fecha.")
    return redirect("oracion:read", prayer_id)


@sensitive_post_parameters("reply")
@community_view
@require_POST
def reply(request, prayer_id: int):
    _reader_or_404(request)
    prayer = get_object_or_404(access.readable(request.user, request.church).select_related("person"), pk=prayer_id)
    if not services.is_on(request.church):
        raise Http404
    form = ReplyForm(request.POST)
    if form.is_valid():
        try:
            services.reply(request.church, request.user, prayer.pk, form.cleaned_data["reply"])
        except services.Gone:
            raise Http404 from None
        except services.PrayerError as error:
            form.add_error("reply" if error.code == "reply" else None, error.message)
        else:
            messages.success(request, "Guardamos tu respuesta. Quien escribió la petición la verá cuando entre a Emmaus.")
            return redirect("oracion:read", prayer.pk)
    return _detail(request, prayer, form)


@community_view
@require_POST
def mark_talked(request, prayer_id: int):
    _reader_or_404(request)
    try:
        services.mark_talked(request.church, request.user, prayer_id)
    except services.Gone:
        raise Http404 from None
    messages.success(request, "Marcaste que hablaron en persona.")
    return redirect("oracion:read", prayer_id)


@community_view
@require_POST
def reader_email(request):
    _reader_or_404(request)
    wanted = request.POST.get("avisar") == "si"
    services.set_wants_email(request.church, request.user, wanted)
    if wanted:
        messages.success(request, "Guardamos tu elección: un correo cuando haya una petición nueva, sin decir de quién ni qué dice.")
    else:
        messages.success(request, "Guardamos tu elección: no te llega correo de las peticiones.")
    return redirect("oracion:inbox")


# ---- the church's switch --------------------------------------------------------------------------
@community_view
def terms(request):
    """What the church promises, and the switch. For a pastor of this church only: the
    administration cannot open it, and with no pastor nobody can."""
    _reader_or_404(request)
    church = request.church
    now = services.terms(church)
    readers = access.readers(church)
    step = request.POST.get("paso", "") if request.method == "POST" else ""
    if step == "apagar":
        if services.switch_off(church, request.user):
            messages.success(request, "Apagaste las peticiones de oración. Ya no se puede escribir una nueva; las que hay se quedan hasta su fecha.")
        return redirect("oracion:terms")
    form = TermsForm(request.POST or None, initial={"retention_days": now.retention_days, "emergency_line": now.own_line})
    if request.method == "POST" and step not in ("encender", "guardar"):
        raise Http404
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            if step == "encender":
                services.switch_on(
                    church, request.user, accepted=data["accepted"], token=request.POST.get("promesas", ""),
                    emergency_line=data["emergency_line"], retention_days=data["retention_days"],
                )
                messages.success(request, "Encendiste las peticiones de oración. Quedó anotado que tú aceptaste lo que la iglesia promete.")
            else:
                services.change_terms(church, request.user, emergency_line=data["emergency_line"], retention_days=data["retention_days"])
                messages.success(request, "Guardamos los cambios.")
        except services.PrayerError as error:
            form.add_error(error.code if error.code in form.fields else None, error.message)
        else:
            return redirect("oracion:terms")
    return render(request, "oracion/terms.html", {
        "form": form, "terms": now, "readers": readers, "token": services.readers_token(readers),
        "emergency": services.EMERGENCY_911, "crisis_line": services.CRISIS_LINE,
        "max_days": services.policy.PRAYER_RETENTION_DAYS,
    })
