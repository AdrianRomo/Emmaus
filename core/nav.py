"""Where a POST goes back to.

An action taken on one page (answering a serving date from "Mi iglesia", saying «Enterado» from the list of
notices) returns to that page when the form says so in ``siguiente``; anything that is not a path of this site is
ignored and the view's usual page is used. emmaus.js then reads the updated region from that page (data-inline).
"""

from django.utils.http import url_has_allowed_host_and_scheme


def safe_next(request, candidate, fallback):
    """``candidate`` when it is a URL of this host (a relative path, in practice); ``fallback`` otherwise."""
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return candidate
    return fallback
