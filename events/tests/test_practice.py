"""Issue #49: the practice campero, the readiness checklist and "Equipo".

What never counts is proved screen by screen in ``test_practice_exclusion.py``. Here: the practice campero's own life
(one per event, created, passed around and removed by the administration, labelled wherever it shows, rehearsed with
before the event is in course), the steps of "Preparación" (each one counted and linked to the screen that fixes it),
and what "Equipo" says about each member.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from events.management.commands.purge_expired_event_data import has_open_care, purge_event
from events.models import (
    PRACTICE_NAME,
    Answer,
    AuditEvent,
    ConsentRecord,
    Event,
    EventMembership,
    EventParticipant,
    FollowUp,
    Interview,
    InterviewStatus,
    MembershipRole,
    QuestionnaireVersion,
)
from events.services import audit
from events.services import event_questionnaires as eq
from events.services import follow_ups as follow_up_svc
from events.services import interviews as interview_svc
from events.services import participants as participant_svc
from events.services import practice as practice_svc
from events.services.metrics import build_payload, event_scope
from events.services.questionnaire import duplicate_version, publish_version

from .base import CampFixture

SECRET = "RESPUESTA-SECRETA-DE-PRACTICA"


class PracticeBase(CampFixture):
    def practice(self, coordinator="coord1"):
        membership = {"coord1": self.m_coord1, "coord2": self.m_coord2, None: None}[coordinator]
        return practice_svc.create(self.event, actor=self.admin, coordinator=membership)

    def rehearse(self, practice, client=None, user=None):
        """A whole practice interview through the screens, as its coordinador: a confidential answer, a request for
        support in a confidential area, and "se requiere seguimiento"."""
        client = client or self.login(user or self.coord1, Client())
        self.assertEqual(self.start(practice, client=client).status_code, 302)
        interview = Interview.objects.get(participant=practice, status=Interview.Status.IN_PROGRESS)
        detail = {f"q_{self.question('dudas_detalle').id}": SECRET}  # asked on the same screen as "dudas"
        for code, value, extra in [("salvo", "no_seguro", None), ("dudas", "si", detail), ("apoyo", "sexualidad", None), ("decision", "hablar_lider", None)]:
            self.answer(interview, code, value, client=client, extra=extra)
        interview.refresh_from_db()
        response = client.post(self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "requerido"})
        self.assertRedirects(response, self.url("interview_done", interview.public_id), fetch_redirect_response=False)
        interview.refresh_from_db()
        self.assertEqual(interview.status, Interview.Status.COMPLETED)
        return interview

    def checklist(self, user=None):
        page = self.login(user or self.admin, Client()).get(self.url("admin_home"))
        self.assertEqual(page.status_code, 200)
        return {step["key"]: step for step in page.context["steps"]}

    def sign_in_everybody(self):
        for membership in EventMembership.objects.filter(event=self.event).select_related("user"):
            membership.user.last_login = timezone.now()
            membership.user.save(update_fields=["last_login"])


# ---- one per event, by the administration ------------------------------------------------------------------------


class PracticeCamperoTests(PracticeBase):
    def test_it_is_one_per_event_and_takes_no_number(self):
        practice = self.practice()
        self.assertTrue(practice.is_practice)
        self.assertEqual((practice.full_name, practice.number, practice.coordinator), (PRACTICE_NAME, 0, self.m_coord1))
        self.assertFalse(practice.is_minor)  # no guardian authorization to invent
        self.assertEqual(practice_svc.create(self.event, actor=self.admin).pk, practice.pk)  # a second click creates nothing
        self.assertEqual(EventParticipant.objects.practice().filter(event=self.event).count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():  # and the database refuses a second one
            EventParticipant.objects.create(event=self.event, number=999, full_name="Otro", age=20, room="", church_group="", is_practice=True)
        # The next campero takes the number it would have taken without it
        real = participant_svc.create_participant(
            self.event, actor=self.admin, full_name="Nueva Persona Real", age=20, room="3", church_group="Centro", coordinator=None
        )
        self.assertEqual(real.number, 6)
        self.assertEqual(practice_svc.create(self.other_event, actor=self.admin).event, self.other_event)  # each event has its own

    def test_creating_it_is_audited(self):
        self.practice()
        row = AuditEvent.objects.get(event=self.event, action="practica_creada")
        self.assertEqual((row.actor, row.summary), (self.admin, "Creó el campero de práctica"))

    def test_it_goes_only_to_a_coordinador_of_this_event(self):
        for membership in (self.m_tutor1, self.m_other):
            with self.assertRaises(practice_svc.PracticeError):
                practice_svc.create(self.event, actor=self.admin, coordinator=membership)
        self.m_coord2.is_active = False
        self.m_coord2.save(update_fields=["is_active"])
        with self.assertRaises(practice_svc.PracticeError):
            practice_svc.create(self.event, actor=self.admin, coordinator=self.m_coord2)
        self.assertIsNone(practice_svc.get(self.event))

    def test_removing_it_deletes_its_interviews_answers_and_follow_ups_and_is_audited(self):
        practice = self.practice()
        interview = self.rehearse(practice)
        self.assertEqual(Answer.objects.filter(interview=interview).count(), 5)
        self.assertEqual(FollowUp.objects.filter(participant=practice).count(), 2)  # the ordinary one and the confidential one
        self.assertTrue(ConsentRecord.objects.filter(participant=practice).exists())
        self.assertEqual(practice_svc.delete(self.event, actor=self.admin), 1)
        self.assertIsNone(practice_svc.get(self.event))
        self.assertFalse(Interview.objects.filter(pk=interview.pk).exists())
        self.assertFalse(Answer.objects.filter(interview_id=interview.pk).exists())
        self.assertFalse(FollowUp.objects.filter(participant_id=practice.pk).exists())
        self.assertFalse(ConsentRecord.objects.filter(participant_id=practice.pk).exists())
        row = AuditEvent.objects.get(event=self.event, action="practica_eliminada")
        self.assertEqual((row.actor, row.metadata), (self.admin, {"interviews": 1}))
        self.assertEqual(practice_svc.delete(self.event, actor=self.admin), 0)  # nothing left: nothing happens, nothing is logged
        self.assertEqual(AuditEvent.objects.filter(event=self.event, action="practica_eliminada").count(), 1)
        # Nothing of what was answered reached the activity log, and who practised is kept
        self.assertFalse(AuditEvent.objects.filter(summary__contains=SECRET).exists())
        self.assertFalse(any(SECRET in str(row.metadata) for row in AuditEvent.objects.all()))
        self.assertEqual(practice_svc.practised_user_ids(self.event), {self.coord1.id})

    def test_passing_it_on_leaves_it_as_new_for_the_next_person(self):
        practice = self.practice()
        self.rehearse(practice)
        handed = practice_svc.hand_to(self.event, self.m_coord2, actor=self.admin)
        self.assertEqual((handed.coordinator, handed.interview_status), (self.m_coord2, InterviewStatus.PENDING))
        self.assertFalse(Interview.objects.filter(participant__is_practice=True).exists())
        self.assertFalse(FollowUp.objects.filter(participant__is_practice=True).exists())
        self.assertEqual(EventParticipant.objects.practice().filter(event=self.event).count(), 1)
        row = AuditEvent.objects.get(event=self.event, action="practica_asignada")
        self.assertEqual((row.actor, row.metadata), (self.admin, {"interviews": 1}))
        self.rehearse(handed, user=self.coord2)  # and the second coordinador can do the whole thing
        self.assertEqual(practice_svc.practised_user_ids(self.event), {self.coord1.id, self.coord2.id})
        with self.assertRaises(practice_svc.PracticeError):
            practice_svc.hand_to(self.other_event, None, actor=self.admin)  # no practice campero there

    def test_every_audit_action_has_a_label(self):
        for action in ("practica_creada", "practica_asignada", "practica_eliminada", audit.PRACTICE_COMPLETED):
            self.assertIn(action, audit.ACTION_LABELS)


class PracticePageTests(PracticeBase):
    def setUp(self):
        self.client = self.login(self.admin, Client())
        self.page = self.url("practice")

    def test_only_the_administration_opens_it(self):
        for user in (self.pastor, self.care, self.tutor1, self.coord1):
            client = self.login(user, Client())
            self.assertEqual(client.get(self.page).status_code, 403, user)
            self.assertEqual(client.post(self.page, {"accion": "crear"}).status_code, 403, user)
        self.assertIsNone(practice_svc.get(self.event))
        self.assertRedirects(Client().get(self.page), f"{reverse('login')}?next={self.page}", fetch_redirect_response=False)

    def test_create_pass_and_remove(self):
        response = self.client.get(self.page)
        self.assertContains(response, "Este evento no tiene campero de práctica")
        self.assertContains(response, "Crear el campero de práctica")
        self.assertRedirects(self.client.post(self.page, {"accion": "crear", "coordinador": str(self.m_coord1.id)}), self.page)
        self.assertEqual(practice_svc.get(self.event).coordinator, self.m_coord1)
        response = self.client.get(self.page)
        self.assertContains(response, "En “Mis camperos” de Carla Uno, marcado “De práctica”.")
        self.assertContains(response, "Todavía nadie lo entrevista.")
        self.assertContains(response, 'Quién ya practicó <span class="count">0 de 3</span>', html=True)
        self.rehearse(practice_svc.get(self.event))
        response = self.client.get(self.page)
        self.assertContains(response, "Tiene 1 entrevista de práctica.")
        self.assertContains(response, 'Quién ya practicó <span class="count">1 de 3</span>', html=True)
        self.assertContains(response, "Carla Uno (ya practicó)")
        self.assertNotContains(response, SECRET)
        self.assertRedirects(self.client.post(self.page, {"accion": "pasar", "coordinador": str(self.m_coord2.id)}), self.page)
        self.assertEqual(practice_svc.get(self.event).coordinator, self.m_coord2)
        self.assertFalse(Interview.objects.filter(participant__is_practice=True).exists())
        self.assertRedirects(self.client.post(self.page, {"accion": "pasar", "coordinador": "nadie"}), self.page)
        self.assertIsNone(practice_svc.get(self.event).coordinator)
        self.assertContains(self.client.get(self.page), "Sin coordinador: por ahora solo la administración puede practicar con él.")
        self.assertRedirects(self.client.post(self.page, {"accion": "quitar"}), self.page)
        self.assertIsNone(practice_svc.get(self.event))
        self.assertContains(self.client.get(self.page), 'Quién ya practicó <span class="count">1 de 3</span>', html=True)  # kept

    def test_a_person_who_is_not_a_coordinador_here_is_refused(self):
        for value in (str(self.m_tutor1.id), str(self.m_other.id), "999999", "x", "²"):
            response = self.client.post(self.page, {"accion": "crear", "coordinador": value}, follow=True)
            self.assertContains(response, "Elige a un coordinador con acceso a este evento.")
            self.assertIsNone(practice_svc.get(self.event), value)
        response = self.client.post(self.page, {"accion": "otra-cosa"}, follow=True)
        self.assertContains(response, "No entendimos qué querías hacer.")

    def test_the_administration_can_practise_too(self):
        practice = self.practice(None)
        self.assertContains(self.client.get(self.page), f'href="{self.url("interview_start", practice.public_id)}"')
        self.rehearse(practice, client=self.client)
        self.assertEqual(practice_svc.practised_user_ids(self.event), {self.admin.id})

    def test_without_a_questionnaire_it_says_so_and_offers_no_practice(self):
        event = Event.objects.create(church=self.church, name="Sin cuestionario", slug="sin-cuestionario")
        practice_svc.create(event, actor=self.admin)
        response = self.client.get(reverse("events:practice", args=[event.slug]))
        self.assertContains(response, "todavía no tiene un cuestionario publicado")
        self.assertNotContains(response, "Practicar yo")


# ---- rehearsing ---------------------------------------------------------------------------------------------------


class RehearsalTests(PracticeBase):
    def test_the_team_rehearses_while_the_event_is_still_in_preparation(self):
        """Defect: practice only possible once the camp is live, which is the morning the issue is about."""
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.DRAFT, procedures_approved_at=None)
        practice = self.practice()
        client = self.login(self.coord1, Client())
        self.assertEqual(self.start(self.p2, client=client).status_code, 200)  # a real campero: the page again, with why not
        self.assertFalse(Interview.objects.filter(participant=self.p2).exists())
        self.assertNotContains(client.get(self.url("interview_start", practice.public_id)), "todavía no está en curso")
        self.rehearse(practice, client=client)
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.CLOSED)
        self.assertEqual(interview_svc.event_blockers(Event.objects.get(pk=self.event.pk), practice=True),
                         ["El evento ya terminó. Si falta alguna entrevista, avisa a la administración."])

    def test_every_screen_of_the_rehearsal_says_de_practica(self):
        practice = self.practice()
        client = self.login(self.coord1, Client())
        label = '<p class="notice__title">De práctica</p>'
        for response in (client.get(self.url("participant_detail", practice.public_id)), client.get(self.url("interview_start", practice.public_id))):
            self.assertContains(response, label)
        self.start(practice, client=client)
        interview = Interview.objects.get(participant=practice)
        step = self.step("salvo").number
        self.assertContains(client.get(self.url("interview_step", interview.public_id, step)), label)
        self.assertContains(client.get(self.url("interview_step", interview.public_id, step)), "<span>De práctica</span>")  # the bar, instead of number and room
        self.answer(interview, "salvo", "si", client=client)
        review = client.get(self.url("interview_review", interview.public_id))
        self.assertContains(review, label)
        self.assertNotContains(review, "Reportar una situación de riesgo")  # a rehearsal alerts nobody
        self.assertContains(client.get(self.url("interview_discard", interview.public_id)), label)
        self.assertContains(client.get(self.url("interview_handoff", interview.public_id)), label)
        interview.refresh_from_db()
        client.post(self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "no_requerido"})
        for name in ("interview_done", "interview_detail"):
            self.assertContains(client.get(self.url(name, interview.public_id)), label)
        detail = client.get(self.url("participant_detail", practice.public_id))
        self.assertContains(detail, "Ya practicaste.")
        for word in ("Corregir datos", "Reportar una situación de riesgo", "Seguimiento"):
            self.assertNotContains(detail, word)
        # A real campero's screens say nothing of the kind
        self.assertNotContains(client.get(self.url("participant_detail", self.p1.public_id)), "De práctica")
        self.assertNotContains(client.get(self.url("interview_start", self.p2.public_id)), "De práctica")

    def test_the_coordinadors_list_shows_it_apart_and_counts_only_camperos(self):
        client = self.login(self.coord1, Client())
        before = client.get(self.url("my_participants"))
        practice = self.practice()
        after = client.get(self.url("my_participants"))
        self.assertContains(after, '<h2 id="practica" class="notice__title">De práctica</h2>', html=True)
        self.assertContains(after, f'href="{self.url("interview_start", practice.public_id)}"')
        self.assertContains(after, "Empezar la práctica")
        self.assertEqual(after.context["total"], before.context["total"])
        self.assertEqual(after.context["tabs"], before.context["tabs"])
        self.assertEqual([p.pk for p in after.context["participants"]], [p.pk for p in before.context["participants"]])
        self.assertContains(after, "3 de 3 camperos")
        # Not on anybody else's list, and not on the administration's (it reaches it from Preparación)
        for user in (self.coord2, self.admin):
            self.assertNotContains(self.login(user, Client()).get(self.url("my_participants")), "De práctica")
        self.start(practice, client=client)
        self.assertContains(client.get(self.url("my_participants")), "Retomar la práctica")

    def test_a_practice_interview_leaves_follow_ups_nobody_sees_and_alerts_nobody(self):
        now = timezone.now()
        before = build_payload(event_scope(self.event), include_safety=True, include_outcomes=True, now=now)
        practice = self.practice()
        interview = self.rehearse(practice)
        self.assertEqual(FollowUp.objects.filter(participant=practice).count(), 2)
        for user in (self.admin, self.care, self.pastor, self.tutor1, self.coord1):
            client = self.login(user, Client())
            response = client.get(self.url("follow_ups") + "?estado=todos")
            self.assertEqual((response.context["total"], response.context["counts"]), (0, {}), user)
        self.assertEqual(follow_up_svc.owners_to_remind(self.event), [])
        self.assertEqual(build_payload(event_scope(self.event), include_safety=True, include_outcomes=True, now=now), before)
        # No risk report on it: not from the screen, not from the service; and no e-mail to the care team
        client = self.login(self.coord1, Client())
        self.assertEqual(client.get(self.url("safety_report", practice.public_id)).status_code, 404)
        self.assertEqual(client.post(self.url("safety_report", practice.public_id), {"details": "algo"}).status_code, 404)
        from events.access import resolve_access

        with self.assertRaises(interview_svc.NotAllowed):
            interview_svc.report_safety_concern(resolve_access(self.coord1, self.event), practice, details="algo", interview=interview)
        self.assertFalse(FollowUp.objects.filter(kind=FollowUp.Kind.SAFETY).exists())
        self.assertEqual(mail.outbox, [])
        # The activity log says somebody practised, with its own action: never "Completó una entrevista"
        self.assertEqual(AuditEvent.objects.filter(event=self.event, action=audit.PRACTICE_COMPLETED, actor=self.coord1).count(), 1)
        self.assertFalse(AuditEvent.objects.filter(event=self.event, action="entrevista_completada").exists())

    def test_the_rest_of_the_event_does_not_trip_over_it(self):
        practice = self.practice()
        self.rehearse(practice)
        # A campero named like it is not a possible duplicate of it
        self.assertEqual(participant_svc.possible_duplicates(self.event, PRACTICE_NAME), [])
        # Its confidential follow-up does not hold the retention purge; the purge counts camperos and takes it too
        self.assertFalse(has_open_care(self.event))
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.CLOSED, ends_on=date(2020, 1, 1))
        self.assertEqual(purge_event(Event.objects.get(pk=self.event.pk)), 5)
        self.assertFalse(EventParticipant.objects.filter(event=self.event).exists())

    def test_its_interview_does_not_keep_the_last_questionnaire_or_the_event_open(self):
        practice = self.practice()
        self.start(practice, client=self.login(self.coord1, Client()))  # left open on a computer
        admin = self.login(self.admin, Client())
        event = self.event
        response = admin.post(self.url("settings"), {
            "form": "basics", "basics-name": event.name, "basics-kind": event.kind, "basics-slug": event.slug, "basics-location": event.location,
            "basics-starts_on": event.starts_on.isoformat(), "basics-ends_on": event.ends_on.isoformat(), "basics-status": Event.Status.CLOSED,
        })
        self.assertEqual(response.status_code, 302)  # no "1 entrevista sigue en curso"
        self.assertEqual(Event.objects.get(pk=event.pk).status, Event.Status.CLOSED)
        eq.stop_using(event, self.version.questionnaire)  # the only questionnaire: refused only while real interviews exist
        self.assertEqual(eq.links_for(event.pk), [])

    def test_the_purge_command_reports_camperos_only(self):
        self.practice()
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.CLOSED, ends_on=date(2020, 1, 1))
        from io import StringIO

        out = StringIO()
        call_command("purge_expired_event_data", stdout=out)
        self.assertIn("se borrarían 5 camperos", out.getvalue())


# ---- "Preparación": computed, each with the screen that fixes it -----------------------------------------------


class ReadinessChecklistTests(PracticeBase):
    def assert_step(self, step, state, url, *texts):
        self.assertEqual(step["state"], state, step)
        self.assertEqual(step["url"], url, step)
        self.assertEqual(step["attention"], state != "listo")
        meta = " ".join(str(line) for line in step["meta"])
        for text in texts:
            self.assertIn(text, meta)

    def test_every_step_opens_a_screen_of_the_event(self):
        """Each row's button goes to the screen that fixes it, and that screen exists for the administration."""
        admin = self.login(self.admin, Client())
        for ready in (False, True):
            if ready:
                self.sign_in_everybody()
                self.practice()
            for key, step in self.checklist().items():
                self.assertTrue(step["url"].startswith(f"/eventos/{self.event.slug}/"), step)
                self.assertTrue(step["label"], step)
                self.assertEqual(admin.get(step["url"].split("#")[0]).status_code, 200, key)

    def test_nothing_on_the_checklist_can_be_ticked(self):
        """The page has no form in "Preparación": a step changes only when what it counts changes."""
        import re

        html = self.login(self.admin, Client()).get(self.url("admin_home")).content.decode()
        section = re.search(r'<section aria-labelledby="preparacion">.*?</section>', html, re.S).group(0)
        for tag in ("<form", "<input", "<button", 'type="checkbox"'):
            self.assertNotIn(tag, section)
        self.assertEqual(self.login(self.admin, Client()).post(self.url("admin_home"), {"acceso": "listo"}).status_code, 200)
        self.assertEqual(self.checklist()["acceso"]["state"], "revisar")  # posting at it changes nothing

    def test_access_counts_who_has_never_signed_in(self):
        team = self.url("team")
        # 7 on the team; the admin of the fixture holds a church role, not a membership, so signing in changes nothing
        self.assert_step(self.checklist()["acceso"], "revisar", team, "7 personas del equipo nunca han entrado.")
        self.login(self.coord1, Client())
        self.login(self.tutor1, Client())
        self.assert_step(self.checklist()["acceso"], "revisar", team, "5 personas del equipo nunca han entrado.")
        self.m_coord3.is_active = False  # somebody without access is not waited for
        self.m_coord3.save(update_fields=["is_active"])
        self.assert_step(self.checklist()["acceso"], "revisar", team, "4 personas del equipo nunca han entrado.")
        self.sign_in_everybody()
        self.assert_step(self.checklist()["acceso"], "listo", team, "Las 6 personas del equipo ya entraron al menos una vez.")
        empty = Event.objects.create(church=self.church, name="Vacío", slug="vacio")
        step = {s["key"]: s for s in self.login(self.admin, Client()).get(reverse("events:admin_home", args=[empty.slug])).context["steps"]}["acceso"]
        self.assertEqual((step["state"], step["url"]), ("falta", reverse("events:member_add", args=[empty.slug])))

    def test_coordinadores_without_camperos(self):
        campers = self.url("participants")
        self.assert_step(self.checklist()["coordinadores"], "listo", self.url("team"), "Cada coordinador tiene camperos.")
        self.p4.coordinator = None  # coord3's only campero
        self.p4.save(update_fields=["coordinator"])
        self.assert_step(self.checklist()["coordinadores"], "revisar", f"{campers}?coordinador=ninguno", "1 coordinador no tiene camperos.")
        # The practice campero in coord3's list is not a campero of coord3
        practice_svc.create(self.event, actor=self.admin, coordinator=self.m_coord3)
        self.assert_step(self.checklist()["coordinadores"], "revisar", f"{campers}?coordinador=ninguno", "1 coordinador no tiene camperos.")
        self.p4.is_active = False  # nobody left without coordinador to hand out: the button opens the whole list
        self.p4.save(update_fields=["is_active"])
        self.p3.is_active = False  # and a campero who no longer takes part is not one either
        self.p3.save(update_fields=["is_active"])
        self.assert_step(self.checklist()["coordinadores"], "revisar", campers, "2 coordinadores no tienen camperos.")

    def test_tutors_without_coordinadores(self):
        assignments = self.url("assignments")
        self.assert_step(self.checklist()["tutores"], "listo", assignments, "Cada tutor tiene coordinadores.")
        self.m_coord3.tutor_assignment.delete()  # tutor2's only coordinador
        self.assert_step(self.checklist()["tutores"], "revisar", assignments, "1 tutor no tiene coordinadores.")
        EventMembership.objects.filter(event=self.event, role=MembershipRole.TUTOR).update(is_active=False)
        self.assert_step(self.checklist()["tutores"], "falta", self.url("member_add"), "Todavía no hay tutores.")

    def test_follow_up_owners(self):
        follow_ups = self.url("follow_ups")
        self.assert_step(self.checklist()["seguimientos"], "listo", follow_ups, "1 persona tiene acceso confidencial.")
        orphan = FollowUp.objects.create(event=self.event, participant=self.p1)
        self.assert_step(self.checklist()["seguimientos"], "revisar", f"{follow_ups}?responsable=ninguno", "1 seguimiento abierto no tiene responsable.")
        orphan.assigned_to = self.coord1
        orphan.save(update_fields=["assigned_to"])
        self.assert_step(self.checklist()["seguimientos"], "listo", follow_ups)
        # A practice follow-up nobody attends is not one of the event's
        practice = self.practice()
        FollowUp.objects.create(event=self.event, participant=practice)
        self.assert_step(self.checklist()["seguimientos"], "listo", follow_ups)
        self.m_care.can_view_sensitive = False
        self.m_care.save(update_fields=["can_view_sensitive"])
        self.assert_step(self.checklist()["seguimientos"], "falta", self.url("team"), "Nadie tiene acceso confidencial")

    def test_practice(self):
        page = self.url("practice")
        step = self.checklist()["practica"]
        self.assert_step(step, "falta", page, "0 de 3 coordinadores ya practicaron.", "No hay campero de práctica.")
        self.assertEqual(step["label"], "Crear")
        practice = self.practice()
        step = self.checklist()["practica"]
        self.assert_step(step, "revisar", page, "0 de 3 coordinadores ya practicaron.", "El campero de práctica está con Carla Uno.")
        self.assertEqual(step["label"], "Ver")
        self.rehearse(practice)
        self.assert_step(self.checklist()["practica"], "revisar", page, "1 de 3 coordinadores ya practicaron.")
        practice_svc.delete(self.event, actor=self.admin)  # who practised survives the practice campero
        self.assert_step(self.checklist()["practica"], "revisar", page, "1 de 3 coordinadores ya practicaron.", "No hay campero de práctica.")
        for user in (self.coord2, self.coord3):
            audit.record(user, self.event, audit.PRACTICE_COMPLETED, "Completó una entrevista de práctica")
        self.assert_step(self.checklist()["practica"], "listo", page, "Los 3 coordinadores ya practicaron.")
        # A practice in another event says nothing here
        self.assertEqual(practice_svc.practised_user_ids(self.other_event), set())

    def test_the_questionnaire_is_final_and_in_use(self):
        questionnaires = self.url("questionnaires")
        self.assert_step(self.checklist()["cuestionario"], "listo", questionnaires)
        draft = duplicate_version(self.version, self.admin)  # somebody is editing the next version
        self.assertEqual(draft.status, QuestionnaireVersion.Status.DRAFT)
        self.assert_step(self.checklist()["cuestionario"], "revisar", questionnaires, "Hay 1 borrador sin publicar")
        publish_version(draft, self.admin)  # published, and the event still uses the old one
        self.assert_step(self.checklist()["cuestionario"], "revisar", questionnaires, "tiene una versión publicada más nueva que la que usa el evento")
        eq.use_version(self.event, draft)
        self.assert_step(self.checklist()["cuestionario"], "listo", questionnaires)

    def test_stations(self):
        self.assert_step(self.checklist()["computadoras"], "listo", f"{self.url('settings')}#computadoras", "10 disponibles")
        self.event.stations.update(is_active=False)
        self.assert_step(self.checklist()["computadoras"], "falta", f"{self.url('settings')}#computadoras", "Ninguna disponible")


# ---- "Equipo": last sign-in and "ya practicó", nothing of any interview ---------------------------------------------


class TeamActivityTests(PracticeBase):
    def test_each_member_says_when_they_last_signed_in_and_whether_they_practised(self):
        self.coord2.last_login = timezone.now() - timedelta(hours=3)
        self.coord2.save(update_fields=["last_login"])
        self.rehearse(self.practice())  # coord1 signs in and practises
        for viewer in (self.admin, self.pastor):
            html = self.login(viewer, Client()).get(self.url("team")).content.decode()
            rows = {name: html.split(f">{name}</", 1)[1].split("</li>", 1)[0] for name in ("Carla Uno", "César Dos", "Cora Tres", "Teresa Uno")}
            self.assertIn("Última entrada: hace un momento. Ya practicó", rows["Carla Uno"])
            self.assertIn("Última entrada: hace 3 horas. Aún no practica", rows["César Dos"])
            self.assertIn("<strong>Nunca ha entrado</strong>. Aún no practica", rows["Cora Tres"])
            self.assertIn("<strong>Nunca ha entrado</strong></p>", rows["Teresa Uno"])  # a tutor does not interview: no "practica"
            self.assertNotIn("practic", rows["Teresa Uno"])
            self.assertNotIn(SECRET, html)
            self.assertNotIn(PRACTICE_NAME, html)
            self.assertIn("Coordinador, 3 camperos", rows["Carla Uno"])  # the practice campero is not one of hers

    def test_a_tutor_and_a_coordinador_do_not_see_the_team(self):
        for user in (self.tutor1, self.coord1):
            self.assertEqual(self.login(user, Client()).get(self.url("team")).status_code, 403)
