"""Decision 0015, on synthetic data: an age is verified by the team and a typed date never skips the
authorization; what an authorization allows and what counts as evidence; and the registrations the
team writes at the door (annulling one, going over a rule of the page on purpose)."""

from datetime import date, timedelta
from unittest import mock

from django.urls import reverse

from agenda import services
from agenda.models import EventPage, EventRegistration, GuardianConsent
from agenda.tests.test_event_extras import AGE_TEXT, CONSENT, PENDING_TEXT, ExtrasFixture
from core.templatetags.shell import icon
from events.models import Event
from groups.forms import MyDataForm
from people import policy
from people import services as people_services
from people.models import ChurchAuditEvent, Person

CONFIRMED = ("inscrita", "")
WAITS_AGE = ("pendiente", "age")
WAITS_CONSENT = ("pendiente", "consent")
CALL = {**CONSENT, "method": "verificado"}
OVERRIDE = {"override_reason": "llego"}
# How the event's page titles a registration that waits, or is confirmed (the notice's first line).
SIGNED_UP = '<p class="notice__title">Te inscribiste.</p>'
WAITS_CONSENT_TITLE = '<p class="notice__title">Pendiente de autorización.</p>'
# The age on a person's page, as a pill: confirmed by the team, or not.
AGE_CONFIRMED = '<span class="status status--done"><span class="status__mark" aria-hidden="true"></span>Edad confirmada</span>'
AGE_UNCONFIRMED = '<span class="status status--pending"><span class="status__mark" aria-hidden="true"></span>Edad sin confirmar por el equipo</span>'


def where(row: EventRegistration) -> tuple[str, str]:
    row.refresh_from_db()
    return row.status, row.pending_reason


class SafeguardFixture(ExtrasFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def people_of_the_table(self):
        """One person per row of the table that has a ``Person``: who they are and where they must stand."""
        self.verified(self.asked, 30)       # adult, the team verified the date
        self.born(self.leader, 30)          # adult by a date somebody typed
        #     self.no_account               # unknown date of birth
        self.born(self.member, 15)          # minor by a typed date: believed
        self.verified(self.coord_person, 15)  # minor, the team verified the date
        return [(self.asked, CONFIRMED), (self.leader, WAITS_AGE), (self.no_account, WAITS_AGE),
                (self.member, WAITS_CONSENT), (self.coord_person, WAITS_CONSENT)]

    def user_of(self, person):
        return person.user or self.admin


class TheOneFunctionTests(SafeguardFixture):
    """``services.standing``, row by row, without a database row in between."""

    def test_every_row_of_the_table(self):
        self.require_consent()
        page = EventPage.objects.get(pk=self.page.pk)
        for person, expected in self.people_of_the_table():
            with self.subTest(person=person.full_name):
                self.assertEqual(tuple(services.standing(self.retiro, page, person=person)), expected)
        paper, call = GuardianConsent(**CONSENT), GuardianConsent(**CALL)
        withdrawn = GuardianConsent(**CONSENT, revoked_at=self.retiro.created_at)
        other_words = GuardianConsent(**CONSENT, policy_version="borrador-2025-01")
        no_part = GuardianConsent(**{**CONSENT, "allows_participation": False, "allows_transport": True})
        for minor in (self.member, self.coord_person):
            self.assertEqual(tuple(services.standing(self.retiro, page, person=minor, consent=paper)), CONFIRMED)
            for not_enough in (call, withdrawn, other_words, no_part, None):
                self.assertEqual(tuple(services.standing(self.retiro, page, person=minor, consent=not_enough)), WAITS_CONSENT)
        # An authorization says nothing about an age nobody verified.
        for person in (self.leader, self.no_account):
            self.assertEqual(tuple(services.standing(self.retiro, page, person=person, consent=paper)), WAITS_AGE)
        # A visitor: the team's word at the door is all there is, and no word means no age is assumed.
        self.assertEqual(tuple(services.standing(self.retiro, page, said_minor=False)), WAITS_AGE)
        self.assertEqual(tuple(services.standing(self.retiro, page, age_checked=True)), CONFIRMED)
        self.assertEqual(tuple(services.standing(self.retiro, page, consent=paper)), WAITS_AGE)
        self.assertEqual(tuple(services.standing(self.retiro, page, said_minor=True, age_checked=True)), WAITS_CONSENT)
        # "Seen as an adult" is a word about a visitor: it never stands in for a person's verified date.
        for person in (self.leader, self.no_account):
            self.assertEqual(tuple(services.standing(self.retiro, page, person=person, age_checked=True)), WAITS_AGE)
        self.assertEqual(tuple(services.standing(self.retiro, page, said_minor=True)), WAITS_CONSENT)
        self.assertEqual(tuple(services.standing(self.retiro, page, said_minor=True, consent=paper)), CONFIRMED)
        self.assertEqual(tuple(services.standing(self.retiro, page, said_minor=True, consent=call)), WAITS_CONSENT)
        # The team's "minor" about a person counts only while the date is unknown, and only towards "minor".
        self.assertEqual(tuple(services.standing(self.retiro, page, person=self.no_account, said_minor=True)), WAITS_CONSENT)
        self.assertEqual(tuple(services.standing(self.retiro, page, person=self.asked, said_minor=True)), CONFIRMED)
        self.assertEqual(tuple(services.standing(self.retiro, page, person=self.leader, said_minor=True)), WAITS_AGE)

    def test_an_event_that_does_not_ask_confirms_everybody(self):
        page = EventPage.objects.get(pk=self.page.pk)
        for person, _ in self.people_of_the_table():
            self.assertEqual(tuple(services.standing(self.retiro, page, person=person)), CONFIRMED)
        self.assertEqual(tuple(services.standing(self.retiro, None, person=self.member)), CONFIRMED)
        # A row that already waits is judged as if the event asked.
        self.assertEqual(tuple(services.standing(self.retiro, page, person=self.member, assume_rule=True)), WAITS_CONSENT)

    def test_the_first_day_of_the_event_decides_who_is_a_minor(self):
        self.require_consent()
        page, first = EventPage.objects.get(pk=self.page.pk), self.retiro.starts_on
        Person.objects.filter(pk=self.asked.pk).update(birth_date=date(first.year - 18, first.month, first.day) + timedelta(days=1))
        self.asked.refresh_from_db()
        people_services.verify_birth_date(self.asked, self.admin, "registro")
        self.asked.refresh_from_db()
        self.assertEqual(tuple(services.standing(self.retiro, page, person=self.asked)), WAITS_CONSENT)  # 18 the day after


class SignUpTableTests(SafeguardFixture):
    def test_signing_up_follows_the_table(self):
        self.require_consent()
        for person, expected in self.people_of_the_table():
            with self.subTest(person=person.full_name):
                row = services.register(self.retiro, person, self.user_of(person))
                self.assertEqual((row.status, row.pending_reason, row.attended), (*expected, None))
        self.assertEqual(services.taken(self.retiro), 5)  # who waits holds the place

    def test_the_member_reads_which_one_it_is_and_what_to_do(self):
        self.require_consent()
        self.born(self.leader, 30)
        self.born(self.member, 15)
        for user, title, text, word in (
            (self.leader_user, '<p class="notice__title">Falta confirmar tu edad.</p>', AGE_TEXT, "Falta confirmar tu edad"),
            (self.member_user, WAITS_CONSENT_TITLE, PENDING_TEXT, "Pendiente de autorización"),
        ):
            self.client.force_login(user)
            response = self.client.post(self.url("register"), follow=True)
            self.assertContains(response, title)
            self.assertContains(response, text)
            self.assertNotContains(response, SIGNED_UP)
            self.assertContains(self.client.get(reverse("agenda:list")), word)
        self.assertContains(self.client.get(self.url()), WAITS_CONSENT_TITLE)

    def test_a_minor_with_a_valid_authorization_and_one_withdrawn(self):
        row = self.minor_pending()
        services.record_consent(row, self.admin, **CONSENT)
        self.assertEqual(where(row), CONFIRMED)
        # Cancel and sign up again: the same event, the same words, in force, for taking part.
        services.cancel(row, self.member_user)
        self.assertEqual(where(services.register(self.retiro, self.member, self.member_user)), CONFIRMED)
        # Withdrawn: not authorized to take part, said as it is to who administers and to the minor.
        services.revoke_consent(row, self.admin)
        self.assertEqual(where(row), WAITS_CONSENT)
        self.assertContains(self.client.get(self.url("registrations")), "<strong>Sin autorización.</strong>")
        self.client.force_login(self.member_user)
        detail = self.client.get(self.url())
        self.assertContains(detail, '<p class="notice__title">Sin autorización.</p>')
        self.assertContains(detail, "Por ahora no tienes autorización para participar en este evento.")
        self.assertNotContains(detail, SIGNED_UP)
        self.assertNotContains(detail, "Marta")
        services.cancel(row, self.member_user)
        self.assertEqual(where(services.register(self.retiro, self.member, self.member_user)), WAITS_CONSENT)

    def test_an_authorization_survives_only_under_the_same_words(self):
        row = self.minor_pending()
        services.record_consent(row, self.admin, **CONSENT)
        self.assertEqual(GuardianConsent.objects.get().policy_version, "borrador-2026-11")
        services.cancel(row, self.member_user)
        with mock.patch.object(policy, "CONSENT_POLICY_VERSION", "aprobado-2027-01"):
            self.assertEqual(where(services.register(self.retiro, self.member, self.member_user)), WAITS_CONSENT)
            # Recorded again, it is recorded under the new words and confirms.
            services.record_consent(row, self.admin, **CONSENT)
            self.assertEqual(where(row), CONFIRMED)
            self.assertEqual(GuardianConsent.objects.get().policy_version, "aprobado-2027-01")

    def test_an_authorization_is_of_one_event_only(self):
        row = self.minor_pending()
        services.record_consent(row, self.admin, **CONSENT)
        other = self._future(self.church, "Convivencia", "convivencia", 3, 3)
        services.save_page(other, self.admin, published=True, registration="abierta", requires_guardian_consent=True)
        self.assertEqual(where(services.register(other, self.member, self.member_user)), WAITS_CONSENT)


class TurningTheRuleOnTests(SafeguardFixture):
    def test_every_registration_is_evaluated_and_nothing_stays_confirmed_by_default(self):
        table = self.people_of_the_table()
        rows = {person.pk: services.register(self.retiro, person, self.user_of(person)) for person, _ in table}
        self.start()
        guests = {
            "adult": services.register_walkup(self.retiro, self.admin, first_name="Ada", last_name="Adulta"),  # nobody said
            "seen": services.register_walkup(self.retiro, self.admin, first_name="Eva", last_name="Vista", seen_adult=True),
            "minor": services.register_walkup(self.retiro, self.admin, first_name="Mía", last_name="Menor", is_minor=True),
            "signed": services.register_walkup(self.retiro, self.admin, first_name="Leo", last_name="Firmado", is_minor=True),
            "called": services.register_walkup(self.retiro, self.admin, first_name="Luz", last_name="Llamada", is_minor=True),
        }
        # Rows from before this slice: an authorization on paper, and one "verified" by a call that had confirmed.
        GuardianConsent.objects.create(registration=guests["signed"], guardian_name="Tutora", relationship="madre", method="papel",
                                       allows_participation=True)
        GuardianConsent.objects.create(registration=guests["called"], guardian_name="Tutor", relationship="padre",
                                       method="verificado", allows_participation=True)
        everybody = [*rows.values(), *guests.values()]
        services.record_attendance(self.retiro, [row.pk for row in everybody], self.admin)
        for row in everybody:
            self.assertEqual((*where(row), row.attended), (*CONFIRMED, True))  # the event did not ask

        services.save_page(self.retiro, self.admin, requires_guardian_consent=True)

        for person, expected in table:
            with self.subTest(person=person.full_name):
                row = rows[person.pk]
                self.assertEqual(where(row), expected)
                self.assertEqual((row.attended, row.checked_in_at), (True, row.checked_in_at) if expected == CONFIRMED else (None, None))
        for key, expected in (("adult", WAITS_AGE), ("seen", CONFIRMED), ("minor", WAITS_CONSENT), ("signed", CONFIRMED), ("called", WAITS_CONSENT)):
            with self.subTest(guest=key):
                self.assertEqual(where(guests[key]), expected)
                self.assertIs(guests[key].attended, True if expected == CONFIRMED else None)
        # The attendance list no longer has who waits, and each change left its row in the audit.
        self.assertEqual(services.record_attendance(self.retiro, [row.pk for row in everybody], self.admin), (3, 3))
        reviewed = ChurchAuditEvent.objects.filter(action="evento_inscripcion_revisada")
        self.assertEqual(reviewed.count(), 7)
        self.assertEqual(sorted(audit.metadata["standing"] for audit in reviewed),
                         ["age", "age", "age", "consent", "consent", "consent", "consent"])
        for audit in reviewed:
            self.assertLessEqual(set(audit.metadata), {"event_id", "registration_id", "person_id", "standing"})
        page = self.client.get(self.url("registrations"))
        self.assertContains(page, "3 personas inscritas, 3 con edad por confirmar, 4 pendientes de autorización.")
        text = page.content.decode()
        self.assertLess(text.index("Edad por confirmar"), text.index("Pendientes de autorización"))
        self.assertLess(text.index("Pendientes de autorización"), text.index("Personas inscritas"))
        self.assertContains(page, "Confirmar edad", count=3)
        self.assertContains(page, "Nadie dijo si es mayor o menor de edad.", count=1)
        self.assertContains(page, "Nota de contacto", count=1)
        row_facts = self.client.get(reverse("agenda:admin_list")).content.decode().split("Retiro de jóvenes</a>", 1)[1].split("</ul>", 1)[0]
        self.assertIn(f'<li>{icon("personas", "sm")}3 personas inscritas</li>', row_facts)
        self.assertIn('<li><span class="chip chip--wait">3 con edad por confirmar</span></li>', row_facts)
        self.assertIn('<li><span class="chip chip--wait">4 pendientes de autorización</span></li>', row_facts)

    def test_saving_the_page_again_keeps_asking(self):
        """Not only the moment the rule is turned on: while the page asks, every save settles every row."""
        self.require_consent()
        self.verified(self.asked)
        row = services.register(self.retiro, self.asked, self.asked_user)
        EventRegistration.objects.filter(pk=row.pk).update(status="inscrita", pending_reason="")
        Person.objects.filter(pk=self.asked.pk).update(birth_date_verified_at=None)  # as if it had been confirmed by default
        services.save_page(self.retiro, self.admin, capacity=80)
        self.assertEqual(where(row), WAITS_AGE)

    def test_turning_the_rule_off_confirms_nobody(self):
        self.require_consent()
        rows = [services.register(self.retiro, person, self.user_of(person)) for person, _ in self.people_of_the_table()]
        before = [where(row) for row in rows]
        services.save_page(self.retiro, self.admin, requires_guardian_consent=False)
        self.assertEqual([where(row) for row in rows], before)
        self.assertEqual(before.count(CONFIRMED), 1)
        # While it is off, what the church learns about an age confirms nobody either.
        people_services.verify_birth_date(self.leader, self.admin, "documento")
        self.assertEqual([where(row) for row in rows], before)
        self.assertFalse(ChurchAuditEvent.objects.filter(action="evento_inscripcion_revisada").exists())
        # On again: the one function, with what the church knows now.
        services.save_page(self.retiro, self.admin, requires_guardian_consent=True)
        self.assertEqual(where(rows[1]), CONFIRMED)
        self.assertEqual([where(row) for row in rows[2:]], before[2:])


class NothingElseConfirmsTests(SafeguardFixture):
    def waiting_rows(self):
        self.require_consent()
        self.born(self.leader, 30)
        self.born(self.member, 15)
        age = services.register(self.retiro, self.leader, self.leader_user)
        consent = services.register(self.retiro, self.member, self.member_user)
        self.assertEqual((where(age), where(consent)), (WAITS_AGE, WAITS_CONSENT))
        return age, consent

    def test_no_path_confirms_a_row_that_waits(self):
        age, consent = self.waiting_rows()
        rows = ((age, self.leader, self.leader_user, WAITS_AGE), (consent, self.member, self.member_user, WAITS_CONSENT))

        def still_waiting():
            for row, _person, _user, expected in rows:
                self.assertEqual((*where(row), row.attended, row.checked_in_at), (*expected, None, None))

        for row, person, user, _ in rows:
            # Signing up again, by the screen and by the service; cancelling and coming back.
            self.client.force_login(user)
            self.client.post(self.url("register"))
            self.assertEqual(services.register(self.retiro, person, user).pk, row.pk)
            still_waiting()
            services.cancel(row, user)
            self.assertEqual(services.register(self.retiro, person, user).pk, row.pk)
            still_waiting()
        # The page changes: more places, fewer, none, the rule off and on again.
        for fields in ({"capacity": 50}, {"capacity": 2}, {"capacity": None}, {"requires_guardian_consent": False},
                       {"description": "Otra"}, {"requires_guardian_consent": True}):
            services.save_page(self.retiro, self.admin, **fields)
            still_waiting()
        # Attendance: they are not on the list and their ids are ignored.
        self.start()
        self.client.force_login(self.admin)
        page = self.client.get(self.url("registrations"))
        for row in (age, consent):
            self.assertNotContains(page, f'id="vino-{row.pk}"')
        self.client.post(self.url("registrations"), {"vino": [age.pk, consent.pk], "lista": [age.pk, consent.pk]})
        self.assertEqual(services.record_attendance(self.retiro, [age.pk, consent.pk], self.admin), (0, 0))
        still_waiting()
        # Arriving at the door: already on the list; after a cancellation, the walk-up waits the same.
        for row, person, _user, expected in rows:
            with self.assertRaises(services.AgendaError) as caught:
                services.register_walkup(self.retiro, self.admin, person=person)
            self.assertEqual(caught.exception.code, "already")
            EventRegistration.objects.filter(pk=row.pk).update(status="cancelada", pending_reason="")
            back = services.register_walkup(self.retiro, self.admin, person=person)
            self.assertEqual((back.pk, back.status, back.pending_reason, back.attended), (row.pk, *expected, None))
        # An authorization is not a way around the age, and an age is not a way around the authorization.
        with self.assertRaises(services.AgendaError) as caught:
            services.record_consent(age, self.admin, **CONSENT)
        self.assertEqual(caught.exception.code, "age_first")
        with self.assertRaises(services.AgendaError) as caught:
            services.confirm_age(consent, self.admin, "documento", date(1990, 1, 1))
        self.assertEqual(caught.exception.code, "not_age")
        still_waiting()
        self.assertFalse(GuardianConsent.objects.exists())

    def test_a_visitor_who_waits_keeps_waiting_when_added_to_the_people_of_the_church(self):
        self.require_consent()
        self.start()
        self.verified(self.asked, 30)
        self.born(self.leader, 30)
        self.born(self.member, 15)
        # A new person, a minor, and an adult: a visitor marked as a minor is never recognized as an adult.
        new = services.register_walkup(self.retiro, self.admin, first_name="Mía", is_minor=True)
        services.promote_guest(new, self.admin)
        self.assertEqual((*where(new), new.attended, new.guest_is_minor), (*WAITS_CONSENT, None, True))
        known_minor = services.register_walkup(self.retiro, self.admin, first_name="Diego", is_minor=True)
        services.promote_guest(known_minor, self.admin, existing=self.member)
        self.assertEqual((*where(known_minor), known_minor.guest_is_minor), (*WAITS_CONSENT, False))
        for adult in (self.asked, self.leader):
            guest = services.register_walkup(self.retiro, self.admin, first_name=adult.first_name, is_minor=True)
            with self.assertRaises(services.AgendaError) as caught:
                services.promote_guest(guest, self.admin, existing=adult)
            self.assertEqual(caught.exception.code, "age_conflict")
            self.assertEqual((*where(guest), guest.person), (*WAITS_CONSENT, None))
        response = self.client.post(self.url("guest_promote", guest.pk), {"persona": self.leader.pk}, follow=True)
        self.assertContains(response, "registraron a esta visita como menor de edad")

    def test_a_confirmed_visitor_is_settled_by_the_record_of_who_they_turn_out_to_be(self):
        self.require_consent()
        self.start()
        self.verified(self.asked, 30)
        self.born(self.leader, 30)

        def guest(**fields):
            fields.setdefault("seen_adult", not fields.get("is_minor"))
            row = services.register_walkup(self.retiro, self.admin, first_name="Vera", **fields)
            self.assertEqual((*where(row), row.attended), (*CONFIRMED, True))
            return row

        # The team's eyes at the door confirm a visitor; a record of the church is judged as a record:
        # "seen as an adult" stays written on the row and no longer counts once there is a person.
        row = guest()
        self.assertTrue(row.guest_age_checked)
        self.assertContains(self.client.get(self.url("guest_promote", row.pk)), "queda pendiente hasta que confirmes su edad")
        services.promote_guest(row, self.admin)
        self.assertEqual((*where(row), row.attended), (*WAITS_AGE, None))
        row = guest()
        services.promote_guest(row, self.admin, existing=self.leader)
        self.assertEqual((*where(row), row.attended), (*WAITS_AGE, None))
        row = guest()
        services.promote_guest(row, self.admin, existing=self.asked)
        self.assertEqual((*where(row), row.attended), (*CONFIRMED, True))
        # A minor the team authorized at the door stays authorized as a new person.
        row = guest(is_minor=True, consent=CONSENT)
        services.promote_guest(row, self.admin)
        self.assertEqual((*where(row), row.attended), (*CONFIRMED, True))


class AgeVerificationTests(SafeguardFixture):
    def test_only_the_service_leaves_a_date_verified_and_the_audit_has_ids_and_the_method(self):
        self.assertFalse(self.member.birth_date_verified)
        for method, birth_date, code in (("pasaporte", date(1990, 3, 14), "method"), ("documento", None, "birth_date"),
                                         ("documento", self.today + timedelta(days=1), "birth_date")):
            with self.assertRaises(people_services.PeopleError) as caught:
                people_services.verify_birth_date(self.member, self.admin, method, birth_date)
            self.assertEqual(caught.exception.code, code)
        person = people_services.verify_birth_date(self.member, self.admin, "documento", date(1990, 3, 14))
        self.assertEqual((person.birth_date, person.birth_date_verified, person.birth_date_verified_by, person.birth_date_verified_method),
                         (date(1990, 3, 14), True, self.admin, "documento"))
        audit = ChurchAuditEvent.objects.get(action="persona_edad_confirmada")
        self.assertEqual((audit.actor, audit.metadata), (self.admin, {"person_id": self.member.pk, "method": "documento"}))
        self.assertNotIn("1990", f"{audit.summary} {audit.metadata}")
        # Nothing about a document can be kept: there is no column for it.
        names = " ".join(field.name for field in Person._meta.get_fields())
        for word in ("document", "image", "photo", "foto", "numero", "curp", "file"):
            self.assertNotIn(word, names)
        self.assertEqual([choice.label for choice in Person.BirthDateMethod], [
            "Acta o documento oficial visto por el equipo", "Registro de la iglesia",
            "Lo confirmó su madre, padre o tutor en persona",
        ])
        detail = self.client.get(reverse("people:detail", args=[self.member.pk]))
        self.assertContains(detail, AGE_CONFIRMED)
        self.assertContains(detail, "Por el equipo el ")
        self.assertNotContains(detail, AGE_UNCONFIRMED)

    def test_changing_the_date_any_other_way_clears_the_verification(self):
        def verified_again() -> Person:
            return people_services.verify_birth_date(Person.objects.get(pk=self.member.pk), self.admin, "registro", date(1990, 3, 14))

        def cleared() -> bool:
            person = Person.objects.get(pk=self.member.pk)
            return (person.birth_date_verified_at, person.birth_date_verified_by, person.birth_date_verified_method) == (None, None, "")

        # The administration edits the person.
        verified_again()
        data = {"first_name": "Diego", "last_name": "Hernández Cruz", "email": self.member.email, "phone": "55 1111 2222",
                "preferred_contact_method": "email", "birth_date": "15/03/1990"}
        self.client.post(reverse("people:edit", args=[self.member.pk]), data)
        self.assertEqual(Person.objects.get(pk=self.member.pk).birth_date, date(1990, 3, 15))
        self.assertTrue(cleared())
        cleared_page = self.client.get(reverse("people:detail", args=[self.member.pk]))
        self.assertContains(cleared_page, AGE_UNCONFIRMED)
        self.assertNotContains(cleared_page, AGE_CONFIRMED)
        # The same form with the same date keeps it: only a change of the date clears.
        person = verified_again()
        self.client.post(reverse("people:edit", args=[self.member.pk]), {**data, "birth_date": "14/03/1990", "phone": "55 0000 1111"})
        self.assertEqual(Person.objects.get(pk=self.member.pk).phone, "55 0000 1111")
        self.assertFalse(cleared())
        # ``save_person``, a bare save and a save of that one column.
        person.birth_date = date(1991, 1, 1)
        people_services.save_person(person, self.admin, created=False)
        self.assertTrue(cleared())
        person = verified_again()
        person.birth_date = None
        person.save()
        self.assertTrue(cleared())
        person = verified_again()
        person.birth_date = date(2012, 1, 1)
        person.save(update_fields=["birth_date"])
        self.assertTrue(cleared())
        # A person never arrives verified.
        new = Person(church=self.church, first_name="Nueva", birth_date=date(1990, 1, 1), birth_date_verified_method="documento",
                     birth_date_verified_at=self.retiro.created_at, birth_date_verified_by=self.admin)
        people_services.save_person(new, self.admin, created=True)
        self.assertFalse(Person.objects.get(pk=new.pk).birth_date_verified)

    def test_the_person_cannot_change_or_verify_their_own_date(self):
        people_services.verify_birth_date(self.member, self.admin, "documento", date(1990, 3, 14))
        self.assertNotIn("birth_date", MyDataForm.base_fields)
        self.assertFalse([name for name in MyDataForm.base_fields if name.startswith("birth_date")])
        self.client.force_login(self.member_user)
        self.client.post(reverse("community:profile"), {"phone": "55 0000 0000", "email": "", "preferred_contact_method": "phone",
                                                         "birth_date": "01/01/2012", "birth_date_verified_method": "documento"})
        services.set_birth_date(Person.objects.get(pk=self.member.pk), date(2012, 1, 1), self.member_user)
        person = Person.objects.get(pk=self.member.pk)
        self.assertEqual((person.phone, person.birth_date, person.birth_date_verified), ("55 0000 0000", date(1990, 3, 14), True))
        # And a date the person types is saved as a claim.
        services.set_birth_date(self.leader, date(1990, 1, 1), self.leader_user)
        self.assertFalse(Person.objects.get(pk=self.leader.pk).birth_date_verified)

    def test_confirming_the_age_from_the_list_settles_the_registration(self):
        self.require_consent()
        self.born(self.leader, 30)
        adult = services.register(self.retiro, self.leader, self.leader_user)
        unknown = services.register(self.retiro, self.no_account, self.admin)
        young = services.register(self.retiro, self.asked, self.asked_user)
        page = self.client.get(self.url("registrations"))
        self.assertContains(page, "Edad por confirmar", count=1)
        self.assertContains(page, "0 personas inscritas, 3 con edad por confirmar.")
        self.assertContains(page, "Confirmar edad", count=3)
        self.assertContains(page, "Tenemos una fecha de nacimiento que el equipo no ha confirmado.", count=1)
        self.assertContains(page, "No tenemos su fecha de nacimiento.", count=2)
        self.assertNotContains(page, "Registrar autorización")
        form = self.client.get(self.url("confirm_age", adult.pk))
        self.assertContains(form, "<h1", count=1)
        self.assertContains(form, "Confirmar su edad", count=1)
        for word in ("Acta o documento oficial visto por el equipo", "Registro de la iglesia",
                     "Lo confirmó su madre, padre o tutor en persona", "No guardes fotos ni números de ningún documento"):
            self.assertContains(form, word)
        self.assertNotContains(form, 'type="file"')
        self.assertContains(form, f'value="{self.leader.birth_date.strftime("%d/%m/%Y")}"')
        missing = self.client.post(self.url("confirm_age", unknown.pk), {})
        self.assertContains(missing, "Escribe su fecha de nacimiento.")
        self.assertContains(missing, "Elige cómo confirmaste su edad.")
        self.assertEqual(where(unknown), WAITS_AGE)
        # An adult: confirmed.
        response = self.client.post(self.url("confirm_age", adult.pk),
                                    {"birth_date": self.leader.birth_date.strftime("%d/%m/%Y"), "method": "documento"}, follow=True)
        self.assertContains(response, "Confirmaste su edad. La inscripción de Gala Guía quedó confirmada.")
        self.assertEqual(where(adult), CONFIRMED)
        self.assertEqual(self.client.get(self.url("confirm_age", adult.pk)).status_code, 404)
        # A minor: on to the other queue, and never confirmed by the age alone.
        first = self.retiro.starts_on
        response = self.client.post(self.url("confirm_age", young.pk),
                                    {"birth_date": date(first.year - 15, 1, 1).strftime("%d/%m/%Y"), "method": "tutor"}, follow=True)
        self.assertContains(response, "Pía Pidió es menor de edad. Su inscripción queda pendiente")
        self.assertEqual(where(young), WAITS_CONSENT)
        self.assertContains(response, "Registrar autorización", count=1)
        self.asked.refresh_from_db()
        self.assertEqual((self.asked.birth_date_verified, self.asked.birth_date_verified_method), (True, "tutor"))
        self.assertEqual(ChurchAuditEvent.objects.filter(action="persona_edad_confirmada").count(), 2)
        reviewed = ChurchAuditEvent.objects.filter(action="evento_inscripcion_revisada").order_by("id")
        self.assertEqual([(audit.actor, audit.metadata["standing"]) for audit in reviewed], [(self.admin, "inscrita"), (self.admin, "consent")])

    def test_a_verified_age_reaches_every_event_that_asks_and_a_changed_date_takes_it_back(self):
        self.require_consent()
        other = self._future(self.church, "Convivencia", "convivencia", 3, 4)
        services.save_page(other, self.admin, published=True, registration="abierta", requires_guardian_consent=True)
        ended = self._future(self.church, "Vigilia pasada", "vigilia-pasada", 2, 2)
        services.save_page(ended, self.admin, published=True, registration="abierta", requires_guardian_consent=True)
        self.born(self.leader, 30)
        rows = [services.register(event, self.leader, self.leader_user) for event in (self.retiro, other, ended)]
        self.move(ended, -9, -8)
        services.confirm_age(rows[0], self.admin, "registro")
        self.assertEqual([where(row) for row in rows], [CONFIRMED, CONFIRMED, WAITS_AGE])  # what ended is history
        # The date changes in Personas: it is a claim again, and what it had confirmed waits again.
        self.move(self.retiro, 0, 2)
        services.record_attendance(self.retiro, [rows[0].pk], self.admin)
        self.leader.refresh_from_db()
        self.leader.birth_date = date(1991, 5, 5)
        people_services.save_person(self.leader, self.admin, created=False)
        self.assertEqual([where(row) for row in rows[:2]], [WAITS_AGE, WAITS_AGE])
        self.assertIsNone(rows[0].attended)
        self.assertEqual(ChurchAuditEvent.objects.filter(action="evento_inscripcion_revisada", actor=self.admin).count(), 4)


class ConsentEvidenceTests(SafeguardFixture):
    def test_a_call_or_a_message_is_a_note_and_does_not_confirm(self):
        row = self.minor_pending()
        form = self.client.get(self.url("consent", row.pk))
        self.assertContains(form, "Una llamada o un mensaje se guarda como nota de contacto")
        response = self.client.post(self.url("consent", row.pk), CALL, follow=True)
        self.assertContains(response, "Guardamos la nota de contacto. La inscripción de Diego Hernández Cruz sigue pendiente.")
        self.assertContains(response, "hace falta el formato firmado en papel o verificar en persona")
        self.assertNotContains(response, "quedó confirmada")
        self.assertEqual(where(row), WAITS_CONSENT)
        self.assertContains(response, "Nota de contacto")
        self.assertContains(response, "No confirma: falta el formato firmado o la verificación en persona.")
        self.assertNotContains(response, "Autorizada por")
        self.assertNotContains(response, "Retirar autorización")
        self.assertIn("no confirma", ChurchAuditEvent.objects.get(action="evento_autorizacion").summary)
        self.assertEqual(self.client.get(self.url("consent_revoke", row.pk)).status_code, 404)
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(self.url()), WAITS_CONSENT_TITLE)
        # The two that count.
        for method in ("papel", "en_persona"):
            services.record_consent(row, self.admin, **{**CONSENT, "method": method})
            self.assertEqual(where(row), CONFIRMED)
            services.revoke_consent(row, self.admin)

    def test_a_call_at_the_door_keeps_the_minor_waiting(self):
        self.require_consent()
        self.start()
        response = self.client.post(self.url("walkup"), {"first_name": "Mía", "age": "menor", **CALL}, follow=True)
        self.assertContains(response, "Queda pendiente hasta que registres la autorización")
        self.assertContains(response, "hace falta el formato firmado en papel o verificar en persona")
        row = EventRegistration.objects.get(guest_first_name="Mía")
        self.assertEqual((*where(row), row.attended, row.consent.method), (*WAITS_CONSENT, None, "verificado"))

    def test_taking_part_is_required_and_the_other_purposes_are_choices(self):
        row = self.minor_pending()
        body = {key: value for key, value in CONSENT.items() if key != "allows_participation"}
        refused = self.client.post(self.url("consent", row.pk), {**body, "allows_transport": "on", "allows_photos": "on"})
        self.assertContains(refused, "Marca que autoriza que participe en este evento")
        for fields in (body, {**body, "allows_participation": False}, {**body, "allows_participation": "on"}):
            with self.assertRaises(services.AgendaError):
                services.record_consent(row, self.admin, **fields)
        self.assertFalse(GuardianConsent.objects.exists())
        self.assertEqual(where(row), WAITS_CONSENT)
        self.client.post(self.url("consent", row.pk), {**CONSENT, "allows_participation": "on", "allows_transport": "on"})
        consent = GuardianConsent.objects.get()
        self.assertEqual((consent.allows_participation, consent.allows_transport, consent.allows_photos, consent.policy_version),
                         (True, True, False, "borrador-2026-11"))
        self.assertEqual(where(row), CONFIRMED)
        # Nothing medical and nothing to type: the columns are these.
        names = {field.name for field in GuardianConsent._meta.fields}
        self.assertFalse({name for name in names if any(word in name for word in ("medic", "emergenc", "alerg", "notes", "nota"))})

    def test_only_the_administration_reads_what_was_authorized(self):
        row = self.minor_pending()
        services.record_consent(row, self.admin, **CONSENT, allows_photos=True)
        page = self.client.get(self.url("registrations"))
        self.assertContains(page, "Autorizó participar en el evento. Transporte organizado por la iglesia: no. Fotografías: sí.")
        self.assertEqual(ChurchAuditEvent.objects.get(action="evento_autorizacion").metadata,
                         {"event_id": self.retiro.pk, "registration_id": row.pk, "person_id": self.member.pk, "method": "papel"})
        for user in (self.member_user, self.leader_user):
            self.client.force_login(user)
            for path in (self.url(), reverse("agenda:list"), reverse("community:home")):
                page = self.client.get(path)
                for secret in ("Transporte", "Fotografías", "Marta", "Formato firmado"):
                    self.assertNotContains(page, secret)
            self.assertEqual(self.client.get(self.url("registrations")).status_code, 403)

    def test_the_words_are_marked_as_provisional(self):
        row = self.minor_pending()
        self.assertContains(self.client.get(self.url("consent", row.pk)), "Texto provisional: lo aprueba el liderazgo de la iglesia")
        self.start()
        self.assertContains(self.client.get(self.url("walkup")), "Texto provisional: lo aprueba el liderazgo de la iglesia")
        self.assertEqual(policy.consent_policy_version(self.church), "borrador-2026-11")


class AnnulTests(SafeguardFixture):
    def test_a_mistaken_walk_up_is_annulled_on_its_own_page_with_a_reason(self):
        self.set_page(capacity=2)
        self.start()
        guest = services.register_walkup(self.retiro, self.admin, first_name="Vera", last_name="Visita")
        person = services.register_walkup(self.retiro, self.admin, person=self.no_account)
        page = self.client.get(self.url("registrations"))
        self.assertContains(page, "Anular registro", count=2)
        self.assertContains(page, "2 personas inscritas de 2 lugares")
        form = self.client.get(self.url("annul", guest.pk))
        self.assertContains(form, "<h1", count=1)
        self.assertContains(form, "¿Quieres anular este registro?")
        self.assertContains(form, "Sí, anular el registro", count=1)
        self.assertNotContains(form, "<dialog")
        for word in ("Se registró por error", "Registro duplicado", "La persona no se quedó"):
            self.assertContains(form, word)
        self.assertContains(self.client.post(self.url("annul", guest.pk), {}), "Elige por qué anulas este registro.")
        for reason in ("", "otra", "porque sí"):
            with self.assertRaises(services.AgendaError):
                services.annul_walkup(guest, self.admin, reason)
        self.assertEqual(where(guest), CONFIRMED)
        response = self.client.post(self.url("annul", guest.pk), {"reason": "duplicado"}, follow=True)
        self.assertContains(response, "Anulaste el registro de Vera Visita. Su lugar quedó libre.")
        guest.refresh_from_db()
        self.assertEqual((guest.status, guest.annulled_reason, guest.attended, guest.checked_in_at, guest.guest_first_name),
                         ("cancelada", "duplicado", None, None, "Vera"))  # the row stays, for the audit
        self.assertIsNotNone(guest.cancelled_at)
        audit = ChurchAuditEvent.objects.get(action="evento_registro_anulado")
        self.assertEqual((audit.actor, audit.metadata), (self.admin, {"event_id": self.retiro.pk, "registration_id": guest.pk, "reason": "duplicado"}))
        # Out of every list and every count: not even among who cancelled.
        self.assertContains(response, "1 persona inscrita de 2 lugares.")
        self.assertNotContains(response, "Vera Visita</p>")
        self.assertNotContains(response, "cancel")
        self.assertNotContains(response, f'id="vino-{guest.pk}"')
        self.assertEqual(services.taken(self.retiro), 1)
        self.assertEqual(services.record_attendance(self.retiro, [guest.pk, person.pk], self.admin), (1, 1))
        self.assertContains(self.client.get(reverse("agenda:admin_list")), "1 persona inscrita")
        for path in (self.url("annul", guest.pk), self.url("guest_promote", guest.pk)):
            self.assertEqual(self.client.get(path).status_code, 404)
        with self.assertRaises(services.AgendaError) as caught:
            services.annul_walkup(guest, self.admin, "error")
        self.assertEqual(caught.exception.code, "not_walkup")
        # The place is free for somebody else.
        self.assertEqual(services.register_walkup(self.retiro, self.admin, first_name="Otra").status, "inscrita")

    def test_a_person_of_the_church_annulled_stays_a_person_and_may_arrive_again(self):
        self.start()
        row = services.register_walkup(self.retiro, self.admin, person=self.no_account)
        self.assertContains(self.client.get(self.url("annul", row.pk)), "Nora sigue en Personas.")
        services.annul_walkup(row, self.admin, "no_se_quedo")
        self.assertTrue(Person.objects.filter(pk=self.no_account.pk).exists())
        back = services.register_walkup(self.retiro, self.admin, person=self.no_account)
        self.assertEqual((back.pk, back.status, back.annulled_reason, back.attended), (row.pk, "inscrita", "", True))
        services.annul_walkup(back, self.admin, "error")
        self.client.force_login(self.member_user)

    def test_who_signed_up_by_themselves_is_not_annulled(self):
        row = services.register(self.retiro, self.member, self.member_user)
        self.start()
        self.assertNotContains(self.client.get(self.url("registrations")), "Anular registro")
        self.assertEqual(self.client.get(self.url("annul", row.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url("annul", row.pk), {"reason": "error"}).status_code, 404)
        with self.assertRaises(services.AgendaError) as caught:
            services.annul_walkup(row, self.admin, "error")
        self.assertEqual(caught.exception.code, "not_walkup")
        self.assertEqual(where(row), CONFIRMED)

    def test_a_walk_up_that_waits_can_be_annulled_too(self):
        self.require_consent()
        self.start()
        minor = services.register_walkup(self.retiro, self.admin, first_name="Mía", is_minor=True)
        unknown = services.register_walkup(self.retiro, self.admin, person=self.no_account)
        self.assertContains(self.client.get(self.url("registrations")), "Anular registro", count=2)
        for row in (minor, unknown):
            services.annul_walkup(row, self.admin, "error")
            self.assertEqual((*where(row), row.annulled_reason), ("cancelada", "", "error"))
        self.assertEqual(services.taken(self.retiro), 0)


class WalkUpOverrideTests(SafeguardFixture):
    """A walk-up respects the page like a sign-up; the administration goes over a rule on purpose."""

    def rules(self):
        """(name, how to get there, the event, the words the form says)."""
        yesterday = self.today - timedelta(days=1)
        return [
            ("sin_pagina", lambda: None, self.no_page, "Este evento no tiene página"),
            ("sin_publicar", lambda: self.set_page(published=False), self.retiro, "Este evento no está publicado para la iglesia."),
            ("sin_inscripcion", lambda: self.set_page(published=True, registration="cerrada"), self.retiro,
             "La inscripción a este evento no se hace desde Emmaus."),
            ("cerro", lambda: self.set_page(registration="abierta", registration_closes_on=yesterday), self.retiro,
             "La inscripción a este evento ya cerró."),
        ]

    def setUp(self):
        super().setUp()
        self.start()
        self.move(self.no_page, 0, 0)

    def test_nothing_to_override_when_the_page_takes_sign_ups(self):
        form = self.client.get(self.url("walkup"))
        self.assertNotContains(form, "Registrar de todos modos")
        self.assertNotContains(form, 'name="override')
        self.assertEqual(services.walkup_blocks(self.retiro, EventPage.objects.get(pk=self.page.pk), 0), [])
        row = services.register_walkup(self.retiro, self.admin, first_name="Vera", override=["cerro", "llena"], override_reason="llego")
        self.assertNotIn("override", ChurchAuditEvent.objects.get(action="evento_registro_puerta").metadata)
        self.assertEqual(where(row), CONFIRMED)

    def test_each_rule_of_the_page_is_said_and_needs_an_explicit_override_with_a_reason(self):
        for rule, arrange, event, words in self.rules():
            with self.subTest(rule=rule):
                arrange()
                url, before = self.url("walkup", event=event), EventRegistration.objects.count()
                form = self.client.get(url)
                self.assertContains(form, words)
                self.assertContains(form, "Registrar de todos modos", count=1)
                self.assertContains(form, "¿Por qué la registras de todos modos?")
                self.assertNotContains(form, 'name="override_full"')
                # Nothing ticked, the box without a reason, a reason without the box: nothing is written.
                for body, message in (
                    ({}, "Marca «Registrar de todos modos»"), ({"override": "on"}, "Elige por qué la registras de todos modos."),
                    ({"override_reason": "llego"}, "Marca «Registrar de todos modos»"),
                    ({"override": "on", "override_reason": "porque sí"}, "Elige por qué la registras de todos modos."),
                ):
                    self.assertContains(self.client.post(url, {"first_name": "Vera", **body}), message)
                with self.assertRaises(services.AgendaError) as caught:
                    services.register_walkup(event, self.admin, first_name="Vera")
                self.assertEqual(caught.exception.code, rule)
                for override, reason in (([rule], ""), ([rule], "porque sí"), (["llena"], "llego")):
                    with self.assertRaises(services.AgendaError):
                        services.register_walkup(event, self.admin, first_name="Vera", override=override, override_reason=reason)
                self.assertEqual(EventRegistration.objects.count(), before)
                response = self.client.post(url, {"first_name": "Vera", "last_name": rule, "override": "on", "override_reason": "invitado"}, follow=True)
                self.assertContains(response, "Ya quedó en la lista como que vino.")
                row = EventRegistration.objects.get(guest_last_name=rule)
                self.assertEqual((*where(row), row.attended), (*CONFIRMED, True))
                audit = ChurchAuditEvent.objects.filter(action="evento_registro_puerta").latest("id")
                self.assertEqual((audit.actor, audit.metadata), (self.admin, {
                    "event_id": event.pk, "registration_id": row.pk, "minor": False, "override": [rule], "reason": "invitado"}))
                self.assertIn("pasó por encima de una regla del evento", audit.summary)

    def test_the_capacity_holds_unless_it_is_overridden_by_its_own_checkbox(self):
        self.set_page(capacity=1, registration="cerrada")
        services.register_walkup(self.retiro, self.admin, first_name="Primera", override=["sin_inscripcion"], override_reason="llego")
        form = self.client.get(self.url("walkup"))
        self.assertContains(form, "El evento está lleno: ya no hay lugares.")
        self.assertContains(form, "El evento está lleno: registrar de todos modos, aunque ya no haya lugares", count=1)
        self.assertContains(form, 'name="override"')
        # Going over the page is not going over the capacity.
        response = self.client.post(self.url("walkup"), {"first_name": "Otra", "override": "on", "override_reason": "llego"})
        self.assertContains(response, "Marca esta casilla si decides registrarla aunque ya no haya lugares.")
        with self.assertRaises(services.AgendaError) as caught:
            services.register_walkup(self.retiro, self.admin, first_name="Otra", override=["sin_inscripcion"], override_reason="llego")
        self.assertEqual(caught.exception.code, "full")
        with self.assertRaises(services.AgendaError) as caught:
            services.register_walkup(self.retiro, self.admin, first_name="Otra", override=["llena"], override_reason="llego")
        self.assertEqual(caught.exception.code, "sin_inscripcion")
        self.assertEqual(services.taken(self.retiro), 1)
        response = self.client.post(self.url("walkup"), {"first_name": "Otra", "override": "on", "override_full": "on",
                                                         "override_reason": "coordinacion"}, follow=True)
        self.assertContains(response, "Registraste a Otra.")
        self.assertContains(response, "2 personas inscritas de 1 lugar")
        audit = ChurchAuditEvent.objects.filter(action="evento_registro_puerta").latest("id")
        self.assertEqual((audit.metadata["override"], audit.metadata["reason"]), (["sin_inscripcion", "llena"], "coordinacion"))
        # A member still cannot sign up past the capacity.
        self.set_page(registration="abierta")
        with self.assertRaises(services.AgendaError) as caught:
            services.register(self.retiro, self.member, self.member_user)
        self.assertEqual(caught.exception.code, "full")

    def test_the_minors_rule_is_never_overridden(self):
        self.born(self.leader, 30)
        self.born(self.member, 15)
        for rule, arrange, event, _words in [*self.rules(), ("llena", lambda: self.set_page(
                registration="abierta", registration_closes_on=None, capacity=EventRegistration.objects.filter(event=self.retiro).count()),
                self.retiro, "")]:
            with self.subTest(rule=rule):
                arrange()
                if event == self.no_page:
                    EventPage.objects.create(event=event, requires_guardian_consent=True, published=False)
                    rule = "sin_publicar"
                    over = {"override": ["sin_publicar", "sin_inscripcion"], "override_reason": "llego"}
                else:
                    EventPage.objects.filter(event=event).update(requires_guardian_consent=True)
                    over = {"override": [rule], "override_reason": "llego"}
                form = self.client.get(self.url("walkup", event=event))
                self.assertContains(form, "La autorización para menores de edad no se puede saltar.")
                minor = services.register_walkup(event, self.admin, first_name="Mía", last_name=rule, is_minor=True, **over)
                self.assertEqual((*where(minor), minor.attended), (*WAITS_CONSENT, None))
                called = services.register_walkup(event, self.admin, first_name="Luz", is_minor=True, consent=CALL, **over)
                self.assertEqual(where(called), WAITS_CONSENT)
                audit = ChurchAuditEvent.objects.filter(action="evento_registro_puerta").latest("id")
                self.assertIn("override", audit.metadata)
                self.assertIn("pendiente de autorización", audit.summary)
                for person, expected in ((self.leader, WAITS_AGE), (self.no_account, WAITS_AGE), (self.member, WAITS_CONSENT)):
                    row = services.register_walkup(event, self.admin, person=person, **over)
                    self.assertEqual((*where(row), row.attended), (*expected, None))
                    EventRegistration.objects.filter(pk=row.pk).delete()
                # By the screen, with every box ticked.
                body = {"first_name": "Leo", "last_name": rule, "age": "menor", "override": "on", "override_full": "on",
                        "override_reason": "coordinacion"}
                self.client.post(self.url("walkup", event=event), body)
                self.assertEqual(where(EventRegistration.objects.get(event=event, guest_first_name="Leo", guest_last_name=rule)), WAITS_CONSENT)
                EventRegistration.objects.filter(event=event).exclude(person=None, guest_first_name="").filter(guest_is_minor=True).delete()

    def test_an_event_that_is_not_active_counts_as_not_published(self):
        Event.objects.filter(pk=self.retiro.pk).update(status=Event.Status.CLOSED)
        self.retiro.refresh_from_db()
        self.assertEqual(services.walkup_blocks(self.retiro, EventPage.objects.get(pk=self.page.pk), 0), ["sin_publicar"])
