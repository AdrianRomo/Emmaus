"""Scheduled notices are e-mailed on their day by the scheduler's pass (issue #80).

The owner's acceptance test, as written on issue #80: "schedule a synthetic notice for tomorrow,
verify no early delivery, run the due job twice, and confirm one delivery to an eligible recipient
and zero to an opted-out recipient." ``test_a_notice_for_tomorrow_is_mailed_once_on_its_day_to_who_chose_it``
is that sentence; the rest are its companions (an urgent notice, a mail server that fails once) and
what the people who publish read while nothing is sending those e-mails.
"""

from datetime import datetime, time, timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import SchedulerHeartbeat
from core.scheduler import NOTICE_MAIL
from groups.tests.base import CommunityFixture
from notices import email
from notices.models import Announcement, EmailPreference, NoticeEmail
from people.models import Person

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
CONSOLE = "django.core.mail.backends.console.EmailBackend"
FAILING = "core.test_notify.FailingBackend"
BASE = "https://emmaus.prueba.mx"
CREATE = reverse("notices:create")
Choice = EmailPreference.Choice
NOT_SENDING = "Ahora mismo Emmaus no está enviando los correos de los avisos programados."
PROMISE = "El día que se publique, Emmaus le enviará el correo a quien lo eligió."


@override_settings(EMAIL_BACKEND=LOCMEM, EMMAUS_PUBLIC_BASE_URL=BASE)
class ScheduledDeliveryTests(CommunityFixture):
    """Church A, three people with an account besides the administration, who writes the notice:

    * ``eligible`` chose "Todos";
    * ``opted_out`` chose "Ninguno";
    * ``urgent_only`` chose nothing, which is "Solo los urgentes".
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.eligible, cls.opted_out, cls.urgent_only = cls.member, cls.leader, cls.coord_person
        email.set_preference(cls.eligible, Choice.ALL)
        email.set_preference(cls.opted_out, Choice.NONE)
        # Nobody else of the church has an account, so every count below is about these three.
        three = [cls.admin_person.pk, cls.eligible.pk, cls.opted_out.pk, cls.urgent_only.pk]
        Person.objects.filter(church=cls.church).exclude(pk__in=three).update(user=None)
        cls.today = timezone.localdate()
        cls.tomorrow = cls.today + timedelta(days=1)

    def at(self, day, hour=12, minute=0):
        """Freeze the clock at that local time of ``day``, the way the suite does it everywhere."""
        moment = timezone.make_aware(datetime.combine(day, time(hour, minute)))
        return mock.patch("django.utils.timezone.now", return_value=moment)

    def schedule_for_tomorrow(self, priority="normal") -> Announcement:
        """The administration saves a synthetic notice for the whole church, to be published tomorrow."""
        with self.at(self.today, 9):
            self.client.force_login(self.admin)
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(CREATE, {
                    "para": "iglesia", "title": "Aviso sintético", "body": "Texto sintético de prueba.",
                    "priority": priority, "publish_on": self.tomorrow.isoformat(),
                })
        notice = Announcement.objects.get()
        self.assertRedirects(response, reverse("notices:detail", args=[notice.pk]), fetch_redirect_response=False)
        self.assertEqual(notice.state, "programado")
        return notice

    def run_pass(self, day, hour=12, minute=0) -> str:
        """One pass of the scheduler (what the ``scheduler`` service runs every 300 seconds)."""
        out = StringIO()
        with self.at(day, hour, minute):
            call_command("run_scheduled_jobs", stdout=out)
        return out.getvalue()

    def mailed(self) -> list[str]:
        return sorted(address for message in mail.outbox for address in message.to)

    # ---- the acceptance test of issue #80 -----------------------------------------------------------
    def test_a_notice_for_tomorrow_is_mailed_once_on_its_day_to_who_chose_it(self):
        """Issue #80: schedule a synthetic notice for tomorrow, verify no early delivery, run the due
        job twice, and confirm one delivery to an eligible recipient and zero to an opted-out one."""
        notice = self.schedule_for_tomorrow()

        # Today the job runs twice: nothing goes out early.
        for minute in (0, 5):
            self.assertIn("correos de avisos: 0 enviados, 0 fallidos, 0 pendientes", self.run_pass(self.today, 12, minute))
        self.assertEqual(mail.outbox, [])
        self.assertFalse(NoticeEmail.objects.exists())

        # Tomorrow the job runs twice: one e-mail, to who chose "Todos".
        first = self.run_pass(self.tomorrow, 0, 5)
        second = self.run_pass(self.tomorrow, 0, 10)
        self.assertIn("correos de avisos: 1 enviados, 0 fallidos, 0 pendientes", first)
        self.assertIn("correos de avisos: 0 enviados, 0 fallidos, 0 pendientes", second)
        self.assertEqual(self.mailed(), [self.eligible.email])
        self.assertNotIn(self.opted_out.email, self.mailed())  # "Ninguno"
        self.assertNotIn(self.urgent_only.email, self.mailed())  # "Solo los urgentes", and the notice is normal
        delivery = NoticeEmail.objects.get()  # exactly one delivery row
        self.assertEqual((delivery.announcement_id, delivery.person_id, delivery.outcome), (notice.pk, self.eligible.pk, "sent"))
        self.assertIn(f"{BASE}/mi-iglesia/avisos/{notice.pk}/", mail.outbox[0].body)
        for private in ("Aviso sintético", "Texto sintético", "@", "Diego"):
            self.assertNotIn(private, first)  # the pass prints counts only

        # The day after: nothing more.
        self.assertIn("correos de avisos: 0 enviados", self.run_pass(self.tomorrow + timedelta(days=1)))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(NoticeEmail.objects.count(), 1)

    # ---- its companions -----------------------------------------------------------------------------
    def test_an_urgent_notice_for_tomorrow_also_reaches_who_only_wants_the_urgent_ones(self):
        self.schedule_for_tomorrow(priority="urgent")
        for minute in (0, 5):
            self.run_pass(self.today, 12, minute)
        self.assertEqual(mail.outbox, [])
        first = self.run_pass(self.tomorrow, 0, 5)
        self.run_pass(self.tomorrow, 0, 10)
        self.assertIn("correos de avisos: 2 enviados, 0 fallidos, 0 pendientes", first)
        self.assertEqual(self.mailed(), sorted([self.eligible.email, self.urgent_only.email]))
        self.assertEqual(NoticeEmail.objects.count(), 2)
        self.run_pass(self.tomorrow + timedelta(days=1))
        self.assertEqual(len(mail.outbox), 2)

    def test_a_mail_server_that_fails_on_the_first_pass_still_ends_in_exactly_one_mail(self):
        notice = self.schedule_for_tomorrow()
        with self.settings(EMAIL_BACKEND=FAILING), self.assertLogs("core.services", "ERROR"):
            failed = self.run_pass(self.tomorrow, 0, 5)
        self.assertIn("correos de avisos: 0 enviados, 1 fallidos, 1 pendientes", failed)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(NoticeEmail.objects.get().outcome, "failed")
        # The job ran, and says so: the mail that failed is in its counts, not in its heartbeat.
        self.assertEqual(SchedulerHeartbeat.objects.get(job=NOTICE_MAIL).last_error, "")

        self.assertIn("correos de avisos: 1 enviados, 0 fallidos, 0 pendientes", self.run_pass(self.tomorrow, 0, 10))
        self.run_pass(self.tomorrow, 0, 15)
        self.assertEqual(self.mailed(), [self.eligible.email])
        delivery = NoticeEmail.objects.get()
        self.assertEqual((delivery.announcement_id, delivery.person_id, delivery.outcome), (notice.pk, self.eligible.pk, "sent"))

    # ---- what who publishes reads -------------------------------------------------------------------
    def page(self, url, day, hour=12, minute=0):
        with self.at(day, hour, minute):
            self.client.force_login(self.admin)
            return self.client.get(url)

    def test_the_notice_page_promises_the_mail_only_while_the_job_runs(self):
        notice = self.schedule_for_tomorrow()
        url = reverse("notices:detail", args=[notice.pk])
        # A server where the scheduler never ran (a fresh deploy) is not sending anything.
        page = self.page(url, self.today, 9, 1)
        self.assertContains(page, "No ha salido ningún correo por este aviso.")
        self.assertContains(page, NOT_SENDING)
        self.assertContains(page, "no saldrá ningún correo")
        self.assertNotContains(page, PROMISE)

        self.run_pass(self.today, 10, 0)
        page = self.page(url, self.today, 10, 14)
        self.assertContains(page, PROMISE)
        self.assertNotContains(page, NOT_SENDING)

        # Sixteen minutes without a pass: the promise is withdrawn.
        page = self.page(url, self.today, 10, 16)
        self.assertContains(page, NOT_SENDING)
        self.assertNotContains(page, PROMISE)

        # A pass that fails does not count as one that ran.
        with mock.patch("notices.email.send_due", side_effect=RuntimeError("x")), self.assertLogs("core.scheduler", "ERROR"), \
                self.assertLogs("emmaus.scheduler", "ERROR"):
            self.run_pass(self.today, 10, 20)
        self.assertContains(self.page(url, self.today, 10, 21), NOT_SENDING)

    def test_without_the_public_address_nothing_is_promised(self):
        notice = self.schedule_for_tomorrow()
        with self.settings(EMMAUS_PUBLIC_BASE_URL=""):
            self.assertIn("correos de avisos: sin dirección pública", self.run_pass(self.today, 10, 0))
            page = self.page(reverse("notices:detail", args=[notice.pk]), self.today, 10, 1)
            self.assertContains(page, NOT_SENDING)
            self.assertNotContains(page, PROMISE)
            self.assertFalse(SchedulerHeartbeat.objects.filter(job=NOTICE_MAIL).exists())
            # Its day comes and nothing goes out.
            self.run_pass(self.tomorrow, 0, 5)
        self.assertEqual(mail.outbox, [])

    def test_without_e_mail_the_page_says_that_and_the_job_does_not_run(self):
        notice = self.schedule_for_tomorrow()
        with self.settings(EMAIL_BACKEND=CONSOLE):
            self.assertIn("correos de avisos: sin correo configurado", self.run_pass(self.today, 10, 0))
            page = self.page(reverse("notices:detail", args=[notice.pk]), self.today, 10, 1)
            self.assertContains(page, "No hay correo configurado: este aviso solo se ve en Emmaus.")
            self.assertNotContains(page, PROMISE)
            form = self.page(CREATE, self.today, 10, 2)
            self.assertContains(form, "No hay correo configurado")
            self.assertNotContains(form, NOT_SENDING)  # one reason is enough

    def test_the_form_warns_before_a_notice_is_kept_for_a_day_nobody_will_mail(self):
        form = self.page(CREATE, self.today, 9, 0)
        self.assertContains(form, NOT_SENDING)
        self.assertContains(form, "Si lo publicas hoy, el correo sí sale.")
        self.run_pass(self.today, 9, 5)
        self.assertNotContains(self.page(CREATE, self.today, 9, 6), NOT_SENDING)

        notice = self.schedule_for_tomorrow()
        edit = reverse("notices:edit", args=[notice.pk])
        self.assertNotContains(self.page(edit, self.today, 9, 10), NOT_SENDING)
        late = self.page(edit, self.today, 9, 30)
        self.assertContains(late, NOT_SENDING)
        self.assertContains(late, "el día que se publique este aviso se verá en Emmaus, pero no saldrá ningún correo")
        # A notice that is already published is mailed from the request: its form says nothing about the scheduler.
        Announcement.objects.filter(pk=notice.pk).update(publish_on=self.today)
        self.assertNotContains(self.page(edit, self.today, 9, 31), NOT_SENDING)
