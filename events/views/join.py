"""The public way into an event team: the printed code, then an access request the administration decides.

Nobody gets an account here. A request is a name and an e-mail; approving it on "Equipo" runs the same
path as "Agregar persona", so the person still creates their password from a link only they receive.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse

from ..access import resolve_access
from ..forms import JOIN_NOT_FOUND, AccessRequestForm, JoinForm
from ..models import AccessRequest, Event
from ..services import audit, throttle
from ..services.join import find_event_by_key
from .common import role_home_url

NOT_FOUND_PARAM = "no_encontrado"
# Searching: /unirme/ and every miss on /e/<x>/ share this budget per browser session. Hits are free.
SEARCH_LIMIT = {"limit": 10, "window_seconds": 600, "block_seconds": 60}
REQUEST_LIMIT = {"limit": 5, "window_seconds": 3600, "block_seconds": 300}
# More pending requests than an administration can read means someone is filling the list.
PENDING_REQUESTS_LIMIT = 50
LIST_FULL = "La lista de solicitudes está llena. Pide a la administración que te agregue directamente."
OPEN_STATUSES = (Event.Status.ACTIVE, Event.Status.DRAFT)


def _session_key(request) -> str:
    """A bounded throttle key (one per browser session), never the typed text. create() also sends the cookie."""
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


def _search_blocked(request) -> int:
    """Seconds this browser must wait before searching again; 0 without a session (it never searched)."""
    key = request.session.session_key
    return throttle.blocked_for(f"join:{key}") if key else 0


def _with_error(form, field, message):
    form.is_valid()
    form.errors.clear()
    form.add_error(field, message)
    return form


def join(request):
    """/unirme/: type the code or the name, land on the event's page. Same answer for a code and a name."""
    if request.method == "POST":
        form = JoinForm(request.POST)
        wait = throttle.consume(f"join:{_session_key(request)}", **SEARCH_LIMIT)
        if wait:
            _with_error(form, None, throttle.wait_message(wait))
        elif form.is_valid():
            event = find_event_by_key(form.cleaned_data["clave"])
            if event is not None:  # closed events too: their page says so
                return redirect("event_entry", code=event.join_code or event.slug)
            form.add_error("clave", JOIN_NOT_FOUND)
    elif NOT_FOUND_PARAM in request.GET:  # sent back from /e/<x>/: show what was tried, and why it failed
        form = JoinForm({"clave": request.GET[NOT_FOUND_PARAM][:160]})
        wait = _search_blocked(request)
        _with_error(form, None if wait else "clave", throttle.wait_message(wait) if wait else JOIN_NOT_FOUND)
    else:
        form = JoinForm()
    return render(request, "events/join/join.html", {"form": form})


def _event_meta(event) -> str:
    dates = render_to_string("events/rotulo/_event_dates.html", {"event": event}).strip()
    return f"{event.church.name}, {dates}." if dates else f"{event.church.name}."


def event_entry(request, code):
    """/e/<code>/: the event's own page.

    A miss spends the same search budget as /unirme/; a hit is free and writes nothing on GET. While the
    browser is blocked, a hit answers exactly like a miss, so the page cannot be used to test codes.
    """
    not_found = f"{reverse('join')}?{urlencode({NOT_FOUND_PARAM: code[:160]})}"
    event = find_event_by_key(code)
    if event is None:
        throttle.consume(f"join:{_session_key(request)}", **SEARCH_LIMIT)
        return redirect(not_found)
    if _search_blocked(request):
        return redirect(not_found)
    user = request.user
    if user.is_authenticated:
        access = resolve_access(user, event)
        if access is not None:
            return redirect(role_home_url(event, access))
    context = {"join_event": event, "meta": _event_meta(event)}
    if event.status not in OPEN_STATUSES:
        return render(request, "events/join/entry.html", {**context, "closed": True})
    account_email = (user.email or "").strip().lower() if user.is_authenticated else ""
    initial = {"full_name": user.get_full_name(), "email": account_email} if account_email else None
    form = AccessRequestForm(request.POST if request.method == "POST" else None, initial=initial, account_email=account_email)
    if request.method == "POST":
        wait = throttle.consume(f"join-request:{_session_key(request)}", **REQUEST_LIMIT)
        if wait:
            _with_error(form, None, throttle.wait_message(wait))
        elif form.is_valid():
            if _create_request(event, form.cleaned_data):
                # One answer for everyone: no hint about the account, the team or how e-mail is sent
                return render(request, "events/join/requested.html", {"join_event": event})
            form.add_error(None, LIST_FULL)
    return render(request, "events/join/entry.html", {**context, "form": form})


def _create_request(event, data) -> bool:
    """Store the request unless the list is full (False). One pending request per person: a repeat changes nothing."""
    with transaction.atomic():
        Event.objects.select_for_update().filter(pk=event.pk).first()  # one request at a time per event: the cap holds
        pending = AccessRequest.objects.filter(event=event, status=AccessRequest.Status.PENDING)
        if pending.count() >= PENDING_REQUESTS_LIMIT:
            return False
        mine = pending.filter(email=data["email"])
        if mine.exists():
            return True
        try:
            with transaction.atomic():
                access_request = AccessRequest.objects.create(
                    event=event,
                    full_name=data["full_name"].strip(),
                    email=data["email"],
                    requested_role=data["requested_role"],
                    note=data["note"].strip(),
                )
                # The note is the person's own words: it stays on the request, never in the activity log.
                audit.record(None, event, "solicitud_acceso",
                             f"{access_request.full_name} pidió acceso como {access_request.get_requested_role_display().lower()}", access_request)
        except IntegrityError:
            if not mine.exists():  # not the one-pending rule: a real error
                raise
    return True
