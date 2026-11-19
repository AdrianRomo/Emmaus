from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


DEBUG = env_bool("DEBUG", False)  # production-safe default; development sets DEBUG=true explicitly
TESTING = "test" in sys.argv or "pytest" in sys.modules
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "unsafe-dev-secret-key-change-me"
    else:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG=false.")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")


def public_base_url(raw: str, *, debug: bool, allowed_hosts: list[str]) -> str:
    """The address people reach this Emmaus at (``https://emmaus.ejemplo.org``), for the links of the e-mails a
    command sends: a command has no request to read it from. Empty means "not said", and is valid. Refused at
    startup, because a wrong value would put a wrong link in every e-mail: anything but https (http only with
    DEBUG), a path, a query, a user, or a host that ``ALLOWED_HOSTS`` would not answer. The message says which rule
    failed and never repeats the value."""
    value = (raw or "").strip()
    if not value:
        return ""

    def refuse(rule: str) -> ImproperlyConfigured:
        return ImproperlyConfigured(
            f"EMMAUS_PUBLIC_BASE_URL {rule} Write only the scheme and the host (https://emmaus.example.org), "
            "or leave it empty: Emmaus starts without it, and the scheduler then sends no e-mail about scheduled notices."
        )

    try:
        parts = urlparse(value)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise refuse("is not an address (its host or its port cannot be read).") from None
    if parts.scheme not in ("http", "https"):
        raise refuse("must start with https://.")
    if parts.scheme == "http" and not debug:
        raise refuse("must be https: http is only accepted with DEBUG=true.")
    if not host:
        raise refuse("has no host.")
    if parts.username or parts.password:
        raise refuse("must not carry a user or a password.")
    if parts.path not in ("", "/"):
        raise refuse("must not have a path after the host.")
    if parts.params or parts.query or parts.fragment:
        raise refuse("must not have a query (?…) or a fragment (#…).")
    # urlparse hands an IPv6 literal over without its brackets; ALLOWED_HOSTS and a link both write them.
    host = f"[{host}]" if ":" in host else host
    if "*" not in allowed_hosts and not any(
        host == pattern.lower() or (pattern.startswith(".") and (host.endswith(pattern.lower()) or host == pattern[1:].lower()))
        for pattern in allowed_hosts
    ):
        raise refuse("names a host that is not in ALLOWED_HOSTS.")
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


EMMAUS_PUBLIC_BASE_URL = public_base_url(os.environ.get("EMMAUS_PUBLIC_BASE_URL", ""), debug=DEBUG, allowed_hosts=ALLOWED_HOSTS)
# The scheduler's community jobs (core/management/commands/run_scheduled_jobs.py): the e-mails of notices whose day
# came and the waiting-list offers that expired. False leaves the cutoffs pass exactly as it was.
EMMAUS_SCHEDULED_COMMUNITY_JOBS = env_bool("EMMAUS_SCHEDULED_COMMUNITY_JOBS", True)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "events",
    "people",
    "access",
    "groups",
    "notices",
    "agenda",
    "resources",
    "serving",
    "care",
    "feedback",
    "overview",
    "policies",
    "sitio",
    "espacios",
]

MIDDLEWARE = [
    # Outermost: it times the whole request and sees the final status. One line per request, no path (issue #52).
    "core.request_log.RequestLogMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "events.middleware.PrivateResponseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "events.middleware.KioskConfinementMiddleware",
    "events.middleware.StaffIdleLogoutMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "emmaus.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.current_role",
                "events.context_processors.event_context",
            ],
        },
    },
]

WSGI_APPLICATION = "emmaus.wsgi.application"


def database_config() -> dict:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }

    parsed = urlparse(url)
    if parsed.scheme.startswith("postgres"):
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
            "HOST": parsed.hostname or "",
            "PORT": parsed.port or "",
            # Small deployments often sit behind free-tier Postgres with ~20 connections:
            # reuse connections for a minute instead of opening one per request.
            "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", "60")),
            "CONN_HEALTH_CHECKS": True,
            "TEST": {"NAME": os.environ.get("TEST_DATABASE_NAME") or None},
        }
    if parsed.scheme == "sqlite":
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": parsed.path,
        }
    raise ImproperlyConfigured(f"Unsupported DATABASE_URL scheme: {parsed.scheme}")


DATABASES = {"default": database_config()}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-mx"
LANGUAGES = [("es-mx", "Español (México)")]
# Tú-voice overrides of Django's own messages (its Spanish catalogs use "usted"): locale/es_MX/LC_MESSAGES/django.po.
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = os.environ.get("TIME_ZONE", "America/Mexico_City")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []
# Uploads (the church logo, core.logos). There is no MEDIA_URL and nothing serves this directory as static:
# core.views.church_logo answers /iglesia/<slug>/logotipo/<variant>/. A volume in production (docs/deployment.md).
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if not DEBUG and "test" not in sys.argv
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        )
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# Shared interview computers: sessions end with the browser, after 12 hours at most,
# and after EMMAUS_IDLE_LOGOUT_SECONDS without activity (see events.middleware).
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 12 * 60 * 60
SESSION_COOKIE_SAMESITE = "Lax"
EMMAUS_IDLE_LOGOUT_SECONDS = int(os.environ.get("EMMAUS_IDLE_LOGOUT_SECONDS", "1800"))
# How long group attendance is kept (groups/management/commands/prune_attendance.py). Product defaults, to be
# checked against each church's privacy notice; they are not legal deadlines. Who came to a meeting is kept for the
# first period; after it only the meeting's totals remain, and after the second the meeting is deleted too.
EMMAUS_ATTENDANCE_IDENTIFIABLE_MONTHS = 12
EMMAUS_ATTENDANCE_AGGREGATE_MONTHS = 24
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "0"))

if "test" in sys.argv or "pytest" in sys.modules:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    # Request bodies (questionnaire answers) are never logged: no handler here prints POST data.
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        # core.request_log: one JSON line per request (URL name, method, status, ms, role) and one per unhandled
        # exception (URL name, method, class name). Their own levels, so LOG_LEVEL=WARNING does not silence the metrics.
        "emmaus.request": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "emmaus.error": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        # core.scheduler: one JSON line per scheduled job and pass (job, ok, ms, counts or an exception class name).
        "emmaus.scheduler": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
# Off switch for both lines. On unless a test run, which turns it on where it tests it.
EMMAUS_REQUEST_LOG = env_bool("EMMAUS_REQUEST_LOG", not TESTING)

EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587") or 587)
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
# Seconds a connection to the mail server may block. Without it one hung server holds a web request (a publish
# that mails a notice, an offer from a waiting list) until the worker is killed.
EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "10") or 10)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Emmaus <no-reply@example.org>")

# Kill switch for the per-church module flags (decision 0004): false reads every flag as off, whatever the rows say.
EMMAUS_V2_MODULES = env_bool("EMMAUS_V2_MODULES", True)

LLM_ENABLED = env_bool("LLM_ENABLED", False)
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", not DEBUG)
CSRF_FAILURE_VIEW = "core.views.csrf_failure"
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SECURE_REDIRECT_EXEMPT = [r"^health/$"]  # the container healthcheck speaks plain HTTP
