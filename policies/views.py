"""Políticas de la iglesia: one form under "Configuración", for the church's administration only.

Behind the ``community`` module (404 while it is off) and inside ``request.church``: there is no id
in the address, so there is no other church to ask for. Three steps at one address:

* ``GET``: the form, with the values in force.
* ``POST``: the form is checked. A change to a sensitive value (anything but the days of the
  opinions) is not saved: it is kept in the session and the review page lists it, from → to.
* ``POST`` with ``paso=confirmar``: saves what the review page showed, and only that. Without a
  review of this church in the session, or with another review's token, nothing is saved.

The session keeps only the fields that were reviewed, each with the value that was shown as
"Antes". If any of them is no longer that value when the confirmation arrives (somebody else
changed it), nothing is saved and the review is shown again with today's values. A field that was
not in the review is never written by the confirmation.
"""

import secrets

from django.contrib import messages
from django.shortcuts import redirect, render

from groups.views import church_admin_view

from . import services
from .forms import ChurchPolicyForm

SESSION_KEY = "policy_review"
NO_REVIEW = "No se guardó nada: primero revisa los cambios y después confírmalos."
STALE = "Alguien cambió estas políticas mientras revisabas. Revisa otra vez."


def _saved(request, changed) -> None:
    if len(changed) == 1:
        messages.success(request, f"Guardamos el cambio. {changed[0].label}: {changed[0].new_words.lower()}.")
    else:
        messages.success(request, f"Guardamos los {len(changed)} cambios en las políticas de la iglesia.")


def _pending(request) -> dict | None:
    """The reviewed fields waiting in the session, ``{field: (old, new)}`` under ``fields``, when
    it is a review of this church and has the shape ``_review`` wrote."""
    kept = request.session.get(SESSION_KEY)
    if not isinstance(kept, dict) or kept.get("church") != request.church.pk:
        return None
    fields = kept.get("fields")
    if not isinstance(fields, dict) or not fields or set(fields) - set(services.FIELDS):
        return None
    if not all(isinstance(pair, (list, tuple)) and len(pair) == 2 for pair in fields.values()):
        return None
    return {"token": str(kept.get("token", "")), "fields": {field: tuple(pair) for field, pair in fields.items()}}


def _review(request, changed):
    """Keep what is about to be shown, and show it."""
    token = secrets.token_urlsafe(16)
    request.session[SESSION_KEY] = {
        "church": request.church.pk, "token": token,
        "fields": {change.field: [change.old, change.new] for change in changed},
    }
    return render(request, "policies/review.html", {"changes": changed, "token": token})


def _review_again(request, asked: dict):
    """Somebody changed a policy meanwhile: nothing was saved. The same request, against the
    values in force now; back to the form when it no longer makes sense."""
    church = request.church
    messages.error(request, STALE)
    wanted = {**services.current(church), **asked}
    try:
        services.check(wanted)
    except services.PolicyError:
        return redirect("church_policies")
    changed = [change for change in services.changes(church, wanted) if change.field in asked]
    if not changed:
        return redirect("church_policies")
    return _review(request, changed)


@church_admin_view
def church_policies(request):
    church = request.church
    if request.method == "POST" and request.POST.get("paso") == "confirmar":
        kept = _pending(request)
        request.session.pop(SESSION_KEY, None)
        token = request.POST.get("revision", "")
        if kept is None or not token or not secrets.compare_digest(kept["token"], token):
            messages.error(request, NO_REVIEW)
            return redirect("church_policies")
        try:
            changed = services.save(church, request.user, kept["fields"])
        except (services.StaleReview, services.PolicyError):
            return _review_again(request, {field: new for field, (_old, new) in kept["fields"].items()})
        _saved(request, changed)
        return redirect("church_policies")

    if request.method == "POST":
        form = ChurchPolicyForm(request.POST)
        if form.is_valid():
            changed = services.changes(church, form.cleaned_data)
            request.session.pop(SESSION_KEY, None)
            if not changed:
                messages.info(request, "No cambiaste nada: las políticas siguen como estaban.")
                return redirect("church_policies")
            if any(change.sensitive for change in changed):
                return _review(request, changed)
            try:
                _saved(request, services.save(church, request.user, {c.field: (c.old, c.new) for c in changed}))
            except (services.StaleReview, services.PolicyError):
                messages.error(request, STALE)
            return redirect("church_policies")
    else:
        initial = services.current(church)
        kept = _pending(request) if "editar" in request.GET else None
        if kept:
            initial.update({field: new for field, (_old, new) in kept["fields"].items()})
        form = ChurchPolicyForm(initial=initial)
    from oracion.services import is_on as prayer_requests_on  # read-only here: only a pastor switches it (oracion.views.terms)

    return render(request, "policies/form.html", {"form": form, "prayer_requests_on": prayer_requests_on(church)})
