"""Servir, wave 10: the reminder of a date that is near (``serving.reminders``) and the scheduler's job."""

from datetime import time, timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone

from core import scheduler
from core.models import Church
from events.models import Event
from people import flags
from people.models import Person
from serving import reminders, rotas, services
from serving.models import Assignment, ServiceDate, ServiceReminder, ServingPreference, Team

from .test_serving import ServingFixture

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
CONSOLE = "django.core.mail.backends.console.EmailBackend"
BASE = "https://emmaus.example.org"


@override_settings(EMAIL_BACKEND=LOCMEM, EMMAUS_PUBLIC_BASE_URL=BASE)
class ReminderTests(ServingFixture):
    """Bienvenida sends reminders two days before; its date is tomorrow and Diego is asked."""

    def setUp(self):
        Team.objects.filter(pk=self.team.pk).update(reminder_days=2)
        ServiceDate.objects.filter(pk=self.day.pk).update(date=self.today + timedelta(days=1), starts_at=None)
        self.day.refresh_from_db()
        self.mine = self.assign()

    def send(self, **kwargs) -> dict:
        return reminders.send_due(BASE, **kwargs)

    def test_it_is_off_until_who_coordinates_turns_it_on(self):
        Team.objects.filter(pk=self.team.pk).update(reminder_days=0)
        self.assertEqual(self.send()["sent"], 0)
        self.assertEqual((mail.outbox, ServiceReminder.objects.count()), ([], 0))
        self.assertEqual(Team._meta.get_field("reminder_days").default, 0)

    def test_one_e_mail_that_says_only_that_a_date_is_near(self):
        Team.objects.filter(pk=self.team.pk).update(description="Notas privadas del equipo")
        services.assign(self.day, self.asked, self.leader_user)
        done = self.send()
        self.assertEqual((done["sent"], done["failed"], done["left"]), (2, 0, 0))
        message = next(message for message in mail.outbox if message.to == ["diego@prueba.mx"])
        self.assertEqual(message.subject, "Emmaus: tienes un servicio próximo")
        self.assertIn(f"{BASE}/mi-iglesia/servir/", message.body)
        self.assertIn("Tienes un servicio próximo en Iglesia de prueba", message.body)
        for hidden in ("Bienvenida", "Templo", "Pía", "Gala", "Diego", "Notas", "domingo", str(self.day.date.day) + " de"):
            self.assertNotIn(hidden, message.subject + message.body)
        self.assertEqual({tuple(message.to) for message in mail.outbox}, {("diego@prueba.mx",), ("pidio@prueba.mx",)})
        # Again, and again: nobody is mailed twice about one assignment.
        self.assertEqual(self.send()["sent"], 0)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(list(ServiceReminder.objects.values_list("outcome", "attempts").distinct()), [("sent", 1)])

    def test_the_days_before_are_the_teams(self):
        ServiceDate.objects.filter(pk=self.day.pk).update(date=self.today + timedelta(days=4))
        self.assertEqual(self.send()["sent"], 0)
        Team.objects.filter(pk=self.team.pk).update(reminder_days=5)
        self.assertEqual(self.send()["sent"], 1)
        ServiceReminder.objects.all().delete()
        ServiceDate.objects.filter(pk=self.day.pk).update(date=self.today + timedelta(days=6))
        self.assertEqual(self.send()["sent"], 0)

    def test_who_is_never_mailed(self):
        def nobody(label):
            with self.subTest(label):
                self.assertEqual(self.send()["sent"], 0, label)
                self.assertEqual((mail.outbox, ServiceReminder.objects.count()), ([], 0), label)

        Assignment.objects.filter(pk=self.mine.pk).update(status="declinada")
        nobody("said no")
        Assignment.objects.filter(pk=self.mine.pk).update(status="pendiente")
        Person.objects.filter(pk=self.member.pk).update(email="")
        nobody("no e-mail")
        Person.objects.filter(pk=self.member.pk).update(email="diego@prueba.mx", user=None)
        nobody("no account")
        Person.objects.filter(pk=self.member.pk).update(user=self.member_user)
        type(self.member_user).objects.filter(pk=self.member_user.pk).update(is_active=False)
        nobody("account switched off")
        type(self.member_user).objects.filter(pk=self.member_user.pk).update(is_active=True)
        reminders.set_reminders(self.member, False)
        nobody("turned reminders off")
        reminders.set_reminders(self.member, True)
        Person.objects.filter(pk=self.member.pk).update(merged_into=self.leader)
        nobody("a record joined to another")
        Person.objects.filter(pk=self.member.pk).update(merged_into=None)
        flags.set_enabled(self.church, "community", False)
        nobody("module off")
        flags.set_enabled(self.church, "community", True)
        Church.objects.filter(pk=self.church.pk).update(active=False)
        nobody("church not active")
        Church.objects.filter(pk=self.church.pk).update(active=True)
        Team.objects.filter(pk=self.team.pk).update(status="archivado")
        nobody("archived team")
        Team.objects.filter(pk=self.team.pk).update(status="activo")
        ServiceDate.objects.filter(pk=self.day.pk).update(date=self.today, starts_at=time(0, 0))
        nobody("already started")
        ServiceDate.objects.filter(pk=self.day.pk).update(date=self.today - timedelta(days=1), starts_at=None)
        nobody("yesterday")
        # With every exclusion undone, the same person is mailed: each one above was what stopped it.
        ServiceDate.objects.filter(pk=self.day.pk).update(date=self.today + timedelta(days=1))
        self.assertEqual(self.send()["sent"], 1)
        Team.objects.filter(pk=self.team.pk).update(status="activo")  # leave the projection as the fixture expects

    def test_two_runs_at_once_send_one_e_mail(self):
        waiting = reminders.due()
        self.assertEqual([row.pk for row in waiting], [self.mine.pk])
        with mock.patch.object(reminders, "due", return_value=waiting):  # both passes read the list before either wrote
            first, second = self.send(), self.send()
        self.assertEqual((first["sent"], second["sent"], len(mail.outbox)), (1, 0, 1))
        self.assertEqual(ServiceReminder.objects.count(), 1)
        now = timezone.now()
        self.assertEqual(reminders._claim(self.mine.pk, now), 0)  # the one-to-one key is what refuses the second writer
        ServiceReminder.objects.all().delete()
        self.assertEqual((reminders._claim(self.mine.pk, now), reminders._claim(self.mine.pk, now)), (1, 0))

    def test_a_mail_that_failed_is_tried_again_later_and_then_given_up(self):
        with mock.patch.object(reminders, "notify", return_value="failed"):
            done = self.send()
            self.assertEqual((done["failed"], done["first_failed"], reminders.verdict(done)), (1, 1, "AllSendsFailed"))
            self.assertEqual(self.send()["failed"], 0)  # not at once
            for minutes in (31, 31 + 181):
                later = timezone.now() + timedelta(minutes=minutes)
                self.assertEqual(self.send(now=later)["failed"], 1)
            self.assertEqual(self.send(now=timezone.now() + timedelta(days=1))["failed"], 0)  # three attempts: no more
        self.assertEqual(list(ServiceReminder.objects.values_list("outcome", "attempts")), [("failed", 3)])
        self.assertEqual(mail.outbox, [])

    def test_a_sender_that_died_is_taken_over_once(self):
        ServiceReminder.objects.create(assignment=self.mine, attempts=1, last_attempt_at=timezone.now())
        self.assertEqual(self.send()["sent"], 0)  # somebody is sending it
        later = timezone.now() + timedelta(minutes=11)
        self.assertEqual(self.send(now=later)["sent"], 1)
        self.assertEqual(self.send(now=later)["sent"], 0)

    def test_without_e_mail_or_address_nothing_is_claimed(self):
        with self.settings(EMAIL_BACKEND=CONSOLE):
            done = self.send()
        self.assertEqual((done["not_configured"], done["sent"], done["left"]), (True, 0, 1))
        done = reminders.send_due("")
        self.assertTrue(done["not_configured"])
        self.assertFalse(ServiceReminder.objects.exists())
        self.assertEqual(self.send()["sent"], 1)  # and goes out once the server can

    def test_the_date_changing_hands_or_day_gives_the_new_one_its_own_reminder(self):
        self.send()
        swap = rotas.request_swap(self.mine, self.asked, self.member_user)
        rotas.answer_swap(swap, True, self.asked_user)
        self.assertEqual(self.send()["sent"], 1)
        self.assertEqual([message.to for message in mail.outbox], [["diego@prueba.mx"], ["pidio@prueba.mx"]])
        services.save_date(self.team, self.leader_user, service_date=self.day, date=self.today + timedelta(days=2), what=self.day.what)
        self.assertEqual(self.send()["sent"], 1)
        self.assertEqual(self.send()["sent"], 0)
        self.assertEqual(len(mail.outbox), 3)

    def test_the_scheduler_sends_them_and_says_only_numbers(self):
        Event.objects.update(reports_enabled=False)
        out = StringIO()
        with self.assertLogs("emmaus.scheduler", "INFO") as logs:
            call_command("run_scheduled_jobs", stdout=out)
        self.assertIn("recordatorios de servir: 1 enviados, 0 fallidos", out.getvalue())
        self.assertEqual(len(mail.outbox), 1)
        line = next(record.getMessage() for record in logs.records if scheduler.SERVING_REMINDERS in record.getMessage())
        for hidden in ("diego", "Bienvenida", "prueba.mx"):
            self.assertNotIn(hidden, line)
        call_command("run_scheduled_jobs", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)
        ServiceReminder.objects.all().delete()
        for off in ({"EMMAUS_SCHEDULED_COMMUNITY_JOBS": False}, {"EMMAUS_V2_MODULES": False}, {"EMMAUS_PUBLIC_BASE_URL": ""},
                    {"EMAIL_BACKEND": CONSOLE}):
            with self.subTest(off=off), self.settings(**off):
                call_command("run_scheduled_jobs", stdout=StringIO())
                self.assertEqual((len(mail.outbox), ServiceReminder.objects.count()), (1, 0))
        self.assertEqual(scheduler.skipped(scheduler.SERVING_REMINDERS), "")
        with self.settings(EMMAUS_PUBLIC_BASE_URL=""):
            self.assertEqual(scheduler.skipped(scheduler.SERVING_REMINDERS), scheduler.NO_BASE_URL)

    def test_a_failing_pass_is_a_failed_job(self):
        Event.objects.update(reports_enabled=False)
        with mock.patch.object(reminders, "notify", return_value="failed"), self.assertLogs("emmaus.scheduler", "INFO"), \
                self.assertRaises(CommandError):
            call_command("run_scheduled_jobs", stdout=StringIO())
        self.assertEqual(scheduler.last_error(scheduler.SERVING_REMINDERS), "AllSendsFailed")

    def test_the_person_turns_reminders_off_and_on_from_servir(self):
        self.client.force_login(self.member_user)
        page = self.client.get(self.url("home"))
        self.assertContains(page, "Recordatorios por correo")
        self.assertContains(page, "No quiero recordatorios")
        self.assertTrue(reminders.wants_reminders(self.member))  # no row is "yes" once the team turned them on
        self.client.post(self.url("reminders_choice"), {"recordatorios": "no"})
        self.assertFalse(ServingPreference.objects.get(person=self.member).reminders)
        self.assertEqual(self.send()["sent"], 0)
        self.assertContains(self.client.get(self.url("home")), "Quiero recordatorios")
        self.client.post(self.url("reminders_choice"), {"recordatorios": "si"})
        self.assertEqual(self.send()["sent"], 1)
        self.assertEqual(self.client.post(self.url("reminders_choice"), {"recordatorios": "tal vez"}).status_code, 404)
        # A team that sends none does not show the choice.
        Team.objects.filter(pk=self.team.pk).update(reminder_days=0)
        self.assertNotContains(self.client.get(self.url("home")), "Recordatorios por correo")
        # The choice is one person's: another church's person is untouched.
        self.assertFalse(ServingPreference.objects.exclude(person=self.member).exists())
