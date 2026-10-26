"""Shared-computer protections."""

from __future__ import annotations

import re
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect, resolve_url

from .services.interviews import KIOSK_KEY

KIOSK_PREFIX = "/estacion/"
ALWAYS_ALLOWED = ("/static/", "/health/")
# The church logo (core.views.church_logo, core/urls.py): public, shown on the campero's screens too, and cached.
# fullmatch, not "$": "$" also matches before a trailing newline.
CHURCH_LOGO_PATH = re.compile(r"/iglesia/[-a-zA-Z0-9_]+/logotipo/(?:claro|oscuro)/")


def is_church_logo(path: str) -> bool:
    return bool(CHURCH_LOGO_PATH.fullmatch(path))


class KioskConfinementMiddleware:
    """A browser in supervised-capture mode can only reach the capture screens."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if is_church_logo(request.path_info):
            return self.get_response(request)  # before reading the session: the image stays cacheable
        in_kiosk = KIOSK_KEY in request.session
        if in_kiosk and not path.startswith(KIOSK_PREFIX) and not path.startswith(ALWAYS_ALLOWED):
            return redirect("kiosk:home")
        return self.get_response(request)


class StaffIdleLogoutMiddleware:
    """Close an idle staff login on a shared computer (default 30 minutes)."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.limit = getattr(settings, "EMMAUS_IDLE_LOGOUT_SECONDS", 1800)

    def __call__(self, request):
        if is_church_logo(request.path_info):
            # An image the page asks for is not activity, and must not log anyone out or carry a session cookie.
            return self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and self.limit:
            now = int(time.time())
            last = request.session.get("last_seen", now)
            if now - last > self.limit:
                logout(request)
                messages.info(request, "Por seguridad cerramos tu sesión después de un tiempo sin actividad. Vuelve a entrar.")
                return redirect(f"{resolve_url(settings.LOGIN_URL)}?next={request.path}")
            background = request.headers.get("X-Requested-With") == "fetch"  # dashboard auto-refresh
            if not background and (now - last > 30 or "last_seen" not in request.session):
                request.session["last_seen"] = now
        return self.get_response(request)


class PrivateResponseMiddleware:
    """No confidential page may be cached by the browser or a proxy (shared computers)."""

    CSP = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith("/static/"):
            # The church logo keeps the cache headers its view set, unless something attached a cookie to it.
            public = getattr(response, "public_asset", False) and not response.cookies
            if not public:
                response["Cache-Control"] = "no-store, max-age=0"
                response["Pragma"] = "no-cache"
            response.setdefault("Content-Security-Policy", self.CSP)
            response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        return response
