"""What the independent review of the minors' slice found (wave 5): each test failed before its fix."""

from datetime import date, timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from agenda import services
from agenda.models import EventPage, EventRegistration, GuardianConsent
from agenda.simple import update_simple_event
from agenda.tests.test_event_extras import CONSENT
from agenda.tests.test_minors_safeguards import CONFIRMED, WAITS_AGE, WAITS_CONSENT, SafeguardFixture, where
from events.models import Event
from people import policy
from people import services as people_services
from people.models import ChurchAuditEvent, Person


class AnnulRevokesTests(SafeguardFixture):
    """A. An authorization recorded on a row that was a mistake must not confirm anybody later."""

    def test_annulling_withdraws_the_authorization_of_the_row(self):
        self.require_consent()
        self.born(self.member, 15)
        self.start()
        # The wrong minor is picked at the door and the signed paper is recorded on that row.
        row = services.register_walkup(self.retiro, self.admin, person=self.member, consent=CONSENT)
        self.assertEqual(where(row), CONFIRMED)
        services.annul_walkup(row, self.admin, "error")
        consent = GuardianConsent.objects.get()
        self.assertIsNotNone(consent.revoked_at)
        self.assertEqual(consent.revoked_by, self.admin)
        # The day that minor is written down again, or signs up, nothing confirms them.
        again = services.register_walkup(self.retiro, self.admin, person=self.member)
        self.assertEqual((again.pk, *where(again), again.attended), (row.pk, *WAITS_CONSENT, None))
        services.annul_walkup(again, self.admin, "error")
        self.move(self.retiro, 5, 6)
        self.assertEqual(where(services.register(self.retiro, self.member, self.member_user)), WAITS_CONSENT)


class ResettleCommandTests(SafeguardFixture):
    """B. Rows confirmed before this code, or under older words, are found by a command, not by luck."""

    def run_command(self, *args) -> str:
        out = StringIO()
        call_command("resettle_registrations", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_by_default_then_apply_and_a_second_run_changes_nothing(self):
        self.require_consent()
        self.born(self.leader, 30)   # an adult by a typed date, confirmed by older code
        self.born(self.member, 15)   # a minor confirmed by a call
        self.verified(self.asked)
        typed = services.register(self.retiro, self.leader, self.leader_user)
        called = services.register(self.retiro, self.member, self.member_user)
        fine = services.register(self.retiro, self.asked, self.asked_user)
        GuardianConsent.objects.create(registration=called, guardian_name="Tutor Reservado", relationship="padre",
                                       method="verificado", allows_participation=True)
        EventRegistration.objects.filter(pk__in=[typed.pk, called.pk]).update(status="inscrita", pending_reason="")
        # An event that ended and one that does not ask are left alone.
        ended = self._future(self.church, "Vigilia pasada", "vigilia-pasada", 2, 2)
        services.save_page(ended, self.admin, published=True, registration="abierta", requires_guardian_consent=True)
        old = services.register(ended, self.leader, self.leader_user)
        EventRegistration.objects.filter(pk=old.pk).update(status="inscrita", pending_reason="")
        self.move(ended, -9, -8)
        audits = ChurchAuditEvent.objects.count()

        out = self.run_command()
        self.assertIn("Simulación", out)
        self.assertIn("eventos revisados: 1", out)
        self.assertIn("inscripciones revisadas: 3", out)
        self.assertIn("cambiarían: 2", out)
        self.assertIn("a pendiente de confirmar la edad: 1", out)
        self.assertIn("a pendiente de autorización: 1", out)
        for secret in ("Gala", "Diego", "Tutor", "Retiro"):
            self.assertNotIn(secret, out)
        self.assertEqual([where(row) for row in (typed, called, fine)], [CONFIRMED] * 3)  # nothing written
        self.assertEqual(ChurchAuditEvent.objects.count(), audits)

        out = self.run_command("--apply")
        self.assertIn("Aplicado", out)
        self.assertIn("cambiaron: 2", out)
        self.assertEqual([where(row) for row in (typed, called, fine, old)], [WAITS_AGE, WAITS_CONSENT, CONFIRMED, CONFIRMED])
        reviewed = ChurchAuditEvent.objects.filter(action="evento_inscripcion_revisada")
        self.assertEqual(sorted(audit.metadata["standing"] for audit in reviewed), ["age", "consent"])
        self.assertEqual({audit.actor_label for audit in reviewed}, {"Sistema"})

        self.assertIn("cambiaron: 0", self.run_command("--apply"))
        self.assertEqual(reviewed.count(), 2)

    def test_new_words_of_the_authorization_are_found_by_the_command(self):
        row = self.minor_pending()
        services.record_consent(row, self.admin, **CONSENT)
        self.assertIn("cambiarían: 0", self.run_command())
        with mock.patch.object(policy, "CONSENT_POLICY_VERSION", "aprobado-2027-01"):
            self.assertIn("a pendiente de autorización: 1", self.run_command("--apply"))
        self.assertEqual(where(row), WAITS_CONSENT)


class VisitorAgeTests(SafeguardFixture):
    """C. No age is assumed for a visitor: somebody of the team says it, at the door or later."""

    def test_a_visitor_written_down_before_the_rule_waits_until_the_team_says(self):
        self.start()
        adult = services.register_walkup(self.retiro, self.admin, first_name="Ada", last_name="Antes")
        young = services.register_walkup(self.retiro, self.admin, first_name="Beto", last_name="Antes")
        self.assertEqual((*where(adult), adult.guest_age_checked, adult.attended), (*CONFIRMED, False, True))
        services.save_page(self.retiro, self.admin, requires_guardian_consent=True)
        self.assertEqual((*where(adult), adult.attended), (*WAITS_AGE, None))
        page = self.client.get(self.url("registrations"))
        self.assertContains(page, "Nadie dijo si es mayor o menor de edad.", count=2)
        self.assertContains(page, self.url("guest_age", adult.pk))
        self.assertEqual(self.client.get(self.url("confirm_age", adult.pk)).status_code, 404)  # that page is for people
        form = self.client.get(self.url("guest_age", adult.pk))
        self.assertContains(form, "<h1", count=1)
        self.assertContains(form, "La vi en la puerta: es mayor de edad")
        self.assertContains(form, "Es menor de edad")
        self.assertNotContains(form, "checked")
        self.assertContains(self.client.post(self.url("guest_age", adult.pk), {}), "Elige si es mayor o menor de edad.")
        self.assertEqual(where(adult), WAITS_AGE)
        response = self.client.post(self.url("guest_age", adult.pk), {"age": "adulto"}, follow=True)
        self.assertContains(response, "El registro de Ada Antes quedó confirmado.")
        self.assertEqual((*where(adult), adult.guest_age_checked, adult.guest_is_minor, adult.attended), (*CONFIRMED, True, False, True))
        response = self.client.post(self.url("guest_age", young.pk), {"age": "menor"}, follow=True)
        self.assertContains(response, "Beto Antes es menor de edad.")
        self.assertEqual((*where(young), young.guest_age_checked, young.guest_is_minor, young.attended), (*WAITS_CONSENT, False, True, None))
        audits = ChurchAuditEvent.objects.filter(action="evento_visita_edad").order_by("id")
        self.assertEqual([(audit.actor, audit.metadata) for audit in audits], [
            (self.admin, {"event_id": self.retiro.pk, "registration_id": adult.pk, "minor": False}),
            (self.admin, {"event_id": self.retiro.pk, "registration_id": young.pk, "minor": True}),
        ])
        # Done once: the page is gone, and the service refuses a row that does not wait for it.
        self.assertEqual(self.client.get(self.url("guest_age", adult.pk)).status_code, 404)
        for row in (adult, young):
            with self.assertRaises(services.AgendaError):
                services.confirm_guest_age(row, self.admin, is_minor=False)
        self.assertEqual(where(young), WAITS_CONSENT)

    def test_at_the_door_of_an_event_that_asks_the_choice_is_explicit_and_audited(self):
        self.require_consent()
        self.start()
        nothing = services.register_walkup(self.retiro, self.admin, first_name="Nadie", last_name="Dijo")
        self.assertEqual((*where(nothing), nothing.attended), (*WAITS_AGE, None))
        with self.assertRaises(services.AgendaError):
            services.register_walkup(self.retiro, self.admin, first_name="Las", last_name="Dos", is_minor=True, seen_adult=True)
        response = self.client.post(self.url("walkup"), {"first_name": "Ada", "last_name": "Adulta", "age": "adulto"}, follow=True)
        self.assertContains(response, "Registraste a Ada Adulta. Ya quedó en la lista como que vino.")
        row = EventRegistration.objects.get(guest_first_name="Ada")
        self.assertEqual((*where(row), row.guest_age_checked, row.guest_is_minor, row.attended), (*CONFIRMED, True, False, True))
        audit = ChurchAuditEvent.objects.filter(action="evento_registro_puerta").latest("id")
        self.assertEqual((audit.actor, audit.metadata), (self.admin, {
            "event_id": self.retiro.pk, "registration_id": row.pk, "minor": False, "age_checked": True}))
        # "Seen as an adult" is never a word about a person of the church.
        person = services.register_walkup(self.retiro, self.admin, person=self.no_account, seen_adult=True)
        self.assertEqual((*where(person), person.guest_age_checked), (*WAITS_AGE, False))

    def test_an_event_that_does_not_ask_keeps_its_one_checkbox(self):
        self.start()
        form = self.client.get(self.url("walkup"))
        self.assertContains(form, 'name="is_minor"')
        self.assertNotContains(form, 'name="age"')
        self.client.post(self.url("walkup"), {"first_name": "Vera"})
        self.assertEqual(where(EventRegistration.objects.get(guest_first_name="Vera")), CONFIRMED)

    def test_a_visitor_seen_as_an_adult_who_becomes_a_person_waits_for_a_verified_date(self):
        """Kept on purpose: once there is a record of the church, the record decides (see the report of wave 5)."""
        self.require_consent()
        self.start()
        row = services.register_walkup(self.retiro, self.admin, first_name="Ada", seen_adult=True)
        services.promote_guest(row, self.admin)
        self.assertEqual((*where(row), row.attended), (*WAITS_AGE, None))
        self.assertIsNotNone(row.person)


class EventDateMovedTests(SafeguardFixture):
    """D. Who is a minor is counted on the event's first day: moving that day settles the rows again."""

    def eighteen_on_the_first_day(self) -> EventRegistration:
        self.require_consent()
        first = self.retiro.starts_on
        Person.objects.filter(pk=self.asked.pk).update(birth_date=date(first.year - 18, first.month, first.day))
        self.asked.refresh_from_db()
        people_services.verify_birth_date(self.asked, self.admin, "documento")
        row = services.register(self.retiro, self.asked, self.asked_user)
        self.assertEqual(where(row), CONFIRMED)
        return row

    def test_moving_a_simple_event_earlier_makes_who_is_not_yet_eighteen_wait(self):
        row = self.eighteen_on_the_first_day()
        Event.objects.filter(pk=self.retiro.pk).update(camp_tools=False)
        earlier = self.retiro.starts_on - timedelta(days=3)
        update_simple_event(self.retiro, self.admin, starts_on=earlier, ends_on=earlier)
        self.assertEqual(where(row), WAITS_CONSENT)
        audit = ChurchAuditEvent.objects.get(action="evento_inscripcion_revisada")
        self.assertEqual(audit.metadata["standing"], "consent")
        # Back to the day they turn 18: the same function, the other way.
        update_simple_event(self.retiro, self.admin, starts_on=earlier + timedelta(days=3), ends_on=earlier + timedelta(days=4))
        self.assertEqual(where(row), CONFIRMED)

    def test_any_other_save_of_the_event_moves_it_too(self):
        """The camp's settings form and everything else save the model: the receiver is on the model."""
        row = self.eighteen_on_the_first_day()
        event = Event.objects.get(pk=self.retiro.pk)
        event.starts_on = event.starts_on - timedelta(days=1)
        event.save()
        self.assertEqual(where(row), WAITS_CONSENT)
        event.starts_on = event.starts_on + timedelta(days=1)
        event.save(update_fields=["starts_on"])
        self.assertEqual(where(row), CONFIRMED)

    def test_other_saves_cost_one_query_and_touch_nothing(self):
        row = self.eighteen_on_the_first_day()
        EventRegistration.objects.filter(pk=row.pk).update(status="pendiente", pending_reason="age")  # would be settled if it ran
        event = Event.objects.get(pk=self.retiro.pk)
        event.location = "Otro lugar"
        with CaptureQueriesContext(connection) as full:
            event.save()
        self.assertEqual(len([q for q in full if "agenda_" in q["sql"]]), 0)
        self.assertEqual(len(full), 2)  # the first day the database has, and the save
        with CaptureQueriesContext(connection) as partial:
            event.save(update_fields=["location"])
        self.assertEqual(len(partial), 1)
        self.assertEqual(where(row), WAITS_AGE)
        # A date moved in an event that does not ask: one query more, to learn that it does not ask.
        EventPage.objects.filter(pk=self.page.pk).update(requires_guardian_consent=False)
        event.starts_on = event.starts_on - timedelta(days=1)
        event.save()
        self.assertEqual(where(row), WAITS_AGE)
        # A new event has nothing to settle and asks nothing.
        with CaptureQueriesContext(connection) as created:
            Event.objects.create(church=self.church, name="Nuevo", slug="nuevo-evento", starts_on=self.today)
        self.assertFalse([q for q in created if "agenda_" in q["sql"]])


class StalePersonTests(SafeguardFixture):
    """E. The verification follows the database row, never what an instance remembers."""

    def verify(self, birth_date=date(1990, 3, 14)):
        people_services.verify_birth_date(Person.objects.get(pk=self.member.pk), self.admin, "documento", birth_date)

    def stored(self):
        person = Person.objects.get(pk=self.member.pk)
        return person.birth_date, person.birth_date_verified

    def test_a_partial_save_in_between_does_not_hide_a_changed_date(self):
        self.verify()
        person = Person.objects.get(pk=self.member.pk)
        person.birth_date = date(2012, 1, 1)
        person.phone = "55 0000 9999"
        person.save(update_fields=["phone"])
        self.assertEqual(self.stored(), (date(1990, 3, 14), True))  # the date was not written
        with mock.patch("agenda.services.reevaluate_person") as told:
            person.save()
        self.assertEqual(self.stored(), (date(2012, 1, 1), False))
        told.assert_called_once()

    def test_a_stale_instance_cannot_write_a_verification_or_an_old_date_back(self):
        self.verify()
        stale = Person.objects.get(pk=self.member.pk)   # read while 1990 was verified
        # The administration corrects the date: it is a claim again.
        fresh = Person.objects.get(pk=self.member.pk)
        fresh.birth_date = date(2012, 1, 1)
        people_services.save_person(fresh, self.admin, created=False)
        self.assertEqual(self.stored(), (date(2012, 1, 1), False))
        stale.phone = "55 0000 9999"
        stale.save()
        person = Person.objects.get(pk=self.member.pk)
        self.assertEqual((person.phone, person.birth_date, person.birth_date_verified, person.birth_date_verified_method),
                         ("55 0000 9999", date(2012, 1, 1), False, ""))
        # Nor by naming the columns, nor on an instance that never read the date.
        stale = Person.objects.get(pk=self.member.pk)
        stale.birth_date_verified_at, stale.birth_date_verified_by, stale.birth_date_verified_method = self.retiro.created_at, self.admin, "documento"
        stale.save(update_fields=["birth_date_verified_at", "birth_date_verified_by", "birth_date_verified_method"])
        stale.save()
        self.assertEqual(self.stored(), (date(2012, 1, 1), False))
        # And the other way: a stale save does not clear a verification made meanwhile.
        stale = Person.objects.get(pk=self.member.pk)
        self.verify(date(2012, 1, 1))
        stale.phone = "55 1111 0000"
        stale.save()
        self.assertEqual(self.stored(), (date(2012, 1, 1), True))

    def test_registering_with_a_stale_person_reads_the_date_the_church_has_now(self):
        self.require_consent()
        self.verify()
        stale = Person.objects.get(pk=self.member.pk)   # a verified adult, as some request read it
        first = self.retiro.starts_on
        fresh = Person.objects.get(pk=self.member.pk)
        fresh.birth_date = date(first.year - 15, 1, 1)   # corrected: a minor, unverified
        people_services.save_person(fresh, self.admin, created=False)
        self.assertTrue(stale.birth_date_verified)
        self.assertEqual(where(services.register(self.retiro, stale, self.member_user)), WAITS_CONSENT)
        EventRegistration.objects.all().delete()
        self.start()
        row = services.register_walkup(self.retiro, self.admin, person=stale)
        self.assertEqual((*where(row), row.attended), (*WAITS_CONSENT, None))
        EventRegistration.objects.all().delete()
        guest = services.register_walkup(self.retiro, self.admin, first_name="Diego", seen_adult=True)
        services.promote_guest(guest, self.admin, existing=stale)
        self.assertEqual((*where(guest), guest.attended), (*WAITS_CONSENT, None))
