"""The request line and the error line of issue #52: what they hold, and above all what they never hold.

This module is also the URLconf of its own tests (override_settings(ROOT_URLCONF=__name__)): the project's
routes plus a few that carry every kind of identifier, fail on purpose, or pretend to be an event screen.
"""

import io
import json
import logging
import re
from datetime import datetime
from datetime import timezone as dt_timezone
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import include, path, reverse
from django.utils.functional import SimpleLazyObject

import emmaus.urls as project_urls
from events.tests.base import CampFixture

from . import request_log

UUID = "3f2b8c1e-9a4d-4e6f-8b7a-0c1d2e3f4a5b"
SLUG = "campamento-secreto"
PK = "987654"
EMAIL = "persona.real@iglesia.example"
SECRET = "clave-super-secreta-de-la-base hunter2 persona.real@iglesia.example"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
REQUEST_KEYS = ["route", "method", "status", "ms", "role"]


class SecretLookingError(Exception):
    pass


def probe(request, uid, slug, pk):
    request.user.is_authenticated  # noqa: B018 - a page reads the user, as the context processors do
    return HttpResponse("ok")


def quiet(request):
    return HttpResponse("ok")


def boom(request, uid, slug, pk):
    raise SecretLookingError(SECRET)


def event_screen(request):
    request.user.is_authenticated  # noqa: B018
    request.access = object()  # what events.views.common.event_view leaves on the request
    return HttpResponse("ok")


def community_screen(request):
    request.user.is_authenticated  # noqa: B018
    request.is_church_admin = True  # what groups.views.community_view leaves on the request
    return HttpResponse("ok")


probe_patterns = (
    [
        path("ver/<uuid:uid>/<slug:slug>/<int:pk>/", probe, name="detail"),
        path("falla/<uuid:uid>/<slug:slug>/<int:pk>/", boom, name="boom"),
        path("callada/", quiet, name="quiet"),
        path("evento/", event_screen, name="event_screen"),
        path("comunidad/", community_screen, name="community_screen"),
    ],
    "sonda",
)
urlpatterns = [path("sonda/", include(probe_patterns)), path("sin-nombre/", quiet), *project_urls.urlpatterns]
handler400 = project_urls.handler400
handler403 = project_urls.handler403
handler404 = project_urls.handler404
handler500 = project_urls.handler500

PROBE_URL = f"/sonda/ver/{UUID}/{SLUG}/{PK}/?correo={EMAIL}&token={UUID}&pk={PK}"
BOOM_URL = f"/sonda/falla/{UUID}/{SLUG}/{PK}/?correo={EMAIL}"


def assert_no_identifiers(test, text):
    test.assertIsNone(UUID_RE.search(text), text)
    test.assertNotIn("@", text)
    test.assertNotIn("?", text)
    test.assertNotIn(PK, text)
    test.assertNotIn(SLUG, text)
    test.assertNotIn("/", text)  # no path at all
    test.assertNotIn("\n", text)


@override_settings(ROOT_URLCONF=__name__, EMMAUS_REQUEST_LOG=True)
class RequestLineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username=EMAIL, email=EMAIL, password="x", first_name="Persona", last_name="Real")
        cls.staff = User.objects.create_user(username="staff@iglesia.example", email="staff@iglesia.example", password="x", is_staff=True)

    def line(self, url, method="get", **kwargs):
        with self.assertLogs("emmaus.request", "INFO") as logs:
            response = getattr(self.client, method)(url, **kwargs)
        self.assertEqual(len(logs.records), 1)
        text = logs.records[0].getMessage()
        return response, text, json.loads(text)

    def test_the_line_has_no_uuid_slug_pk_query_string_or_email(self):
        self.client.force_login(self.user)
        response, text, data = self.line(PROBE_URL, HTTP_USER_AGENT="Navegador/1.0", REMOTE_ADDR="203.0.113.77")
        self.assertEqual(response.status_code, 200)
        assert_no_identifiers(self, text)
        for leaked in ("Navegador", "203.0.113", "Persona", "iglesia"):
            self.assertNotIn(leaked, text)
        self.assertEqual(list(data), REQUEST_KEYS)  # exactly these keys, nothing else
        self.assertEqual(data["route"], "sonda:detail")
        self.assertEqual((data["method"], data["status"], data["role"]), ("GET", 200, "auth"))
        self.assertIsInstance(data["ms"], int)
        self.assertGreaterEqual(data["ms"], 0)

    def test_anonymous_request(self):
        _, text, data = self.line(PROBE_URL)
        assert_no_identifiers(self, text)
        self.assertEqual((data["route"], data["role"]), ("sonda:detail", "anon"))

    def test_a_real_route_with_a_query_string_logs_its_name(self):
        _, text, data = self.line(reverse("login") + f"?next=/eventos/{SLUG}/&correo={EMAIL}")
        assert_no_identifiers(self, text)
        self.assertEqual((data["route"], data["status"], data["role"]), ("login", 200, "anon"))

    def test_roles_come_from_what_the_request_already_holds(self):
        self.client.force_login(self.user)
        self.assertEqual(self.line("/sonda/evento/")[2]["role"], "event")
        self.assertEqual(self.line("/sonda/comunidad/")[2]["role"], "church_admin")
        self.client.force_login(self.staff)
        self.assertEqual(self.line("/sonda/evento/")[2]["role"], "staff")

    def test_the_line_adds_no_query(self):
        self.client.force_login(self.user)
        self.client.get(PROBE_URL)  # the first request of a session also stores its last_seen
        with override_settings(EMMAUS_REQUEST_LOG=False), CaptureQueriesContext(connection) as without:
            self.client.get(PROBE_URL)
        with CaptureQueriesContext(connection) as with_line:
            self.line(PROBE_URL)
        self.assertEqual(len(with_line), len(without))

    def test_not_found_logs_no_path(self):
        response, text, data = self.line(f"/no-existe/{UUID}/{SLUG}/{PK}/?correo={EMAIL}")
        self.assertEqual(response.status_code, 404)
        assert_no_identifiers(self, text)
        self.assertEqual((data["route"], data["status"]), ("-", 404))

    def test_a_route_without_a_name_is_a_dash(self):
        self.assertEqual(self.line("/sin-nombre/")[2]["route"], "-")

    def test_post_and_unknown_methods(self):
        self.assertEqual(self.line("/sonda/callada/", method="post")[2]["method"], "POST")
        with self.assertLogs("emmaus.request", "INFO") as logs:
            self.client.generic("PROPFIND-persona@x", "/sonda/callada/")
        self.assertEqual(json.loads(logs.records[0].getMessage())["method"], "OTHER")

    def test_server_error_logs_the_class_name_only(self):
        self.client.force_login(self.user)
        self.client.raise_request_exception = False
        with (
            self.assertLogs("emmaus.request", "INFO") as requests,
            self.assertLogs("emmaus.error", "ERROR") as errors,
            self.assertLogs("django.request", "ERROR") as django_errors,  # Django still keeps the traceback
        ):
            response = self.client.get(BOOM_URL)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(errors.records), 1)
        record = errors.records[0]
        text = record.getMessage()
        self.assertEqual(json.loads(text), {"route": "sonda:boom", "method": "GET", "exception": "SecretLookingError"})
        self.assertIsNone(record.exc_info)  # no traceback on this logger
        self.assertIsNotNone(django_errors.records[0].exc_info)
        request_text = requests.records[0].getMessage()
        self.assertEqual(json.loads(request_text), {**json.loads(request_text), "route": "sonda:boom", "status": 500, "role": "auth"})
        for line in (text, request_text):
            assert_no_identifiers(self, line)
            for leaked in ("clave", "secreta", "hunter2", "Traceback"):
                self.assertNotIn(leaked, line)

    def test_a_404_and_a_403_are_not_errors(self):
        with self.assertNoLogs("emmaus.error", "ERROR"):
            self.client.get("/no-existe/")
            self.client.get(PROBE_URL)

    def test_static_files_the_idle_ping_and_the_healthcheck_are_skipped(self):
        self.client.force_login(self.user)
        with self.assertNoLogs("emmaus.request", "INFO"):
            self.assertEqual(self.client.get(reverse("events:still_here") + "?comprobar=1").status_code, 204)
            self.assertEqual(self.client.get(reverse("health")).status_code, 200)
            self.client.get("/static/css/rotulo.css")
            self.client.get("/static/no-existe.css")

    def test_the_middleware_swallows_its_own_failure(self):
        for target in ("route_of", "role_of", "dumps"):
            with self.subTest(target), mock.patch.object(request_log, target, side_effect=RuntimeError("roto")):
                with self.assertNoLogs("emmaus.request", "INFO"):
                    response = self.client.get(PROBE_URL)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"ok")
        with mock.patch.object(request_log.request_logger, "info", side_effect=OSError("stdout cerrado")):
            self.assertEqual(self.client.get(PROBE_URL).status_code, 200)

    def test_the_error_line_swallows_its_own_failure(self):
        self.client.raise_request_exception = False
        with mock.patch.object(request_log, "route_of", side_effect=RuntimeError("roto")), self.assertLogs("django.request", "ERROR"):
            self.assertEqual(self.client.get(BOOM_URL).status_code, 500)

    @override_settings(EMMAUS_REQUEST_LOG=False)
    def test_the_switch_turns_both_lines_off(self):
        self.client.raise_request_exception = False
        with self.assertNoLogs("emmaus.request", "INFO"), self.assertNoLogs("emmaus.error", "ERROR"), self.assertLogs("django.request", "ERROR"):
            self.client.get(PROBE_URL)
            self.client.get(BOOM_URL)


class RequestLogSettingsTests(SimpleTestCase):
    def test_off_under_tests_and_first_in_the_chain(self):
        self.assertFalse(settings.EMMAUS_REQUEST_LOG)
        self.assertEqual(settings.MIDDLEWARE[0], "core.request_log.RequestLogMiddleware")

    def test_both_loggers_write_to_the_console_whatever_log_level_says(self):
        for name, level in (("emmaus.request", logging.INFO), ("emmaus.error", logging.ERROR)):
            self.assertEqual(settings.LOGGING["loggers"][name]["handlers"], ["console"])
            self.assertFalse(settings.LOGGING["loggers"][name]["propagate"])
            self.assertEqual(logging.getLogger(name).level, level)

    def test_a_user_nobody_loaded_is_not_loaded_for_the_line(self):
        def explode():
            raise AssertionError("the line must not load the user")

        factory = RequestFactory()
        request = factory.get("/")
        request.user = SimpleLazyObject(explode)
        self.assertEqual(request_log.role_of(request), "anon")  # no session cookie: nobody is signed in
        factory.cookies[settings.SESSION_COOKIE_NAME] = "una-sesion"
        request = factory.get("/")
        request.user = SimpleLazyObject(explode)
        self.assertEqual(request_log.role_of(request), "-")  # the church logo: signed in or not, it was not asked


class SchedulerLineTests(CampFixture):
    """The scheduler's summary tells the cutoffs it stored on this pass from the ones that were already there."""

    def run_pass(self) -> str:
        out = io.StringIO()
        slot = datetime(2026, 7, 14, 20, 0, tzinfo=dt_timezone.utc)
        with mock.patch("events.services.reports.latest_due_slot", return_value=slot):
            call_command("run_report_cutoffs", stdout=out)
        return out.getvalue()

    def test_new_cutoffs_are_counted_once(self):
        first = re.search(r"cortes: (\d+) nuevos, (\d+) ya existían, (\d+) con error", self.run_pass())
        self.assertIsNotNone(first)
        created = int(first.group(1))
        self.assertGreater(created, 0)
        self.assertEqual((first.group(2), first.group(3)), ("0", "0"))
        second = self.run_pass()
        self.assertIn(f"cortes: 0 nuevos, {created} ya existían, 0 con error", second)
        self.assertNotIn("listos", second)

    def test_a_failed_cutoff_is_neither_new_nor_existing(self):
        with mock.patch("events.services.reports.build_payload", side_effect=RuntimeError("roto")), self.assertLogs("events.services.reports", "ERROR"):
            line = self.run_pass()
        failed = re.search(r"cortes: 0 nuevos, 0 ya existían, (\d+) con error", line)
        self.assertIsNotNone(failed)
        self.assertGreater(int(failed.group(1)), 0)
