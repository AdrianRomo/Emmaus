"""What an event gained after the first slice: frequent questions, the programme, somebody who
arrived without signing up (a visitor never needs an account), and minors who wait for a
guardian's authorization."""

from datetime import date, time, timedelta

from django.db import IntegrityError, transaction
from django.urls import reverse

from agenda import services
from agenda.models import EventFaq, EventPage, EventRegistration, EventScheduleItem, GuardianConsent
from agenda.tests.test_agenda import AgendaFixture
from core.templatetags.shell import icon
from people import flags
from people import services as people_services
from people.models import ChurchAuditEvent, Person

CONSENT = {"guardian_name": "Marta Tutora Reservada", "relationship": "madre", "method": "papel", "guardian_phone": "55 9999 0000",
           "allows_participation": True}
PENDING_TEXT = "Tu inscripción queda pendiente hasta que tu mamá, papá o tutor la autorice con el equipo de la iglesia."
AGE_TEXT = "Tu inscripción queda pendiente: el equipo de la iglesia tiene que confirmar tu edad."


class ExtrasFixture(AgendaFixture):
    def url(self, name="detail", *args, event=None):
        return reverse(f"agenda:{name}", args=[(event or self.retiro).slug, *args])

    def require_consent(self, **fields):
        self.set_page(requires_guardian_consent=True, **fields)

    def born(self, person, years: float):
        """Give ``person`` an age, counted on the first day of the retreat."""
        first = self.retiro.starts_on
        Person.objects.filter(pk=person.pk).update(birth_date=date(first.year - int(years), first.month, 1) - timedelta(days=40))
        person.refresh_from_db()

    def verified(self, person, years: float = 30):
        """An age the team verified, as ``people.services.verify_birth_date`` leaves it."""
        self.born(person, years)
        people_services.verify_birth_date(person, self.admin, "documento")
        person.refresh_from_db()

    def start(self):
        self.move(self.retiro, 0, 2)

    def minor_pending(self) -> EventRegistration:
        self.require_consent()
        self.born(self.member, 15)
        registration = services.register(self.retiro, self.member, self.member_user)
        self.assertEqual(registration.status, "pendiente")
        return registration


class FaqTests(ExtrasFixture):
    def test_the_administration_adds_orders_edits_and_deletes_questions(self):
        self.client.force_login(self.admin)
        page = self.client.get(self.url("page_edit"))
        self.assertContains(page, self.url("faq"))
        self.assertContains(page, self.url("schedule"))
        self.assertContains(page, "Todavía no tiene preguntas.")
        empty = self.client.get(self.url("faq"))
        self.assertContains(empty, "<h1", count=1)
        self.assertContains(empty, "Agregar la pregunta", count=1)
        self.client.post(self.url("faq"), {"question": "¿Qué llevo?", "answer": "Ropa cómoda\ny tu Biblia.", "position": "2"})
        response = self.client.post(self.url("faq"), {"question": "¿Cuánto cuesta?", "answer": "Nada.", "position": "1"}, follow=True)
        self.assertContains(response, "Agregaste la pregunta.")
        self.assertEqual(list(EventFaq.objects.filter(event=self.retiro).values_list("question", flat=True)),
                         ["¿Cuánto cuesta?", "¿Qué llevo?"])
        first = EventFaq.objects.get(question="¿Cuánto cuesta?")
        self.assertContains(self.client.get(self.url("faq_edit", first.pk)), "Guardar cambios", count=1)
        self.client.post(self.url("faq_edit", first.pk), {"question": "¿Tiene costo?", "answer": "No.", "position": "5"})
        first.refresh_from_db()
        self.assertEqual((first.question, first.answer, first.position), ("¿Tiene costo?", "No.", 5))
        self.assertContains(self.client.get(self.url("faq_delete", first.pk)), "¿Quieres borrar esta pregunta?")
        self.assertTrue(EventFaq.objects.filter(pk=first.pk).exists())
        self.client.post(self.url("faq_delete", first.pk))
        self.assertFalse(EventFaq.objects.filter(pk=first.pk).exists())
        self.assertContains(self.client.get(self.url("page_edit")), "1 pregunta.")
        audits = ChurchAuditEvent.objects.filter(action="evento_preguntas")
        self.assertEqual(audits.count(), 4)
        for audit in audits:
            self.assertEqual(audit.metadata, {"event_id": self.retiro.pk})
            self.assertNotIn("costo", audit.summary)

    def test_empty_questions_are_refused(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url("faq"), {"question": " ", "answer": "", "position": "1"})
        self.assertContains(response, "Escribe la pregunta.")
        self.assertContains(response, "Escribe la respuesta.")
        self.assertFalse(EventFaq.objects.exists())
        with self.assertRaises(services.AgendaError):
            services.save_faq(self.retiro, self.admin, question="¿?", answer="  ")

    def test_a_member_reads_them_on_the_event_and_nothing_when_there_are_none(self):
        self.client.force_login(self.member_user)
        detail = self.client.get(self.url())
        self.assertNotContains(detail, "Preguntas frecuentes")
        self.assertNotContains(detail, 'id="programa"')
        services.save_faq(self.retiro, self.admin, question="¿Qué llevo?", answer="Ropa cómoda.", position=2)
        services.save_faq(self.retiro, self.admin, question="¿A qué hora salimos?", answer="A las 7.", position=1)
        services.save_faq(self.retiro_b, self.admin_b, question="¿Pregunta de la iglesia B?", answer="Sí.")
        detail = self.client.get(self.url())
        self.assertContains(detail, "<h1", count=1)
        self.assertContains(detail, "Preguntas frecuentes", count=1)
        text = detail.content.decode()
        self.assertLess(text.index("¿A qué hora salimos?"), text.index("¿Qué llevo?"))
        # Each question is folded with its answer under it.
        self.assertContains(detail, '<details class="fold"><summary>¿Qué llevo?</summary><p class="faq__answer">Ropa cómoda.</p></details>', html=True)
        self.assertNotContains(detail, "iglesia B?")
        self.assertNotContains(detail, "Editar")


class ScheduleTests(ExtrasFixture):
    def day(self, offset=0):
        return self.retiro.starts_on + timedelta(days=offset)

    def post(self, **fields):
        data = {"day": self.day().isoformat(), "starts_at": "09:00", "ends_at": "", "title": "Desayuno", "place": ""}
        data.update(fields)
        return self.client.post(self.url("schedule"), data, follow=True)

    def test_the_administration_builds_the_programme(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url("schedule")), "Agregar al programa", count=1)
        self.assertContains(self.post(), "Agregaste el momento al programa.")
        self.post(day=self.day(1).isoformat(), starts_at="18:00", ends_at="19:30", title="Fogata", place="El claro")
        self.post(starts_at="07:30", title="Oración")
        self.assertEqual(
            [(day, [item.title for item in items]) for day, items in services.schedule_by_day(self.retiro)],
            [(self.day(), ["Oración", "Desayuno"]), (self.day(1), ["Fogata"])],
        )
        item = EventScheduleItem.objects.get(title="Fogata")
        self.assertEqual((item.starts_at, item.ends_at, item.place), (time(18, 0), time(19, 30), "El claro"))
        self.client.post(self.url("schedule_edit", item.pk),
                         {"day": self.day(2).isoformat(), "starts_at": "20:00", "ends_at": "", "title": "Fogata final", "place": ""})
        item.refresh_from_db()
        self.assertEqual((item.day, item.title, item.ends_at, item.place), (self.day(2), "Fogata final", None, ""))
        self.assertContains(self.client.get(self.url("schedule_delete", item.pk)), "¿Quieres quitar este momento del programa?")
        self.client.post(self.url("schedule_delete", item.pk))
        self.assertFalse(EventScheduleItem.objects.filter(pk=item.pk).exists())
        self.assertEqual(ChurchAuditEvent.objects.filter(action="evento_programa").count(), 5)

    def test_a_day_outside_the_event_and_backwards_hours_are_refused(self):
        self.client.force_login(self.admin)
        for offset in (-1, 3):
            self.assertContains(self.post(day=self.day(offset).isoformat()), "Elige un día dentro de las fechas del evento.")
            with self.assertRaises(services.AgendaError):
                services.save_schedule_item(self.retiro, self.admin, day=self.day(offset), starts_at=time(9), title="X")
        self.assertContains(self.post(ends_at="08:00"), "tiene que ser después de la hora en que empieza")
        self.assertContains(self.post(title=""), "Escribe qué es.")
        self.assertFalse(EventScheduleItem.objects.exists())
        with self.assertRaises(services.AgendaError):
            services.save_schedule_item(self.retiro, self.admin, day=self.day(), starts_at=time(9), ends_at=time(9), title="X")
        # A one-day event says so; an event without dates takes any day.
        one_day = self._future(self.church, "Un día", "un-dia", 4, 4)
        self.assertEqual(services.schedule_day_error(one_day, one_day.starts_on + timedelta(days=1)),
                         "El evento es un solo día: elige ese día.")
        no_dates = self._future(self.church, "Sin fechas", "sin-fechas", 4, 4)
        no_dates.starts_on = no_dates.ends_on = None
        self.assertIsNone(services.schedule_day_error(no_dates, self.today))

    def test_a_member_reads_the_programme_by_day_and_hour(self):
        services.save_schedule_item(self.retiro, self.admin, day=self.day(1), starts_at=time(18), ends_at=time(19, 30),
                                    title="Fogata", place="El claro")
        services.save_schedule_item(self.retiro, self.admin, day=self.day(), starts_at=time(9), title="Desayuno")
        services.save_schedule_item(self.retiro, self.admin, day=self.day(), starts_at=time(7, 30), title="Oración")
        self.client.force_login(self.member_user)
        detail = self.client.get(self.url())
        self.assertContains(detail, 'id="programa"', count=1)
        text = detail.content.decode()
        self.assertLess(text.index("Oración"), text.index("Desayuno"))
        self.assertLess(text.index("Desayuno"), text.index("Fogata"))
        # The hour drawn, then until when and where as facts.
        self.assertContains(
            detail,
            f'<li class="row row--lead"><span class="hora">18:00</span><div class="row__main"><p class="row__title">Fogata</p>'
            f'<ul class="meta"><li>{icon("reloj", "sm")}Hasta las 19:30</li><li>{icon("lugar", "sm")}El claro</li></ul></div></li>',
            html=True,
        )
        self.assertContains(
            detail,
            '<li class="row row--lead"><span class="hora">09:00</span><div class="row__main"><p class="row__title">Desayuno</p></div></li>',
            html=True,
        )
        self.assertEqual(text.count('<h3 class="day-label">'), 2)


class WalkUpTests(ExtrasFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def guest(self, **fields) -> EventRegistration:
        return services.register_walkup(self.retiro, self.admin, **{"first_name": "Vera", "last_name": "Visita", **fields})

    def test_only_from_the_first_day(self):
        self.assertNotContains(self.client.get(self.url("registrations")), "Registrar a alguien que llegó")
        response = self.client.post(self.url("walkup"), {"first_name": "Vera"}, follow=True)
        self.assertContains(response, "El evento todavía no empieza.")
        with self.assertRaises(services.AgendaError) as caught:
            self.guest()
        self.assertEqual(caught.exception.code, "not_started")
        self.assertFalse(EventRegistration.objects.exists())

    def test_a_visitor_is_written_down_without_an_account_or_a_person(self):
        self.start()
        self.assertContains(self.client.get(self.url("registrations")), "Registrar a alguien que llegó", count=1)
        form = self.client.get(self.url("walkup"))
        self.assertContains(form, "<h1", count=1)
        self.assertContains(form, "Registrar que llegó", count=1)
        self.assertContains(form, "Es menor de edad")
        self.assertNotContains(form, "¿Quién autoriza?")  # the event does not ask for it
        self.assertContains(self.client.post(self.url("walkup"), {"last_name": "Sin Nombre"}), "Escribe el nombre de quien llegó.")
        people, users = Person.objects.count(), self.admin.__class__.objects.count()
        response = self.client.post(
            self.url("walkup"), {"first_name": " Vera ", "last_name": "Visita  López", "phone": "55 4444 3333", "is_minor": "on"},
            follow=True,
        )
        self.assertContains(response, "Registraste a Vera Visita López. Ya quedó en la lista como que vino.")
        row = EventRegistration.objects.get()
        self.assertEqual(
            (row.person, row.guest_first_name, row.guest_last_name, row.guest_phone, row.guest_is_minor, row.registered_by),
            (None, "Vera", "Visita López", "55 4444 3333", True, self.admin),
        )
        self.assertEqual((row.status, row.attended, row.checked_in_by), ("inscrita", True, self.admin))
        self.assertIsNotNone(row.checked_in_at)
        self.assertEqual((Person.objects.count(), self.admin.__class__.objects.count()), (people, users))
        audit = ChurchAuditEvent.objects.get(action="evento_registro_puerta")
        # What the team said at the door about the visitor's age stays in the audit, as one word.
        self.assertEqual(audit.metadata, {"event_id": self.retiro.pk, "registration_id": row.pk, "minor": True})
        self.assertNotIn("4444", f"{audit.summary} {audit.metadata}")

    def test_a_row_is_a_person_or_a_visitor_never_both_or_neither(self):
        for fields in ({}, {"person": self.member, "guest_first_name": "Vera"}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                EventRegistration.objects.create(event=self.retiro, **fields)
        EventRegistration.objects.create(event=self.retiro, guest_first_name="Vera")
        EventRegistration.objects.create(event=self.retiro, guest_first_name="Vera")  # two visitors may share a name
        self.assertEqual(EventRegistration.objects.count(), 2)

    def test_a_person_of_the_church_is_found_by_name_and_only_in_this_church(self):
        self.start()
        merged = Person.objects.create(church=self.church, first_name="Nora", last_name="Unida", merged_into=self.no_account)
        services.register(self.retiro, self.leader, self.leader_user)
        found = self.client.get(self.url("walkup"), {"q": "nora"})
        self.assertContains(found, "Nora Sin Cuenta")
        self.assertContains(found, f"?persona={self.no_account.pk}")
        self.assertNotContains(found, "Nora Unida")
        self.assertContains(self.client.get(self.url("walkup"), {"q": "Bruno"}), "No encontramos a nadie con ese nombre")
        self.assertContains(self.client.get(self.url("walkup"), {"q": "Gala"}), "No encontramos a nadie")  # already on the list
        for person in (self.member_b, merged):
            self.assertEqual(self.client.get(self.url("walkup"), {"persona": person.pk}).status_code, 404)
            self.assertEqual(self.client.post(self.url("walkup"), {"persona": person.pk}).status_code, 404)
            with self.assertRaises(services.AgendaError):
                services.register_walkup(self.retiro, self.admin, person=person)
        chosen = self.client.get(self.url("walkup"), {"persona": self.no_account.pk})
        self.assertContains(chosen, "Vas a registrar a <strong>Nora Sin Cuenta</strong>")
        self.assertNotContains(chosen, 'name="first_name"')
        response = self.client.post(self.url("walkup"), {"persona": self.no_account.pk}, follow=True)
        self.assertContains(response, "Registraste a Nora Sin Cuenta.")
        row = EventRegistration.objects.get(person=self.no_account)
        self.assertEqual((row.guest_first_name, row.status, row.attended, row.registered_by), ("", "inscrita", True, self.admin))
        again = self.client.post(self.url("walkup"), {"persona": self.no_account.pk})
        self.assertContains(again, "Nora Sin Cuenta ya está en la lista de inscritos.")
        self.assertEqual(EventRegistration.objects.filter(event=self.retiro).count(), 2)

    def test_somebody_who_cancelled_and_arrives_gets_the_same_row_back(self):
        cancelled = services.cancel(services.register(self.retiro, self.member, self.member_user), self.member_user)
        self.start()
        row = services.register_walkup(self.retiro, self.admin, person=self.member)
        self.assertEqual((row.pk, row.status, row.attended, row.cancelled_at), (cancelled.pk, "inscrita", True, None))

    def test_walk_ups_respect_the_capacity(self):
        self.set_page(capacity=1)
        self.start()
        self.guest()
        response = self.client.post(self.url("walkup"), {"first_name": "Otra"})
        self.assertContains(response, "El evento está lleno: ya no hay lugares.")
        with self.assertRaises(services.AgendaError) as caught:
            services.register_walkup(self.retiro, self.admin, person=self.no_account)
        self.assertEqual(caught.exception.code, "full")
        self.assertEqual(EventRegistration.objects.count(), 1)

    def test_guest_rows_render_everywhere(self):
        self.set_page(capacity=5)
        services.register(self.retiro, self.member, self.member_user)
        self.start()
        guest = self.guest(phone="55 4444 3333")
        self.guest(first_name="Solo", last_name="")
        page = self.client.get(self.url("registrations"))
        self.assertContains(page, "3 personas inscritas de 5 lugares")
        self.assertContains(page, "Vera Visita")
        self.assertContains(page, "Visita. Llegó el")
        self.assertContains(page, 'href="tel:')
        self.assertContains(page, f'id="vino-{guest.pk}"')
        self.assertContains(page, "Agregar a personas de la iglesia", count=2)
        self.assertContains(self.client.get(reverse("agenda:admin_list")), "3 personas inscritas")
        self.assertEqual(str(guest), f"Vera Visita en {self.retiro}")
        # Attendance is corrected by registration, so a visitor can be unmarked.
        self.client.post(self.url("registrations"), {"vino": [EventRegistration.objects.get(person=self.member).pk]})
        guest.refresh_from_db()
        self.assertIs(guest.attended, False)
        for user in (self.admin, self.member_user):
            self.client.force_login(user)
            for path in (reverse("agenda:list"), self.url(), reverse("community:home")):
                page = self.client.get(path)
                self.assertEqual(page.status_code, 200, path)
                self.assertNotContains(page, "Vera Visita")
        self.assertContains(self.client.get(self.url()), "Quedan 2 lugares")

    def test_a_visitor_becomes_a_person_of_the_church(self):
        self.start()
        guest = self.guest(phone="55 4444 3333", is_minor=True)
        page = self.client.get(self.url("guest_promote", guest.pk))
        self.assertContains(page, "<h1", count=1)
        self.assertNotContains(page, "¿Ya está en Personas?")
        self.assertContains(page, "Agregar a personas de la iglesia</button>", count=1)
        response = self.client.post(self.url("guest_promote", guest.pk), follow=True)
        self.assertContains(response, "Agregaste a Vera Visita a las personas de la iglesia.")
        person = Person.objects.get(first_name="Vera")
        self.assertEqual((person.church, person.last_name, person.phone, person.created_from, person.user),
                         (self.church, "Visita", "55 4444 3333", "event", None))
        guest.refresh_from_db()
        # "Es menor de edad" is what the team said at the door: it stays while the date of birth is unknown.
        self.assertEqual((guest.person, guest.guest_first_name, guest.guest_last_name, guest.guest_phone, guest.guest_is_minor),
                         (person, "", "", "", True))
        self.assertEqual((guest.status, guest.attended), ("inscrita", True))
        self.assertTrue(ChurchAuditEvent.objects.filter(action="persona_creada", metadata__person_id=person.pk).exists())
        audit = ChurchAuditEvent.objects.get(action="evento_visita_persona")
        self.assertEqual(audit.metadata, {"event_id": self.retiro.pk, "registration_id": guest.pk, "person_id": person.pk})
        self.assertEqual(self.client.get(self.url("guest_promote", guest.pk)).status_code, 404)  # no longer a visitor
        self.assertNotContains(self.client.get(self.url("registrations")), "Agregar a personas de la iglesia")

    def test_a_visitor_who_is_already_a_person_is_recognized_instead_of_duplicated(self):
        self.start()
        guest = self.guest(first_name="Nora", last_name="Sin Cuenta")
        same_phone = self.guest(first_name="Otro", last_name="Nombre", phone="5511112222")
        page = self.client.get(self.url("guest_promote", guest.pk))
        self.assertContains(page, "¿Ya está en Personas?")
        self.assertContains(page, "Es esta persona", count=1)
        self.assertEqual(services.similar_people(same_phone), [self.member])
        people = Person.objects.count()
        for other in (self.member_b.pk, "x"):
            self.assertEqual(self.client.post(self.url("guest_promote", guest.pk), {"persona": other}).status_code, 404)
        response = self.client.post(self.url("guest_promote", guest.pk), {"persona": self.no_account.pk}, follow=True)
        self.assertContains(response, "Esta inscripción ahora es de Nora Sin Cuenta.")
        guest.refresh_from_db()
        self.assertEqual((guest.person, guest.guest_first_name), (self.no_account, ""))
        self.assertEqual(Person.objects.count(), people)
        # Who already has a row of their own in this event cannot take a second one.
        response = self.client.post(self.url("guest_promote", same_phone.pk), {"persona": self.no_account.pk}, follow=True)
        self.assertContains(response, "ya tiene su propia inscripción a este evento")
        same_phone.refresh_from_db()
        self.assertIsNone(same_phone.person)

    def test_without_the_people_module_a_visitor_stays_a_visitor(self):
        self.start()
        guest = self.guest()
        flags.set_enabled(self.church, "people", False)
        self.assertNotContains(self.client.get(self.url("registrations")), "Agregar a personas de la iglesia")
        self.assertEqual(self.client.get(self.url("guest_promote", guest.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url("guest_promote", guest.pk)).status_code, 404)
        with self.assertRaises(services.AgendaError) as caught:
            services.promote_guest(guest, self.admin)
        self.assertEqual(caught.exception.code, "no_people")
        self.assertFalse(Person.objects.filter(first_name="Vera").exists())


class MinorSignUpTests(ExtrasFixture):
    def setUp(self):
        self.client.force_login(self.member_user)

    def test_the_page_form_turns_the_rule_on(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url("page_edit")),
                            "Los menores de edad necesitan autorización de su madre, padre o tutor")
        self.assertFalse(EventPage.objects.get(pk=self.page.pk).requires_guardian_consent)
        self.client.post(self.url("page_edit"), {"published": "on", "registration": "abierta", "requires_guardian_consent": "on"})
        self.assertTrue(EventPage.objects.get(pk=self.page.pk).requires_guardian_consent)

    def test_an_event_that_does_not_ask_never_asks_for_the_date(self):
        response = self.client.post(self.url("register"), {"birth_date": "14/03/2012"}, follow=True)
        self.assertContains(response, "Te inscribiste a Retiro de jóvenes.")
        self.member.refresh_from_db()
        self.assertIsNone(self.member.birth_date)
        self.assertEqual(EventRegistration.objects.get().status, "inscrita")

    def test_an_unknown_date_of_birth_is_asked_and_a_typed_one_never_confirms(self):
        self.require_consent()
        self.assertContains(self.client.get(self.url()), "tu inscripción queda pendiente hasta que tu mamá, papá o tutor la autorice")
        asked = self.client.post(self.url("register"))
        self.assertContains(asked, "<h1", count=1)
        self.assertContains(asked, "¿Cuál es tu fecha de nacimiento?")
        self.assertContains(asked, "Inscribirme", count=1)
        self.assertContains(asked, "queda pendiente hasta que el equipo de la iglesia confirme tu edad")
        self.assertFalse(EventRegistration.objects.exists())
        future = (self.today + timedelta(days=1)).strftime("%d/%m/%Y")
        for bad, message in (("ayer", "Escribe la fecha así"), (future, "Revisa la fecha de nacimiento.")):
            self.assertContains(self.client.post(self.url("register"), {"birth_date": bad}), message)
        self.assertFalse(EventRegistration.objects.exists())
        self.member.refresh_from_db()
        self.assertIsNone(self.member.birth_date)
        # An adult's date, typed: saved, never verified, and the sign-up waits for the team.
        response = self.client.post(self.url("register"), {"birth_date": "14/03/1990"}, follow=True)
        self.assertContains(response, AGE_TEXT)
        self.assertContains(response, '<p class="notice__title">Falta confirmar tu edad.</p>')
        self.assertNotContains(response, '<p class="notice__title">Te inscribiste.</p>')
        self.member.refresh_from_db()
        self.assertEqual((self.member.birth_date, self.member.birth_date_verified), (date(1990, 3, 14), False))
        row = EventRegistration.objects.get()
        self.assertEqual((row.status, row.pending_reason), ("pendiente", "age"))
        self.assertContains(self.client.get(reverse("agenda:list")), "Falta confirmar tu edad")
        audit = ChurchAuditEvent.objects.filter(action="persona_modificada").latest("id")
        self.assertEqual((audit.actor, audit.metadata), (self.member_user, {"person_id": self.member.pk}))
        self.assertIn("pendiente de confirmar su edad", ChurchAuditEvent.objects.get(action="evento_inscripcion").summary)
        # Once the church knows it, the sign-up cannot change it.
        services.set_birth_date(self.member, date(2015, 1, 1), self.member_user)
        self.member.refresh_from_db()
        self.assertEqual(self.member.birth_date, date(1990, 3, 14))

    def test_the_date_may_be_left_empty_and_the_sign_up_waits_for_the_team(self):
        self.require_consent()
        response = self.client.post(self.url("register"), {"birth_date": ""}, follow=True)
        self.assertContains(response, AGE_TEXT)
        self.member.refresh_from_db()
        self.assertIsNone(self.member.birth_date)
        row = EventRegistration.objects.get()
        self.assertEqual((row.status, row.pending_reason, row.attended), ("pendiente", "age", None))
        # The service decides the same without the screen.
        row = services.register(self.retiro, self.leader, self.leader_user)
        self.assertEqual((row.status, row.pending_reason), ("pendiente", "age"))

    def test_the_date_is_not_asked_when_nobody_can_sign_up(self):
        self.require_consent(capacity=1)
        services.register(self.retiro, self.leader, self.leader_user)
        response = self.client.post(self.url("register"), follow=True)
        self.assertNotContains(response, "¿Cuál es tu fecha de nacimiento?")
        self.assertContains(response, "Ya no hay lugares.")

    def test_a_minor_is_registered_as_pending_and_reads_what_happens_next(self):
        self.require_consent()
        first = self.retiro.starts_on
        eighteen_the_next_day = date(first.year - 18, first.month, first.day) + timedelta(days=1)
        response = self.client.post(self.url("register"), {"birth_date": eighteen_the_next_day.strftime("%d/%m/%Y")}, follow=True)
        self.assertContains(response, PENDING_TEXT)
        self.assertContains(response, '<p class="notice__title">Pendiente de autorización.</p>')
        self.assertContains(response, "Cancelar mi inscripción")
        self.assertNotContains(response, '<p class="notice__title">Te inscribiste.</p>')
        self.assertNotContains(response, '<section class="sign"')  # no panel to sign up again
        row = EventRegistration.objects.get()
        self.assertEqual((row.status, row.attended, row.registered_by), ("pendiente", None, None))
        page = self.client.get(reverse("agenda:list"))
        self.assertContains(page, "Tus inscripciones")
        self.assertContains(page, "Pendiente de autorización")
        self.assertNotContains(page, "Te inscribiste")
        self.assertEqual(self.client.get(reverse("community:home")).status_code, 200)
        event = services.upcoming_for(self.member, self.church)[0]
        self.assertEqual((event.state, event.my_registration, event.my_place), ("pendiente", None, row))
        self.assertIn("pendiente de autorización", ChurchAuditEvent.objects.filter(action="evento_inscripcion").get().summary)

    def test_who_is_eighteen_on_the_first_day_is_confirmed(self):
        self.require_consent()
        first = self.retiro.starts_on
        self.assertFalse(services.is_minor_at(self.retiro, date(first.year - 18, first.month, first.day)))
        self.assertTrue(services.is_minor_at(self.retiro, date(first.year - 18, first.month, first.day) + timedelta(days=1)))
        self.verified(self.member, 18)
        self.assertEqual(services.register(self.retiro, self.member, self.member_user).status, "inscrita")

    def test_a_pending_registration_holds_its_place(self):
        self.require_consent(capacity=1)
        self.born(self.member, 15)
        self.born(self.leader, 30)
        services.register(self.retiro, self.member, self.member_user)
        self.assertEqual(services.taken(self.retiro), 1)
        with self.assertRaises(services.AgendaError) as caught:
            services.register(self.retiro, self.leader, self.leader_user)
        self.assertEqual(caught.exception.code, "full")
        self.client.force_login(self.leader_user)
        self.assertContains(self.client.get(self.url()), "Ya no hay lugares")
        self.assertContains(self.client.get(reverse("agenda:list")), "Ya no hay lugares")
        self.start()
        with self.assertRaises(services.AgendaError) as caught:
            services.register_walkup(self.retiro, self.admin, first_name="Vera")
        self.assertEqual(caught.exception.code, "full")

    def test_a_pending_registration_never_becomes_confirmed_another_way(self):
        row = self.minor_pending()

        def still_pending():
            row.refresh_from_db()
            self.assertEqual((row.status, row.attended, row.checked_in_at), ("pendiente", None, None))

        # Signing up again, by the screen or by the service.
        self.client.post(self.url("register"))
        self.assertEqual(services.register(self.retiro, self.member, self.member_user).status, "pendiente")
        still_pending()
        # Cancelling and coming back.
        services.cancel(row, self.member_user)
        self.assertEqual(services.register(self.retiro, self.member, self.member_user).pk, row.pk)
        still_pending()
        # The page changes: more places, fewer, the rule switched off.
        for fields in ({"capacity": 50}, {"capacity": 1}, {"capacity": None}, {"requires_guardian_consent": False}):
            services.save_page(self.retiro, self.admin, **fields)
            still_pending()
        self.client.post(self.url("register"))
        still_pending()
        services.save_page(self.retiro, self.admin, requires_guardian_consent=True)
        # Attendance: it is not on the list, and its id is ignored.
        self.start()
        self.client.force_login(self.admin)
        page = self.client.get(self.url("registrations"))
        self.assertNotContains(page, f'id="vino-{row.pk}"')
        self.client.post(self.url("registrations"), {"vino": [row.pk]})
        self.assertEqual(services.record_attendance(self.retiro, [row.pk], self.admin), (0, 0))
        still_pending()
        # Arriving at the door without the authorization.
        with self.assertRaises(services.AgendaError) as caught:
            services.register_walkup(self.retiro, self.admin, person=self.member)
        self.assertEqual(caught.exception.code, "already")
        still_pending()
        self.assertFalse(GuardianConsent.objects.exists())

    def test_cancelling_a_pending_registration_frees_the_place(self):
        row = self.minor_pending()
        self.assertContains(self.client.get(self.url("cancel")), "¿Quieres cancelar tu inscripción")
        self.client.post(self.url("cancel"))
        row.refresh_from_db()
        self.assertEqual(row.status, "cancelada")
        self.assertEqual(services.taken(self.retiro), 0)


class ConsentTests(ExtrasFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_pending_rows_come_first_with_the_way_to_record_the_authorization(self):
        row = self.minor_pending()
        self.verified(self.leader)
        services.register(self.retiro, self.leader, self.leader_user)
        page = self.client.get(self.url("registrations"))
        self.assertContains(page, "1 persona inscrita, 1 pendiente de autorización.")
        self.assertContains(page, "Registrar autorización", count=1)
        text = page.content.decode()
        self.assertLess(text.index("Pendientes de autorización"), text.index("Personas inscritas"))
        self.assertLess(text.index("Diego Hernández Cruz"), text.index("Gala Guía"))
        row_facts = self.client.get(reverse("agenda:admin_list")).content.decode().split("Retiro de jóvenes</a>", 1)[1].split("</ul>", 1)[0]
        self.assertIn(f'<li>{icon("personas", "sm")}1 persona inscrita</li>', row_facts)
        self.assertIn('<li><span class="chip chip--wait">1 pendiente de autorización</span></li>', row_facts)
        form = self.client.get(self.url("consent", row.pk))
        self.assertContains(form, "<h1", count=1)
        self.assertContains(form, "Registrar la autorización", count=1)
        for word in ("Madre", "Padre", "Tutor legal", "Formato firmado en papel",
                     "En persona, con identidad y parentesco verificados por el equipo", "Por llamada o mensaje",
                     "Autoriza que participe en este evento", "Autoriza el transporte organizado por la iglesia",
                     "Autoriza que aparezca en fotografías del evento",
                     "Texto provisional: lo aprueba el liderazgo de la iglesia"):
            self.assertContains(form, word)
        self.assertNotContains(form, "verificado</span>")  # the call is no longer called "verified"
        self.assertNotContains(form, "<textarea")

    def test_recording_the_authorization_confirms_and_the_audit_has_no_personal_data(self):
        row = self.minor_pending()
        missing = self.client.post(self.url("consent", row.pk), {"guardian_name": ""})
        for message in ("Escribe el nombre de quien autoriza.", "Elige si es su madre, su padre o su tutor legal.", "Elige cómo autorizó.",
                        "Marca que autoriza que participe en este evento"):
            self.assertContains(missing, message)
        row.refresh_from_db()
        self.assertEqual(row.status, "pendiente")
        response = self.client.post(self.url("consent", row.pk), CONSENT, follow=True)
        self.assertContains(response, "La inscripción de Diego Hernández Cruz quedó confirmada.")
        row.refresh_from_db()
        self.assertEqual((row.status, row.attended), ("inscrita", None))
        consent = GuardianConsent.objects.get()
        self.assertEqual(
            (consent.registration, consent.guardian_name, consent.relationship, consent.method, consent.guardian_phone,
             consent.recorded_by, consent.revoked_at),
            (row, "Marta Tutora Reservada", "madre", "papel", "55 9999 0000", self.admin, None),
        )
        audit = ChurchAuditEvent.objects.get(action="evento_autorizacion")
        self.assertEqual(audit.metadata, {"event_id": self.retiro.pk, "registration_id": row.pk, "person_id": self.member.pk,
                                          "method": "papel"})
        for audit in ChurchAuditEvent.objects.filter(church=self.church):
            text = f"{audit.summary} {audit.metadata} {audit.object_type} {audit.object_id}"
            for secret in ("Marta", "Tutora", "Reservada", "9999", "madre"):
                self.assertNotIn(secret, text)
        # The administration reads who authorized; a second authorization has nothing to confirm.
        page = self.client.get(self.url("registrations"))
        self.assertContains(page, "Autorizada por Marta Tutora Reservada (madre)")
        self.assertContains(page, "Retirar autorización", count=1)
        self.assertNotContains(page, "Registrar autorización")
        self.assertEqual(self.client.get(self.url("consent", row.pk)).status_code, 404)
        with self.assertRaises(services.AgendaError) as caught:
            services.record_consent(row, self.admin, **CONSENT)
        self.assertEqual(caught.exception.code, "not_pending")

    def test_the_member_reads_one_word_and_never_who_authorized(self):
        row = self.minor_pending()
        services.record_consent(row, self.admin, **CONSENT)
        self.client.force_login(self.member_user)
        for path in (self.url(), reverse("agenda:list"), reverse("community:home")):
            page = self.client.get(path)
            for secret in ("Marta", "Tutora", "9999", "Formato firmado"):
                self.assertNotContains(page, secret)
        detail = self.client.get(self.url())
        self.assertContains(detail, '<p class="notice__title">Te inscribiste.</p>')
        self.assertContains(detail, "<p>Autorizada por tu mamá, papá o tutor.</p>")
        # An adult's registration says nothing about authorizations.
        self.born(self.leader, 30)
        services.register(self.retiro, self.leader, self.leader_user)
        self.client.force_login(self.leader_user)
        self.assertNotContains(self.client.get(self.url()), "Autorizada")

    def test_withdrawing_the_authorization_sets_it_back_to_pending(self):
        row = self.minor_pending()
        services.record_consent(row, self.admin, **CONSENT)
        self.start()
        services.record_attendance(self.retiro, [row.pk], self.admin)
        row.refresh_from_db()
        self.assertIs(row.attended, True)
        self.assertContains(self.client.get(self.url("consent_revoke", row.pk)), "¿Quieres retirar la autorización de Diego Hernández Cruz?")
        row.refresh_from_db()
        self.assertEqual(row.status, "inscrita")
        response = self.client.post(self.url("consent_revoke", row.pk), follow=True)
        self.assertContains(response, "La inscripción de Diego Hernández Cruz quedó pendiente.")
        row.refresh_from_db()
        self.assertEqual((row.status, row.attended, row.checked_in_at, row.checked_in_by), ("pendiente", None, None, None))
        consent = GuardianConsent.objects.get()
        self.assertEqual(consent.revoked_by, self.admin)
        self.assertIsNotNone(consent.revoked_at)
        self.assertIsNone(row.valid_consent)
        audit = ChurchAuditEvent.objects.get(action="evento_autorizacion_retirada")
        self.assertEqual(set(audit.metadata), {"event_id", "registration_id", "person_id", "method"})
        self.assertNotIn("Marta", audit.summary)
        self.assertEqual(self.client.get(self.url("consent_revoke", row.pk)).status_code, 404)
        self.assertNotContains(self.client.get(self.url("registrations")), "Marta")
        # Recorded again, by the father this time: one row per registration, the newest answer.
        services.record_consent(row, self.admin, guardian_name="Pedro Papá", relationship="padre", method="en_persona",
                                allows_participation=True)
        consent = GuardianConsent.objects.get()
        self.assertEqual((consent.guardian_name, consent.guardian_phone, consent.revoked_at, consent.revoked_by),
                         ("Pedro Papá", "", None, None))
        row.refresh_from_db()
        self.assertEqual(row.status, "inscrita")

    def test_an_authorization_in_force_survives_cancelling_and_signing_up_again(self):
        row = self.minor_pending()
        services.record_consent(row, self.admin, **CONSENT)
        services.cancel(row, self.member_user)
        self.assertEqual(services.register(self.retiro, self.member, self.member_user).status, "inscrita")
        services.revoke_consent(row, self.admin)
        services.cancel(row, self.member_user)
        self.assertEqual(services.register(self.retiro, self.member, self.member_user).status, "pendiente")

    def test_unknown_options_are_refused_by_the_service(self):
        row = self.minor_pending()
        for fields in ({**CONSENT, "relationship": "abuela"}, {**CONSENT, "method": "dicho"}, {**CONSENT, "guardian_name": " "}):
            with self.assertRaises(services.AgendaError):
                services.record_consent(row, self.admin, **fields)
        with self.assertRaises(ValueError):
            services.record_consent(row, self.admin, **CONSENT, notes="texto libre")
        self.assertFalse(GuardianConsent.objects.exists())
        self.assertNotIn("notes", [field.name for field in GuardianConsent._meta.fields])

    def test_a_minor_who_arrives_waits_unless_the_authorization_is_recorded_in_the_same_form(self):
        self.require_consent()
        self.start()
        form = self.client.get(self.url("walkup"))
        self.assertContains(form, "¿Quién autoriza?")
        self.assertContains(form, "Registrar que llegó", count=1)
        # The event asks: the team says "mayor" or "menor" out loud, and nothing is chosen beforehand.
        self.assertContains(form, 'name="age"', count=2)
        self.assertNotContains(form, 'name="is_minor"')
        self.assertNotContains(form, "checked")
        silent = self.client.post(self.url("walkup"), {"first_name": "Sin", "last_name": "Decir"})
        self.assertContains(silent, "Elige si es mayor o menor de edad.")
        self.assertFalse(EventRegistration.objects.filter(guest_first_name="Sin").exists())
        response = self.client.post(self.url("walkup"), {"first_name": "Mía", "last_name": "Menor", "age": "menor"}, follow=True)
        self.assertContains(response, "Queda pendiente hasta que registres la autorización")
        waiting = EventRegistration.objects.get(guest_first_name="Mía")
        self.assertEqual((waiting.status, waiting.attended, waiting.guest_is_minor), ("pendiente", None, True))
        self.assertContains(response, "Visita. Llegó el")
        self.assertContains(response, "Registrar autorización", count=1)
        # Half an authorization, or one for an adult, is an error and writes nothing.
        half = self.client.post(self.url("walkup"), {"first_name": "Leo", "age": "menor", "guardian_name": "Marta"})
        self.assertContains(half, "Elige cómo autorizó.")
        adult = self.client.post(self.url("walkup"), {"first_name": "Leo", "age": "adulto", **CONSENT})
        self.assertContains(adult, "La autorización es solo para menores de edad.")
        self.assertFalse(EventRegistration.objects.filter(guest_first_name="Leo").exists())
        response = self.client.post(self.url("walkup"), {"first_name": "Leo", "age": "menor", **CONSENT}, follow=True)
        self.assertContains(response, "Registraste a Leo. Ya quedó en la lista como que vino.")
        leo = EventRegistration.objects.get(guest_first_name="Leo")
        self.assertEqual((leo.status, leo.attended, leo.consent.method, leo.consent.recorded_by), ("inscrita", True, "papel", self.admin))
        self.assertEqual(
            ChurchAuditEvent.objects.get(action="evento_autorizacion").metadata,
            {"event_id": self.retiro.pk, "registration_id": leo.pk, "method": "papel"},
        )
        # An adult visitor of the same event is confirmed at once.
        self.assertEqual(services.register_walkup(self.retiro, self.admin, first_name="Ada", seen_adult=True).status, "inscrita")
        # Recording it later: the visitor is there, so the row is marked as present too.
        self.client.post(self.url("consent", waiting.pk), CONSENT)
        waiting.refresh_from_db()
        self.assertEqual((waiting.status, waiting.attended, waiting.checked_in_by), ("inscrita", True, self.admin))

    def test_the_date_of_birth_of_a_person_decides_at_the_door(self):
        self.require_consent()
        self.born(self.member, 15)
        self.born(self.leader, 30)
        self.start()
        minor = self.client.get(self.url("walkup"), {"persona": self.member.pk})
        self.assertContains(minor, "Por su fecha de nacimiento, es menor de edad.")
        self.assertNotContains(minor, 'name="is_minor"')
        self.assertContains(minor, "¿Quién autoriza?")
        adult = self.client.get(self.url("walkup"), {"persona": self.leader.pk})
        self.assertNotContains(adult, "¿Quién autoriza?")
        self.assertNotContains(adult, 'name="is_minor"')
        self.assertContains(adult, "El equipo todavía no ha confirmado su edad")
        self.client.post(self.url("walkup"), {"persona": self.member.pk})
        response = self.client.post(self.url("walkup"), {"persona": self.leader.pk, **CONSENT}, follow=True)  # ignored: not a minor
        self.assertContains(response, "Queda pendiente hasta que confirmes su edad")
        minor, adult = EventRegistration.objects.get(person=self.member), EventRegistration.objects.get(person=self.leader)
        self.assertEqual((minor.status, minor.pending_reason), ("pendiente", "consent"))
        # An adult by a date nobody verified waits for the team, even standing at the door.
        self.assertEqual((adult.status, adult.pending_reason, adult.attended), ("pendiente", "age", None))
        self.assertFalse(GuardianConsent.objects.exists())
        self.verified(self.asked)
        seen = self.client.get(self.url("walkup"), {"persona": self.asked.pk})
        self.assertNotContains(seen, "El equipo todavía no ha confirmado su edad")
        row = services.register_walkup(self.retiro, self.admin, person=self.asked)
        self.assertEqual((row.status, row.attended), ("inscrita", True))
        # Without a date of birth, the team says it: its word "minor" is believed and kept with the row.
        unknown = self.client.get(self.url("walkup"), {"persona": self.no_account.pk})
        self.assertContains(unknown, 'name="is_minor"')
        self.client.post(self.url("walkup"), {"persona": self.no_account.pk, "is_minor": "on"})
        row = EventRegistration.objects.get(person=self.no_account)
        self.assertEqual((row.status, row.pending_reason, row.guest_is_minor), ("pendiente", "consent", True))

    def test_a_pending_visitor_keeps_waiting_when_added_to_the_people_of_the_church(self):
        self.require_consent()
        self.start()
        waiting = services.register_walkup(self.retiro, self.admin, first_name="Mía", is_minor=True)
        services.promote_guest(waiting, self.admin)
        waiting.refresh_from_db()
        self.assertEqual((waiting.status, waiting.attended), ("pendiente", None))
        self.assertIsNotNone(waiting.person)


class ExtrasAccessTests(ExtrasFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        EventPage.objects.filter(pk=cls.page.pk).update(requires_guardian_consent=True)
        Person.objects.filter(pk=cls.member.pk).update(birth_date=date(cls.today.year - 14, 1, 1))
        cls.member.refresh_from_db()
        cls.waiting = services.register(cls.retiro, cls.member, cls.member_user)
        cls.unverified = services.register(cls.retiro, cls.leader, cls.leader_user)  # no date of birth: waits for the team
        cls.move(cls, cls.retiro, 0, 2)
        cls.guest = services.register_walkup(cls.retiro, cls.admin, first_name="Vera", last_name="Visita", is_minor=True, consent=CONSENT)
        cls.faq = services.save_faq(cls.retiro, cls.admin, question="¿Qué llevo?", answer="Ropa cómoda.")
        cls.item = services.save_schedule_item(cls.retiro, cls.admin, day=cls.retiro.starts_on, starts_at=time(9), title="Desayuno")
        cls.faq_b = services.save_faq(cls.retiro_b, cls.admin_b, question="¿De B?", answer="Sí.")
        cls.item_b = services.save_schedule_item(cls.retiro_b, cls.admin_b, day=cls.retiro_b.starts_on, starts_at=time(9), title="De B")

    # A valid body for every form of the administration: what a guessed request would send.
    BODIES = (
        {**CONSENT, "reason": "error", "first_name": "X", "question": "X", "answer": "X"},
        {"method": "documento", "birth_date": "14/03/1990"},  # "Confirmar edad" has its own methods
    )

    def paths(self, event=None):
        return [
            self.url("walkup", event=event),
            self.url("consent", self.waiting.pk, event=event),
            self.url("consent_revoke", self.guest.pk, event=event),
            self.url("confirm_age", self.unverified.pk, event=event),
            self.url("annul", self.guest.pk, event=event),
            self.url("guest_promote", self.guest.pk, event=event),
            self.url("faq", event=event),
            self.url("faq_edit", self.faq.pk, event=event),
            self.url("faq_delete", self.faq.pk, event=event),
            self.url("schedule", event=event),
            self.url("schedule_edit", self.item.pk, event=event),
            self.url("schedule_delete", self.item.pk, event=event),
        ]

    def unchanged(self, registrations=3):
        self.waiting.refresh_from_db()
        self.guest.refresh_from_db()
        self.unverified.refresh_from_db()
        self.leader.refresh_from_db()
        self.assertEqual((self.waiting.status, self.guest.status, self.guest.person), ("pendiente", "inscrita", None))
        self.assertEqual((self.unverified.status, self.unverified.pending_reason, self.leader.birth_date_verified),
                         ("pendiente", "age", False))
        self.assertIsNone(GuardianConsent.objects.get().revoked_at)
        self.assertEqual((EventFaq.objects.count(), EventScheduleItem.objects.count(), EventRegistration.objects.count()),
                         (2, 2, registrations))

    def test_the_admin_opens_every_page(self):
        self.client.force_login(self.admin)
        for path in self.paths():
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200, path)
            self.assertContains(page, "<h1", count=1)

    def test_a_member_or_a_guide_cannot_open_the_administration(self):
        for user in (self.member_user, self.leader_user):
            self.client.force_login(user)
            for path in self.paths():
                self.assertEqual(self.client.get(path).status_code, 403, path)
                for body in self.BODIES:
                    self.assertEqual(self.client.post(path, body).status_code, 403, path)
        self.unchanged()

    def test_the_admin_of_another_church_gets_a_404(self):
        self.client.force_login(self.admin_b)
        for path in self.paths():
            self.assertEqual(self.client.get(path).status_code, 404, path)
            for body in self.BODIES:
                self.assertEqual(self.client.post(path, body).status_code, 404, path)
        self.unchanged()

    def test_ids_of_another_event_are_a_404_inside_the_own_church(self):
        """The slug is of the admin's church; the id belongs to another church's event (or to another event)."""
        other = services.register(self.retiro_b, self.member_b, self.member_b_user)
        self.client.force_login(self.admin)
        guessed = [
            self.url("consent", other.pk), self.url("consent_revoke", other.pk), self.url("guest_promote", other.pk),
            self.url("confirm_age", other.pk), self.url("annul", other.pk),
            self.url("confirm_age", self.unverified.pk, event=self.hidden), self.url("annul", self.guest.pk, event=self.hidden),
            # The right event, the wrong kind of row: a minor's row has no age to confirm, a sign-up is not annulled.
            self.url("confirm_age", self.waiting.pk), self.url("annul", self.waiting.pk), self.url("consent", self.unverified.pk),
            self.url("faq_edit", self.faq_b.pk), self.url("faq_delete", self.faq_b.pk),
            self.url("schedule_edit", self.item_b.pk), self.url("schedule_delete", self.item_b.pk),
            self.url("faq_edit", self.faq.pk, event=self.hidden), self.url("schedule_delete", self.item.pk, event=self.hidden),
            self.url("consent", self.waiting.pk, event=self.hidden),
        ]
        for path in guessed:
            self.assertEqual(self.client.get(path).status_code, 404, path)
            for body in self.BODIES:
                self.assertEqual(self.client.post(path, body).status_code, 404, path)
        self.unchanged(registrations=4)
        self.assertEqual(EventRegistration.objects.get(pk=other.pk).status, "inscrita")

    def test_with_the_module_off_everything_is_a_404(self):
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.admin)
        for path in self.paths():
            self.assertEqual(self.client.get(path).status_code, 404, path)
            self.assertEqual(self.client.post(path).status_code, 404, path)
        self.unchanged()

    def test_signed_out_goes_to_sign_in(self):
        for path in self.paths():
            self.assertEqual(self.client.get(path).status_code, 302, path)


class ReviewFixesTests(ExtrasFixture):
    """What an independent review found in this slice (docs/v2/PROGRESS.md, wave 4)."""

    def setUp(self):
        self.client.force_login(self.admin)

    def test_a_visitor_recognized_as_a_known_minor_goes_back_to_waiting(self):
        self.require_consent()
        self.start()
        self.born(self.member, 15)
        row = services.register_walkup(self.retiro, self.admin, first_name="Diego", last_name="Hernández", seen_adult=True)
        self.assertEqual((row.status, row.attended), ("inscrita", True))
        services.promote_guest(row, self.admin, existing=self.member)
        row.refresh_from_db()
        self.assertEqual((row.person, row.status, row.attended), (self.member, "pendiente", None))
        services.record_consent(row, self.admin, **CONSENT)
        row.refresh_from_db()
        self.assertEqual(row.status, "inscrita")

    def test_an_adult_visitor_recognized_keeps_the_place_confirmed(self):
        self.require_consent()
        self.start()
        self.verified(self.member)
        row = services.register_walkup(self.retiro, self.admin, first_name="Diego", last_name="Hernández", seen_adult=True)
        services.promote_guest(row, self.admin, existing=self.member)
        row.refresh_from_db()
        self.assertEqual((row.status, row.attended), ("inscrita", True))

    def test_turning_the_rule_on_makes_known_minors_wait(self):
        self.born(self.member, 15)
        self.verified(self.asked)
        minor = services.register(self.retiro, self.member, self.member_user)
        adult = services.register(self.retiro, self.asked, self.asked_user)
        self.assertEqual(minor.status, "inscrita")
        services.save_page(self.retiro, self.admin, requires_guardian_consent=True)
        minor.refresh_from_db()
        adult.refresh_from_db()
        self.assertEqual((minor.status, adult.status), ("pendiente", "inscrita"))
        services.record_consent(minor, self.admin, **CONSENT)
        services.save_page(self.retiro, self.admin, description="Otra vez")  # saving again asks nothing new
        minor.refresh_from_db()
        self.assertEqual(minor.status, "inscrita")

    def test_saving_attendance_from_an_old_page_keeps_who_arrived_meanwhile(self):
        first = services.register(self.retiro, self.member, self.member_user)
        self.start()
        page = self.client.get(self.url("registrations"))
        self.assertContains(page, f'name="lista" value="{first.pk}"')
        late = services.register_walkup(self.retiro, self.admin, first_name="Llegó", last_name="Después")
        self.client.post(self.url("registrations"), {"lista": [first.pk], "vino": []})
        first.refresh_from_db()
        late.refresh_from_db()
        self.assertEqual((first.attended, late.attended), (False, True))

    def test_a_simple_event_is_not_offered_to_bring_camperos_from(self):
        from events.models import Event

        Event.objects.filter(pk=self.retiro.pk).update(camp_tools=False, status=Event.Status.CLOSED)
        self.move(self.retiro, -5, -3)
        self.assertEqual(self.client.get(reverse("people:from_event", args=[self.retiro.slug])).status_code, 404)
