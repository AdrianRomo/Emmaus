"""The three pages of an event's e-mail subscriptions, all without an account (``agenda.subscriptions``).

* ``/evento/<slug>/avisos-por-correo/``: the form, one field. It answers only while the event's public page does,
  the administration turned the option on and this server can send; otherwise the same 404 as the public page.
  CSRF: the ordinary check, with the ordinary cookie, set by THIS page (as the sign-in page does). That is why the
  form is not on the event's public page, which stays free of cookies. After a valid POST the answer is one
  redirect to one text, whatever happened to the address, and the address is not in it.
* ``…/avisos-por-correo/confirmar/<token>/``: a button; only pressing it confirms (ordinary CSRF check).
* ``…/avisos-por-correo/baja/<token>/``: a button; pressing it deletes the address. Its POST is exempt from the
  CSRF check on purpose: the mail program's own "darse de baja" button (``List-Unsubscribe-Post``, RFC 8058) posts
  here without cookies. Nothing is lost: the check protects what a browser's cookies authorize, and here nothing
  comes from a cookie; the signed token in the address is the whole authorization, for a person and for a forger.

A link that is tampered with, expired, of the other kind, or of a row that is gone gets one answer (404 with a
plain text). Who is signed in is never looked at, nothing here reads a table of people, and no page is cached.
"""

from django import forms
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.forms import TuErrorMessagesMixin

from . import public, subscriptions

PAGE = "agenda/subscription.html"


class SubscribeForm(TuErrorMessagesMixin, forms.Form):
    """An e-mail address and nothing else: no name, no phone, no relation to anybody."""

    email = forms.EmailField(
        label="Tu correo", max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email", "inputmode": "email", "autocapitalize": "none", "spellcheck": "false"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].error_messages.update({
            "required": "Escribe tu correo.",
            "invalid": "Escribe un correo completo, por ejemplo nombre@ejemplo.com.",
        })


def _page(request, state: str, *, event=None, status: int = 200, **context):
    church = event.church if event is not None else None
    return render(
        request, PAGE,
        {
            "state": state, "event": event, "church": church, "purpose": subscriptions.PURPOSE,
            "confirm_hours": subscriptions.CONFIRM_HOURS, "retention_days": subscriptions.RETENTION_DAYS,
            # As the public page: it wears the event's church, whoever reads it.
            "brand_church": church, "theme_church": church, "ui_v2": True,
            **context,
        },
        status=status,
    )


def _shown_event(subscription):
    """The event, to name it on the page, only while its public page answers: a name that stopped being public
    is not repeated here."""
    event = subscription.event
    return event if public.public_page(event.slug) is not None else None


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def subscribe(request, slug: str):
    page = subscriptions.offered_page(slug)
    if page is None:
        raise Http404
    event = page.event
    back = public.public_path(event)
    if request.method != "POST" and request.GET.get("listo") == "1":
        return _page(request, "asked", event=event, back=back)
    form = SubscribeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        subscriptions.request(event, form.cleaned_data["email"])
        return redirect(f"{public.subscribe_path(event)}?listo=1")
    return _page(request, "form", event=event, back=back, form=form)


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def confirm(request, token: str):
    subscription = subscriptions.for_confirmation(token)
    if subscription is None:
        return _page(request, "invalid", status=404)
    event = subscription.event  # ``for_confirmation`` answers only while its public page does
    back = public.notices_path(event)
    if request.method == "POST":
        subscriptions.confirm(subscription)
        return _page(request, "confirmed", event=event, back=back)
    return _page(request, "confirm", event=event, back=back)


@csrf_exempt
@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def unsubscribe(request, token: str):
    subscription = subscriptions.for_unsubscribe(token)
    if subscription is None:
        return _page(request, "invalid", status=404)
    event = _shown_event(subscription)
    if request.method == "POST":
        subscriptions.unsubscribe(subscription)
        return _page(request, "left", event=event)
    return _page(request, "leave", event=event)
