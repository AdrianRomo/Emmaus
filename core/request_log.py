"""Privacy-safe request metrics and an error line an alert can key on (issue #52).

``RequestLogMiddleware`` writes one JSON line per request to the ``emmaus.request`` logger with exactly five keys::

    {"route": "agenda:detail", "method": "GET", "status": 200, "ms": 41, "role": "auth"}

* ``route`` is the resolved URL **name** with its namespace, or ``"-"`` when nothing resolved (a 404, a redirect made
  by an earlier middleware) or the route has no name. Never the path: paths carry slugs, ids and tokens.
* ``role`` is one fixed word, read only from what the request already holds. It never asks the database:

  - ``anon``: no session cookie, or the user was loaded and is anonymous (a campero at a station is ``anon``);
  - ``staff``: ``is_staff`` or ``is_superuser`` on the loaded account;
  - ``event``: an event screen resolved the caller's event role (``request.access``, ``events.views.common``);
  - ``church_admin``: a community screen found the caller manages the church (``request.is_church_admin``);
  - ``auth``: signed in, and nothing on the request says more. The church role (``pastor``, ``mentor``,
    ``member``) is not kept on the request today, so those words are reserved and not written yet: reading the
    role here would cost a query on every request;
  - ``-``: there is a session cookie but nothing loaded the user (the church logo, a redirect made before the
    session is read), and loading it here would cost the session and user queries the request avoided.

Nothing else is written: no path, query string, id, slug, user id, e-mail, IP, user agent or church name.

Static files, the "¿Sigues aquí?" ping and the container healthcheck are not logged. The middleware never raises:
whatever fails while building or writing the line is swallowed and the response is returned as it came.

An unhandled exception (a 500) also writes one line to ``emmaus.error``, from Django's ``got_request_exception``::

    {"route": "events:report_detail", "method": "GET", "exception": "OperationalError"}

The class name only: no message and no traceback (both can quote data). The traceback stays where Django already
writes it (``django.request``).

``settings.EMMAUS_REQUEST_LOG`` turns both lines off.
"""

from __future__ import annotations

import json
import logging
import sys
import time

from django.conf import settings
from django.core.signals import got_request_exception

request_logger = logging.getLogger("emmaus.request")
error_logger = logging.getLogger("emmaus.error")

# URL names that are noise, not use: the idle ping of emmaus.js and the container healthcheck.
SKIPPED_ROUTES = frozenset({"events:still_here", "health"})
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
UNKNOWN = "-"


def enabled() -> bool:
    return bool(getattr(settings, "EMMAUS_REQUEST_LOG", False))


def route_of(request) -> str:
    match = getattr(request, "resolver_match", None)
    if match is None or not match.url_name:
        return UNKNOWN
    return match.view_name


def method_of(request) -> str:
    # The method is whatever the client typed: only the known words are written.
    method = request.method or ""
    return method if method in METHODS else "OTHER"


def role_of(request) -> str:
    # request._cached_user is where django.contrib.auth keeps the user once something asked for request.user.
    user = getattr(request, "_cached_user", None)
    if user is None:
        return UNKNOWN if settings.SESSION_COOKIE_NAME in request.COOKIES else "anon"
    if not user.is_authenticated:
        return "anon"
    if user.is_staff or user.is_superuser:
        return "staff"
    if getattr(request, "access", None) is not None:
        return "event"
    if getattr(request, "is_church_admin", False) is True:
        return "church_admin"
    return "auth"


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(", ", ": "))


def is_static(request) -> bool:
    static_url = settings.STATIC_URL or ""
    return bool(static_url) and request.path_info.startswith(static_url)


class RequestLogMiddleware:
    """One line per request on ``emmaus.request``; see the module docstring for what it holds and what it never does."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)
        try:
            self.log(request, response, start)
        except Exception:  # noqa: BLE001, S110 - a metrics line must never break or change a response
            pass
        return response

    def log(self, request, response, start: float) -> None:
        if not enabled() or is_static(request):
            return
        route = route_of(request)
        if route in SKIPPED_ROUTES:
            return
        request_logger.info(
            dumps(
                {
                    "route": route,
                    "method": method_of(request),
                    "status": int(response.status_code),
                    "ms": int((time.monotonic() - start) * 1000),
                    "role": role_of(request),
                }
            )
        )


def log_unhandled_exception(sender=None, request=None, **kwargs) -> None:
    """``got_request_exception``: Django sends it for the exceptions that become a 500, not for a 404 or a 403."""
    try:
        if not enabled() or request is None:
            return
        exception = sys.exc_info()[0]
        error_logger.error(
            dumps(
                {
                    "route": route_of(request),
                    "method": method_of(request),
                    "exception": exception.__name__ if exception is not None else UNKNOWN,
                }
            )
        )
    except Exception:  # noqa: BLE001, S110 - never replace the error being handled with one of ours
        pass


got_request_exception.connect(log_unhandled_exception, dispatch_uid="emmaus.error")
