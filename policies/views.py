"""Políticas de la iglesia: one form under "Configuración", for the church's administration only.

Behind the ``community`` module (404 while it is off) and inside ``request.church``: there is no id
in the address, so there is no other church to ask for. Three steps at one address:

* ``GET``: the form, with the values in force.
* ``POST``: the form is checked. A change to a sensitive value (anything but the days of the
  opinions) is not saved: it is kept in the session and the review page lists it, from → to.
* ``POST`` with ``paso=confirmar``: saves what the review page showed, and only that. Without a
  review of this church in the session, or with another review's token, nothing is saved.
"""

import secrets

from django.contrib import messages
from django.shortcuts import redirect, render

from groups.views import church_admin_view

from . import services
from .forms import ChurchPolicyForm

SESSION_KEY = "policy_review"
NO_REVIEW = "No se guardó nada: primero revisa los cambios y después confírmalos."


def _saved(request, changed) -> None:
    if len(changed) == 1:
        messages.success(request, f"Guardamos el cambio. {changed[0].label}: {changed[0].new_words.lower()}.")
    else:
        messages.success(request, f"Guardamos los {len(changed)} cambios en las políticas de la iglesia.")


def _pending(request) -> dict | None:
    """The review waiting in the session, when it is a review of this church."""
    kept = request.session.get(SESSION_KEY)
    if not isinstance(kept, dict) or kept.get("church") != request.church.pk or not isinstance(kept.get("values"), dict):
        return None
    return kept


@church_admin_view
def church_policies(request):
    church = request.church
    if request.method == "POST" and request.POST.get("paso") == "confirmar":
        kept = _pending(request)
        request.session.pop(SESSION_KEY, None)
        token = request.POST.get("revision", "")
        if kept is None or not token or not secrets.compare_digest(str(kept.get("token", "")), token):
            messages.error(request, NO_REVIEW)
            return redirect("church_policies")
        form = ChurchPolicyForm(kept["values"])  # the values that were reviewed, checked again
        if not form.is_valid():
            messages.error(request, NO_REVIEW)
            return redirect("church_policies")
        changed = services.save(church, request.user, form.cleaned_data)
        if changed:
            _saved(request, changed)
        else:
            messages.info(request, "No había nada que cambiar.")
        return redirect("church_policies")

    if request.method == "POST":
        form = ChurchPolicyForm(request.POST)
        if form.is_valid():
            wanted = form.cleaned_data
            changed = services.changes(church, wanted)
            if not changed:
                request.session.pop(SESSION_KEY, None)
                messages.info(request, "No cambiaste nada: las políticas siguen como estaban.")
                return redirect("church_policies")
            if not any(change.sensitive for change in changed):
                request.session.pop(SESSION_KEY, None)
                _saved(request, services.save(church, request.user, wanted))
                return redirect("church_policies")
            token = secrets.token_urlsafe(16)
            request.session[SESSION_KEY] = {"church": church.pk, "token": token, "values": wanted}
            return render(request, "policies/review.html", {"changes": changed, "token": token})
    else:
        kept = _pending(request) if "editar" in request.GET else None
        form = ChurchPolicyForm(initial=kept["values"] if kept else services.current(church))
    return render(request, "policies/form.html", {"form": form})
