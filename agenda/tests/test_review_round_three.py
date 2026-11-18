"""Third review round, on synthetic data: one lock order for every write (the person, then the
page, then registration rows), and a new or duplicated event starts with the church's policy about
minors."""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from agenda import services
from agenda.duplicate import duplicate_simple_event
from agenda.models import EventPage
from agenda.simple import create_simple_event
from agenda.tests.test_event_extras import ExtrasFixture
from events.models import Event
from people import policy
from policies.models import ChurchPolicy


class LockOrderTests(ExtrasFixture):
    """SQLite takes no row locks, so what is checked is the order in which each write reads the rows
    it locks on PostgreSQL: never a registration before the person, never a registration before the page."""

    def setUp(self):
        self.start()
        self.guest = services.register_walkup(self.retiro, self.admin, first_name="Vera", last_name="Visita")

    def test_asking_somebody_to_accompany_locks_the_person_before_the_row(self):
        services.promote_guest(self.guest, self.admin)
        with CaptureQueriesContext(connection) as queries:
            services.ask_to_accompany(self.guest, self.admin)
        selects = [query["sql"] for query in queries.captured_queries if query["sql"].startswith("SELECT")]
        person_at = next(i for i, sql in enumerate(selects) if sql.split(" FROM ")[1].startswith('"people_person"'))
        full_row_at = next(i for i, sql in enumerate(selects)
                           if sql.split(" FROM ")[1].startswith('"agenda_eventregistration"') and "INNER JOIN" in sql)
        self.assertLess(person_at, full_row_at, selects)

    def test_recognizing_a_visitor_locks_the_person_before_the_row(self):
        with CaptureQueriesContext(connection) as queries:
            services.promote_guest(self.guest, self.admin, existing=self.no_account)
        selects = [query["sql"] for query in queries.captured_queries if query["sql"].startswith("SELECT")]
        person_at = next(i for i, sql in enumerate(selects) if sql.split(" FROM ")[1].startswith('"people_person"'))
        row_at = next(i for i, sql in enumerate(selects) if sql.split(" FROM ")[1].startswith('"agenda_eventregistration"'))
        self.assertLess(person_at, row_at, selects)

    def test_the_writes_that_take_the_page_take_it_before_any_row(self):
        row = services.register_walkup(self.retiro, self.admin, person=self.leader)
        other = services.register_walkup(self.retiro, self.admin, person=self.asked)
        self.move(self.retiro, 10, 12)
        for run in (lambda: services.cancel(row, self.admin), lambda: services.annul_walkup(other, self.admin, "error"),
                    lambda: services.register(self.retiro, self.member, self.member_user),
                    lambda: services.advance(self.retiro)):
            with CaptureQueriesContext(connection) as queries:
                run()
            selects = [query["sql"] for query in queries.captured_queries if query["sql"].startswith("SELECT")]
            page_at = next(i for i, sql in enumerate(selects) if sql.split(" FROM ")[1].startswith('"agenda_eventpage"'))
            row_at = next((i for i, sql in enumerate(selects) if sql.split(" FROM ")[1].startswith('"agenda_eventregistration"')), len(selects))
            self.assertLess(page_at, row_at, selects)


class PolicyDefaultTests(ExtrasFixture):
    def policy_on(self, church=None):
        church = church or self.church
        ChurchPolicy.objects.update_or_create(church=church, defaults={"minors_need_consent_by_default": True})
        policy.forget(church)
        self.assertTrue(policy.minors_consent_default(church))

    def new_event(self, church=None, **extra):
        fields = {"name": "Tarde de juegos", "slug": "tarde-de-juegos", "kind": Event.Kind.RETREAT, "starts_on": self.today, **extra}
        return create_simple_event(church or self.church, self.admin, **fields)

    def test_a_new_simple_event_starts_with_the_churchs_policy(self):
        without = self.new_event(slug="sin-politica")
        self.assertFalse(EventPage.objects.get(event=without).requires_guardian_consent)
        self.policy_on()
        event = self.new_event()
        self.assertTrue(EventPage.objects.get(event=event).requires_guardian_consent)
        # Another church's policy says nothing here.
        other = self.new_event(self.church_b, slug="tarde-b")
        self.assertFalse(EventPage.objects.get(event=other).requires_guardian_consent)

    def test_the_screen_that_creates_the_event_does_the_same(self):
        self.policy_on()
        self.client.force_login(self.admin)
        response = self.client.post(reverse("agenda:create"), {"name": "Noche de alabanza", "kind": Event.Kind.RETREAT,
                                                               "starts_on": self.today.isoformat(), "slug": "noche-de-alabanza"})
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["form"].errors)
        self.assertTrue(EventPage.objects.get(event__slug="noche-de-alabanza").requires_guardian_consent)

    def copy_of(self, source, slug):
        return duplicate_simple_event(source, self.admin, name="Copia", slug=slug, starts_on=None, ends_on=None).event

    def test_a_copy_takes_the_stricter_of_the_original_and_the_policy(self):
        simple = self.new_event(slug="original")
        self.assertFalse(EventPage.objects.get(event=self.copy_of(simple, "copia-1")).requires_guardian_consent)
        # The original asks, the policy does not: the copy asks.
        EventPage.objects.filter(event=simple).update(requires_guardian_consent=True)
        self.assertTrue(EventPage.objects.get(event=self.copy_of(simple, "copia-2")).requires_guardian_consent)
        # The original does not ask, the policy does: the copy asks.
        EventPage.objects.filter(event=simple).update(requires_guardian_consent=False)
        self.policy_on()
        self.assertTrue(EventPage.objects.get(event=self.copy_of(simple, "copia-3")).requires_guardian_consent)
        EventPage.objects.filter(event=simple).update(requires_guardian_consent=True)
        self.assertTrue(EventPage.objects.get(event=self.copy_of(simple, "copia-4")).requires_guardian_consent)
