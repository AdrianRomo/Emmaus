"""Issue #96, second part, on synthetic data: updates of one event by e-mail for somebody without an account.
Off by default, double opt-in, no oracle, one e-mail per notice and address, automatic endings, and an address
nobody ever sees. Each test names the defect it would catch."""

import json
import time
from datetime import timedelta
from io import StringIO
from smtplib import SMTPRecipientsRefused
from unittest import mock

from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from agenda import public, services, subscription_mail, subscriptions
from agenda.duplicate import duplicate_simple_event
from agenda.models import (
    EventNoticeEmail,
    EventPage,
    EventRegistration,
    EventSubscription,
    MailOutcome,
    SubscriptionSuppression,
)
from agenda.tests.test_public_notices import BODY, TITLE, NoticesFixture, public_url
from core.models import Church
from events.models import AuthThrottle, Event
from people import flags
from people.models import ChurchAuditEvent, Person
from policies.models import ChurchPolicy

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
CONSOLE = "django.core.mail.backends.console.EmailBackend"
BASE = "https://emmaus.prueba.mx"
ADDRESS = "mama.de.prueba@demo.invalid"
OTHER = "papa.de.prueba@demo.invalid"


class RefusingBackend(BaseEmailBackend):
    """A mail server that refuses the recipient, and says the address while it does."""

    def send_messages(self, email_messages):
        raise SMTPRecipientsRefused({message.to[0]: (550, b"refused") for message in email_messages})


def subscribe_url(event) -> str:
    return reverse("evento:subscribe", args=[event.slug])


@override_settings(EMAIL_BACKEND=LOCMEM, EMMAUS_PUBLIC_BASE_URL=BASE, EMMAUS_SCHEDULED_COMMUNITY_JOBS=True)
class SubscriptionFixture(NoticesFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        services.save_page(cls.retiro, cls.admin, notice_mail=True)
        services.save_page(cls.retiro_b, cls.admin_b, notice_mail=True)

    def ask(self, address=ADDRESS, event=None, client=None):
        return (client or self.client).post(subscribe_url(event or self.retiro), {"email": address})

    def row(self, address=ADDRESS, event=None) -> EventSubscription:
        return EventSubscription.objects.get(event=event or self.retiro, email_hash=subscriptions.address_hash(address))

    def confirmed(self, address=ADDRESS, event=None, minutes_ago=5) -> EventSubscription:
        """A confirmed subscription, as asking and pressing the button leave it, confirmed a while ago."""
        subscriptions.request(event or self.retiro, address)
        row = self.row(address, event)
        subscriptions.confirm(row)
        EventSubscription.objects.filter(pk=row.pk).update(
            confirmed_at=timezone.now() - timedelta(minutes=minutes_ago), confirm_outcome=MailOutcome.SENT, confirm_attempts=1)
        row.refresh_from_db()
        return row

    def run_pass(self) -> dict:
        return subscriptions.run_pass(BASE)

    def link_in(self, message, name: str) -> str:
        """The path of the link of ``name`` ("confirmar" or "baja") in an e-mail."""
        for line in message.body.splitlines():
            if line.startswith(f"{BASE}/evento/avisos-por-correo/{name}/"):
                return line[len(BASE):]
        raise AssertionError(f"no {name} link in the e-mail")

    def later(self, **delta):
        return mock.patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(**delta))


class OfferTests(SubscriptionFixture):
    def test_off_by_default_and_the_public_page_is_as_it_was(self):
        """Defect: a new event, or a copy, taking addresses without anybody deciding it."""
        fresh = self._future(self.church, "Noche de alabanza", "noche-alabanza", 3, 3)
        page = services.save_page(fresh, self.admin, published=True, public=True)
        self.assertFalse(page.notice_mail)
        before = self.client.get(public_url(fresh)).content
        self.assertNotIn(b"Avisos por correo", before)
        self.assertEqual(self.client.get(subscribe_url(fresh)).status_code, 404)
        self.assertEqual(self.ask(event=fresh).status_code, 404)
        self.assertFalse(EventSubscription.objects.exists())
        # With the option on for ANOTHER event, this page is the same bytes.
        self.assertEqual(self.client.get(public_url(fresh)).content, before)

    def test_a_copy_of_the_event_starts_without_the_option_and_without_addresses(self):
        simple = Event.objects.create(church=self.church, name="Noche de alabanza", slug="noche-alabanza", camp_tools=False,
                                      starts_on=self.today + timedelta(days=3))
        services.save_page(simple, self.admin, published=True, public=True, notice_mail=True)
        self.confirmed(event=simple)
        copy = duplicate_simple_event(simple, self.admin, name="Otra noche", slug="otra-noche",
                                      starts_on=self.today + timedelta(days=30)).event
        self.assertFalse(EventPage.objects.get(event=copy).notice_mail)
        self.assertFalse(EventSubscription.objects.filter(event=copy).exists())
        self.assertEqual(EventSubscription.objects.count(), 1)

    def test_the_public_page_links_to_the_form_and_still_sets_no_cookie(self):
        """Defect: a form with a CSRF token on the public page gives every reader a cookie."""
        page = self.client.get(public_url(self.retiro))
        self.assertContains(page, '<h2 id="avisos-correo" class="heading">Avisos por correo</h2>', count=1)
        self.assertContains(page, subscriptions.PURPOSE)
        self.assertContains(page, f'href="{subscribe_url(self.retiro)}"', count=1)
        self.assertNotContains(page, "<form")
        self.assertNotContains(page, "csrfmiddlewaretoken")
        self.assertEqual(dict(page.cookies), {})
        self.assertEqual(dict(self.client.head(public_url(self.retiro)).cookies), {})

    def test_the_form_asks_for_an_address_and_nothing_else(self):
        page = self.client.get(subscribe_url(self.retiro))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "<h1", count=1)
        self.assertContains(page, subscriptions.PURPOSE)
        self.assertContains(page, "<input", count=2)  # the CSRF token and the address
        self.assertContains(page, 'type="email"', count=1)
        self.assertNotContains(page, "<textarea")
        self.assertNotContains(page, "<select")
        self.assertEqual(set(page.cookies), {"csrftoken"})  # the form's own cookie; no session
        self.assertIn("no-store", page["Cache-Control"])

    def test_the_form_answers_only_while_the_page_takes_subscriptions(self):
        """Defect: an address taken for an event that cannot mail it. Each case is the public page's own 404."""
        url = subscribe_url(self.retiro)
        self.assertEqual(self.client.get(url).status_code, 200)
        cases = {
            "option off": lambda: self.set_page(notice_mail=False),
            "not public": lambda: self.set_page(public=False),
            "not published": lambda: self.set_page(published=False),
            "module off": lambda: flags.set_enabled(self.church, "community", False),
            "ended": lambda: self.move(self.retiro, -3, -1),
            "church inactive": lambda: Church.objects.filter(pk=self.church.pk).update(active=False),
        }
        for name, change in cases.items():
            with self.subTest(name), transaction.atomic():
                change()
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.ask().status_code, 404)
                transaction.set_rollback(True)
        for name, setting in {"no address": {"EMMAUS_PUBLIC_BASE_URL": ""}, "no e-mail": {"EMAIL_BACKEND": CONSOLE},
                              "jobs off": {"EMMAUS_SCHEDULED_COMMUNITY_JOBS": False}, "kill switch": {"EMMAUS_V2_MODULES": False}}.items():
            with self.subTest(name), self.settings(**setting):
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.ask().status_code, 404)
                if name != "kill switch":  # the page itself still answers, without the offer
                    self.assertNotContains(self.client.get(public_url(self.retiro)), "Avisos por correo")
        self.assertEqual(self.client.get(subscribe_url(self.hidden)).status_code, 404)
        self.assertEqual(self.client.get(reverse("evento:subscribe", args=["no-existe"])).status_code, 404)
        self.assertFalse(EventSubscription.objects.exists())

    def test_the_post_needs_the_csrf_token(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(self.ask(client=strict).status_code, 403)
        self.assertFalse(EventSubscription.objects.exists())
        token = strict.get(subscribe_url(self.retiro)).context["csrf_token"]
        done = strict.post(subscribe_url(self.retiro), {"email": ADDRESS, "csrfmiddlewaretoken": str(token)})
        self.assertEqual(done.status_code, 302)
        self.assertEqual(EventSubscription.objects.count(), 1)

    def test_an_address_that_is_not_one_is_refused_and_nothing_is_kept(self):
        for typed in ("", "sin-arroba", "a@b", "dos@demo.invalid otra@demo.invalid", "x" * 250 + "@demo.invalid"):
            with self.subTest(typed):
                page = self.ask(typed)
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, "Hay un problema")
        self.assertFalse(EventSubscription.objects.exists())

    def test_the_event_name_is_escaped_on_every_page(self):
        Event.objects.filter(pk=self.retiro.pk).update(name="<script>alert(1)</script>")
        for page in (self.client.get(subscribe_url(self.retiro)), self.ask(), self.client.get(subscribe_url(self.retiro) + "?listo=1")):
            self.assertNotIn(b"<script>alert(1)", page.content)

    def test_who_is_signed_in_gets_the_same_page_and_no_person_is_touched(self):
        """Defect: the form reading who is signed in, or making a visitor or a person out of an address."""
        anonymous = self.client.get(subscribe_url(self.retiro) + "?listo=1").content
        people, registrations = Person.objects.count(), EventRegistration.objects.count()
        member = Client()
        member.force_login(self.member_user)
        self.assertEqual(member.get(subscribe_url(self.retiro) + "?listo=1").content, anonymous)
        self.ask(self.member_user.email or ADDRESS, client=member)
        self.assertEqual((Person.objects.count(), EventRegistration.objects.count()), (people, registrations))
        fields = {field.name for field in EventSubscription._meta.get_fields()}
        self.assertFalse(fields & {"person", "user", "registration", "name", "phone"})


class NoOracleTests(SubscriptionFixture):
    def answer(self, address=ADDRESS):
        first = self.ask(address)
        return first.status_code, first["Location"], self.client.get(first["Location"]).content

    def test_the_answer_is_the_same_whatever_the_address_was(self):
        """Defect: the form telling whether somebody is subscribed to a youth event."""
        new = self.answer()
        self.assertEqual(new[0], 302)
        self.assertEqual(new[1], subscribe_url(self.retiro) + "?listo=1")
        self.assertEqual(mail.outbox, [])  # nothing is handed over inside the request: no clock to read either
        asked_twice = self.answer()
        subscriptions.confirm(self.row())
        already_confirmed = self.answer()
        with mock.patch.object(subscriptions, "PENDING_PER_EVENT", 1):  # the event's ceiling of unconfirmed rows
            subscriptions.request(self.retiro, OTHER)
            over_the_limit = self.answer("tercera@demo.invalid")
        self.assertEqual(new, asked_twice)
        self.assertEqual(new, already_confirmed)
        self.assertEqual(new, over_the_limit)
        self.assertFalse(EventSubscription.objects.filter(email="tercera@demo.invalid").exists())
        self.assertNotIn(ADDRESS.encode(), new[2])
        self.assertNotIn(b"demo.invalid", new[2])

    def test_one_row_and_one_confirmation_e_mail_however_often_it_is_asked(self):
        """Defect: the form used to fill somebody's inbox."""
        for spelling in (ADDRESS, ADDRESS.upper(), f"  {ADDRESS} "):
            self.ask(spelling)
        self.assertEqual(EventSubscription.objects.count(), 1)
        self.run_pass()
        self.ask()
        self.run_pass()
        with self.later(hours=30):  # more than a day later the same request is still the same row: no second e-mail
            self.ask()
            self.run_pass()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [ADDRESS])

    def test_after_its_48_hours_an_unconfirmed_request_can_be_made_again(self):
        self.ask()
        self.run_pass()
        old_key = self.row().key
        with self.later(hours=49):
            self.ask()
            self.assertNotEqual(self.row().key, old_key)
            self.run_pass()
        self.assertEqual(len(mail.outbox), 2)

    def test_an_address_gets_few_confirmations_across_events(self):
        """Defect: one request per event of every church adds up to a flood."""
        events = [self.retiro, self.retiro_b]
        for number in range(3):
            event = self._future(self.church, f"Evento {number}", f"evento-{number}", 4, 4)
            services.save_page(event, self.admin, published=True, public=True, notice_mail=True)
            events.append(event)
        for event in events:
            subscriptions.request(event, ADDRESS)
        self.assertEqual(EventSubscription.objects.count(), subscriptions.ADDRESS_REQUESTS_PER_DAY)
        self.run_pass()
        self.assertEqual(len(mail.outbox), subscriptions.ADDRESS_REQUESTS_PER_DAY)

    def test_an_event_holds_a_bounded_number_of_unconfirmed_rows(self):
        """The hard ceiling: over it an unknown address is not stored. Nothing is counted per hour at the form."""
        with mock.patch.object(subscriptions, "PENDING_PER_EVENT", 5):
            for number in range(9):
                subscriptions.request(self.retiro, f"persona{number}@demo.invalid")
            self.assertEqual(EventSubscription.objects.filter(event=self.retiro).count(), 5)
            subscriptions.request(self.retiro_b, ADDRESS)  # another event has its own
            subscriptions.confirm(self.row("persona0@demo.invalid"))  # a confirmed row makes room
            subscriptions.request(self.retiro, "nueva@demo.invalid")
        self.assertEqual(EventSubscription.objects.filter(event=self.retiro).count(), 6)
        self.assertEqual(EventSubscription.objects.filter(event=self.retiro_b).count(), 1)
        self.assertFalse(AuthThrottle.objects.exists())

    def test_spellings_of_one_inbox_are_one_address_for_the_limits(self):
        """Defect (review, reproduced): ``victima+0…29@`` made thirty rows and thirty e-mails in one pass."""
        for number in range(30):
            self.ask(f"victima+{number}@demo.invalid")
        self.ask("Victima@demo.invalid")
        self.run_pass()
        self.assertEqual([message.to for message in mail.outbox], [["victima+0@demo.invalid"]])  # one row per inbox
        self.assertEqual(EventSubscription.objects.count(), 1)
        key = subscriptions.limit_key
        self.assertEqual(key(" Ana.Ruiz+retiro@Demo.Invalid "), "ana.ruiz@demo.invalid")  # dots kept: unknown provider
        self.assertEqual(key("ana.ruiz+x@googlemail.com"), key("anaruiz@gmail.com"))  # the one provider known to ignore them
        self.assertNotEqual(subscriptions.limit_hash(ADDRESS), subscriptions.address_hash(ADDRESS))
        # What is stored for the limits is a hash: no address, no part of one.
        stored = json.dumps(list(SubscriptionSuppression.objects.values()), default=str)
        self.assertNotIn("victima", stored)
        self.assertNotIn("demo", stored)

    def test_a_hundred_families_on_one_provider_the_same_evening_lose_nobody(self):
        """Defect (product correction): a cap per mail provider at the form dropped ninety parents of a camp.
        Every request is stored; the confirmations leave at a pace, oldest first, and each link works 48 hours
        from the moment it was SENT."""
        for number in range(100):
            self.ask(f"familia{number:03}@demo.invalid")
        self.assertEqual(EventSubscription.objects.count(), 100)
        sent = [self.run_pass()["confirmations"] for _ in range(3)]
        self.assertEqual(sent, [subscriptions.CONFIRMATIONS_PER_PASS, subscriptions.DOMAIN_CONFIRMATIONS_PER_HOUR - subscriptions.CONFIRMATIONS_PER_PASS, 0])
        self.assertEqual([message.to[0] for message in mail.outbox[:2]], ["familia000@demo.invalid", "familia001@demo.invalid"])
        subscriptions.request(self.retiro, "otra@otra.demo.invalid")  # another provider is not held back by this one
        self.assertEqual(self.run_pass()["confirmations"], 1)
        with self.later(hours=1, minutes=1):
            self.assertEqual(self.run_pass()["confirmations"], 40)
            self.assertEqual(self.run_pass()["left"], 0)
        self.assertEqual(len(mail.outbox), 101)
        self.assertEqual(len({message.to[0] for message in mail.outbox}), 101)
        # Who waited in the queue for two days still gets a link that works, and 48 hours from then.
        subscriptions.request(self.retiro, "tarde@tercera.demo.invalid")
        with self.later(hours=60):
            self.assertEqual(subscriptions.delete_due()["unconfirmed"], 101)
            self.run_pass()
            path = self.link_in(mail.outbox[-1], "confirmar")
            self.assertEqual(mail.outbox[-1].to, ["tarde@tercera.demo.invalid"])
        with self.later(hours=60 + 47):
            self.assertEqual(self.client.get(path).status_code, 200)
        with self.later(days=subscriptions.QUEUE_DAYS + 1):  # and one never sent does not wait for ever
            subscriptions.request(self.retiro_b, OTHER)
        with self.later(days=2 * subscriptions.QUEUE_DAYS + 2):
            self.assertEqual(subscriptions.delete_due()["unconfirmed"], 2)

    def test_an_address_that_never_answers_is_not_asked_for_ever(self):
        """Defect (review, reproduced): a new request every 49 hours mailed an unconfirmed address without end."""
        for round_ in range(5):
            with self.later(hours=49 * round_):
                self.ask()
                self.run_pass()
        self.assertEqual(len(mail.outbox), subscriptions.UNANSWERED_PER_EVENT)
        first, last = mail.outbox
        self.assertIn("como mucho, le escribiremos una vez más a esta dirección por este evento", first.body)
        self.assertIn("no le escribiremos más a esta dirección por este evento", last.body)  # and it is true:
        with self.later(days=9):  # the day before the event, and the deletions have run: still none
            subscriptions.delete_due()
            self.ask()
            self.run_pass()
        self.assertEqual(len(mail.outbox), 2)
        # Across the church's events: four unanswered in thirty days, then none.
        for number in range(4):
            event = self._future(self.church, f"Evento {number}", f"evento-{number}", 4, 4)
            services.save_page(event, self.admin, published=True, public=True, notice_mail=True)
            with self.later(days=1 + number):
                subscriptions.request(event, ADDRESS)
        self.assertEqual(EventSubscription.objects.filter(email_hash=subscriptions.address_hash(ADDRESS)).count(), 2)

    def test_confirming_takes_the_request_off_the_count(self):
        self.ask()
        subscriptions.confirm(self.row())
        self.assertEqual(SubscriptionSuppression.objects.get().unanswered, 0)
        subscriptions.confirm(self.row())  # twice changes nothing
        self.assertEqual(SubscriptionSuppression.objects.get().unanswered, 0)

    def test_the_church_and_the_pass_have_a_ceiling_of_confirmations(self):
        """Defect: many addresses at once leaving the server as one burst. Over a ceiling they wait; none is lost."""
        for number in range(6):
            subscriptions.request(self.retiro, f"persona@casa{number}.demo.invalid")
        with mock.patch.object(subscriptions, "CONFIRMATIONS_PER_PASS", 4):
            self.assertEqual((self.run_pass()["confirmations"], len(mail.outbox)), (4, 4))
        with mock.patch.object(subscriptions, "CONFIRMATIONS_PER_CHURCH_PER_DAY", 5):
            counts = self.run_pass()
            self.assertEqual((counts["confirmations"], counts["left"]), (1, 1))
            self.assertEqual(EventSubscription.objects.filter(event=self.retiro).count(), 6)  # waiting, not dropped
            subscriptions.request(self.retiro_b, ADDRESS)  # another church has its own ceiling
            self.assertEqual(self.run_pass()["confirmations"], 1)
            with self.later(hours=25):
                self.assertEqual(self.run_pass()["confirmations"], 1)
        self.assertEqual(len(mail.outbox), 7)

    def test_repeating_a_known_address_blocks_nobody(self):
        """Defect (review, reproduced): 31 requests, even of one known address, closed an event's form for an hour."""
        self.ask()
        for _ in range(40):
            self.ask()
            self.ask("mama.de.prueba+otra@demo.invalid")  # another spelling of the same inbox: the same row
        self.ask(OTHER)
        self.assertTrue(EventSubscription.objects.filter(email=OTHER).exists())
        self.assertEqual(EventSubscription.objects.count(), 2)
        self.assertEqual(SubscriptionSuppression.objects.get(address_hash=subscriptions.limit_hash(ADDRESS)).unanswered, 1)

    def test_a_known_address_costs_the_form_about_what_a_new_one_does(self):
        """Defect: the form answering sooner for an address that is already there. Both do the same reads and
        write; a new one writes its row and its count besides."""
        self.ask()
        self.ask(OTHER)
        with CaptureQueriesContext(connection) as known:
            subscriptions.request(self.retiro, ADDRESS)
        with CaptureQueriesContext(connection) as new:
            subscriptions.request(self.retiro, "tercera@demo.invalid")

        def kinds(queries, word):
            return sum(1 for query in queries if query["sql"].startswith(word))

        self.assertEqual(kinds(known, "SELECT"), kinds(new, "SELECT"))  # the same reads, whoever asks
        self.assertEqual(kinds(known, "UPDATE"), 1)
        self.assertLessEqual(len(new) - len(known), 4)  # its row, its count, and the savepoint around them

    def test_the_same_address_twice_at_once_is_one_row(self):
        subscriptions.request(self.retiro, ADDRESS)
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventSubscription.objects.create(event=self.retiro, email=ADDRESS, email_hash=subscriptions.address_hash(ADDRESS), key="otra")


class ConfirmTests(SubscriptionFixture):
    def setUp(self):
        self.ask()
        self.run_pass()
        self.message = mail.outbox[0]
        self.path = self.link_in(self.message, "confirmar")

    def test_the_confirmation_e_mail_says_which_event_and_nothing_about_anybody(self):
        self.assertEqual(self.message.subject, "Emmaus: confirma tu correo para los avisos de un evento")
        self.assertEqual(self.message.to, [ADDRESS])
        self.assertIn("«Retiro de jóvenes»", self.message.body)
        self.assertIn(self.church.name, self.message.body)
        self.assertIn("Nada más.", self.message.body)
        self.assertNotIn(ADDRESS, self.message.body + self.path)
        self.assertNotIn("List-Unsubscribe", self.message.extra_headers)

    def test_opening_the_link_confirms_nothing_and_pressing_the_button_does(self):
        """Defect: a mail scanner that follows links subscribing an address its owner never confirmed."""
        page = self.client.get(self.path)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Confirma tu correo")
        self.assertContains(page, "Retiro de jóvenes")
        self.assertNotContains(page, ADDRESS)
        self.assertIsNone(self.row().confirmed_at)
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.path).status_code, 403)
        self.assertIsNone(self.row().confirmed_at)
        done = self.client.post(self.path)
        self.assertContains(done, "Listo, tu correo quedó confirmado")
        self.assertNotContains(done, ADDRESS)
        self.assertIsNotNone(self.row().confirmed_at)

    def test_using_the_link_again_changes_nothing(self):
        self.client.post(self.path)
        first = self.row().confirmed_at
        with self.later(hours=1):
            self.assertEqual(self.client.post(self.path).status_code, 200)
        self.assertEqual(self.row().confirmed_at, first)
        self.run_pass()
        self.assertEqual(len(mail.outbox), 1)  # and no second confirmation e-mail

    def test_an_expired_link_confirms_nothing(self):
        with mock.patch("django.core.signing.time.time", return_value=time.time() + 49 * 3600):
            self.assertEqual(self.client.get(self.path).status_code, 404)
            self.assertEqual(self.client.post(self.path).status_code, 404)
        self.assertIsNone(self.row().confirmed_at)
        # The row's own age counts too, whatever the link says, and so for a copy read before it expired.
        stale = self.row()
        EventSubscription.objects.filter(pk=stale.pk).update(confirm_last_attempt_at=timezone.now() - timedelta(hours=49))
        self.assertEqual(self.client.post(self.path).status_code, 404)
        subscriptions.confirm(stale)
        self.assertIsNone(self.row().confirmed_at)

    def test_a_tampered_link_or_one_of_the_other_kind_gets_the_one_answer(self):
        token = self.path.rstrip("/").rsplit("/", 1)[1]
        changed = token[:-1] + ("A" if token[-1] != "A" else "B")
        other_kind = subscriptions.unsubscribe_token(self.row())
        answers = set()
        for bad in (changed, other_kind, "x", token.split(":")[0], "a:b:c"):
            page = self.client.post(reverse("evento:subscription_confirm", args=[bad]))
            self.assertEqual(page.status_code, 404)
            self.assertContains(page, "Este enlace ya no sirve", status_code=404)
            answers.add(page.content)
        self.assertEqual(len(answers), 1)
        self.assertIsNone(self.row().confirmed_at)

    def test_the_link_stops_working_when_the_event_stops_taking_subscriptions(self):
        self.set_page(notice_mail=False)
        self.assertEqual(self.client.post(self.path).status_code, 404)
        self.assertIsNone(self.row().confirmed_at)


class UnsubscribeTests(SubscriptionFixture):
    def setUp(self):
        self.mine = self.confirmed()
        self.path = public.unsubscribe_path(subscriptions.unsubscribe_token(self.mine))

    def test_the_link_shows_a_button_and_only_the_button_deletes(self):
        page = self.client.get(self.path)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "¿Quieres dejar de recibir estos avisos?")
        self.assertContains(page, "Retiro de jóvenes")
        self.assertNotContains(page, ADDRESS)
        self.assertEqual(dict(page.cookies), {})
        self.assertTrue(EventSubscription.objects.filter(pk=self.mine.pk).exists())

    def test_one_click_from_the_mail_program_deletes_the_address_at_once(self):
        """Defect: an unsubscribe that needs a cookie never works from the mail program's own button (RFC 8058)."""
        EventNoticeEmail.objects.create(notice=self.publish(), subscription=self.mine, outcome=MailOutcome.SENT, attempts=1)
        done = Client(enforce_csrf_checks=True).post(self.path, {"List-Unsubscribe": "One-Click"})
        self.assertEqual(done.status_code, 200)
        self.assertContains(done, "Listo, te dimos de baja")
        self.assertFalse(EventSubscription.objects.exists())
        self.assertFalse(EventNoticeEmail.objects.exists())
        self.assertNotIn(ADDRESS, json.dumps(list(EventSubscription.objects.values()), default=str))
        again = self.client.post(self.path)
        self.assertEqual(again.status_code, 404)
        self.assertContains(again, "Este enlace ya no sirve", status_code=404)

    def test_a_tampered_link_or_one_of_the_other_kind_deletes_nothing(self):
        token = subscriptions.unsubscribe_token(self.mine)
        for bad in (token[:-1] + ("A" if token[-1] != "A" else "B"), subscriptions.confirm_token(self.mine), "x", "a:b:c"):
            self.assertEqual(self.client.post(public.unsubscribe_path(bad)).status_code, 404)
        self.assertTrue(EventSubscription.objects.filter(pk=self.mine.pk).exists())

    def test_the_link_of_one_event_leaves_the_same_address_in_another(self):
        other = self.confirmed(event=self.retiro_b)
        self.client.post(self.path)
        self.assertEqual(list(EventSubscription.objects.values_list("pk", flat=True)), [other.pk])

    def test_it_still_works_when_the_page_is_no_longer_public_and_then_names_no_event(self):
        """Defect: an address that cannot be taken off because the event was hidden."""
        self.set_page(public=False, notice_mail=False)
        page = self.client.get(self.path)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Retiro de jóvenes")
        self.assertContains(self.client.post(self.path), "Listo, te dimos de baja")
        self.assertFalse(EventSubscription.objects.exists())


class SendingTests(SubscriptionFixture):
    def notice_mails(self):
        return [message for message in mail.outbox if message.subject == subscription_mail.SUBJECTS[subscription_mail.NOTICE]]

    def test_nobody_is_mailed_about_a_notice_before_confirming(self):
        self.ask()
        self.publish()
        self.run_pass()
        self.assertEqual(self.notice_mails(), [])
        self.assertEqual(len(mail.outbox), 1)  # only the e-mail that asks to confirm

    def test_one_e_mail_per_notice_that_says_there_is_one_and_never_what_it_says(self):
        row = self.confirmed()
        notice = self.publish()
        self.assertTrue(notice.mail_subscribers)
        counts = self.run_pass()
        self.assertEqual((counts["sent"], counts["failed"], counts["left"]), (1, 0, 0))
        message, = self.notice_mails()
        self.assertEqual(message.to, [ADDRESS])
        self.assertEqual(message.subject, "Emmaus: hay un aviso nuevo de un evento")
        self.assertIn("«Retiro de jóvenes»", message.body)
        self.assertIn(f"{BASE}/evento/retiro-jovenes/#avisos", message.body)
        for secret in (TITLE, "camión", "chamarra", *BODY.splitlines(), ADDRESS):
            self.assertNotIn(secret, message.body + message.subject)
        unsubscribe = BASE + self.link_in(message, "baja")
        self.assertEqual(message.extra_headers, {"List-Unsubscribe": f"<{unsubscribe}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
        self.assertEqual(subscriptions.for_unsubscribe(unsubscribe.rstrip("/").rsplit("/", 1)[1]), row)
        self.run_pass()
        self.run_pass()
        self.assertEqual(len(self.notice_mails()), 1)
        self.assertEqual(EventNoticeEmail.objects.get().outcome, MailOutcome.SENT)

    def test_an_edit_and_a_retire_send_nothing(self):
        self.confirmed()
        notice = self.publish()
        self.run_pass()
        services.save_public_notice(self.retiro, self.admin, notice, title="Sale a las 8:00", body="Cambio de hora.")
        self.run_pass()
        self.assertEqual(len(self.notice_mails()), 1)
        second = self.publish("Otro aviso", "Texto.")
        services.retire_public_notice(second, self.admin)
        self.run_pass()
        self.assertEqual(len(self.notice_mails()), 1)

    def test_who_confirms_after_a_notice_is_not_mailed_about_it(self):
        self.publish()
        self.confirmed(minutes_ago=-1)
        self.run_pass()
        self.assertEqual(self.notice_mails(), [])

    def test_two_passes_at_once_send_one_e_mail(self):
        """Defect: two schedulers (or a retry of a hung one) mailing everybody twice. The second pass runs
        while the first is handing its e-mail over: the row the first wrote is what stops it."""
        self.confirmed()
        self.confirmed(OTHER)
        notice = self.publish()
        real, nested = subscription_mail.send, []

        def send_while_another_pass_runs(*args, **kwargs):
            if not nested:
                nested.append(None)
                nested[0] = subscriptions.send_due(BASE)
            return real(*args, **kwargs)

        with mock.patch("agenda.subscription_mail.send", side_effect=send_while_another_pass_runs):
            outer = subscriptions.send_due(BASE)
        self.assertEqual(outer["sent"] + nested[0]["sent"], 2)
        self.assertEqual(sorted(message.to[0] for message in self.notice_mails()), sorted([ADDRESS, OTHER]))
        # The constraint itself, and the claim that relies on it.
        row = EventNoticeEmail.objects.first()
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventNoticeEmail.objects.create(notice=notice, subscription_id=row.subscription_id, attempts=1)
        self.assertEqual(subscriptions._claim_notice(notice, row.subscription_id), 0)

    def test_two_passes_at_once_send_one_confirmation(self):
        self.ask()
        stale = self.row()
        self.assertEqual(subscriptions._claim_confirmation(stale), subscriptions.FIRST_ATTEMPT)
        self.assertEqual(subscriptions._claim_confirmation(stale), 0)  # a copy read before the first claim

    def test_nothing_is_sent_while_any_condition_fails_and_it_goes_out_when_it_holds_again(self):
        """Defect: mailing for an event that is hidden, switched off, or of a church that left the module."""
        self.confirmed()
        self.publish()
        cases = {
            "option off": lambda: self.set_page(notice_mail=False),
            "not public": lambda: self.set_page(public=False),
            "not published": lambda: self.set_page(published=False),
            "module off": lambda: flags.set_enabled(self.church, "community", False),
            "ended": lambda: self.move(self.retiro, -3, -1),
            "church inactive": lambda: Church.objects.filter(pk=self.church.pk).update(active=False),
            "event archived": lambda: Event.objects.filter(pk=self.retiro.pk).update(status=Event.Status.ARCHIVED),
        }
        for name, change in cases.items():
            with self.subTest(name), transaction.atomic():
                change()
                self.run_pass()
                self.assertEqual(mail.outbox, [])
                self.assertFalse(EventNoticeEmail.objects.exists())
                transaction.set_rollback(True)
        for name, setting in {"no address": {"EMMAUS_PUBLIC_BASE_URL": ""}, "no e-mail": {"EMAIL_BACKEND": CONSOLE},
                              "kill switch": {"EMMAUS_V2_MODULES": False}}.items():
            with self.subTest(name), self.settings(**setting):
                counts = subscriptions.run_pass()
                self.assertEqual(mail.outbox, [])
                self.assertFalse(EventNoticeEmail.objects.exists())
                self.assertEqual(counts["not_configured"], name != "kill switch")
        self.assertEqual(subscriptions.run_pass()["sent"], 1)

    def test_a_notice_published_while_nothing_could_be_sent_is_never_mailed(self):
        """Defect: the screen said "nobody is mailed", and days later an e-mail goes out for that notice."""
        self.confirmed()
        self.set_page(notice_mail=False)
        silent = self.publish()
        with self.settings(EMMAUS_PUBLIC_BASE_URL=""):
            self.set_page(notice_mail=True)
            also_silent = self.publish("Sin dirección", "Texto.")
        self.assertFalse(silent.mail_subscribers or also_silent.mail_subscribers)
        self.run_pass()
        self.assertEqual(mail.outbox, [])

    def test_an_old_notice_is_not_mailed(self):
        self.confirmed(minutes_ago=60 * 24 * 5)
        self.publish(at=timezone.now() - timedelta(days=subscriptions.WINDOW_DAYS, minutes=1))
        self.run_pass()
        self.assertEqual(mail.outbox, [])

    def test_another_church_s_subscribers_are_not_mailed(self):
        self.confirmed()
        self.confirmed(OTHER, event=self.retiro_b)
        self.publish()
        self.run_pass()
        self.assertEqual([message.to for message in mail.outbox], [[ADDRESS]])

    @override_settings(EMAIL_BACKEND="agenda.tests.test_subscriptions.RefusingBackend")
    def test_a_refused_e_mail_waits_is_tried_again_and_is_given_up_and_the_address_reaches_no_log(self):
        self.confirmed()
        self.publish()
        with self.assertLogs("agenda", "ERROR") as logs:
            counts = self.run_pass()
        self.assertEqual((counts["sent"], counts["failed"], counts["first_failed"]), (0, 1, 1))
        self.assertEqual(subscriptions.verdict(counts), subscriptions.ALL_SENDS_FAILED)
        row = EventNoticeEmail.objects.get()
        self.assertEqual((row.outcome, row.attempts), (MailOutcome.FAILED, 1))
        with self.assertNoLogs("agenda", "ERROR"):
            self.assertEqual(self.run_pass()["failed"], 0)  # not again at once
        moment = timedelta()
        for attempt, minutes in enumerate(subscriptions.BACKOFF_MINUTES, start=2):
            moment += timedelta(minutes=minutes + 1)
            with mock.patch("django.utils.timezone.now", return_value=timezone.now() + moment), self.assertLogs("agenda", "ERROR") as more:
                self.run_pass()
            logs.output.extend(more.output)
            row.refresh_from_db()
            self.assertEqual(row.attempts, attempt)
        with mock.patch("django.utils.timezone.now", return_value=timezone.now() + moment + timedelta(days=1)):
            self.assertEqual(self.run_pass()["failed"], 0)  # given up
        row.refresh_from_db()
        self.assertEqual(row.attempts, subscriptions.MAX_ATTEMPTS)
        printed = "\n".join(logs.output)
        self.assertIn("SMTPRecipientsRefused", printed)
        self.assertNotIn("demo.invalid", printed)
        self.assertNotIn("mama", printed)

    @override_settings(EMAIL_BACKEND="agenda.tests.test_subscriptions.RefusingBackend")
    def test_a_confirmation_that_is_refused_is_tried_again_later(self):
        self.ask()
        with self.assertLogs("agenda", "ERROR"):
            self.run_pass()
        row = self.row()
        self.assertEqual((row.confirm_outcome, row.confirm_attempts), (MailOutcome.FAILED, 1))
        self.run_pass()
        self.assertEqual(self.row().confirm_attempts, 1)
        with self.later(minutes=6), self.assertLogs("agenda", "ERROR"):
            self.run_pass()
        self.assertEqual(self.row().confirm_attempts, 2)

    def test_a_sender_that_died_is_never_taken_over(self):
        """Defect (review): a crash between handing the e-mail over and writing how it went, and a second copy
        later. The attempt is written first, and a claim nobody finished is never taken again: at most once."""
        row = self.confirmed()
        notice = self.publish()
        self.assertEqual(subscriptions._claim_notice(notice, row.pk), subscriptions.FIRST_ATTEMPT)  # and then it died
        claim = EventNoticeEmail.objects.get()
        self.assertEqual((claim.outcome, claim.attempts), (MailOutcome.PENDING, 1))
        for wait in ({"minutes": 1}, {"minutes": 11}, {"hours": 13}, {"days": 2}):
            with self.later(**wait):
                self.run_pass()
        self.assertEqual(mail.outbox, [])
        self.ask(OTHER)
        self.assertEqual(subscriptions._claim_confirmation(self.row(OTHER)), subscriptions.FIRST_ATTEMPT)  # the same
        with self.later(hours=1):
            self.run_pass()
        self.assertEqual(mail.outbox, [])

    def test_the_whole_journey_leaves_the_address_in_no_log_no_audit_row_and_no_page(self):
        """Defect: the address copied to where somebody else reads it."""
        audit_rows = ChurchAuditEvent.objects.count()
        out = StringIO()
        with self.assertLogs("emmaus.scheduler", "DEBUG") as logs, self.assertNoLogs("agenda", "DEBUG"), \
                self.assertNoLogs("core", "DEBUG"), self.assertNoLogs("django", "WARNING"):
            self.ask()
            call_command("run_scheduled_jobs", stdout=out)
            confirm = self.link_in(mail.outbox[0], "confirmar")
            pages = [self.client.get(confirm), self.client.post(confirm)]
            EventSubscription.objects.update(confirmed_at=timezone.now() - timedelta(minutes=1))
            self.publish()
            call_command("run_scheduled_jobs", stdout=out)
            leave = self.link_in(mail.outbox[-1], "baja")
            self.client.force_login(self.admin)
            pages += [self.client.get(self.url("public_notices")), self.client.get(self.url("page_edit"))]
            self.client.logout()
            pages += [self.client.get(public_url(self.retiro)), self.client.get(leave), self.client.post(leave)]
        self.assertEqual(len(mail.outbox), 2)
        self.assertFalse(EventSubscription.objects.exists())
        everything = "\n".join(logs.output) + out.getvalue() + "".join(page.content.decode() for page in pages)
        everything += json.dumps(list(ChurchAuditEvent.objects.values()), default=str)
        for trace in (ADDRESS, "mama.de.prueba", subscriptions.address_hash(ADDRESS)):
            self.assertNotIn(trace, everything)
        # Subscribing, confirming and leaving write no audit row at all; publishing wrote its own.
        self.assertEqual(ChurchAuditEvent.objects.count(), audit_rows + 1)
        self.assertIn("suscripciones de eventos: 0 direcciones borradas, 1 correos enviados, 0 fallidos, 0 pendientes", out.getvalue())


class EndingTests(SubscriptionFixture):
    def test_an_unconfirmed_request_is_deleted_48_hours_after_its_e_mail_was_sent(self):
        self.ask()
        self.run_pass()
        self.confirmed(OTHER)
        with self.later(hours=47):
            self.assertEqual(subscriptions.delete_due(), {"unconfirmed": 0, "expired": 0, "limits": 0})
        with self.later(hours=49):
            self.assertEqual(subscriptions.delete_due(), {"unconfirmed": 1, "expired": 0, "limits": 0})
        self.assertEqual(SubscriptionSuppression.objects.count(), 2)  # the counts stay: that is their point
        self.assertEqual([row.email for row in EventSubscription.objects.all()], [OTHER])

    def test_every_address_is_deleted_14_days_after_the_event_ends(self):
        self.confirmed()
        self.confirmed(OTHER, event=self.retiro_b)
        EventNoticeEmail.objects.create(notice=self.publish(), subscription=self.row(), attempts=1)
        self.move(self.retiro, -20, -subscriptions.RETENTION_DAYS)
        self.assertEqual(subscriptions.delete_due()["expired"], 0)  # the fourteenth day is still inside
        self.move(self.retiro, -20, -subscriptions.RETENTION_DAYS - 1)
        self.assertEqual(subscriptions.delete_due()["expired"], 1)
        self.assertEqual([row.email for row in EventSubscription.objects.all()], [OTHER])
        self.assertFalse(EventNoticeEmail.objects.exists())
        # An event of one day, and one that lost its dates.
        Event.objects.filter(pk=self.retiro_b.pk).update(starts_on=self.today - timedelta(days=15), ends_on=None)
        self.assertEqual(subscriptions.delete_due()["expired"], 1)
        self.confirmed()
        Event.objects.filter(pk=self.retiro.pk).update(starts_on=None, ends_on=None)
        self.assertEqual(subscriptions.delete_due()["expired"], 0)
        with self.later(days=15):
            self.assertEqual(subscriptions.delete_due()["expired"], 1)

    def test_the_deletions_run_whatever_the_flags_and_without_e_mail(self):
        """Defect: addresses kept for ever in a church that stopped using the module, or on a server without e-mail."""
        self.ask()
        EventSubscription.objects.update(confirm_attempts=1, confirm_last_attempt_at=timezone.now() - timedelta(hours=49))
        flags.set_enabled(self.church, "community", False)
        with self.settings(EMMAUS_PUBLIC_BASE_URL="", EMAIL_BACKEND=CONSOLE):
            counts = subscriptions.run_pass()
        self.assertEqual((counts["unconfirmed"], counts["not_configured"]), (1, True))
        self.assertFalse(EventSubscription.objects.exists())

    def test_the_deletions_are_a_function_of_their_own_that_reads_no_switch(self):
        """Defect (review, reproduced): with the community jobs switched off nothing deleted the addresses.
        ``delete_due`` takes no flag and no setting, so whoever schedules it runs it always."""
        self.ask()
        EventSubscription.objects.update(confirm_attempts=1, confirm_last_attempt_at=timezone.now() - timedelta(hours=49))
        self.confirmed(OTHER, event=self.retiro_b)
        self.move(self.retiro_b, -40, -30)
        off = {"EMMAUS_SCHEDULED_COMMUNITY_JOBS": False, "EMMAUS_V2_MODULES": False, "EMMAUS_PUBLIC_BASE_URL": "", "EMAIL_BACKEND": CONSOLE}
        with self.settings(**off), mock.patch("agenda.subscriptions.send_due") as send:
            self.assertEqual(subscriptions.delete_due(), {"unconfirmed": 1, "expired": 1, "limits": 1})
        send.assert_not_called()
        self.assertFalse(EventSubscription.objects.exists())
        self.assertEqual(SubscriptionSuppression.objects.count(), 1)  # of the event that is not over

    def test_deleting_the_event_deletes_its_addresses(self):
        self.confirmed()
        self.retiro.delete()
        self.assertFalse(EventSubscription.objects.exists())
        self.assertFalse(SubscriptionSuppression.objects.exists())


class StaffTests(SubscriptionFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def notices_page(self):
        return self.client.get(self.url("public_notices"))

    def test_only_the_administration_of_the_church_turns_it_on(self):
        self.set_page(notice_mail=False)
        page = self.client.get(self.url("page_edit"))
        self.assertContains(page, "Avisos por correo para quien no tiene cuenta")
        self.assertContains(page, "Nadie ve la lista de direcciones")
        self.assertContains(page, "se conservan sin recibir correos")
        data = {"published": "on", "public": "on", "notice_mail": "on", "registration": "abierta", "description": "x"}
        for user in (None, self.member_user, self.admin_b):
            client = Client()
            if user:
                client.force_login(user)
            self.assertIn(client.post(self.url("page_edit"), data).status_code, (302, 403, 404))
            self.assertFalse(EventPage.objects.get(event=self.retiro).notice_mail)
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.url("page_edit"), data).status_code, 403)
        self.assertEqual(self.client.post(self.url("page_edit"), data).status_code, 302)
        self.assertTrue(EventPage.objects.get(event=self.retiro).notice_mail)

    def test_with_the_option_off_the_screen_says_what_it_always_said(self):
        self.set_page(notice_mail=False)
        page = self.notices_page()
        self.assertContains(page, "Emmaus no le envía nada a nadie: el aviso solo queda en la página del evento")
        self.assertContains(page, "nadie recibe un mensaje")
        self.assertNotContains(page, "correo")
        self.client.post(self.url("public_notices"), {"title": TITLE, "body": BODY})
        self.assertFalse(self.retiro.public_notices.get().mail_subscribers)

    def test_it_says_what_publishing_does_and_counts_only_from_the_minimum_up(self):
        """Defect: "2 direcciones suscritas" on a small event says whose parents they are."""
        for number in range(4):
            self.confirmed(f"persona{number}@demo.invalid")
        page = self.notices_page()
        self.assertContains(page, "Al publicar un aviso, Emmaus envía un correo a quien dejó y confirmó su correo")
        self.assertContains(page, "menos de 5 direcciones suscritas (puede no haber ninguna)")
        self.assertNotContains(page, "4 direcciones")
        self.assertNotContains(page, "Emmaus no le envía nada a nadie")
        self.assertNotContains(page, "nadie recibe un mensaje")
        self.confirmed("quinta@demo.invalid")
        subscriptions.request(self.retiro, "sin.confirmar@demo.invalid")  # an unconfirmed row is not counted
        page = self.notices_page()
        self.assertContains(page, "5 o más direcciones suscritas", count=2)
        self.assertNotContains(page, "demo.invalid")
        for number in range(4):  # one more, or four more, reads the same; the tenth changes the step
            self.confirmed(f"mas{number}@demo.invalid")
            self.assertContains(self.notices_page(), "5 o más direcciones suscritas", count=2)
        self.confirmed("decima@otra.demo.invalid")
        self.assertContains(self.notices_page(), "10 o más direcciones suscritas", count=2)
        ChurchPolicy.objects.update_or_create(church=self.church, defaults={"min_reporting_size": 8})
        self.assertContains(self.notices_page(), "10 o más direcciones suscritas")
        self.assertEqual(subscriptions._stepped(9, 8), 8)  # never a step under the minimum
        ChurchPolicy.objects.update_or_create(church=self.church, defaults={"min_reporting_size": 12})
        self.assertContains(self.notices_page(), "menos de 12 direcciones suscritas")

    def test_publishing_says_whether_an_e_mail_follows(self):
        self.confirmed()
        with self.assertLogs("emmaus.scheduler"):
            call_command("run_scheduled_jobs", stdout=StringIO())
        done = self.client.post(self.url("public_notices"), {"title": TITLE, "body": BODY}, follow=True)
        self.assertContains(done, "El correo a las direcciones suscritas sale en los próximos minutos.")
        self.assertContains(done, "Por este aviso salieron menos de 5 correos (puede que ninguno).")
        with self.later(minutes=30):  # nothing has run the pass for a while
            late = self.client.post(self.url("public_notices"), {"title": "Otro", "body": "Texto."}, follow=True)
            self.assertContains(late, "sale cuando el envío programado del servidor vuelva a correr")
            self.assertContains(late, "El envío programado del servidor no ha terminado bien")

    def test_it_says_plainly_when_nothing_will_be_sent(self):
        self.confirmed()
        for setting, words in (({"EMMAUS_PUBLIC_BASE_URL": ""}, "falta configurar la dirección pública de Emmaus en el servidor"),
                               ({"EMAIL_BACKEND": CONSOLE}, "no hay correo configurado"),
                               ({"EMMAUS_SCHEDULED_COMMUNITY_JOBS": False}, "los envíos programados están apagados en este servidor")):
            with self.subTest(words), self.settings(**setting):
                page = self.notices_page()
                self.assertContains(page, f"no se envía ningún correo: {words}.")
                self.assertContains(page, "nadie recibe un mensaje")
                self.assertNotContains(page, "Al publicar un aviso, Emmaus envía")
                done = self.client.post(self.url("public_notices"), {"title": TITLE, "body": BODY}, follow=True)
                self.assertContains(done, "Publicaste el aviso.")
                self.assertNotContains(done, "direcciones suscritas sale")
        self.set_page(public=False)
        page = self.notices_page()
        self.assertContains(page, "mientras la página no sea pública no se envía ningún correo")
        self.assertContains(page, "se conservan sin recibir nada hasta que se borran solas, 14 días después")
        self.assertFalse(self.retiro.public_notices.filter(mail_subscribers=True).exists())

    def test_the_count_of_e_mails_of_a_notice_is_said_only_from_the_minimum_up(self):
        rows = [self.confirmed(f"persona{number}@demo.invalid") for number in range(5)]
        notice = self.publish()
        for row in rows[:4]:
            EventNoticeEmail.objects.create(notice=notice, subscription=row, outcome=MailOutcome.SENT, attempts=1)
        self.assertContains(self.notices_page(), "Por este aviso salieron menos de 5 correos (puede que ninguno).")
        EventNoticeEmail.objects.create(notice=notice, subscription=rows[4], outcome=MailOutcome.FAILED, attempts=1)
        # Under the minimum a failure is not mentioned: "some failed" would say that there is somebody.
        page = self.notices_page()
        self.assertContains(page, "menos de 5 correos (puede que ninguno).")
        self.assertNotContains(page, "no se pudieron enviar")
        EventNoticeEmail.objects.update(outcome=MailOutcome.SENT)
        self.assertContains(self.notices_page(), "Salieron 5 o más correos por este aviso.")
        extra = [self.confirmed(f"mas{number}@otra.demo.invalid") for number in range(2)]
        EventNoticeEmail.objects.create(notice=notice, subscription=extra[0], outcome=MailOutcome.SENT, attempts=1)
        EventNoticeEmail.objects.create(notice=notice, subscription=extra[1], outcome=MailOutcome.FAILED, attempts=1)
        self.assertContains(self.notices_page(), "Salieron 5 o más correos por este aviso. Algunos correos no se pudieron enviar.")

    def test_the_addresses_have_no_admin_page(self):
        from django.contrib import admin

        self.assertNotIn(EventSubscription, admin.site._registry)
        self.assertNotIn(EventNoticeEmail, admin.site._registry)
        self.assertNotIn(ADDRESS, str(self.confirmed()))


class MailRulesTests(SubscriptionFixture):
    CONTEXT = {"church_name": "Iglesia Central", "event_name": "Retiro", "link": f"{BASE}/evento/retiro/#avisos",
               "unsubscribe_link": f"{BASE}/evento/avisos-por-correo/baja/x/"}

    def test_an_e_mail_carries_only_its_four_values(self):
        """Defect: a notice's title, or anything about a person, handed to the template by a later change."""
        for key in ("title", "body", "name", "email", "person"):
            with self.subTest(key), self.assertRaises(ValueError) as caught:
                subscription_mail.send(subscription_mail.NOTICE, ADDRESS, {**self.CONTEXT, key: "El camión sale a las 7:00"})
            self.assertNotIn("camión", str(caught.exception))
        confirm = {key: value for key, value in self.CONTEXT.items() if key != "unsubscribe_link"}
        with self.assertRaises(ValueError):
            subscription_mail.render(subscription_mail.CONFIRM, self.CONTEXT)  # no unsubscribe link in a confirmation
        for bad in ({"event_name": "Retiro\nBcc: otra@demo.invalid"}, {"event_name": " "}, {"event_name": "x" * 201},
                    {"link": "javascript:alert(1)"}, {"unsubscribe_link": "/evento/avisos-por-correo/baja/x/"}):
            with self.subTest(bad), self.assertRaises(ValueError):
                subscription_mail.render(subscription_mail.NOTICE, {**self.CONTEXT, **bad})
        with self.assertRaises(ValueError):
            subscription_mail.send(subscription_mail.CONFIRM, f"{ADDRESS}\nBcc: otra@demo.invalid", confirm)
        with self.assertRaises(ValueError):
            subscription_mail.render("otra_cosa", confirm)
        self.assertEqual(mail.outbox, [])

    def test_a_name_with_signs_is_written_as_typed(self):
        body = subscription_mail.render(subscription_mail.NOTICE, {**self.CONTEXT, "event_name": "Retiro & <campamento>"})[1]
        self.assertIn("«Retiro & <campamento>»", body)

    def test_the_hash_is_keyed_and_the_same_for_two_spellings(self):
        import hashlib

        self.assertEqual(subscriptions.address_hash(ADDRESS), subscriptions.address_hash(f" {ADDRESS.upper()} "))
        self.assertNotEqual(subscriptions.address_hash(ADDRESS), hashlib.sha256(ADDRESS.encode()).hexdigest())
        with self.settings(SECRET_KEY="otra-llave-de-prueba"):
            other = subscriptions.address_hash(ADDRESS)
        self.assertNotEqual(subscriptions.address_hash(ADDRESS), other)
