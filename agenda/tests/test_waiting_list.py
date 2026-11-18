"""Decision 0011, on synthetic data: the waiting list OFFERS a freed place, with a deadline, and only
the person turns the offer into a registration, through the same ``standing`` as any sign-up. A row
of the list holds no place until it is offered, is never marked as attended, and nothing but the
person (or the deadline) moves it."""

from datetime import timedelta
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from agenda import services
from agenda.models import EventPage, EventRegistration
from agenda.tests.test_event_extras import CONSENT, ExtrasFixture
from people import policy
from people.models import ChurchAuditEvent

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
CONSOLE = "django.core.mail.backends.console.EmailBackend"
BASE = "https://emmaus.example.org/"
WAITING, OFFERED, CANCELLED, ACTIVE, PENDING = "en_espera", "ofrecida", "cancelada", "inscrita", "pendiente"
SUBJECT = "Emmaus: se liberó un lugar en un evento"


class WaitingFixture(ExtrasFixture):
    """The retreat with one place and a waiting list; the administration's person holds the place."""

    def fill(self, capacity=1, holders=None, **page):
        self.set_page(capacity=capacity, waiting_list=True, **page)
        holders = holders if holders is not None else [self.admin_person]
        return [services.register(self.retiro, person, self.user_of(person)) for person in holders]

    def user_of(self, person):
        return person.user or self.admin

    def join(self, person, event=None):
        return services.register(event or self.retiro, person, self.user_of(person))

    def state(self, row):
        row.refresh_from_db()
        return row.status

    def expire(self, row):
        EventRegistration.objects.filter(pk=row.pk).update(offer_expires_at=timezone.now() - timedelta(minutes=1))

    def assert_consistent(self, event=None, over=0):
        """What must hold after every step: no place is given twice, the list never waits beside a
        free place, and an offer always has its deadline."""
        event = event or self.retiro
        page = EventPage.objects.get(event=event)
        rows = EventRegistration.objects.filter(event=event)
        taking = rows.filter(status__in=(ACTIVE, PENDING, OFFERED)).count()
        self.assertLessEqual(taking, page.capacity + over)
        if rows.filter(status=WAITING).exists():
            self.assertGreaterEqual(taking, page.capacity)
        self.assertFalse(rows.filter(status=OFFERED, offer_expires_at__isnull=True).exists())
        self.assertFalse(rows.exclude(status=OFFERED).filter(offer_expires_at__isnull=False).exists())
        self.assertFalse(rows.filter(status__in=(WAITING, OFFERED)).exclude(attended__isnull=True).exists())
        people = list(rows.filter(person__isnull=False).values_list("person_id", flat=True))
        self.assertEqual(len(people), len(set(people)))


class JoinTests(WaitingFixture):
    def test_a_full_event_without_a_list_refuses_and_with_a_list_puts_the_person_in_line(self):
        self.fill()
        self.set_page(waiting_list=False)
        with self.assertRaises(services.AgendaError) as caught:
            self.join(self.leader)
        self.assertEqual(caught.exception.code, "full")
        self.set_page(waiting_list=True)
        row = self.join(self.leader)
        self.assertEqual((row.status, row.pending_reason, row.attended), (WAITING, "", None))
        self.assertIsNotNone(row.waiting_since)
        self.assertIsNone(row.offer_expires_at)
        self.assertEqual(services.taken(self.retiro), 1)  # who waits holds no place
        audit = ChurchAuditEvent.objects.filter(action="evento_lista_espera").get()
        self.assertEqual(audit.metadata, {"event_id": self.retiro.pk, "person_id": self.leader.pk})
        self.assert_consistent()

    def test_the_place_in_line_is_the_order_of_arrival_and_is_told_as_words(self):
        self.fill()
        first, second, third = self.join(self.leader), self.join(self.asked), self.join(self.member)
        self.assertEqual([services.waiting_position(row) for row in (first, second, third)], [1, 2, 3])
        self.assertEqual(services.position_words(1), "Eres la primera persona en la lista de espera.")
        self.assertEqual(services.position_words(3), "Eres la persona 3 en la lista de espera.")
        # Signing up again moves nothing: same row, same place in line.
        since = first.waiting_since
        again = self.join(self.leader)
        self.assertEqual((again.pk, again.status, again.waiting_since), (first.pk, WAITING, since))
        # Leaving and coming back is the end of the line, whatever the row's first day was.
        services.leave_waiting(first, self.leader_user)
        self.assertEqual(self.state(first), CANCELLED)
        self.assertEqual(first.waiting_outcome, "salio")
        back = self.join(self.leader)
        self.assertEqual(back.pk, first.pk)
        self.assertEqual([services.waiting_position(row) for row in (second, third, back)], [1, 2, 3])
        self.assertEqual(back.waiting_outcome, "")
        self.assert_consistent()

    def test_sign_up_closed_is_closed_for_the_list_too(self):
        self.fill()
        self.set_page(registration_closes_on=self.today - timedelta(days=1))
        with self.assertRaises(services.AgendaError) as caught:
            self.join(self.leader)
        self.assertEqual(caught.exception.code, "registration_over")

    def test_the_hours_of_an_offer_are_a_policy_with_a_floor(self):
        self.assertEqual(policy.waiting_offer_hours(self.church), 48)
        self.assertEqual(policy.waiting_offer_hours(), 48)
        with override_settings(EMMAUS_WAITING_OFFER_HOURS=1):
            self.assertEqual(policy.waiting_offer_hours(self.church), 12)
        with override_settings(EMMAUS_WAITING_OFFER_HOURS=72):
            self.assertEqual(policy.waiting_offer_hours(self.church), 72)
        with override_settings(EMMAUS_WAITING_OFFER_HOURS="pronto"):
            self.assertEqual(policy.waiting_offer_hours(self.church), 48)


class OfferTests(WaitingFixture):
    def test_a_cancellation_offers_the_place_to_the_first_and_the_offer_holds_it(self):
        held, = self.fill()
        first, second = self.join(self.leader), self.join(self.asked)
        before = timezone.now()
        services.cancel(held, self.admin)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.status, second.status), (OFFERED, WAITING))
        hours = (first.offer_expires_at - before).total_seconds() / 3600
        self.assertTrue(47.9 < hours < 48.1, hours)
        self.assertEqual(services.taken(self.retiro), 1)  # the offer holds the place
        self.assertEqual(services.waiting_position(second), 1)
        self.assertIsNone(services.waiting_position(first))
        # Somebody who arrives now does not take the offered place: they go to the end of the line.
        late = self.join(self.member)
        self.assertEqual(late.status, WAITING)
        self.assertEqual(services.waiting_position(late), 2)
        self.assertEqual(self.state(first), OFFERED)
        audit = ChurchAuditEvent.objects.filter(action="evento_lista_oferta").get()
        self.assertEqual(audit.actor_label, "Sistema")
        self.assertEqual(audit.metadata, {"event_id": self.retiro.pk, "registration_id": first.pk, "person_id": self.leader.pk})
        self.assert_consistent()

    def test_an_offer_is_never_a_registration_until_the_person_accepts(self):
        held, = self.fill()
        row = self.join(self.leader)
        services.cancel(held, self.admin)
        self.assertEqual(self.state(row), OFFERED)
        self.assertFalse(EventRegistration.objects.filter(event=self.retiro, status__in=(ACTIVE, PENDING)).exists())
        accepted = services.accept_offer(row, self.leader_user)
        self.assertEqual((accepted.status, accepted.offer_expires_at, accepted.waiting_since), (ACTIVE, None, None))
        self.assertEqual(services.accept_offer(row, self.leader_user).status, ACTIVE)  # idempotent
        self.assertEqual(ChurchAuditEvent.objects.filter(action="evento_lista_aceptada").count(), 1)

    def test_declining_passes_the_place_on(self):
        held, = self.fill()
        first, second = self.join(self.leader), self.join(self.asked)
        services.cancel(held, self.admin)
        services.leave_waiting(first, self.leader_user)
        first.refresh_from_db()
        self.assertEqual((first.status, first.waiting_outcome, first.offer_expires_at), (CANCELLED, "declino", None))
        self.assertEqual(self.state(second), OFFERED)
        self.assert_consistent()

    def test_each_way_a_place_is_freed_offers_it(self):
        # An annulled walk-up (confirmed or pending), more places, and a page published again.
        self.fill(capacity=2, holders=[self.admin_person])
        self.start()
        visitor = services.register_walkup(self.retiro, self.admin, first_name="Vera", last_name="Visita Reservada")
        waiting = [self.join(person) for person in (self.leader, self.asked, self.member)]
        self.assertEqual([self.state(row) for row in waiting], [WAITING] * 3)
        services.annul_walkup(visitor, self.admin, "error")
        self.assertEqual([self.state(row) for row in waiting], [OFFERED, WAITING, WAITING])
        services.save_page(self.retiro, self.admin, capacity=3)
        self.assertEqual([self.state(row) for row in waiting], [OFFERED, OFFERED, WAITING])
        self.assert_consistent()
        # While the page is not published the list waits; published again, it moves.
        services.save_page(self.retiro, self.admin, published=False, capacity=4)
        self.assertEqual(self.state(waiting[2]), WAITING)
        services.save_page(self.retiro, self.admin, published=True)
        self.assertEqual(self.state(waiting[2]), OFFERED)
        self.assertFalse(EventRegistration.objects.filter(event=self.retiro, status=ACTIVE).exclude(person=self.admin_person).exists())

    def test_a_pending_walkup_that_is_annulled_frees_its_place_for_the_list(self):
        self.fill(capacity=1, holders=[])
        self.require_consent()
        self.start()
        visitor = services.register_walkup(self.retiro, self.admin, first_name="Vico", last_name="Visita", is_minor=True)
        self.assertEqual(visitor.status, PENDING)
        row = self.join(self.leader)
        self.assertEqual(row.status, WAITING)
        services.annul_walkup(visitor, self.admin, "no_se_quedo")
        self.assertEqual(self.state(row), OFFERED)


class AcceptTruthTableTests(WaitingFixture):
    """Accepting goes through ``standing``: every row of decision 0015's table, from an offer."""

    def offered(self, person):
        """``person`` waits for the retreat's one place and gets the offer."""
        held = self.join(self.admin_person)
        self.assertIn(held.status, (ACTIVE, PENDING))
        row = self.join(person)
        self.assertEqual(row.status, WAITING)
        services.cancel(held, self.admin)
        self.assertEqual(self.state(row), OFFERED)
        return row

    def accept(self, person):
        row = services.accept_offer(self.offered(person), self.user_of(person))
        return row.status, row.pending_reason

    def setUp(self):
        self.set_page(capacity=1, waiting_list=True)
        self.verified(self.admin_person, 40)

    def test_an_event_that_does_not_ask_confirms_whoever_accepts(self):
        self.born(self.member, 15)
        self.assertEqual(self.accept(self.member), (ACTIVE, ""))

    def test_adult_verified_by_the_team_is_confirmed(self):
        self.require_consent()
        self.verified(self.asked, 30)
        self.assertEqual(self.accept(self.asked), (ACTIVE, ""))

    def test_adult_by_a_typed_date_waits_for_the_age(self):
        self.require_consent()
        self.born(self.leader, 30)
        self.assertEqual(self.accept(self.leader), (PENDING, "age"))

    def test_unknown_date_of_birth_waits_for_the_age(self):
        self.require_consent()
        self.assertEqual(self.accept(self.no_account), (PENDING, "age"))

    def test_minor_by_a_typed_date_waits_for_the_authorization(self):
        self.require_consent()
        self.born(self.member, 15)
        self.assertEqual(self.accept(self.member), (PENDING, "consent"))

    def test_minor_verified_by_the_team_waits_for_the_authorization(self):
        self.require_consent()
        self.verified(self.coord_person, 15)
        self.assertEqual(self.accept(self.coord_person), (PENDING, "consent"))

    def test_minor_with_an_authorization_of_this_registration_that_confirms_is_confirmed(self):
        self.require_consent()
        self.born(self.member, 15)
        own = self.join(self.member)
        services.record_consent(own, self.admin, **CONSENT)
        self.assertEqual(self.state(own), ACTIVE)
        services.cancel(own, self.member_user)
        self.assertEqual(self.accept(self.member), (ACTIVE, ""))

    def test_minor_whose_authorization_was_only_a_call_or_was_withdrawn_waits(self):
        self.require_consent()
        self.born(self.member, 15)
        own = self.join(self.member)
        services.record_consent(own, self.admin, **{**CONSENT, "method": "verificado"})
        services.cancel(own, self.member_user)
        self.assertEqual(self.accept(self.member), (PENDING, "consent"))
        row = EventRegistration.objects.get(event=self.retiro, person=self.member)
        services.record_consent(row, self.admin, **CONSENT)
        services.revoke_consent(row, self.admin)
        self.assertEqual((self.state(row), row.pending_reason), (PENDING, "consent"))

    def test_the_audit_says_where_the_acceptance_stands(self):
        self.require_consent()
        self.born(self.member, 15)
        self.accept(self.member)
        audit = ChurchAuditEvent.objects.filter(action="evento_lista_aceptada").get()
        self.assertEqual(audit.metadata["standing"], "consent")
        self.assertIn("queda pendiente de autorización", audit.summary)


class NothingElseMovesTheListTests(WaitingFixture):
    def setUp(self):
        held, _ = self.fill(capacity=2, holders=[self.admin_person, self.coord_person])
        self.offered, self.waiting = self.join(self.leader), self.join(self.asked)
        services.cancel(held, self.admin)
        self.assertEqual((self.state(self.offered), self.state(self.waiting)), (OFFERED, WAITING))

    def unchanged(self):
        self.offered.refresh_from_db()
        self.waiting.refresh_from_db()
        self.assertEqual((self.offered.status, self.waiting.status), (OFFERED, WAITING))
        self.assertEqual((self.offered.attended, self.waiting.attended), (None, None))
        self.assertEqual((self.offered.pending_reason, self.waiting.pending_reason), ("", ""))
        self.assert_consistent()

    def test_attendance_cannot_mark_them(self):
        self.start()
        ids = [self.offered.pk, self.waiting.pk]
        saved = services.record_attendance(self.retiro, ids, self.admin, ids)
        self.assertEqual(tuple(saved), (0, 1))
        self.unchanged()
        self.client.force_login(self.admin)
        page = self.client.get(self.url("registrations"))
        for row in (self.offered, self.waiting):
            self.assertNotContains(page, f'id="vino-{row.pk}"')

    def test_signing_up_again_moves_nothing(self):
        for person in (self.leader, self.asked):
            self.join(person)
        self.unchanged()

    def test_editing_the_page_moves_nothing_and_more_places_only_offer(self):
        services.save_page(self.retiro, self.admin, description="Otro texto.", capacity=2)
        self.unchanged()
        services.save_page(self.retiro, self.admin, requires_guardian_consent=True)
        self.unchanged()
        services.save_page(self.retiro, self.admin, requires_guardian_consent=False, waiting_list=False)
        self.unchanged()
        services.save_page(self.retiro, self.admin, capacity=5)
        self.assertEqual((self.state(self.offered), self.state(self.waiting)), (OFFERED, OFFERED))
        # Nobody of the list became a registration: there is still only who already held a place.
        self.assertEqual(EventRegistration.objects.filter(event=self.retiro, status__in=(ACTIVE, PENDING)).count(), 1)
        services.save_page(self.retiro, self.admin, capacity=None)
        self.assertEqual((self.state(self.offered), self.state(self.waiting)), (OFFERED, OFFERED))

    def test_resettling_the_event_and_a_change_of_age_do_not_touch_them(self):
        self.set_page(requires_guardian_consent=True)
        page = EventPage.objects.get(pk=self.page.pk)
        counts = services.resettle_event(self.retiro, page, self.admin)
        self.assertEqual(counts["reviewed"], 1)  # only who holds a place as a registration
        self.unchanged()
        self.verified(self.leader, 30)
        self.verified(self.asked, 15)
        self.assertEqual(services.reevaluate_person(self.leader, self.admin), 0)
        self.unchanged()
        call_command("resettle_registrations", "--apply", stdout=StringIO())
        self.unchanged()

    def test_a_walkup_is_not_a_way_off_the_list(self):
        self.start()
        for person in (self.leader, self.asked):
            with self.assertRaises(services.AgendaError) as caught:
                services.register_walkup(self.retiro, self.admin, person=person, override=["llena"], override_reason="llego")
            self.assertEqual(caught.exception.code, "listed")
        self.unchanged()

    def test_an_override_at_the_door_adds_a_place_and_never_uses_the_offered_one(self):
        # capacity 2 = 1 confirmed + 1 offered: full for the door, and going over it is one more.
        self.start()
        self.assertEqual(services.walkup_blocks(self.retiro, EventPage.objects.get(pk=self.page.pk), services.taken(self.retiro)), ["llena"])
        with self.assertRaises(services.AgendaError) as caught:
            services.register_walkup(self.retiro, self.admin, first_name="Vera", last_name="Visita")
        self.assertEqual(caught.exception.code, "full")
        visitor = services.register_walkup(self.retiro, self.admin, first_name="Vera", last_name="Visita",
                                           override=["llena"], override_reason="llego")
        self.assertEqual(visitor.status, ACTIVE)
        self.assertEqual((self.state(self.offered), self.state(self.waiting)), (OFFERED, WAITING))
        self.assertEqual(services.taken(self.retiro), 3)  # 2 confirmed + 1 offered, over a capacity of 2
        self.assert_consistent(over=1)
        # The offer is still good, and the place the override added is not handed to the list when it empties.
        self.assertEqual(services.accept_offer(self.offered, self.leader_user).status, ACTIVE)
        services.annul_walkup(visitor, self.admin, "error")
        self.assertEqual(self.state(self.waiting), WAITING)  # 2 confirmed of 2: nothing is free
        self.assert_consistent()


class ExpiryTests(WaitingFixture):
    def setUp(self):
        held, = self.fill()
        self.first, self.second = self.join(self.leader), self.join(self.asked)
        services.cancel(held, self.admin)
        self.expire(self.first)

    def released(self):
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual((self.first.status, self.first.waiting_outcome, self.first.offer_expires_at), (CANCELLED, "expiro", None))
        self.assertEqual(self.first.get_waiting_outcome_display(), "No respondió a tiempo")
        self.assertEqual(self.second.status, OFFERED)
        self.assert_consistent()

    def run_command(self, *args):
        out = StringIO()
        call_command("expire_waiting_offers", *args, stdout=out)
        return out.getvalue()

    def test_the_command_counts_by_default_and_writes_with_apply_once(self):
        printed = self.run_command()
        self.assertIn("Simulación", printed)
        self.assertIn("lugares ofrecidos vencidos: 1", printed)
        self.assertIn("eventos con lugares ofrecidos vencidos: 1", printed)
        self.assertEqual((self.state(self.first), self.state(self.second)), (OFFERED, WAITING))
        printed = self.run_command("--apply")
        self.assertIn("liberados: 1", printed)
        self.assertIn("ofrecidos a quien seguía en la lista: 1", printed)
        for name in ("Gala", "Guía", "Pía", "Pidió", "Retiro"):
            self.assertNotIn(name, printed)
        self.released()
        audit = ChurchAuditEvent.objects.filter(action="evento_lista_vencida").get()
        self.assertEqual(audit.metadata["reason"], "expiro")
        again = self.run_command("--apply")
        self.assertIn("lugares ofrecidos vencidos: 0", again)
        self.assertIn("liberados: 0", again)
        self.released()
        self.assertEqual(ChurchAuditEvent.objects.filter(action="evento_lista_vencida").count(), 1)
        self.assertEqual(ChurchAuditEvent.objects.filter(action="evento_lista_oferta").count(), 2)

    def test_the_command_refuses_a_base_url_that_is_not_an_address(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self.run_command("--apply", "--base-url", "emmaus")

    def test_opening_inscritos_moves_the_list(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url("registrations")).status_code, 200)
        self.released()

    def test_a_sign_up_moves_the_list_before_it_counts_places(self):
        late = self.join(self.member)
        self.released()
        self.assertEqual(late.status, WAITING)

    def test_a_cancellation_moves_the_list(self):
        self.set_page(capacity=2)
        extra = EventRegistration.objects.create(event=self.retiro, person=self.member)
        services.cancel(extra, self.member_user)
        self.released()

    def test_accepting_late_releases_the_place_and_says_so(self):
        with self.assertRaises(services.AgendaError) as caught:
            services.accept_offer(self.first, self.leader_user)
        self.assertEqual(caught.exception.code, "offer_over")
        self.released()

    def test_the_person_reads_that_it_expired_and_may_join_again_at_the_end(self):
        self.client.force_login(self.leader_user)
        page = self.client.get(self.url())
        self.released()
        self.assertContains(page, "No respondiste a tiempo.")
        self.assertContains(page, "Anotarme en la lista de espera")
        self.assertNotContains(page, "Aceptar mi lugar")
        self.client.post(self.url("register"))
        self.first.refresh_from_db()
        self.assertEqual((self.first.status, self.first.waiting_outcome), (WAITING, ""))
        self.assertEqual(services.waiting_position(self.first), 1)  # the other one has the offer now
        events = {event.pk: event for event in services.upcoming_for(self.leader, self.church)}
        self.assertEqual(events[self.retiro.pk].state, "en_espera")

    def test_an_expired_offer_nobody_released_yet_is_not_an_offer_on_the_home(self):
        events = {event.pk: event for event in services.upcoming_for(self.leader, self.church)}
        self.assertEqual(events[self.retiro.pk].state, "lista")
        self.assertIsNone(events[self.retiro.pk].my_wait)


class OneByOneTests(WaitingFixture):
    """Every transition takes the page row lock first, so two requests at once run one after the
    other: these are those two orders, with the invariants checked after each step."""

    def test_two_cancellations_offer_two_different_people_and_a_place_is_offered_once(self):
        a, b = self.fill(capacity=2, holders=[self.admin_person, self.coord_person])
        waiting = [self.join(person) for person in (self.leader, self.asked, self.member)]
        for first, second in ((a, b), (b, a)):
            with self.subTest(first=first.pk):
                sid = transaction.savepoint()
                services.cancel(first, self.admin)
                self.assert_consistent()
                self.assertEqual([self.state(row) for row in waiting], [OFFERED, WAITING, WAITING])
                services.cancel(second, self.admin)
                self.assert_consistent()
                self.assertEqual([self.state(row) for row in waiting], [OFFERED, OFFERED, WAITING])
                offered = EventRegistration.objects.filter(event=self.retiro, status=OFFERED)
                self.assertEqual(offered.count(), len(set(offered.values_list("person_id", flat=True))), 2)
                # Moving the list again, by any door, offers nothing more: there is no free place.
                for _ in range(3):
                    self.assertEqual(services.advance(self.retiro), {"released": 0, "offered": 0})
                self.assertEqual(ChurchAuditEvent.objects.filter(action="evento_lista_oferta").count(), 2)
                transaction.savepoint_rollback(sid)

    def test_a_long_run_of_moves_keeps_the_invariants(self):
        a, b = self.fill(capacity=2, holders=[self.admin_person, self.coord_person])
        leader, asked, member = (self.join(person) for person in (self.leader, self.asked, self.member))
        steps = [
            lambda: services.cancel(a, self.admin),                        # leader offered
            lambda: services.leave_waiting(asked, self.asked_user),        # asked leaves the line
            lambda: services.accept_offer(leader, self.leader_user),       # leader in
            lambda: self.join(self.asked),                                 # asked back, after member
            lambda: services.cancel(b, self.admin),                        # member offered
            lambda: services.remove_from_waiting(member, self.admin),      # member removed -> asked offered
            lambda: services.advance(self.retiro),
            lambda: self.join(self.admin_person),                          # waits behind
            lambda: services.accept_offer(asked, self.asked_user),
            lambda: services.cancel(leader, self.leader_user),             # admin_person offered
        ]
        for step in steps:
            step()
            self.assert_consistent()
        self.assertEqual({row.person_id: row.status for row in EventRegistration.objects.filter(event=self.retiro)}, {
            self.admin_person.pk: OFFERED, self.coord_person.pk: CANCELLED, self.leader.pk: CANCELLED,
            self.asked.pk: ACTIVE, self.member.pk: CANCELLED,
        })

    def test_the_database_refuses_an_offer_without_a_deadline_and_a_deadline_without_an_offer(self):
        self.fill()
        row = self.join(self.leader)
        for values in ({"status": OFFERED}, {"offer_expires_at": timezone.now()}):
            with self.subTest(values=list(values)), self.assertRaises(IntegrityError), transaction.atomic():
                EventRegistration.objects.filter(pk=row.pk).update(**values)

    def test_the_list_is_for_people_of_the_church(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventRegistration.objects.create(event=self.retiro, guest_first_name="Vera", status=WAITING)


class OfferMailTests(WaitingFixture):
    def offer(self, base_url=BASE):
        held, = self.fill()
        row = self.join(self.leader)
        with self.captureOnCommitCallbacks(execute=True):
            services.cancel(held, self.admin, base_url=base_url)
        self.assertEqual(self.state(row), OFFERED)
        return row

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_one_mail_with_the_church_and_the_link_and_nothing_about_who_or_what(self):
        self.offer()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.leader.email])
        self.assertEqual(message.subject, SUBJECT)
        link = BASE.rstrip("/") + reverse("agenda:detail", args=[self.retiro.slug])
        self.assertIn(link, message.body)
        self.assertIn(self.church.name, message.body)
        rest = f"{message.subject}\n{message.body}".replace(link, "")
        for private in (self.retiro.name, "Gala", "Guía", "Rancho", "48", self.admin_person.first_name):
            self.assertNotIn(private, rest)

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_the_mail_goes_after_the_commit_and_not_when_the_change_is_undone(self):
        held, = self.fill()
        self.join(self.leader)
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            services.cancel(held, self.admin, base_url=BASE)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(mail.outbox, [])

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_without_an_address_of_emmaus_or_of_the_person_nothing_is_sent(self):
        self.offer(base_url=None)
        self.assertEqual(mail.outbox, [])

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_a_person_without_an_address_still_gets_the_offer(self):
        held, = self.fill()
        row = self.join(self.no_account)
        with self.captureOnCommitCallbacks(execute=True):
            services.cancel(held, self.admin, base_url=BASE)
        self.assertEqual(self.state(row), OFFERED)
        self.assertEqual(mail.outbox, [])

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_without_configured_mail_the_person_reads_the_offer_in_emmaus(self):
        self.offer()
        self.assertEqual(mail.outbox, [])
        events = {event.pk: event for event in services.upcoming_for(self.leader, self.church)}
        self.assertEqual(events[self.retiro.pk].state, "ofrecido")
        self.assertIsNone(events[self.retiro.pk].my_place)
        self.assertIsNone(events[self.retiro.pk].my_registration)
        self.client.force_login(self.leader_user)
        self.assertContains(self.client.get(self.url()), "Aceptar mi lugar")
        self.assertContains(self.client.get(reverse("agenda:list")), "Te ofrecimos un lugar")

    @override_settings(EMAIL_BACKEND="core.test_notify.FailingBackend")
    def test_a_mail_that_fails_never_reaches_who_freed_the_place(self):
        row = self.offer()
        self.assertEqual(self.state(row), OFFERED)


class MemberScreenTests(WaitingFixture):
    def test_the_whole_path_on_the_event_page(self):
        held, = self.fill()
        self.client.force_login(self.leader_user)
        page = self.client.get(self.url())
        self.assertContains(page, "Ya no hay lugares, pero hay lista de espera")
        self.assertContains(page, "Anotarme en la lista de espera")
        self.assertContains(page, "tendrás 48 horas para aceptarlo")
        self.assertNotContains(page, ">Inscribirme<")
        self.assertContains(self.client.get(reverse("agenda:list")), "Lista de espera")
        joined = self.client.post(self.url("register"), follow=True)
        self.assertContains(joined, "Te anotamos en la lista de espera de Retiro de jóvenes. Eres la primera persona en la lista de espera.")
        self.assertContains(joined, "<strong>Estás en la lista de espera.</strong>")
        self.assertContains(joined, "Salir de la lista de espera")
        self.assertNotContains(joined, "Cancelar mi inscripción")
        listing = self.client.get(reverse("agenda:list"))
        self.assertContains(listing, "Tus inscripciones")
        self.assertContains(listing, "En lista de espera")
        row = EventRegistration.objects.get(event=self.retiro, person=self.leader)
        # Accepting before there is an offer does nothing.
        early = self.client.post(self.url("accept_offer"), follow=True)
        self.assertContains(early, "Por ahora no tienes un lugar ofrecido en este evento.")
        self.assertEqual(self.state(row), WAITING)
        services.cancel(held, self.admin)
        offered = self.client.get(self.url())
        self.assertContains(offered, "Te ofrecimos un lugar")
        self.assertContains(offered, "Aceptar mi lugar")
        self.assertContains(offered, "No voy a usar el lugar")
        self.assertEqual(self.client.get(self.url("accept_offer")).status_code, 405)
        done = self.client.post(self.url("accept_offer"), follow=True)
        self.assertContains(done, "Listo. Aceptaste tu lugar: te inscribiste a Retiro de jóvenes.")
        self.assertContains(done, "<strong>Te inscribiste.</strong>")
        self.assertEqual(self.state(row), ACTIVE)

    def test_a_minor_who_accepts_reads_that_the_registration_waits(self):
        held, = self.fill(requires_guardian_consent=True)
        self.born(self.member, 15)
        self.client.force_login(self.member_user)
        self.client.post(self.url("register"))
        services.cancel(held, self.admin)
        self.assertContains(self.client.get(self.url()), "Este evento cuida a los menores de edad: al aceptar")
        done = self.client.post(self.url("accept_offer"), follow=True)
        self.assertContains(done, "Aceptaste tu lugar en Retiro de jóvenes.")
        self.assertContains(done, "<strong>Pendiente de autorización.</strong>")
        row = EventRegistration.objects.get(event=self.retiro, person=self.member)
        self.assertEqual((row.status, row.pending_reason), (PENDING, "consent"))

    def test_leaving_the_list_and_letting_an_offer_go_ask_first(self):
        held, = self.fill()
        self.client.force_login(self.leader_user)
        self.client.post(self.url("register"))
        row = EventRegistration.objects.get(event=self.retiro, person=self.leader)
        ask = self.client.get(self.url("leave_waiting"))
        self.assertContains(ask, "¿Quieres salir de la lista de espera de Retiro de jóvenes?")
        self.assertEqual(self.state(row), WAITING)
        services.cancel(held, self.admin)
        ask = self.client.get(self.url("leave_waiting"))
        self.assertContains(ask, "¿Quieres dejar pasar tu lugar en Retiro de jóvenes?")
        gone = self.client.post(self.url("leave_waiting"), follow=True)
        self.assertContains(gone, "Dejaste pasar el lugar en Retiro de jóvenes.")
        self.assertEqual(self.state(row), CANCELLED)
        self.assertEqual(self.client.get(self.url("leave_waiting")).status_code, 404)

    def test_nobody_answers_for_another_person_or_another_church(self):
        held, = self.fill()
        row = self.join(self.leader)
        services.cancel(held, self.admin)
        for user in (self.asked_user, self.member_user):
            self.client.force_login(user)
            self.assertEqual(self.client.post(self.url("accept_offer")).status_code, 404)
            self.assertEqual(self.client.post(self.url("leave_waiting")).status_code, 404)
        self.client.force_login(self.member_b_user)
        for name in ("accept_offer", "leave_waiting", "register"):
            self.assertEqual(self.client.post(self.url(name)).status_code, 404)
        self.assertEqual(self.state(row), OFFERED)

    def test_the_position_is_never_another_persons_name(self):
        self.fill()
        self.join(self.leader)
        self.join(self.asked)
        self.client.force_login(self.member_user)
        self.client.post(self.url("register"))
        page = self.client.get(self.url())
        self.assertContains(page, "Eres la persona 3 en la lista de espera.")
        for name in ("Gala", "Pía", "Pidió"):
            self.assertNotContains(page, name)


class StaffListTests(WaitingFixture):
    def setUp(self):
        held, _ = self.fill(capacity=2, holders=[self.admin_person, self.coord_person])
        self.rows = [self.join(person) for person in (self.leader, self.asked, self.member)]
        services.cancel(held, self.admin)
        self.client.force_login(self.admin)

    def test_inscritos_shows_the_list_in_order_with_each_state_and_the_deadline(self):
        html = self.client.get(self.url("registrations")).content.decode()
        self.assertIn("Lista de espera", html)
        self.assertIn(", 3 en lista de espera", html)
        section = html[html.index('id="lista-espera"'):html.index('id="inscritos"')]
        order = [section.index(name) for name in ("Gala Guía", "Pía Pidió", "Diego Hernández Cruz")]
        self.assertEqual(order, sorted(order))
        self.assertIn("<strong>Le ofrecimos un lugar.</strong> Tiene hasta el ", section)
        self.assertIn("En espera, lugar 1 de la lista.", section)
        self.assertIn("En espera, lugar 2 de la lista.", section)
        # All the administration can do from the list is take somebody off it.
        for row in self.rows:
            self.assertIn(self.url("waiting_remove", row.pk), section)
            for name in ("consent", "confirm_age", "annul", "guest_age"):
                self.assertNotIn(self.url(name, row.pk), html)
        self.assertNotIn("Aceptar", section)
        self.assertNotIn("Inscribir", section)

    def test_removing_is_audited_with_a_fixed_reason_and_passes_the_place_on(self):
        offered, second, third = self.rows
        ask = self.client.get(self.url("waiting_remove", offered.pk))
        self.assertContains(ask, "¿Quieres quitar a Gala Guía de la lista de espera?")
        self.assertEqual(self.state(offered), OFFERED)
        done = self.client.post(self.url("waiting_remove", offered.pk), follow=True)
        self.assertContains(done, "Quitaste a Gala Guía de la lista de espera.")
        offered.refresh_from_db()
        self.assertEqual((offered.status, offered.waiting_outcome), (CANCELLED, "retirada"))
        self.assertEqual((self.state(second), self.state(third)), (OFFERED, WAITING))
        audit = ChurchAuditEvent.objects.filter(action="evento_lista_retirada").get()
        self.assertEqual(audit.actor, self.admin)
        self.assertEqual(audit.metadata, {"event_id": self.retiro.pk, "reason": "retirada", "registration_id": offered.pk,
                                          "person_id": self.leader.pk})
        self.assertEqual(self.client.get(self.url("waiting_remove", offered.pk)).status_code, 404)
        # Leaving the list is not a cancellation: the count is still the one registration cancelled in setUp.
        self.assertContains(self.client.get(self.url("registrations")), ", 1 canceló.")
        # The person reads why, and may join again.
        self.client.force_login(self.leader_user)
        self.assertContains(self.client.get(self.url()), "La administración de tu iglesia te quitó de la lista.")

    def test_only_the_administration_of_this_church_removes(self):
        row = self.rows[1]
        for user, status in ((self.member_user, 403), (self.leader_user, 403), (self.admin_b, 404)):
            self.client.force_login(user)
            self.assertEqual(self.client.post(self.url("waiting_remove", row.pk)).status_code, status)
        self.assertEqual(self.state(row), WAITING)

    def test_there_is_no_address_that_confirms_or_reorders_from_the_list(self):
        from agenda.urls import urlpatterns

        names = {pattern.name for pattern in urlpatterns}
        self.assertEqual({name for name in names if "waiting" in name or "offer" in name},
                         {"accept_offer", "leave_waiting", "waiting_remove"})
        # The pages that confirm a registration answer 404 for a row of the list.
        for row in self.rows:
            for name in ("consent", "confirm_age", "guest_age", "annul", "consent_revoke", "guest_promote"):
                self.assertEqual(self.client.get(self.url(name, row.pk)).status_code, 404, name)
