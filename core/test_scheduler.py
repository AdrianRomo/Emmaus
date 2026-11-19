"""The scheduler's pass, its heartbeat and ``scheduler_status`` (issue #80)."""

import json
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core import scheduler
from core.management.commands.run_scheduled_jobs import Command as PassCommand
from core.models import SchedulerHeartbeat
from emmaus.settings import public_base_url
from events.models import Event
from groups.tests.base import CommunityFixture
from notices import email, services
from notices.models import EmailPreference

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
BASE = "https://emmaus.prueba.mx"
CUTOFFS_LINE = "cortes: 0 nuevos, 0 ya existían, 0 con error"


class PublicBaseUrlTests(SimpleTestCase):
    def check(self, raw, debug=False, hosts=("emmaus.prueba.mx",)):
        return public_base_url(raw, debug=debug, allowed_hosts=list(hosts))

    def test_empty_is_not_said(self):
        self.assertEqual(self.check(""), "")
        self.assertEqual(self.check("  "), "")

    def test_the_address_is_kept_without_its_last_slash(self):
        self.assertEqual(self.check("https://emmaus.prueba.mx/"), BASE)
        self.assertEqual(self.check(" https://Emmaus.Prueba.mx "), BASE)
        self.assertEqual(self.check("https://emmaus.prueba.mx:8443"), BASE + ":8443")
        self.assertEqual(self.check("https://a.prueba.mx", hosts=[".prueba.mx"]), "https://a.prueba.mx")
        self.assertEqual(self.check("https://otra.mx", hosts=["*"]), "https://otra.mx")

    def test_an_ipv6_address_keeps_its_brackets(self):
        self.assertEqual(self.check("https://[::1]", hosts=["[::1]"]), "https://[::1]")
        self.assertEqual(self.check("https://[::1]:8443/", hosts=["[::1]"]), "https://[::1]:8443")
        self.assertEqual(self.check("https://[2001:DB8::1]", hosts=["*"]), "https://[2001:db8::1]")
        with self.assertRaises(ImproperlyConfigured):
            self.check("https://[::1]", hosts=["::1"])

    def test_the_message_says_which_rule_failed_and_that_empty_is_valid(self):
        for raw, rule in (
            ("emmaus.prueba.mx", "must start with https://"),
            ("http://emmaus.prueba.mx", "http is only accepted with DEBUG=true"),
            ("https://", "has no host"),
            ("https://u:p@emmaus.prueba.mx", "must not carry a user or a password"),
            ("https://emmaus.prueba.mx/emmaus", "must not have a path"),
            ("https://emmaus.prueba.mx/?a=1", "must not have a query"),
            ("https://otra.mx", "not in ALLOWED_HOSTS"),
            ("https://emmaus.prueba.mx:puerto", "is not an address"),
        ):
            with self.subTest(raw=raw), self.assertRaises(ImproperlyConfigured) as caught:
                self.check(raw)
            message = str(caught.exception)
            self.assertIn(rule, message)
            self.assertIn("or leave it empty", message)
            if raw != "https://":  # the advice itself starts that way
                self.assertNotIn(raw, message)

    def test_http_only_with_debug(self):
        self.assertEqual(self.check("http://localhost:8000", debug=True, hosts=["localhost"]), "http://localhost:8000")
        with self.assertRaises(ImproperlyConfigured):
            self.check("http://emmaus.prueba.mx")

    def test_what_is_refused(self):
        for raw in (
            "emmaus.prueba.mx", "ftp://emmaus.prueba.mx", "https://", "https://emmaus.prueba.mx/emmaus",
            "https://emmaus.prueba.mx/?a=1", "https://emmaus.prueba.mx/#a", "https://u:p@emmaus.prueba.mx",
            "https://otra.mx", "https://malaprueba.mx", "https://emmaus.prueba.mx:puerto",
        ):
            with self.subTest(raw=raw), self.assertRaises(ImproperlyConfigured) as caught:
                self.check(raw, hosts=["emmaus.prueba.mx", ".prueba.mx"] if "mala" in raw else ["emmaus.prueba.mx"])
            self.assertNotIn("u:p", str(caught.exception))  # the message never repeats the value

    @override_settings(EMMAUS_PUBLIC_BASE_URL=BASE)
    def test_a_command_uses_its_option_first_and_the_setting_otherwise(self):
        self.assertEqual(scheduler.base_url_for(""), BASE)
        self.assertEqual(scheduler.base_url_for(" https://otra.mx "), "https://otra.mx")
        with self.settings(EMMAUS_PUBLIC_BASE_URL=""):
            self.assertEqual(scheduler.base_url_for(""), "")


@override_settings(EMAIL_BACKEND=LOCMEM, EMMAUS_PUBLIC_BASE_URL=BASE)
class SchedulerPassTests(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # The fixture leaves an active event with automatic cutoffs: in the half hour after each slot a real cutoff
        # was due and "0 nuevos" failed. No automatic cutoff is due here, whatever the hour.
        Event.objects.update(reports_enabled=False)

    def run_pass(self) -> str:
        """What one pass prints. ``self.code``: 0, or the code the command ended with."""
        out, self.code = StringIO(), 0
        try:
            call_command("run_scheduled_jobs", stdout=out)
        except CommandError as error:
            self.code = error.returncode
        return out.getvalue()

    def heartbeat(self, job) -> SchedulerHeartbeat:
        return SchedulerHeartbeat.objects.get(job=job)

    def test_one_line_with_the_cutoffs_and_the_community_counts(self):
        with self.assertLogs("emmaus.scheduler", "INFO") as logs:
            printed = self.run_pass()
        # The pass's own line comes first; the daily retention line of the cutoffs pass, when it is due, after it.
        self.assertNotIn("correos de avisos", "".join(printed.splitlines()[1:]))
        self.assertRegex(
            printed.splitlines()[0],
            r"^\d{4}-\d\d-\d\d \d\d:\d\d · cortes: 0 nuevos, 0 ya existían, 0 con error · entrevistas liberadas: 0 · "
            r"capturas vencidas: 0 · contadores borrados: 0 · solicitudes antiguas borradas: 0 · "
            r"correos de avisos: 0 enviados, 0 fallidos, 0 pendientes · lista de espera: 0 lugares liberados, 0 ofrecidos · "
            r"series: 0 fechas nuevas · uniones de personas: 0 con datos anteriores borrados · "
            r"formularios: 0 respuestas vencidas borradas$",
        )
        lines = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual([line["job"] for line in lines], list(scheduler.JOBS))
        self.assertTrue(all(line["ok"] is True and isinstance(line["ms"], int) for line in lines))
        self.assertEqual(
            {key: lines[1][key] for key in ("notices", "sent", "failed", "left")}, {"notices": 0, "sent": 0, "failed": 0, "left": 0}
        )
        self.assertEqual(self.code, 0)
        self.assertEqual({key: lines[2][key] for key in ("released", "offered")}, {"released": 0, "offered": 0})
        for job in scheduler.JOBS:
            beat = self.heartbeat(job)
            self.assertIsNotNone(beat.last_ok_at)
            self.assertGreaterEqual(beat.last_ok_at, beat.last_started_at)
            self.assertEqual(beat.last_error, "")

    def test_the_pass_mails_a_notice_a_publish_left_and_logs_numbers_only(self):
        email.set_preference(self.member, EmailPreference.Choice.ALL)
        services.publish(self.church, self.admin, title="Retiro", body="La llave está bajo la maceta")  # no base_url: left for the pass
        with self.assertLogs("emmaus.scheduler", "INFO") as logs:
            printed = self.run_pass()
        self.assertIn("correos de avisos: 1 enviados, 0 fallidos, 0 pendientes", printed)
        self.assertEqual([message.to for message in mail.outbox], [[self.member.email]])
        for private in ("Retiro", "maceta", "@", "Diego", self.church.name):
            self.assertNotIn(private, printed + "".join(logs.output))
        self.assertIn("correos de avisos: 0 enviados", self.run_pass())
        self.assertEqual(len(mail.outbox), 1)

    def test_an_idle_pass_costs_a_small_constant_number_of_queries(self):
        command = PassCommand(stdout=StringIO())
        self.run_pass()  # the heartbeat rows exist from the first pass on

        def community_queries() -> int:
            with CaptureQueriesContext(connection) as queries:
                command.notice_mail_summary()
                command.waiting_offers_summary()
            return len(queries)

        # Each job: its heartbeat twice (started, ended) and one question to learn there is nothing to do;
        # the notice e-mails also read whether their last pass got nothing through.
        self.assertEqual(community_queries(), 7)
        # Notices whose round ended, in two churches, cost nothing more.
        for church, author in ((self.church, self.admin), (self.church_b, self.admin_b)):
            for number in range(5):
                services.publish(church, author, title=f"Aviso {number}", body="Texto", priority="urgent")
        self.run_pass()
        self.assertEqual(community_queries(), 7)

    def test_a_job_that_fails_stops_nothing_else_and_leaves_only_its_class_name(self):
        with mock.patch("notices.email.send_due", side_effect=RuntimeError("secreto@prueba.mx")), \
                self.assertLogs("emmaus.scheduler") as logs, self.assertLogs("core.scheduler", "ERROR") as plain:
            printed = self.run_pass()
        self.assertEqual(self.code, 1)  # run once, for cron: a failed job is an error, after all of them ran
        # The ordinary log says where it failed and what kind of error, never the error's message.
        logged = "\n".join(record.getMessage() for record in plain.records) + "".join(plain.output)
        self.assertIn("RuntimeError", logged)
        self.assertIn("run_scheduled_jobs.py", logged)
        self.assertNotIn("secreto", logged)
        self.assertTrue(all(record.exc_info is None for record in plain.records))
        self.assertIn(CUTOFFS_LINE, printed)
        self.assertIn("correos de avisos: error · lista de espera: 0 lugares liberados, 0 ofrecidos", printed)
        beat = self.heartbeat(scheduler.NOTICE_MAIL)
        self.assertEqual((beat.last_error, beat.last_ok_at), ("RuntimeError", None))
        self.assertIsNotNone(beat.last_started_at)
        self.assertIsNotNone(self.heartbeat(scheduler.WAITING_OFFERS).last_ok_at)
        failed = json.loads(logs.records[1].getMessage())
        self.assertEqual((failed["job"], failed["ok"], failed["exception"]), (scheduler.NOTICE_MAIL, False, "RuntimeError"))
        self.assertNotIn("secreto", printed + "".join(record.getMessage() for record in logs.records))
        # The next good pass clears the error.
        self.run_pass()
        self.assertEqual(self.code, 0)
        self.assertEqual(self.heartbeat(scheduler.NOTICE_MAIL).last_error, "")

    def test_a_heartbeat_that_cannot_be_written_stops_no_job(self):
        """The table is missing (an image started before migrate, or after ``migrate core 0011``): everything still runs."""
        email.set_preference(self.member, EmailPreference.Choice.ALL)
        services.publish(self.church, self.admin, title="Retiro", body="Texto")
        with mock.patch("core.scheduler._mark", side_effect=OperationalError("no such table: core_schedulerheartbeat")), \
                self.assertLogs("core.scheduler", "ERROR") as plain, self.assertLogs("emmaus.scheduler", "INFO") as lines:
            printed = self.run_pass()
        self.assertEqual(self.code, 0)
        self.assertIn(CUTOFFS_LINE, printed)
        self.assertIn("correos de avisos: 1 enviados, 0 fallidos, 0 pendientes · lista de espera: 0 lugares liberados", printed)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual([json.loads(record.getMessage())["ok"] for record in lines.records], [True, True, True, True, True, True])
        self.assertEqual(len(plain.records), 12)  # each write, by its class name only
        self.assertTrue(all("OperationalError" in line and "no such table" not in line for line in plain.output))
        self.assertFalse(SchedulerHeartbeat.objects.exists())

    def test_a_heartbeat_that_cannot_be_read_is_a_job_that_is_not_known_to_run(self):
        self.run_pass()
        self.assertEqual(scheduler.notice_mail_problem(), "")
        with mock.patch("core.scheduler.SchedulerHeartbeat.objects.filter", side_effect=OperationalError("x")):
            self.assertEqual(scheduler.notice_mail_problem(), scheduler.NEVER)
            self.assertEqual(scheduler.last_error(scheduler.NOTICE_MAIL), "")

    def test_the_cutoffs_failing_do_not_stop_the_community_jobs_nor_the_other_way(self):
        target = "events.management.commands.run_report_cutoffs.Command.run_once"
        with mock.patch(target, side_effect=RuntimeError("x")), self.assertLogs("core.scheduler", "ERROR") as plain, \
                self.assertLogs("emmaus.scheduler"):
            printed = self.run_pass()
        self.assertEqual(self.code, 1)  # as run_report_cutoffs, run once, always did
        self.assertIsNotNone(plain.records[0].exc_info)  # the cutoffs keep the traceback they always logged
        self.assertIn("cortes: error · correos de avisos: 0 enviados", printed)
        self.assertEqual(self.heartbeat(scheduler.CUTOFFS).last_error, "RuntimeError")
        self.assertIsNotNone(self.heartbeat(scheduler.NOTICE_MAIL).last_ok_at)
        with mock.patch("agenda.services.expire_offers", side_effect=RuntimeError("x")), self.assertLogs("core.scheduler", "ERROR"), \
                self.assertLogs("emmaus.scheduler"):
            printed = self.run_pass()
        self.assertEqual(self.code, 1)
        self.assertIn(CUTOFFS_LINE, printed)
        self.assertIn("lista de espera: error", printed)
        self.assertEqual(self.heartbeat(scheduler.CUTOFFS).last_error, "")

    def test_the_switch_leaves_the_cutoffs_pass_as_it_was(self):
        for off in ({"EMMAUS_SCHEDULED_COMMUNITY_JOBS": False}, {"EMMAUS_V2_MODULES": False}):
            SchedulerHeartbeat.objects.all().delete()
            frozen = mock.patch("django.utils.timezone.now", return_value=timezone.now())  # both lines carry the minute
            with self.subTest(off=off), self.settings(**off), frozen:
                before = StringIO()
                call_command("run_report_cutoffs", stdout=before)
                with mock.patch("notices.email.send_due") as send, mock.patch("agenda.services.expire_offers") as expire:
                    printed = self.run_pass()
                self.assertEqual(printed, before.getvalue())
                send.assert_not_called()
                expire.assert_not_called()
                self.assertEqual(list(SchedulerHeartbeat.objects.values_list("job", flat=True)), [scheduler.CUTOFFS])
                out = StringIO()
                call_command("scheduler_status", stdout=out)  # a job switched off is not a missed run
                self.assertIn("avisos_correo: apagado", out.getvalue())
                self.assertIn("lista_espera: apagado", out.getvalue())

    def test_the_manual_command_uses_the_setting_when_no_address_is_typed(self):
        email.set_preference(self.member, EmailPreference.Choice.ALL)
        notice = services.publish(self.church, self.admin, title="Retiro", body="Texto")
        out = StringIO()
        call_command("send_notice_emails", "--apply", stdout=out)
        self.assertIn("correos enviados: 1", out.getvalue())
        self.assertIn(f"{BASE}/mi-iglesia/avisos/{notice.pk}/", mail.outbox[0].body)
        with self.settings(EMMAUS_PUBLIC_BASE_URL=""):
            for command in ("send_notice_emails", "remind_follow_up_owners"):
                with self.assertRaises(CommandError):
                    call_command(command, "--apply", stdout=StringIO())


@override_settings(EMAIL_BACKEND=LOCMEM, EMMAUS_PUBLIC_BASE_URL=BASE)
class SchedulerStatusTests(CommunityFixture):
    def status(self, *args):
        out = StringIO()
        try:
            call_command("scheduler_status", *args, stdout=out)
        except CommandError as error:
            return error.returncode, out.getvalue(), str(error)
        return 0, out.getvalue(), ""

    def later(self, minutes):
        return mock.patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(minutes=minutes))

    def test_a_server_where_nothing_ran_yet_is_not_running(self):
        code, printed, error = self.status()
        self.assertEqual(code, 1)
        self.assertEqual(printed.splitlines(), [f"{job}: SIN CORRER · nunca ha terminado bien" for job in scheduler.JOBS])
        self.assertIn("6 de 6 trabajos programados", error)

    def test_it_is_fine_after_a_pass_and_late_after_the_threshold(self):
        call_command("run_scheduled_jobs", stdout=StringIO())
        with self.assertNumQueries(1):  # read-only: one read, nothing written
            code, printed, _ = self.status()
        self.assertEqual(code, 0)
        self.assertEqual(printed.splitlines(), [f"{job}: bien · terminó hace 0 min" for job in scheduler.JOBS])
        with self.later(14):
            self.assertEqual(self.status()[0], 0)
        with self.later(16):
            code, printed, error = self.status()
            self.assertEqual(code, 1)
            self.assertIn("cortes: SIN CORRER · terminó bien por última vez hace 16 min", printed)
            self.assertIn("últimos 15 minutos", error)
            self.assertEqual(self.status("--max-age", "30")[0], 0)
        self.assertEqual(self.status("--max-age", "0")[0], 2)

    def test_one_job_late_is_named_with_its_error_class_and_fails_the_command(self):
        call_command("run_scheduled_jobs", stdout=StringIO())
        with self.later(20):
            with mock.patch("notices.email.send_due", side_effect=ValueError("secreto")), self.assertLogs("core.scheduler", "ERROR"), \
                    self.assertLogs("emmaus.scheduler"), self.assertRaises(CommandError):
                call_command("run_scheduled_jobs", stdout=StringIO())
            code, printed, error = self.status()
        self.assertEqual(code, 1)
        self.assertIn("cortes: bien", printed)
        self.assertIn("avisos_correo: SIN CORRER · terminó bien por última vez hace 20 min · último error: ValueError", printed)
        self.assertIn("lista_espera: bien", printed)
        self.assertIn("series_eventos: bien", printed)
        self.assertIn("uniones_personas: bien", printed)
        self.assertIn("1 de 6", error)
        self.assertNotIn("secreto", printed + error)

    def test_notice_mail_that_is_not_set_up_is_said_and_is_not_a_missed_run(self):
        with self.settings(EMMAUS_PUBLIC_BASE_URL=""):
            call_command("run_scheduled_jobs", stdout=StringIO())
            code, printed, _ = self.status()
            self.assertEqual(code, 0)
            self.assertIn("avisos_correo: no corre: falta EMMAUS_PUBLIC_BASE_URL", printed)
        with self.settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"):
            code, printed, _ = self.status()
            self.assertEqual(code, 0)
            self.assertIn("avisos_correo: no corre: no hay correo configurado", printed)
        # Set up now, and it has never run: that is a missed run.
        code, printed, _ = self.status()
        self.assertEqual(code, 1)
        self.assertIn("avisos_correo: SIN CORRER · nunca ha terminado bien", printed)
