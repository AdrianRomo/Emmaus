"""Avisos por correo: who chose it is told that there is a notice and where, once per version, never what it says."""

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.notify import SUBJECTS
from events.tests.base import make_user
from groups import services as group_services
from groups.models import GroupMembership
from groups.tests.base import CommunityFixture
from notices import email, services
from notices.models import Announcement, EmailPreference, NoticeEmail
from people import flags
from people.models import Person

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
CONSOLE = "django.core.mail.backends.console.EmailBackend"
FAILING = "core.test_notify.FailingBackend"
BASE = "https://emmaus.prueba.mx"
CREATE = reverse("notices:create")
PREFERENCES = reverse("notices:email_preferences")
PROFILE = reverse("community:profile")
TITLE = "Retiro de noviembre"
SECRET = "La llave está bajo la maceta azul"
Choice = EmailPreference.Choice


def detail(notice):
    return reverse("notices:detail", args=[notice.pk])


def body_for(notice, *, urgent=False, base="http://testserver"):
    """The whole e-mail: fixed words, the church's name and the address of the notice's page."""
    if urgent:
        first = "Hay un aviso urgente para ti en Iglesia Central. Entra a Emmaus con tu cuenta para leerlo y responder «Enterado»:"
    else:
        first = "Hay un aviso nuevo para ti en Iglesia Central. Entra a Emmaus con tu cuenta para leerlo:"
    return (
        f"Hola:\n\n{first}\n\n{base}/mi-iglesia/avisos/{notice.pk}/\n\n"
        "Por privacidad, este correo no incluye el aviso: solo se lee en Emmaus. "
        "En «Mis datos» eliges qué avisos te llegan por correo."
    )


@override_settings(EMAIL_BACKEND=LOCMEM)
class NoticeEmailTests(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.church.name = "Iglesia Central"
        cls.church.save(update_fields=["name"])
        group_services.set_membership(cls.group, cls.member, cls.admin, role=GroupMembership.Role.MEMBER)
        cls.today = timezone.localdate()
        # Everybody else of church A with an account, so that a count is a fact of this test.
        known = [cls.admin_person.pk, cls.member.pk, cls.leader.pk, cls.coord_person.pk, cls.asked.pk, cls.no_account.pk]
        Person.objects.filter(church=cls.church).exclude(pk__in=known).update(user=None)

    def choose(self, person, choice):
        email.set_preference(person, choice)

    def publish(self, **changes):
        return services.publish(self.church, self.admin, **{"title": TITLE, "body": SECRET, **changes})

    def deliver(self, notice, **options):
        return email.deliver(notice.pk, BASE, **options)

    def mailed(self):
        return sorted(address for message in mail.outbox for address in message.to)

    def data(self, para, **changes):
        return {"para": para, "title": TITLE, "priority": "normal", "body": SECRET, "publish_on": self.today.isoformat(), **changes}

    def post(self, url, data):
        """A request whose transaction commits: what ``transaction.on_commit`` holds runs after it."""
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url, data)

    # ---- the preferences page ---------------------------------------------------------------------
    def test_mis_datos_links_to_the_page_and_it_starts_at_only_the_urgent_ones(self):
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(PROFILE), f'href="{PREFERENCES}"')
        page = self.client.get(PREFERENCES)
        self.assertContains(page, "Avisos por correo")
        for label in ("Solo los urgentes", "Importantes y urgentes", "Todos", "Ninguno"):
            self.assertContains(page, label)
        self.assertContains(page, '<input type="radio" name="choice" value="urgent" id="id_choice_0" required checked>', html=True)
        self.assertContains(page, "Solo que hay un aviso para ti en tu iglesia y el enlace para leerlo")
        self.assertContains(page, "No lleva el título ni el texto del aviso")
        self.assertContains(page, "<strong>diego@prueba.mx</strong>")
        self.assertNotContains(page, "no hay correo configurado")
        self.assertEqual(page.content.decode().count("<h1"), 1)
        self.assertEqual(email.preference(self.member), Choice.URGENT)
        self.assertFalse(EmailPreference.objects.exists())  # the default needs no row

    def test_a_person_changes_their_own_choice_and_nobody_elses(self):
        self.client.force_login(self.member_user)
        response = self.client.post(PREFERENCES, {"choice": "all"}, follow=True)
        self.assertRedirects(response, PREFERENCES)
        self.assertContains(response, "Guardamos tu elección.")
        self.assertContains(response, '<input type="radio" name="choice" value="all" id="id_choice_2" required checked>', html=True)
        self.client.post(PREFERENCES, {"choice": "none"})
        self.assertEqual(email.preference(self.member), Choice.NONE)
        self.assertEqual(EmailPreference.objects.count(), 1)
        self.assertEqual(email.preference(self.leader), Choice.URGENT)
        for bad in ({}, {"choice": "siempre"}, {"choice": ""}):
            page = self.client.post(PREFERENCES, bad)
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, "Elige una")
        self.assertEqual(email.preference(self.member), Choice.NONE)

    def test_without_an_address_the_page_says_so_and_points_to_mis_datos(self):
        Person.objects.filter(pk=self.member.pk).update(email="")
        self.client.force_login(self.member_user)
        page = self.client.get(PREFERENCES)
        self.assertContains(page, "No tienes un correo en tus datos, así que no te llega ninguno.")
        self.assertContains(page, f'<a href="{PROFILE}">Mis datos</a>')
        self.assertNotContains(page, "diego@prueba.mx")  # the address of the account is another matter

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_without_email_on_the_server_the_page_does_not_promise_any(self):
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(PREFERENCES), "Por ahora no hay correo configurado en Emmaus: no sale ningún correo.")

    def test_the_page_is_for_people_of_a_church_that_uses_the_module(self):
        self.assertEqual(self.client.get(PREFERENCES).status_code, 302)  # nobody signed in
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(PREFERENCES).status_code, 404)
        self.assertEqual(self.client.post(PREFERENCES, {"choice": "all"}).status_code, 404)
        self.assertFalse(EmailPreference.objects.exists())

    # ---- who is mailed ----------------------------------------------------------------------------
    def test_each_level_reaches_who_chose_it(self):
        self.choose(self.member, Choice.ALL)
        self.choose(self.leader, Choice.IMPORTANT)
        self.choose(self.coord_person, Choice.NONE)
        # ``asked`` never chose: only the urgent ones.
        expected = {
            "normal": {self.member},
            "important": {self.member, self.leader},
            "urgent": {self.member, self.leader, self.asked},
        }
        for level, people in expected.items():
            with self.subTest(level):
                notice = self.publish(priority=level)
                self.assertEqual(set(email.recipients(notice)), people)

    def test_publishing_mails_once_after_the_commit_and_the_mail_says_nothing_of_the_notice(self):
        for person in (self.member, self.leader, self.admin_person):
            self.choose(person, Choice.ALL)
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            response = self.client.post(CREATE, self.data("iglesia"))
            self.assertEqual(mail.outbox, [])  # nothing leaves before the publish is committed
        self.assertEqual(len(callbacks), 1)
        notice = Announcement.objects.get()
        self.assertRedirects(response, detail(notice), fetch_redirect_response=False)
        callbacks[0]()
        # Not the author, although they chose "Todos"; not who only wants the urgent ones.
        self.assertEqual(self.mailed(), sorted(["diego@prueba.mx", "guia@prueba.mx"]))
        for message in mail.outbox:
            self.assertEqual(len(message.to), 1)  # one person per e-mail: nobody reads who else got it
            self.assertEqual((message.cc, message.bcc), ([], []))
            self.assertEqual(message.subject, "Emmaus: hay un aviso nuevo de tu iglesia")
            self.assertEqual(message.body, body_for(notice))
            self.assertNotIn(TITLE, message.subject + message.body)
            self.assertNotIn(SECRET, message.subject + message.body)
        self.assertEqual(NoticeEmail.objects.filter(announcement=notice, version=1, outcome="sent").count(), 2)
        self.assertEqual(Announcement.objects.get().emailed_version, 1)
        page = self.client.get(detail(notice))  # the link of the e-mail is this page, which asks for the account
        self.assertEqual(page.status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(detail(notice)).status_code, 302)

    def test_an_important_notice_uses_the_words_of_any_notice(self):
        self.choose(self.member, Choice.IMPORTANT)
        notice = self.publish(priority="important")
        self.deliver(notice)
        self.assertEqual(self.mailed(), sorted(["diego@prueba.mx"]))
        self.assertEqual(mail.outbox[0].subject, SUBJECTS["aviso_publicado"])
        self.assertEqual(mail.outbox[0].body, body_for(notice, base=BASE))
        self.assertNotIn("mportante", mail.outbox[0].subject + mail.outbox[0].body)

    def test_a_group_notice_goes_only_to_its_active_members_and_never_names_the_group(self):
        for person in (self.member, self.asked, self.coord_person, self.admin_person):
            self.choose(person, Choice.ALL)
        gone = group_services.set_membership(self.group, self.coord_person, self.admin)
        group_services.end_membership(gone, self.admin)
        self.client.force_login(self.leader_user)
        self.post(CREATE, self.data(self.group.pk))
        notice = Announcement.objects.get()
        # ``asked`` only asked to join; ``coord`` left; the administration reads it but is not in the group.
        self.assertEqual(self.mailed(), sorted(["diego@prueba.mx"]))
        self.assertEqual(mail.outbox[0].body, body_for(notice))
        for private in (TITLE, SECRET, "Jóvenes Roca", "Jóvenes", "Gala", "guía"):
            self.assertNotIn(private, mail.outbox[0].subject + mail.outbox[0].body)

    def test_an_archived_group_or_a_church_without_the_module_mails_nobody(self):
        notice = self.publish(group=self.group, priority="urgent")
        group_services.archive_group(self.group, self.admin)
        self.assertEqual(self.deliver(notice).sent, 0)
        other = self.publish(priority="urgent")
        flags.set_enabled(self.church, "community", False)
        self.assertEqual(self.deliver(other).sent, 0)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(NoticeEmail.objects.exists())

    def test_a_person_of_another_church_is_never_mailed(self):
        self.choose(self.member_b, Choice.ALL)
        self.choose(self.admin_b_person, Choice.ALL)
        # The same account with a person in both churches: church A's notice goes to church A's address only.
        twin = Person.objects.create(
            church=self.church_b, user=self.member_user, first_name="Diego", last_name="En B", email="diego-b@prueba.mx"
        )
        self.choose(twin, Choice.ALL)
        notice = self.publish(priority="urgent")
        self.deliver(notice)
        self.assertEqual(self.mailed(), sorted(["diego@prueba.mx", "guia@prueba.mx", "pidio@prueba.mx", self.coord1.email]))
        self.assertFalse(NoticeEmail.objects.exclude(person__church=self.church).exists())
        mail.outbox.clear()
        theirs = services.publish(self.church_b, self.admin_b, title="Solo de B", body="Secreto de B", priority="urgent")
        self.deliver(theirs)
        self.assertEqual(self.mailed(), sorted(["diego-b@prueba.mx", "miembro-b@prueba.mx"]))
        self.assertIn("Iglesia B", mail.outbox[0].body)
        self.assertNotIn("Iglesia Central", mail.outbox[0].body)

    def test_no_account_no_address_or_a_closed_account_means_no_mail(self):
        Person.objects.filter(pk=self.no_account.pk).update(email="nora@prueba.mx")
        self.choose(self.no_account, Choice.ALL)
        Person.objects.filter(pk=self.leader.pk).update(email="")
        self.asked_user.is_active = False
        self.asked_user.save(update_fields=["is_active"])
        notice = self.publish(priority="urgent")
        self.deliver(notice)
        self.assertEqual(self.mailed(), sorted(["diego@prueba.mx", self.coord1.email]))

    def test_the_mail_goes_to_the_address_in_mis_datos(self):
        Person.objects.filter(pk=self.member.pk).update(email="otro-diego@prueba.mx")
        self.deliver(self.publish(priority="urgent"))
        self.assertIn("otro-diego@prueba.mx", self.mailed())
        self.assertNotIn("diego@prueba.mx", self.mailed())

    def test_a_change_of_preference_counts_from_the_next_notice(self):
        self.choose(self.member, Choice.NONE)
        self.deliver(self.publish(priority="urgent"))
        self.assertNotIn("diego@prueba.mx", self.mailed())
        self.choose(self.member, Choice.URGENT)
        mail.outbox.clear()
        self.deliver(self.publish(priority="urgent"))
        self.assertIn("diego@prueba.mx", self.mailed())

    # ---- once per version -------------------------------------------------------------------------
    def test_nobody_is_mailed_twice_for_a_version(self):
        notice = self.publish(priority="urgent")
        first = self.deliver(notice)
        self.assertEqual((first.sent, first.failed, first.left), (4, 0, 0))
        again = self.deliver(notice)
        self.assertEqual((again.sent, again.left), (0, 0))
        self.assertEqual(len(mail.outbox), 4)
        # Even with the round open again, the rows say who has it.
        Announcement.objects.filter(pk=notice.pk).update(emailed_version=None)
        self.assertEqual(self.deliver(notice).sent, 0)
        self.assertEqual(len(mail.outbox), 4)
        with self.assertRaises(IntegrityError), transaction.atomic():
            NoticeEmail.objects.create(announcement=notice, version=1, person=self.member)

    def test_a_row_another_sender_wrote_first_wins(self):
        notice = self.publish(priority="urgent")
        NoticeEmail.objects.create(announcement=notice, version=1, person=self.member)  # the other sender, mid-way
        self.assertFalse(email._claim(notice, 1, self.member.pk))
        self.assertTrue(email._claim(notice, 1, self.leader.pk))
        self.assertFalse(email._claim(notice, 1, self.leader.pk))
        # A sender that listed the member before the other one wrote the row still does not mail them.
        with mock.patch.object(email, "_waiting", return_value=Person.objects.filter(pk=self.member.pk)):
            self.assertEqual(self.deliver(notice).sent, 0)
        self.assertEqual(mail.outbox, [])

    def test_who_arrives_after_the_round_is_not_mailed_an_old_notice(self):
        notice = self.publish(priority="urgent")
        self.deliver(notice)
        mail.outbox.clear()
        late_user = make_user("tarde@prueba.mx", "Tania", "Tarde")
        self._person(self.church, late_user)
        self.assertEqual(self.deliver(notice).sent, 0)
        call_command("send_notice_emails", "--apply", "--base-url", BASE, stdout=StringIO())
        self.assertEqual(mail.outbox, [])

    def test_a_change_of_the_information_of_an_urgent_notice_mails_again_and_a_detail_does_not(self):
        self.choose(self.leader, Choice.NONE)
        self.client.force_login(self.admin)
        self.post(CREATE, self.data("iglesia", priority="urgent"))
        notice = Announcement.objects.get()
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(mail.outbox[0].subject, "Emmaus: hay un aviso urgente de tu iglesia")
        self.assertEqual(mail.outbox[0].body, body_for(notice, urgent=True))
        mail.outbox.clear()
        edit = reverse("notices:edit", args=[notice.pk])
        self.post(edit, self.data("iglesia", priority="urgent", body="La llave está bajo la maceta asul", material="no"))
        self.assertEqual(mail.outbox, [])
        self.post(edit, self.data("iglesia", priority="urgent", body="Se cambió de lugar: salón 4", material="si"))
        self.assertEqual(Announcement.objects.get().version, 2)
        self.assertEqual(self.mailed(), sorted(["diego@prueba.mx", "pidio@prueba.mx", self.coord1.email]))
        self.assertEqual(mail.outbox[0].body, body_for(notice, urgent=True))
        self.assertNotIn("salón 4", mail.outbox[0].body)
        self.assertEqual(NoticeEmail.objects.filter(announcement=notice, version=2, outcome="sent").count(), 3)

    def test_correcting_a_notice_that_is_not_urgent_mails_nobody(self):
        self.choose(self.member, Choice.ALL)
        self.client.force_login(self.admin)
        self.post(CREATE, self.data("iglesia"))
        notice = Announcement.objects.get()
        self.assertEqual(len(mail.outbox), 1)
        self.choose(self.leader, Choice.ALL)  # chose after the notice went out
        self.post(reverse("notices:edit", args=[notice.pk]), self.data("iglesia", title="Retiro de diciembre"))
        self.assertEqual(Announcement.objects.get().title, "Retiro de diciembre")
        self.assertEqual(len(mail.outbox), 1)

    def test_a_notice_that_becomes_urgent_mails_who_had_not_been_mailed(self):
        self.choose(self.member, Choice.ALL)
        self.client.force_login(self.admin)
        self.post(CREATE, self.data("iglesia"))
        notice = Announcement.objects.get()
        self.assertEqual(self.mailed(), sorted(["diego@prueba.mx"]))
        mail.outbox.clear()
        self.post(reverse("notices:edit", args=[notice.pk]), self.data("iglesia", priority="urgent"))
        self.assertEqual(self.mailed(), sorted(["guia@prueba.mx", "pidio@prueba.mx", self.coord1.email]))  # not Diego again
        self.assertEqual(mail.outbox[0].subject, SUBJECTS["aviso_urgente"])

    # ---- never in the way of publishing -----------------------------------------------------------
    def test_a_publish_that_is_rolled_back_mails_nobody(self):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    services.publish(self.church, self.admin, title=TITLE, body=SECRET, priority="urgent", base_url=BASE)
                    raise RuntimeError("the request failed after publishing")
            except RuntimeError:
                pass
        self.assertEqual(mail.outbox, [])
        self.assertFalse(Announcement.objects.exists())

    def test_without_an_address_of_emmaus_the_mail_waits_for_the_command(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            notice = self.publish(priority="urgent")
        self.assertEqual((callbacks, mail.outbox), ([], []))
        self.assertEqual(email.waiting_count(notice), 4)

    def test_a_sender_that_breaks_does_not_break_the_publish(self):
        self.client.force_login(self.admin)
        with mock.patch.object(email, "deliver", side_effect=RuntimeError("smtp fell over")):
            with self.assertLogs("django.test", "ERROR"):  # the test runner's copy of what the database wrapper logs
                response = self.post(CREATE, self.data("iglesia", priority="urgent"))
        notice = Announcement.objects.get()
        self.assertRedirects(response, detail(notice), fetch_redirect_response=False)
        self.assertEqual(self.client.get(detail(notice)).status_code, 200)

    @override_settings(EMAIL_BACKEND=FAILING)
    def test_a_mail_the_server_refuses_is_recorded_and_tried_again_by_the_command(self):
        self.client.force_login(self.admin)
        with self.assertLogs("core.services", "ERROR"):
            response = self.post(CREATE, self.data("iglesia", priority="urgent"))
        notice = Announcement.objects.get()
        self.assertRedirects(response, detail(notice), fetch_redirect_response=False)
        self.assertEqual(NoticeEmail.objects.filter(announcement=notice, outcome="failed").count(), 4)
        self.assertIsNone(notice.emailed_version)
        page = self.client.get(detail(notice))
        self.assertContains(page, "Algunos correos no se pudieron enviar.")
        out = StringIO()
        with self.settings(EMAIL_BACKEND=LOCMEM):
            call_command("send_notice_emails", "--apply", "--base-url", BASE, stdout=out)
            call_command("send_notice_emails", "--apply", "--base-url", BASE, stdout=StringIO())
        self.assertIn("correos enviados: 4 · no se pudieron enviar: 0 · quedan pendientes: 0", out.getvalue())
        self.assertEqual(len(mail.outbox), 4)
        self.assertEqual(NoticeEmail.objects.filter(announcement=notice, outcome="sent").count(), 4)

    def test_above_the_cap_the_rest_waits_and_is_sent_later_without_repeating(self):
        notice = self.publish(priority="urgent")
        first = self.deliver(notice, limit=3)
        self.assertEqual((first.sent, first.left), (3, 1))
        self.assertIsNone(Announcement.objects.get().emailed_version)
        second = self.deliver(notice, limit=3)
        self.assertEqual((second.sent, second.left), (1, 0))
        self.assertEqual(len(set(self.mailed())), 4)
        self.assertEqual(len(mail.outbox), 4)
        self.assertEqual(email.SEND_CAP, 2000)

    def test_a_publish_stops_sending_when_its_time_is_up(self):
        notice = self.publish(priority="urgent")
        done = self.deliver(notice, budget_seconds=-1)
        self.assertEqual((done.sent, done.left), (0, 4))
        self.assertEqual(mail.outbox, [])
        self.assertEqual(self.deliver(notice).sent, 4)

    # ---- a later day, an expired or retired notice ------------------------------------------------
    def test_a_notice_for_a_later_day_is_mailed_by_the_command_when_the_day_comes(self):
        tomorrow = self.today + timedelta(days=1)
        self.client.force_login(self.admin)
        self.post(CREATE, self.data("iglesia", priority="urgent", publish_on=tomorrow.isoformat()))
        notice = Announcement.objects.get()
        self.assertEqual(mail.outbox, [])
        self.assertContains(self.client.get(detail(notice)), "No ha salido ningún correo por este aviso.")
        out = StringIO()
        call_command("send_notice_emails", "--apply", "--base-url", BASE, stdout=out)
        self.assertIn("avisos revisados: 0 · correos enviados: 0", out.getvalue())
        self.assertEqual(mail.outbox, [])

        Announcement.objects.filter(pk=notice.pk).update(publish_on=self.today)  # the day came
        out = StringIO()
        call_command("send_notice_emails", stdout=out)
        self.assertEqual(out.getvalue().strip(), "[simulación] avisos con correos por enviar: 1 · correos que saldrían: 4")
        self.assertEqual(mail.outbox, [])
        self.assertFalse(NoticeEmail.objects.exists())
        out = StringIO()
        call_command("send_notice_emails", "--apply", "--base-url", BASE + "/", stdout=out)
        self.assertEqual(
            out.getvalue().strip(), "avisos revisados: 1 · correos enviados: 4 · no se pudieron enviar: 0 · quedan pendientes: 0"
        )
        self.assertEqual(len(mail.outbox), 4)
        self.assertEqual(mail.outbox[0].body, body_for(notice, urgent=True, base=BASE))
        for private in (TITLE, SECRET, "@", "Diego"):
            self.assertNotIn(private, out.getvalue())  # counts only
        out = StringIO()
        call_command("send_notice_emails", "--apply", "--base-url", BASE, stdout=out)
        self.assertIn("avisos revisados: 0", out.getvalue())
        self.assertEqual(len(mail.outbox), 4)

    def test_the_command_needs_the_address_of_emmaus_to_send(self):
        self.publish(priority="urgent")
        for options in (["--apply"], ["--apply", "--base-url", "emmaus.prueba.mx"], ["--apply", "--base-url", "/"]):
            with self.assertRaises(CommandError):
                call_command("send_notice_emails", *options, stdout=StringIO())
        self.assertEqual(mail.outbox, [])

    def test_a_notice_moved_to_today_is_mailed_then(self):
        tomorrow = self.today + timedelta(days=1)
        self.client.force_login(self.admin)
        self.post(CREATE, self.data("iglesia", publish_on=tomorrow.isoformat(), priority="important"))
        notice = Announcement.objects.get()
        self.choose(self.member, Choice.IMPORTANT)
        self.post(reverse("notices:edit", args=[notice.pk]), self.data("iglesia", priority="important"))
        self.assertEqual(self.mailed(), sorted(["diego@prueba.mx"]))

    def test_an_expired_or_retired_notice_is_not_mailed(self):
        yesterday = self.today - timedelta(days=1)
        expired = self.publish(priority="urgent", publish_on=yesterday, expires_on=yesterday)
        retired = services.retire(self.publish(priority="urgent"), self.admin)
        for notice in (expired, retired):
            self.assertEqual(self.deliver(notice).sent, 0)
            self.assertEqual(email.waiting_count(notice), 0)
        out = StringIO()
        call_command("send_notice_emails", "--apply", "--base-url", BASE, stdout=out)
        self.assertIn("avisos revisados: 0", out.getvalue())
        self.assertEqual(mail.outbox, [])
        self.assertFalse(NoticeEmail.objects.exists())

    def test_the_command_leaves_old_notices_alone(self):
        old = self.publish(priority="urgent", publish_on=self.today - timedelta(days=10))
        Announcement.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(days=10))
        call_command("send_notice_emails", "--apply", "--base-url", BASE, stdout=StringIO())
        self.assertEqual(mail.outbox, [])
        call_command("send_notice_emails", "--apply", "--base-url", BASE, "--days", "30", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 4)

    # ---- no e-mail on the server ------------------------------------------------------------------
    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_without_email_nothing_is_recorded_and_every_page_says_so(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(CREATE), "No hay correo configurado: el aviso solo se ve en Emmaus.")
        self.post(CREATE, self.data("iglesia", priority="urgent"))
        notice = Announcement.objects.get()
        self.assertFalse(NoticeEmail.objects.exists())
        self.assertIsNone(notice.emailed_version)
        page = self.client.get(detail(notice))
        self.assertContains(page, "No hay correo configurado: este aviso solo se ve en Emmaus.")
        self.assertNotContains(page, "Salieron")
        self.assertNotContains(page, "Salió")
        done = self.deliver(notice)
        self.assertTrue(done.not_configured)
        out = StringIO()
        call_command("send_notice_emails", "--apply", "--base-url", BASE, stdout=out)
        self.assertEqual(out.getvalue().strip(), "No hay correo configurado: no se envió nada.")
        self.assertFalse(NoticeEmail.objects.exists())

    # ---- what who published reads -----------------------------------------------------------------
    def more_people(self, count):
        for number in range(count):
            self._person(self.church, make_user(f"extra{number}@prueba.mx", "Extra", str(number)))

    def test_who_published_reads_how_many_mails_went_out_and_that_sent_is_not_read(self):
        self.more_people(6)  # 10 people with an account besides the author
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(CREATE), "le llega además un correo que solo dice que hay un aviso")
        self.post(CREATE, self.data("iglesia", priority="urgent"))
        notice = Announcement.objects.get()
        page = self.client.get(detail(notice))
        self.assertContains(page, "Salieron 10 correos: a 10 de las 10 personas con cuenta a quienes va este aviso.")
        self.assertContains(page, "Un correo enviado no es un correo leído.")
        self.assertNotContains(page, "Emmaus no lo envía por correo")
        self.assertNotContains(page, "prueba.mx")  # a number, never an address
        # Who only reads the notice reads nothing about its e-mails.
        self.client.force_login(self.member_user)
        page = self.client.get(detail(notice))
        self.assertNotContains(page, "correos")
        self.assertNotContains(page, "Un correo enviado")

    def test_a_number_that_would_say_who_is_not_given(self):
        self.more_people(6)
        self.client.force_login(self.admin)
        cases = [
            ({self.member: Choice.NONE}, "Salió un correo a casi todas las 10 personas con cuenta a quienes va este aviso."),
            ({person: Choice.NONE for person in Person.objects.filter(church=self.church).exclude(pk=self.member.pk)},
             "Salieron menos de 5 correos por este aviso."),
            ({person: Choice.NONE for person in Person.objects.filter(church=self.church)},
             "Hasta ahora no ha salido ningún correo por este aviso."),
        ]
        for choices, line in cases:
            with self.subTest(line):
                EmailPreference.objects.all().delete()
                for person, choice in choices.items():
                    self.choose(person, choice)
                notice = self.publish(priority="urgent")
                self.deliver(notice)
                page = self.client.get(detail(notice))
                self.assertContains(page, line)
                self.assertNotContains(page, "9 correos")
                self.assertNotContains(page, "Salieron 1 ")

    def test_a_notice_for_very_few_people_gives_no_number(self):
        self.client.force_login(self.admin)
        self.post(CREATE, self.data("iglesia", priority="urgent"))  # four people with an account
        notice = Announcement.objects.get()
        self.assertEqual(len(mail.outbox), 4)
        page = self.client.get(detail(notice))
        self.assertContains(page, "Este aviso es para menos de 5 personas con cuenta: aquí no se cuenta cuántos correos salieron.")
        self.assertNotContains(page, "Salieron")
        self.assertContains(page, "Un correo enviado no es un correo leído.")

    def test_the_count_starts_over_with_a_new_version(self):
        self.more_people(6)
        notice = self.publish(priority="urgent")
        self.deliver(notice)
        self.choose(self.member, Choice.NONE)
        self.choose(self.leader, Choice.NONE)
        with self.captureOnCommitCallbacks(execute=True):
            services.update(
                notice, self.admin, title=TITLE, body="Otro lugar", publish_on=self.today, material=True, base_url=BASE
            )
        self.client.force_login(self.admin)
        page = self.client.get(detail(notice))
        self.assertContains(page, "Salió un correo a casi todas las 10 personas")
        self.assertContains(page, "Cuenta desde el último cambio que pidió «Enterado» otra vez.")
