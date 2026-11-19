"""The public form: anybody with the address answers, without an account.

One answer for everything that is not such a form right now (no form with that address, a form of
the church's people, a draft, before its first day, after its last, closed, the church not using the
module or not active): the same 404, so the address is no oracle. Who is signed in is never looked
at: the page is the same for everybody, and it shows nothing about who else answered or how many.

How the POST is safe without an account, and without knowing anything about who sends:

* **CSRF.** The page carries Django's CSRF token and the browser gets Django's CSRF cookie with it:
  the one cookie of this page. It identifies nobody, holds no data and is not stored on the server.
  No session is opened, nothing is written to one, and ``django.contrib.messages`` is not used.
* **Seal.** Each drawn page carries a signed value: the form, a random part and the second it was
  drawn (``new_seal``). A send needs a seal of this form that is at least ``MIN_SECONDS`` and at most
  ``MAX_SECONDS`` old and that was not used before (``SeenSeal`` keeps a keyed hash of the random
  part until the seal would be too old anyway). So every answer costs a fetched page and a wait, and
  one page gives one answer. It is no proof of a person: a script that fetches and waits passes.
* **Honeypot.** A box people never see. A send that fills it gets the same "gracias" and writes nothing.
* **Never a silent loss.** A seal that is missing, too new or too old does not discard anything: the
  form is drawn again with everything the person typed and a plain sentence. Nobody is told to wait
  because of what others sent: there is no shared counter that refuses.
* **A flood is set aside, not refused.** ``services.submit`` keeps the answers that arrive beyond
  ``FLOW_LIMIT`` per ``FLOW_WINDOW`` (and beyond what a form lists) as "por revisar": saved, with no
  task and no line in the log, for the administration to accept or delete in one step. A person who
  answers during a flood reads "recibimos tu respuesta" and it is true.
* **Twice.** A repeated e-mail or phone discards nothing (``services.submit``); a seal sent twice
  (a double tap) is the answer already kept, and gets the same "gracias".
"""

import hashlib
import hmac
import secrets
from datetime import date, datetime
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_safe

from people import flags

from . import services
from .fields import HONEYPOT, AnswerForm
from .models import Form, SeenSeal

MIN_SECONDS = 3
MAX_SECONDS = 12 * 60 * 60
_signer = signing.Signer(salt="formularios.public.sello")


def new_seal(form: Form, issued=None) -> str:
    issued = int((issued or timezone.now()).timestamp())
    return _signer.sign(f"{form.pk}.{secrets.token_urlsafe(12)}.{issued}")


def read_seal(form: Form, value: str, now=None) -> tuple[str, str, datetime | None]:
    """("ok" | "early" | "bad", the keyed hash of its random part, when it stops being acceptable)."""
    now = now or timezone.now()
    try:
        form_id, nonce, issued = _signer.unsign(value or "").split(".")
        issued = int(issued)
    except (signing.BadSignature, ValueError):
        return "bad", "", None
    age = now.timestamp() - issued
    if form_id != str(form.pk) or age > MAX_SECONDS:
        return "bad", "", None
    key = hmac.new(settings.SECRET_KEY.encode(), f"formularios:sello:{nonce}".encode(), hashlib.sha256).hexdigest()
    expires = datetime.fromtimestamp(issued + MAX_SECONDS, tz=dt_timezone.utc)
    return ("early" if age < MIN_SECONDS else "ok"), key, expires


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


SEAL_WORDS = {
    "bad": "Esta página estuvo abierta mucho tiempo o no terminó de cargar, así que todavía no guardamos nada. "
           "Lo que escribiste sigue aquí abajo: envíalo otra vez.",
    "early": "Eso fue muy rápido y todavía no guardamos nada. Lo que escribiste sigue aquí abajo: revísalo y envíalo otra vez.",
}


@require_http_methods(["GET", "HEAD", "POST"])
def fill(request, token: str):
    form = public_form(token)
    if form is None:
        raise Http404
    version = form.current_version
    answer = AnswerForm(request.POST if request.method == "POST" else None, schema=version.schema)
    changed = full = False
    problem, seal = "", ""
    if request.method == "POST":
        if request.POST.get(HONEYPOT, "").strip():
            return redirect("formulario:thanks", form.public_token)  # not a person: nothing is written
        if request.POST.get("version") != str(version.pk):
            changed = True
            answer = AnswerForm(schema=version.schema)
        elif answer.is_valid():
            sent = request.POST.get("sello", "")
            state, key, expires = read_seal(form, sent)
            if state != "ok":
                problem = SEAL_WORDS[state]
                seal = sent if state == "early" else ""  # the same page, a moment older, is good
            else:
                try:
                    with transaction.atomic():
                        SeenSeal.objects.create(key=key, expires_at=expires)
                        services.submit(form, version.pk, answers=answer.answers, contact=answer.contact,
                                        shown_until=_day(request.POST.get("hasta")))
                except IntegrityError:
                    return redirect("formulario:thanks", form.public_token)  # this page was already sent: it is kept
                except services.FormChanged:
                    return redirect("formulario:fill", form.public_token)  # the form as it is now, or its 404
                except services.FormFull:
                    full = True
                else:
                    return redirect("formulario:thanks", form.public_token)
    context = _context(
        form, version=version, form=answer, items=list(answer.bound_items()), changed=changed, full=full, problem=problem,
        delete_after=services.delete_after(form), honeypot=HONEYPOT, seal=seal or new_seal(form),
    )
    return render(request, "formularios/public.html", context)


def _day(value) -> date | None:
    try:
        return date.fromisoformat(value or "")
    except ValueError:
        return None


@require_safe
def thanks(request, token: str):
    form = public_form(token)
    if form is None:
        raise Http404
    return render(request, "formularios/public_thanks.html", _context(form, version=form.current_version))
