"""The staff session: "Seguir aquí" before the idle logout (audit A11Y-04)."""

from __future__ import annotations

import time

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.views.decorators.http import require_GET


def idle_seconds_left(request) -> int:
    """Seconds before events.middleware.StaffIdleLogoutMiddleware closes this session, from its ``last_seen``.

    The middleware refreshes ``last_seen`` at most every 30 seconds, so a page can be rendered up to 30 seconds after
    the session's real last touch: the page's countdown has to start from this, not from its own load.
    """
    limit = int(getattr(settings, "EMMAUS_IDLE_LOGOUT_SECONDS", 0) or 0)
    last = request.session.get("last_seen")
    if last is None:
        return limit
    return max(0, limit - (int(time.time()) - int(last)))


@require_GET
@login_required
def still_here(request):
    """Touch the session ("Seguir aquí", and typing in a form at most every 2 minutes), or with ?comprobar=1 only say
    whether it is still open, without touching it.

    A session idle for too long never gets here: the middleware has already logged it out and this request redirects to
    the login page. emmaus.js sends the check as a background request (X-Requested-With: fetch), which the middleware
    does not count as activity either. 204 either way; X-Idle-Remaining says how many seconds are left.
    """
    if not request.GET.get("comprobar"):
        request.session["last_seen"] = int(time.time())
    response = HttpResponse(status=204)
    response["X-Idle-Remaining"] = str(idle_seconds_left(request))
    return response
