"""Issue #51: duplicate a camp as a template for the next one. Its configuration, never its people's data.

Each test names the defect it would catch.
"""

from __future__ import annotations

import re
from datetime import date, time, timedelta

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlsafe_base64_decode

from agenda.models import EventFaq, EventNotice, EventPage, EventRegistration, EventScheduleItem, GuardianConsent
from core.models import Church, Invitation, Role
from core.services import assign_role
from events.models import (
    AccessRequest,
    Answer,
    AuditEvent,
    ConsentRecord,
    Event,
    EventMembership,
    EventParticipant,
    FollowUp,
    Interview,
    MembershipAlias,
    MembershipRole,
    ParticipantSession,
    ReportSnapshot,
    Sede,
    TutorAssignment,
)
from events.services import duplicate as dup
from events.services.event_questionnaires import links_for, use_version
from events.services.questionnaire import create_questionnaire_from_definition, duplicate_version, publish_version
from feedback.models import EventFeedback, FeedbackGiven
from people.models import EventParticipantLink, Person

from .base import CampFixture, make_user
from .test_multi_questionnaires import definition

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


def relations():
    """{accessor: relation} for every model that points at Event, straight from the models."""
    return {rel.get_accessor_name(): rel for rel in Event._meta.related_objects}


def count(event, accessor) -> int:
    rel = relations()[accessor]
    return rel.related_model._default_manager.filter(**{rel.field.name: event}).count()


class DuplicateFixture(CampFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        event = cls.event
        cls.sede = Sede.objects.create(church=cls.church, name="Satélite", slug="satelite")
        event.sedes.add(cls.sede)
        Event.objects.filter(pk=event.pk).update(
            kind=Event.Kind.RETREAT, location="Rancho El Encino", time_zone="America/Cancun",
            questionnaires_in_order=True, confidential_level=Event.ConfidentialLevel.COORDINATION,
            reports_enabled=False, report_interval_minutes=180, report_window_start=time(9, 0),
            report_window_end=time(21, 0), min_group_size=7, adult_age=21, requires_guardian_authorization=False,
            privacy_notice="Aviso propio de la iglesia.", safeguarding_instructions="Instrucciones propias.",
            retention_days=400, procedures_approved_by=cls.admin, data_purged_at=timezone.now(),
            join_by_name=False, created_by=cls.admin,
        )
        event.refresh_from_db()
        cls.second = create_questionnaire_from_definition(cls.church, definition("Servicio", "Cuestionario de servicio", "servir"))
        use_version(event, cls.second)
        newer = duplicate_version(cls.version, cls.admin)
        publish_version(newer, cls.admin)
        cls.unused_version = newer  # published, never put in use: the copy must not pick it up
        station = event.stations.get(number=3)
        station.name, station.is_active = "Mesa del comedor", False
        station.save()
        EventMembership.objects.create(event=event, user=cls.outsider, role=MembershipRole.COORDINATOR, is_active=False)

    def duplicate(self, **extra):
        values = {"name": "Campamento B 2027", "slug": "campamento-b-2027", **extra}
        return dup.duplicate_camp(self.event, self.admin, **values)

    def post(self, user=None, event=None, **data):
        client = Client()
        client.force_login(user or self.admin)
        values = {"name": "Campamento 2027", "slug": "", "starts_on": "", "ends_on": "", **data}
        return client, client.post(reverse("events:duplicate", args=[(event or self.event).slug]), values)


class NothingOfThePeopleTests(DuplicateFixture):
    """The copy never includes camperos, interviews, answers, follow-ups, consents, cutoffs or audit rows."""

    def fill_every_table(self):
        """At least one row in every table that points at the event, so "empty in the copy" means something."""
        event = self.event
        client = self.login(self.coord1, Client())
        self.choose_station(client)
        self.start(self.p2, client=client)
        interview = Interview.objects.get(participant=self.p2)
        self.answer(interview, self.steps[0].question.code, self.steps[0].question.options.first().code, client=client)
        FollowUp.objects.create(event=event, participant=self.p2, interview=interview)
        ReportSnapshot.objects.create(
            event=event, scope_key="evento", scope_label="Todo", trigger=ReportSnapshot.Trigger.MANUAL,
            cutoff_at=timezone.now(), interval_minutes=120, metrics_version="1",
        )
        AccessRequest.objects.create(event=event, full_name="Pide Acceso", email="pide@prueba.mx", requested_role="tutor")
        MembershipAlias.objects.create(membership=self.m_coord1, alias="Carlita")  # issue #50
        person = Person.objects.create(church=self.church, first_name="Diego", last_name="Hernández")
        EventParticipantLink.objects.create(participant=self.p2, person=person, event=event, linked_by=self.admin)
        EventPage.objects.create(event=event, published=True, description="Página del campamento")
        registration = EventRegistration.objects.create(event=event, person=person)
        GuardianConsent.objects.create(registration=registration, guardian_name="Mamá", relationship="madre", method="papel")
        EventFaq.objects.create(event=event, question="¿Qué llevo?", answer="Ropa abrigadora.")
        EventNotice.objects.create(event=event, title="El camión sale a las 7:00", body="Lleguen media hora antes.")
        EventScheduleItem.objects.create(event=event, day=event.starts_on, starts_at=time(9, 0), title="Bienvenida")
        EventFeedback.objects.create(event=event, rating="bien", recommend="si")
        FeedbackGiven.objects.create(event=event, token="a" * 64)
        from espacios.models import Booking, Space

        soon = timezone.now() + timedelta(days=2)
        Booking.objects.create(space=Space.objects.create(church=self.church, name="Salón"), title="Ensayo", event=event,
                               starts_at=soon, ends_at=soon + timedelta(hours=1))
        if not AuditEvent.objects.filter(event=event).exists():
            raise AssertionError("the interview should have left audit rows")

    def test_every_model_that_points_at_an_event_has_a_decision(self):
        """Defect: a table added later (a new kind of record about camperos) silently copied, or silently forgotten.
        Adding a model with a foreign key to Event fails here until events/services/duplicate.py says what happens."""
        self.assertEqual(set(dup.RELATIONS), set(relations()))
        self.assertLessEqual(set(dup.RELATIONS.values()), {dup.COPIED, dup.WITH_TEAM, dup.NEVER, dup.OWN})

    def test_every_column_of_the_event_has_a_decision(self):
        """Defect: a new setting not carried to the next camp, or a new per-event stamp (an approval, a purge) carried."""
        columns = {field.name for field in Event._meta.concrete_fields}
        self.assertEqual(set(dup.COPIED_FIELDS) | set(dup.FRESH_FIELDS), columns)
        self.assertFalse(set(dup.COPIED_FIELDS) & set(dup.FRESH_FIELDS))
        self.assertEqual(set(dup.COPIED_M2M), {field.name for field in Event._meta.many_to_many})

    def test_each_table_is_copied_on_purpose_or_empty_in_the_copy(self):
        self.fill_every_table()
        before = {accessor: count(self.event, accessor) for accessor in dup.RELATIONS}
        for accessor, rows in before.items():
            self.assertGreater(rows, 0, f"{accessor}: the original has no row, so the test proves nothing about it")
        audit_before = AuditEvent.objects.filter(event=self.event).count()

        copy = self.duplicate(copy_team=False).event

        for accessor, decision in dup.RELATIONS.items():
            rows = count(copy, accessor)
            if decision == dup.COPIED:
                expected = {"questionnaire_links": 2, "stations": 10}[accessor]
                self.assertEqual(rows, expected, accessor)
            elif decision in (dup.NEVER, dup.WITH_TEAM):
                self.assertEqual(rows, 0, f"{accessor} must be empty in the copy")
            else:
                self.assertEqual(accessor, "audit_events")
        # The audit rows of the copy are its own: its creation. None of the original's came along.
        self.assertEqual(
            list(AuditEvent.objects.filter(event=copy).values_list("action", "summary")),
            [("evento_creado", "Creó el evento Campamento B 2027 como copia de Campamento A")],
        )
        # Tables that hang from a campero, an interview or a registration, not from the event itself.
        self.assertFalse(ConsentRecord.objects.filter(participant__event=copy).exists())
        self.assertFalse(Answer.objects.filter(interview__event=copy).exists())
        self.assertFalse(ParticipantSession.objects.filter(interview__event=copy).exists())
        self.assertFalse(GuardianConsent.objects.filter(registration__event=copy).exists())
        # The original is exactly as it was, plus one row that says it was duplicated.
        after = {accessor: count(self.event, accessor) for accessor in dup.RELATIONS}
        self.assertEqual(after.pop("audit_events"), before.pop("audit_events") + 1)
        self.assertEqual(after, before)
        self.assertEqual(AuditEvent.objects.filter(event=self.event).count(), audit_before + 1)
        self.assertTrue(ConsentRecord.objects.filter(participant__event=self.event).exists())
        self.assertTrue(Answer.objects.filter(interview__event=self.event).exists())

    def test_with_the_team_still_no_camperos_or_answers(self):
        self.fill_every_table()
        copy = self.duplicate(copy_team=True).event
        for accessor, decision in dup.RELATIONS.items():
            if decision == dup.NEVER:
                self.assertEqual(count(copy, accessor), 0, accessor)
        self.assertFalse(EventParticipant.objects.filter(coordinator__event=copy).exists())


class TheCopyTests(DuplicateFixture):
    def test_it_starts_in_borrador_with_a_new_slug_and_join_code(self):
        """Defect: the copy "En curso" (interviews could start), or the printed sign of the last camp opening the new one."""
        copy = self.duplicate().event
        self.assertEqual(copy.status, Event.Status.DRAFT)
        self.assertEqual(copy.slug, "campamento-b-2027")
        self.assertNotEqual(copy.slug, self.event.slug)
        self.assertRegex(copy.join_code, r"^[A-Z2-9]{6}$")
        self.assertNotEqual(copy.join_code, self.event.join_code)
        self.assertTrue(copy.join_by_name)  # the original had it off after a leaked sign; a new event starts clean
        self.assertEqual((copy.starts_on, copy.ends_on), (None, None))
        self.assertEqual(copy.church, self.event.church)
        self.assertEqual(copy.created_by, self.admin)
        self.assertFalse(copy.is_open_for_interviews)

    def test_the_church_confirms_its_procedures_again_and_nothing_is_marked_purged(self):
        """Defect: "Procedimientos confirmados" inherited from a confirmation given for another event."""
        copy = self.duplicate().event
        self.assertIsNone(copy.procedures_approved_at)
        self.assertIsNone(copy.procedures_approved_by)
        self.assertIsNone(copy.data_purged_at)

    def test_the_dates_are_the_ones_asked(self):
        copy = self.duplicate(starts_on=date(2027, 7, 12), ends_on=date(2027, 7, 16)).event
        self.assertEqual((copy.starts_on, copy.ends_on), (date(2027, 7, 12), date(2027, 7, 16)))

    def test_the_configuration_comes_along(self):
        """Defect: the next camp set up again by hand (the reason for the issue), or with the defaults."""
        copy = self.duplicate().event
        for name in dup.COPIED_FIELDS:
            self.assertEqual(getattr(copy, name), getattr(self.event, name), name)
        self.assertTrue(copy.camp_tools)
        self.assertEqual(copy.kind, Event.Kind.RETREAT)
        self.assertEqual(copy.privacy_notice, "Aviso propio de la iglesia.")
        self.assertEqual(list(copy.sedes.all()), [self.sede])

    def test_the_questionnaires_in_use_with_their_versions_and_order(self):
        """Defect: the newest published version instead of the one in use, or the order lost."""
        copy = self.duplicate().event
        self.assertEqual(
            [(link.version, link.order) for link in links_for(copy)], [(self.version, 1), (self.second, 2)]
        )
        self.assertNotIn(self.unused_version, [link.version for link in links_for(copy)])
        self.assertTrue(copy.questionnaires_in_order)
        # the same versions, not copies of them: the questionnaires belong to the church
        self.assertEqual(self.version.questionnaire.versions.count(), 2)

    def test_the_stations_with_their_names_and_state(self):
        copy = self.duplicate().event
        self.assertEqual(
            list(copy.stations.values_list("number", "name", "is_active")),
            list(self.event.stations.values_list("number", "name", "is_active")),
        )
        self.assertIn((3, "Mesa del comedor", False), list(copy.stations.values_list("number", "name", "is_active")))
        self.assertFalse(set(copy.stations.values_list("pk", flat=True)) & set(self.event.stations.values_list("pk", flat=True)))

    def test_it_is_audited_in_both_events(self):
        copy = self.duplicate().event
        created = AuditEvent.objects.get(event=copy, action="evento_creado")
        self.assertEqual(created.actor, self.admin)
        self.assertEqual(created.metadata, {"source_event": self.event.pk})
        row = AuditEvent.objects.filter(event=self.event).order_by("-id").first()
        self.assertEqual((row.action, row.summary), ("evento_duplicado", "Duplicó este evento como plantilla: Campamento B 2027"))
        self.assertEqual(row.actor, self.admin)

    def test_it_is_all_or_nothing(self):
        """Defect: half a copy (an event without questionnaires or computers) left behind by an error."""
        from unittest import mock

        events = Event.objects.count()
        with mock.patch.object(dup.InterviewStation.objects, "bulk_create", side_effect=RuntimeError("falla")):
            with self.assertRaises(RuntimeError):
                self.duplicate()
        self.assertEqual(Event.objects.count(), events)
        self.assertFalse(AuditEvent.objects.filter(action="evento_duplicado").exists())

    def test_a_simple_event_is_not_duplicated_as_a_camp(self):
        """Decision 0015: a copy never changes the type."""
        simple = Event.objects.create(church=self.church, name="Noche de alabanza", slug="noche", camp_tools=False)
        with self.assertRaises(ValueError):
            dup.duplicate_camp(simple, self.admin, name="Otra", slug="otra")

    def test_the_suggested_name(self):
        self.assertEqual(dup.suggest_name("Campamento de Jóvenes 2026"), "Campamento de Jóvenes 2027")
        self.assertEqual(dup.suggest_name("Retiro 2025 de varones 2026"), "Retiro 2025 de varones 2027")
        self.assertEqual(dup.suggest_name("Campamento A"), "Campamento A (copia)")
        self.assertEqual(dup.suggest_name("Grupo 12026"), "Grupo 12026 (copia)")


class TeamCopyTests(DuplicateFixture):
    def test_without_asking_the_team_does_not_come(self):
        """Defect: last year's coordinators with access to the new camp because nobody unticked a box."""
        copy = self.duplicate().event
        self.assertFalse(EventMembership.objects.filter(event=copy).exists())
        self.assertFalse(TutorAssignment.objects.filter(event=copy).exists())
        _, response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertFalse(EventMembership.objects.filter(event__slug="campamento-2027").exists())
        self.assertEqual(mail.outbox, [])

    def test_the_form_does_not_come_ticked(self):
        client = Client()
        client.force_login(self.admin)
        html = client.get(reverse("events:duplicate", args=[self.event.slug])).content.decode()
        box = re.search(r'<input[^>]*name="copy_team"[^>]*>', html).group(0)
        self.assertNotIn("checked", box)

    def test_the_team_comes_with_its_roles_teams_and_supervision(self):
        result = self.duplicate(copy_team=True)
        copy = result.event
        source = EventMembership.objects.filter(event=self.event, is_active=True)
        self.assertEqual(
            sorted(EventMembership.objects.filter(event=copy).values_list("user_id", "role", "team_name")),
            sorted(source.values_list("user_id", "role", "team_name")),
        )
        self.assertEqual(len(result.members), source.count())
        self.assertEqual(
            sorted(TutorAssignment.objects.filter(event=copy).values_list("tutor__user_id", "coordinator__user_id")),
            sorted(TutorAssignment.objects.filter(event=self.event).values_list("tutor__user_id", "coordinator__user_id")),
        )
        # new rows, of the new event
        self.assertFalse(
            set(EventMembership.objects.filter(event=copy).values_list("pk", flat=True)) & set(source.values_list("pk", flat=True))
        )
        self.assertEqual(AuditEvent.objects.filter(event=copy, action="integrante_agregado").count(), source.count())

    def test_somebody_whose_access_was_removed_does_not_come_back(self):
        """Defect: a person taken off the team returns through the copy."""
        copy = self.duplicate(copy_team=True).event
        self.assertFalse(EventMembership.objects.filter(event=copy, user=self.outsider).exists())

    def test_confidential_access_is_not_copied(self):
        """Defect: access to confidential answers granted to the care team of the last camp with nobody deciding it."""
        self.assertTrue(EventMembership.objects.filter(event=self.event, can_view_sensitive=True).exists())
        copy = self.duplicate(copy_team=True).event
        self.assertFalse(EventMembership.objects.filter(event=copy, can_view_sensitive=True).exists())
        self.assertFalse(AuditEvent.objects.filter(event=copy, action="acceso_confidencial").exists())

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_each_person_is_invited_again_with_nothing_reused(self):
        """Defect: an old invitation link or token carried to the new event, or the team never told."""
        never_signed_in = make_user("nueva@prueba.mx", "Nuria", "Nueva")
        never_signed_in.set_unusable_password()
        never_signed_in.save()
        EventMembership.objects.create(event=self.event, user=never_signed_in, role=MembershipRole.COORDINATOR)
        old = Invitation.objects.create(church=self.church, email="tutor1@prueba.mx", role=Role.MENTOR, invited_by=self.admin)
        invitations = Invitation.objects.count()
        mail.outbox.clear()

        client, response = self.post(copy_team="on")
        copy = Event.objects.get(slug="campamento-2027")
        self.assertRedirects(response, reverse("events:admin_home", args=[copy.slug]), fetch_redirect_response=False)
        members = EventMembership.objects.filter(event=copy)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), sorted(members.values_list("user__email", flat=True)))
        for message in mail.outbox:
            self.assertEqual(message.subject, "Te invitaron a Campamento 2027 en Emmaus")
            self.assertIn("Te agregaron al equipo de Campamento 2027", message.body)
            self.assertNotIn(old.token, message.body)
            self.assertNotIn(self.event.join_code, message.body)
            self.assertNotIn(copy.join_code, message.body)
        # No church invitation is created or reused: an event team is told by its own message.
        self.assertEqual(Invitation.objects.count(), invitations)
        # Who has a password signs in as always; who never created one gets a new link that works now.
        with_password = next(m for m in mail.outbox if m.to == ["tutor1@prueba.mx"])
        self.assertIn("Entra con tu contraseña de siempre", with_password.body)
        self.assertNotIn("/restablecer/", with_password.body)
        fresh = next(m for m in mail.outbox if m.to == ["nueva@prueba.mx"])
        uid, token = re.search(r"/([0-9A-Za-z_\-]+)/([0-9A-Za-z]+-[0-9a-f]+)/", fresh.body).groups()
        self.assertEqual(int(urlsafe_base64_decode(uid)), never_signed_in.pk)
        self.assertTrue(default_token_generator.check_token(never_signed_in, token))
        html = client.get(response["Location"]).content.decode()
        self.assertIn(f"Copiamos a {members.count()} personas del equipo. Les avisamos por correo a {members.count()}.", html)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_without_e_mail_the_screen_says_nobody_was_told(self):
        """Defect: "Les avisamos por correo" when the server only writes messages to its log."""
        client, response = self.post(copy_team="on")
        html = client.get(response["Location"]).content.decode()
        self.assertEqual(mail.outbox, [])
        self.assertNotIn("avisamos por correo", html)
        self.assertIn("no les llegó correo: avísales tú.", html)

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_the_administrator_who_duplicates_is_not_written_to(self):
        EventMembership.objects.create(event=self.event, user=self.admin, role=MembershipRole.ADMIN)
        mail.outbox.clear()
        self.post(copy_team="on")
        self.assertNotIn([self.admin.email], [m.to for m in mail.outbox])
        self.assertTrue(EventMembership.objects.filter(event__slug="campamento-2027", user=self.admin).exists())


class ScreenTests(DuplicateFixture):
    def test_the_page_says_what_is_copied_and_suggests_a_name(self):
        Event.objects.filter(pk=self.event.pk).update(name="Campamento 2026")
        client = Client()
        client.force_login(self.admin)
        html = client.get(reverse("events:duplicate", args=[self.event.slug])).content.decode()
        self.assertEqual(len(re.findall(r"<h1\b", html)), 1)
        self.assertRegex(html, r'<label class="label" for="id_name">Nombre del evento nuevo</label>')
        self.assertIn("Duplicar como plantilla", html)
        self.assertIn('value="Campamento 2027"', html)
        self.assertIn("Nunca se copian los camperos, las entrevistas, las respuestas, los seguimientos", html)
        self.assertIn("Copiar también al equipo", html)
        settings_page = client.get(reverse("events:settings", args=[self.event.slug])).content.decode()
        self.assertIn(f'href="{reverse("events:duplicate", args=[self.event.slug])}">Duplicar como plantilla</a>', settings_page)

    def test_posting_creates_the_copy_and_opens_its_home(self):
        client, response = self.post(name="Campamento 2027", starts_on="2027-07-12", ends_on="2027-07-16")
        copy = Event.objects.get(slug="campamento-2027")
        self.assertRedirects(response, reverse("events:admin_home", args=[copy.slug]), fetch_redirect_response=False)
        self.assertEqual((copy.status, copy.starts_on, copy.ends_on), ("borrador", date(2027, 7, 12), date(2027, 7, 16)))
        self.assertEqual(copy.stations.count(), 10)
        html = client.get(response["Location"]).content.decode()
        self.assertIn("Listo. Campamento 2027 ya está creado, en preparación, con la configuración de Campamento A.", html)
        self.assertIn("Confirma los procedimientos de la iglesia", html)

    def test_a_name_already_taken_gets_another_address(self):
        self.post(name="Campamento A")
        self.assertTrue(Event.objects.filter(slug="campamento-a-2", name="Campamento A").exists())

    def test_the_name_is_required_and_the_dates_make_sense(self):
        events = Event.objects.count()
        _, response = self.post(name="")
        self.assertContains(response, "Escribe el nombre del evento nuevo.")
        _, response = self.post(starts_on="2027-07-16", ends_on="2027-07-12")
        self.assertContains(response, "La fecha de término no puede ser anterior a la de inicio.")
        _, response = self.post(ends_on="2027-07-12")
        self.assertContains(response, "Escribe también la fecha de inicio, o deja las dos vacías.")
        self.assertEqual(Event.objects.count(), events)

    def test_another_churchs_administrator_is_refused(self):
        """Defect: an administrator copying the configuration, questionnaires or team of another church's camp.
        Refused with 403, like every address under a camp (people/tests/test_tenant_isolation.py pins that for all of
        them); the agenda's "Duplicar" answers 404 (agenda/tests/test_duplicate.py)."""
        other_church = Church.objects.create(name="Otra iglesia", slug="otra-iglesia")
        stranger = make_user("admin@otra.mx")
        assign_role(stranger, other_church, Role.CHURCH_ADMIN)
        events = Event.objects.count()
        audits = AuditEvent.objects.count()
        client = Client()
        client.force_login(stranger)
        url = reverse("events:duplicate", args=[self.event.slug])
        self.assertEqual(client.get(url).status_code, 403)
        self.assertEqual(client.post(url, {"name": "Robado", "slug": "", "copy_team": "on"}).status_code, 403)
        self.assertEqual(Event.objects.count(), events)
        self.assertEqual(AuditEvent.objects.count(), audits)
        self.assertEqual(Event.objects.filter(church=other_church).count(), 0)

    def test_only_the_churchs_administration_duplicates(self):
        """Creating an event belongs to the church's administration, as in "Crear evento": an administrator of this
        event alone, a pastor or a coordinator does not get the page or the link."""
        event_admin = make_user("solo-evento@prueba.mx")
        EventMembership.objects.create(event=self.event, user=event_admin, role=MembershipRole.ADMIN)
        url = reverse("events:duplicate", args=[self.event.slug])
        for user in (event_admin, self.pastor, self.coord1, self.outsider):
            client = Client()
            client.force_login(user)
            self.assertEqual(client.get(url).status_code, 403, user.email)
            self.assertEqual(client.post(url, {"name": "No", "slug": ""}).status_code, 403, user.email)
        client = Client()
        client.force_login(event_admin)
        settings_page = client.get(reverse("events:settings", args=[self.event.slug])).content.decode()
        self.assertNotIn("Duplicar como plantilla", settings_page)
        self.assertFalse(Event.objects.filter(name="No").exists())

    def test_an_anonymous_visitor_is_sent_to_sign_in(self):
        response = Client().get(reverse("events:duplicate", args=[self.event.slug]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/cuenta/entrar/", response["Location"])

    def test_a_simple_events_address_is_not_a_camp_to_duplicate(self):
        simple = Event.objects.create(church=self.church, name="Noche de alabanza", slug="noche", camp_tools=False)
        client = Client()
        client.force_login(self.admin)
        self.assertEqual(client.get(reverse("events:duplicate", args=[simple.slug])).status_code, 404)
