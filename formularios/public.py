"""The public form: anybody with the address answers, without an account.

One answer for everything that is not such a form right now (no form with that address, a form of
the church's people, a draft, before its first day, after its last, closed, the church not using the
module or not active): the same 404, so the address is no oracle. Who is signed in is never looked
at: the page is the same for everybody, and it shows nothing about who else answered or how many.

How the POST is safe without an account:

* **CSRF.** The page carries Django's CSRF token and the browser gets Django's CSRF cookie with it:
  the one cookie of this page. It identifies nobody, holds no data and is not stored on the server.
  No session is opened, nothing is written to one, and ``django.contrib.messages`` is not used (it
  would need one): what the person reads after sending is a page of its own.
* **Throttle.** ``events.services.throttle`` counts per browser session or account everywhere else;
  here that would give every visitor a session. The counter is per form instead, with no address of
  a network or a device in it: one form takes ``LIMIT`` valid answers per ``WINDOW`` seconds from
  everybody together, then asks people to wait. A flood slows one form down for a few minutes and
  nothing else; ``services.MAX_SUBMISSIONS`` bounds what a form keeps in any case.
* **Honeypot.** A box people never see. A send that fills it gets the same "gracias" and writes nothing.
* **Twice.** The same e-mail or phone answering the same version within a day is one answer and one
  task (``services.submit``); the second send gets the same "gracias", so it says nothing either way.
"""

from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from events.services import throttle
from people import flags

from . import services
from .fields import HONEYPOT, AnswerForm
from .models import Form

LIMIT = 20
WINDOW = 600
BLOCK = 120
MAX_BLOCK = 600


def public_form(token: str) -> Form | None:
    """The form anybody may answer right now at this address, else None."""
    form = (
        Form.objects.filter(public_token=token, audience=Form.Audience.PUBLIC, status=Form.Status.PUBLISHED, church__active=True)
        .select_related("church", "current_version").first()
    )
    if form is None or not flags.enabled(form.church, "community") or not services.is_open(form):
        return None
    return form


def public_path(form: Form) -> str:
    return reverse("formulario:fill", args=[form.public_token])


def _context(row: Form, **extra) -> dict:
    church = row.church
    return {"form_row": row, "church": church, "brand_church": church, "theme_church": church, "ui_v2": True, **extra}


@require_http_methods(["GET", "HEAD", "POST"])
def fill(request, token: str):
    form = public_form(token)
    if form is None:
        raise Http404
    version = form.current_version
    answer = AnswerForm(request.POST if request.method == "POST" else None, schema=version.schema)
    changed = full = False
    wait = 0
    if request.method == "POST":
        if request.POST.get(HONEYPOT, "").strip():
            return redirect("formulario:thanks", form.public_token)  # not a person: nothing is written
        if request.POST.get("version") != str(version.pk):
            changed = True
            answer = AnswerForm(schema=version.schema)
        elif answer.is_valid():
            wait = throttle.consume(f"formulario:{form.pk}", limit=LIMIT, window_seconds=WINDOW, block_seconds=BLOCK, max_block_seconds=MAX_BLOCK)
            if not wait:
                try:
                    services.submit(form, version.pk, answers=answer.answers, contact=answer.contact)
                except services.FormChanged:
                    return redirect("formulario:fill", form.public_token)  # the form as it is now, or its 404
                except services.FormFull:
                    full = True
                else:
                    return redirect("formulario:thanks", form.public_token)
    context = _context(
        form, version=version, form=answer, items=list(answer.bound_items()), changed=changed, full=full, wait=wait,
        delete_after=services.delete_after(form), honeypot=HONEYPOT,
    )
    return render(request, "formularios/public.html", context, status=429 if wait else 200)


@require_safe
def thanks(request, token: str):
    form = public_form(token)
    if form is None:
        raise Http404
    return render(request, "formularios/public_thanks.html", _context(form, version=form.current_version))
