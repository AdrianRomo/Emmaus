"""«Cambiar decisión de seguimiento» (services.follow_up_decision): the owner's decision of 2026-11-18.

A decision already taken («necesita seguimiento» / «no necesita seguimiento») is changed by the campero's coordinador or
an administrador of the event, on its own page, with a brief reason. Who, when, both decisions and the reason are kept
and shown on the interview's record; the activity log keeps all but the reason. To «necesita»: the ordinary care
follow-up, once. To «no necesita»: cancelled if nobody started it, otherwise left open and flagged for whoever attends
it, who closes it with its own form. Confidential referrals and safety reports are never touched. The first decision of
an interview left «Por decidir» stays as it was.
"""

from __future__ import annotations

from django.test import Client
from django.urls import reverse

from events.access import resolve_access
from events.models import AuditEvent, FollowUp, FollowUpDecisionChange, Interview, InterviewStatus
from events.services import follow_up_decision as decision_svc
from events.services import follow_ups
from events.services import interviews as svc
from people import flags

from .base import CampFixture
from .test_ux_coordinator import give_interview

REASON = "Lo platicamos otra vez con su tutor y ya tiene quien lo acompañe."
CARE = FollowUp.Kind.CARE


class ChangeFixture(CampFixture):
    """Mariana (p1, Carla's campero) with a completed interview that «no necesita seguimiento»."""

    def setUp(self):
        self.done = give_interview(self.p1, InterviewStatus.COMPLETED, decision="no_requerido")
        self.page = self.url("interview_follow_up_change", self.done.public_id)
        self.record = self.url("interview_detail", self.done.public_id)

    def as_user(self, user) -> Client:
        return self.login(user, Client())

    def post(self, user, decision, reason=REASON, client=None):
        return (client or self.as_user(user)).post(self.page, {"decision": decision, "reason": reason})

    def access(self, user):
        return resolve_access(user, self.event)

    def ordinary(self):
        return FollowUp.objects.filter(interview=self.done, kind=CARE, confidential=False)

    def decision(self) -> str:
        self.done.refresh_from_db()
        return self.done.follow_up_decision

    def section(self, html: str) -> str:
        return html.split('id="seguimiento"')[1].split("</section>")[0]

    def history(self, html: str) -> str:
        return html.split('aria-labelledby="cambios-seguimiento"')[1].split("</section>")[0]


class WhoMayChangeTests(ChangeFixture):
    def test_the_camperos_coordinador_may(self):
        client = self.as_user(self.coord1)
        self.assertEqual(client.get(self.page).status_code, 200)
        response = self.post(self.coord1, "requerido", client=client)
        self.assertRedirects(response, self.record, fetch_redirect_response=False)
        self.assertEqual(self.decision(), "requerido")

    def test_an_administrador_of_the_event_may(self):
        client = self.as_user(self.admin)
        self.assertEqual(client.get(self.page).status_code, 200)
        self.assertRedirects(self.post(self.admin, "requerido", client=client), self.record, fetch_redirect_response=False)
        self.assertEqual(self.decision(), "requerido")

    def test_everybody_else_is_refused_and_nothing_changes(self):
        refused = [
            (self.coord2, 404),  # another coordinador: the campero is not theirs, so the interview does not exist for them
            (self.tutor1, 403),  # Carla's tutor reads the record, but changes nothing
            (self.pastor, 403),
            (self.care, 403),  # the care team (a pastor with confidential access) attends follow-ups, does not decide them
            (self.outsider, 403),  # a role in another event only
            (self.other_coord, 403),
        ]
        for user, status in refused:
            client = self.as_user(user)
            self.assertEqual(client.get(self.page).status_code, status, user)
            self.assertEqual(self.post(user, "requerido", client=client).status_code, status, user)
        self.assertEqual(self.decision(), "no_requerido")
        self.assertFalse(FollowUpDecisionChange.objects.exists())
        self.assertFalse(self.ordinary().exists())
        self.assertFalse(AuditEvent.objects.filter(action="seguimiento_corregido").exists())

    def test_without_a_session_it_asks_to_sign_in(self):
        anonymous = Client()
        for response in (anonymous.get(self.page), anonymous.post(self.page, {"decision": "requerido", "reason": REASON})):
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response["Location"].startswith(reverse("login")))
        self.assertEqual(self.decision(), "no_requerido")

    def test_only_get_and_post(self):
        client = self.as_user(self.coord1)
        self.assertEqual(client.put(self.page).status_code, 405)
        self.assertEqual(client.delete(self.page).status_code, 405)
        self.assertEqual(self.decision(), "no_requerido")

    def test_opening_the_page_changes_nothing(self):
        self.as_user(self.coord1).get(self.page, {"decision": "requerido", "reason": REASON})
        self.assertEqual(self.decision(), "no_requerido")
        self.assertFalse(FollowUpDecisionChange.objects.exists())

    def test_the_service_asks_can_correct_itself(self):
        for user in (self.tutor1, self.pastor, self.care, self.coord2):
            with self.assertRaises(svc.NotAllowed, msg=user):
                decision_svc.change(self.access(user), self.done, "requerido", reason=REASON)
        self.assertEqual(self.decision(), "no_requerido")

    def test_an_interview_not_completed_has_no_decision_to_change(self):
        live = give_interview(self.p2, InterviewStatus.IN_PROGRESS)
        client = self.as_user(self.coord1)
        url = self.url("interview_follow_up_change", live.public_id)
        self.assertEqual(client.get(url).status_code, 404)
        self.assertEqual(client.post(url, {"decision": "requerido", "reason": REASON}).status_code, 404)


class ReasonTests(ChangeFixture):
    def test_a_reason_is_required(self):
        for blank in ("", "   "):
            response = self.post(self.coord1, "requerido", reason=blank)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Escribe brevemente por qué cambias la decisión.")
        self.assertEqual(self.decision(), "no_requerido")
        with self.assertRaisesMessage(svc.InterviewError, decision_svc.REASON_REQUIRED):
            decision_svc.change(self.access(self.coord1), self.done, "requerido", reason=" \n ")
        self.assertFalse(FollowUpDecisionChange.objects.exists())

    def test_the_reason_is_short(self):
        response = self.post(self.coord1, "requerido", reason="a" * 201)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Escribe el motivo en 200 caracteres o menos.")
        self.assertEqual(self.decision(), "no_requerido")
        with self.assertRaisesMessage(svc.InterviewError, decision_svc.REASON_TOO_LONG):
            decision_svc.change(self.access(self.coord1), self.done, "requerido", reason="a" * 201)
        self.post(self.coord1, "requerido", reason="a" * 200)
        self.assertEqual(FollowUpDecisionChange.objects.get().reason, "a" * 200)

    def test_the_page_asks_for_it_in_a_plain_field_with_its_limit(self):
        html = self.as_user(self.coord1).get(self.page).content.decode()
        self.assertIn('<label class="label" for="id_reason">¿Por qué cambias la decisión?</label>', html)
        self.assertIn('maxlength="200"', html)
        self.assertIn('<input type="hidden" name="decision" value="requerido"', html)
        self.assertIn("Cambiar a «necesita seguimiento»", html)
        self.assertIn("Mariana López Ruiz no necesita seguimiento.", html)  # as it is now
        self.assertIn("Si la cambias, se creará su seguimiento.", html)  # what it will do, before it does it

    def test_a_decision_that_is_not_one_of_the_two_is_refused(self):
        self.assertEqual(self.post(self.coord1, "por_evaluar").status_code, 200)
        with self.assertRaises(svc.InterviewError):
            decision_svc.change(self.access(self.coord1), self.done, "por_evaluar", reason=REASON)
        self.assertEqual(self.decision(), "no_requerido")


class RecordTests(ChangeFixture):
    def test_who_when_both_decisions_and_the_reason_are_kept_and_the_log_keeps_all_but_the_reason(self):
        self.post(self.coord1, "requerido")
        change = FollowUpDecisionChange.objects.get()
        self.assertEqual(
            (change.interview, change.previous, change.decision, change.reason, change.changed_by, change.care),
            (self.done, "no_requerido", "requerido", REASON, self.coord1, "creado"),
        )
        self.assertIsNotNone(change.changed_at)
        row = AuditEvent.objects.get(action="seguimiento_corregido")
        self.assertEqual((row.actor, row.event, row.object_type, row.object_id), (self.coord1, self.event, "Interview", str(self.done.public_id)))
        self.assertEqual(row.metadata, {"antes": "no_requerido", "despues": "requerido", "seguimiento": "creado"})
        self.assertEqual(row.summary, f"Cambió la decisión de seguimiento del campero #{self.p1.code}")
        for logged in AuditEvent.objects.all():
            self.assertNotIn("platicamos", logged.summary + str(logged.metadata))

    def test_the_activity_log_names_the_action(self):
        self.post(self.coord1, "requerido")
        html = self.as_user(self.admin).get(self.url("audit")).content.decode()
        self.assertIn("Cambió una decisión de seguimiento", html)
        self.assertNotIn("platicamos", html)

    def test_the_message_names_the_campero_and_the_change_and_the_url_carries_nothing(self):
        client = self.as_user(self.coord1)
        response = self.post(self.coord1, "requerido", client=client)
        self.assertEqual(response["Location"], self.record)
        page = client.get(self.record)
        self.assertEqual(
            [m.message for m in page.context["messages"]],
            ["Cambiaste la decisión de Mariana López Ruiz: ahora necesita seguimiento. Se creó su seguimiento."],
        )
        self.post(self.coord1, "no_requerido", client=client)
        page = client.get(self.record)
        self.assertEqual(
            [m.message for m in page.context["messages"]],
            ["Cambiaste la decisión de Mariana López Ruiz: ya no necesita seguimiento. Se canceló su seguimiento, que nadie había empezado."],
        )

    def test_the_record_shows_the_history_to_whoever_sees_it_and_the_action_to_whoever_may_use_it(self):
        self.post(self.coord1, "requerido")
        self.post(self.admin, "no_requerido", reason="Se registró por error.")
        tutor_html = self.as_user(self.tutor1).get(self.record).content.decode()
        self.assertNotIn("Cambiar decisión de seguimiento", self.section(tutor_html))
        tutor = self.history(tutor_html)
        self.assertIn('<h2 id="cambios-seguimiento" class="heading">Cambios de la decisión de seguimiento</h2>', tutor)
        self.assertIn("De «necesita seguimiento» a «no necesita seguimiento»", tutor)
        self.assertIn("Ada Admin. Motivo: Se registró por error.", tutor)
        self.assertIn(f"Carla Uno. Motivo: {REASON}", tutor)
        self.assertLess(tutor.index("Se registró por error."), tutor.index(REASON))  # newest first
        self.assertIn("Se canceló su seguimiento: nadie lo había empezado.", tutor)
        self.assertNotIn("Cambiar decisión de seguimiento", tutor)
        coordinator = self.section(self.as_user(self.coord1).get(self.record).content.decode())
        self.assertIn(f'<p class="more"><a href="{self.page}">Cambiar decisión de seguimiento</a></p>', coordinator)


class CareFollowUpTests(ChangeFixture):
    def test_to_required_creates_the_ordinary_follow_up_once(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        self.assertEqual(
            (follow_up.status, follow_up.reason, follow_up.created_by, follow_up.assigned_to, follow_up.participant),
            ("pendiente", "criterio_coordinador", self.coord1, self.coord1, self.p1),  # its default owner, as the first decision
        )
        self.post(self.coord1, "requerido")  # a second tab sending the same change
        self.assertEqual(self.ordinary().count(), 1)
        self.post(self.coord1, "no_requerido")  # nobody started it: cancelled
        self.post(self.coord1, "requerido")  # a new one, never two open
        self.assertEqual(self.ordinary().count(), 2)
        self.assertEqual(self.ordinary().exclude(status="cancelado").count(), 1)

    def test_a_cancelled_one_is_not_reopened_beside_the_new_one(self):
        self.post(self.coord1, "requerido")
        first = self.ordinary().get()
        self.post(self.coord1, "no_requerido")
        self.post(self.coord1, "requerido")
        response = self.as_user(self.coord1).post(self.url("follow_up_detail", first.pk), {"status": "pendiente", "assigned_to": self.coord1.pk})
        self.assertContains(response, "Su entrevista ya tiene otro seguimiento. Continúa en ese.")
        first.refresh_from_db()
        self.assertEqual(first.status, "cancelado")
        self.assertEqual(self.ordinary().exclude(status="cancelado").count(), 1)

    def test_one_already_there_is_kept_not_doubled(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        FollowUp.objects.filter(pk=follow_up.pk).update(status="en_proceso")
        self.post(self.coord1, "no_requerido")
        self.post(self.coord1, "requerido")
        self.assertEqual(list(self.ordinary().values_list("pk", "status")), [(follow_up.pk, "en_proceso")])
        self.assertEqual(FollowUpDecisionChange.objects.first().care, "sin_cambio")

    def test_an_attended_one_stays_as_it_was(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        FollowUp.objects.filter(pk=follow_up.pk).update(status="atendido")
        self.post(self.coord1, "no_requerido")
        self.post(self.coord1, "requerido")
        self.assertEqual(list(self.ordinary().values_list("pk", "status")), [(follow_up.pk, "atendido")])

    def test_to_not_required_before_care_work_cancels_it_and_says_why(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        self.post(self.admin, "no_requerido", reason="Se marcó por error.")
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, "cancelado")
        row = AuditEvent.objects.get(action="seguimiento", object_type="FollowUp", object_id=str(follow_up.pk))
        self.assertEqual((row.actor, row.metadata), (self.admin, {"status": "cancelado"}))
        self.assertNotIn("error", row.summary)
        html = self.as_user(self.coord1).get(self.url("follow_up_detail", follow_up.pk)).content.decode()
        self.assertIn("Se canceló porque cambió la decisión de su entrevista", html)
        self.assertIn("Nadie lo había empezado. Ada Admin la cambió a «no necesita seguimiento» el", html)
        self.assertIn("Motivo: Se marcó por error.", html)

    def test_what_counts_as_care_work_started(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        self.assertFalse(decision_svc.care_started(follow_up))  # given its default owner when created: not work
        for started in ({"status": "en_proceso"}, {"resolution_note": "Hablé con su familia."}, {"details": "Algo"}):
            FollowUp.objects.filter(pk=follow_up.pk).update(**started)
            follow_up.refresh_from_db()
            self.assertTrue(decision_svc.care_started(follow_up), started)
            FollowUp.objects.filter(pk=follow_up.pk).update(status="pendiente", resolution_note="", details="")
            follow_up.refresh_from_db()
        self.assertFalse(decision_svc.care_started(follow_up))
        # Saved on its own screens (here, who attends it, from its row in the list): somebody worked on it.
        self.as_user(self.tutor1).post(self.url("follow_up_assign", follow_up.pk), {"assigned_to": self.tutor1.pk})
        self.assertTrue(decision_svc.care_started(follow_up))

    def test_after_care_work_it_stays_open_flagged_and_its_owner_resolves_it(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        owner = self.as_user(self.coord1)  # Carla attends it (its default owner) and starts it
        detail = self.url("follow_up_detail", follow_up.pk)
        owner.post(detail, {"status": "en_proceso", "assigned_to": self.coord1.pk})
        self.post(self.admin, "no_requerido", reason="Ya lo acompaña su pastor.")
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, "en_proceso")  # never cancelled once started
        self.assertEqual(FollowUpDecisionChange.objects.first().care, "abierto")
        listed = owner.get(self.url("follow_ups") + "?responsable=mio").content.decode()
        self.assertIn("Decisión cambiada", listed)
        self.assertIn("Su entrevista cambió a «no necesita seguimiento»: Ada Admin,", listed)
        self.assertIn("Motivo: Ya lo acompaña su pastor.", listed)
        page = owner.get(detail).content.decode()
        self.assertIn('<div class="notice notice--warning" id="decision-cambiada">', page)
        self.assertIn("Ada Admin la cambió a «no necesita seguimiento» el", page)
        self.assertIn("Motivo: Ya lo acompaña su pastor.", page)
        self.assertIn("en «Estado» elige «Atendido» o «Cancelado» y guarda.", page)
        # Resolved with its own form, audited as every update of a follow-up.
        owner.post(detail, {"status": "atendido", "assigned_to": self.coord1.pk})
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, "atendido")
        self.assertEqual(
            list(AuditEvent.objects.filter(action="seguimiento", object_id=str(follow_up.pk)).values_list("actor", "metadata")),
            [(self.coord1.pk, {"status": "atendido"}), (self.coord1.pk, {"status": "en_proceso"})],
        )
        self.assertNotIn("Decisión cambiada", owner.get(self.url("follow_ups") + "?estado=todos").content.decode())
        self.assertNotIn("decision-cambiada", owner.get(detail).content.decode())

    def test_whoever_cannot_update_it_reads_the_change_without_the_instruction(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        FollowUp.objects.filter(pk=follow_up.pk).update(status="en_proceso")
        self.post(self.coord1, "no_requerido")
        # Every role that sees an ordinary follow-up may update it; one that may not reads the same note, without
        # the instruction. Here: the template given a viewer without can_manage.
        from django.template.loader import render_to_string

        follow_up.refresh_from_db()
        html = render_to_string(
            "events/supervision/_decision_change_note.html",
            {"decision_change": decision_svc.note_for(follow_up), "can_manage": False},
        )
        self.assertIn("Carla Uno la cambió a «no necesita seguimiento»", html)
        self.assertNotIn("elige «Atendido»", html)

    def test_back_to_required_clears_the_flag(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        FollowUp.objects.filter(pk=follow_up.pk).update(status="en_proceso")
        self.post(self.coord1, "no_requerido")
        self.assertIn(follow_up.pk, decision_svc.waiting_on_owner([follow_up]))
        self.post(self.coord1, "requerido")
        follow_up.refresh_from_db()
        self.assertEqual(decision_svc.waiting_on_owner([follow_up]), {})
        self.assertIsNone(decision_svc.note_for(follow_up))


class ConfidentialAndSafetyUntouchedTests(ChangeFixture):
    """A confidential request for support and a safety report keep their own workflow and history."""

    def setUp(self):
        super().setUp()
        self.referral = follow_ups.create(
            event=self.event, participant=self.p1, interview=self.done, kind=CARE, reason=FollowUp.Reason.SUPPORT_REQUESTED,
            confidential=True, created_by=None,
        )
        self.report = follow_ups.create(
            event=self.event, participant=self.p1, interview=self.done, kind=FollowUp.Kind.SAFETY, reason=FollowUp.Reason.SAFETY,
            details="Reporte sintético.", created_by=self.coord1,
        )

    def snapshot(self):
        return list(
            FollowUp.objects.filter(pk__in=[self.referral.pk, self.report.pk])
            .order_by("pk")
            .values_list("status", "assigned_to", "details", "resolution_note", "reason", "updated_at", "resolved_at")
        )

    def test_neither_is_created_cancelled_reopened_or_edited(self):
        before = self.snapshot()
        for decision in ("requerido", "no_requerido", "requerido", "no_requerido"):
            self.post(self.coord1, decision)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(FollowUp.objects.filter(interview=self.done, confidential=True).count(), 1)
        self.assertEqual(FollowUp.objects.filter(interview=self.done, kind=FollowUp.Kind.SAFETY).count(), 1)
        self.assertFalse(AuditEvent.objects.filter(action="seguimiento", object_id__in=[str(self.referral.pk), str(self.report.pk)]).exists())
        self.assertFalse(FollowUpDecisionChange.objects.filter(follow_up__in=[self.referral, self.report]).exists())

    def test_a_cancelled_referral_is_not_reopened_nor_replaced(self):
        FollowUp.objects.filter(pk=self.referral.pk).update(status="cancelado")
        for decision in ("requerido", "no_requerido", "requerido"):
            self.post(self.coord1, decision)
        self.referral.refresh_from_db()
        self.assertEqual(self.referral.status, "cancelado")
        self.assertEqual(FollowUp.objects.filter(interview=self.done, confidential=True).count(), 1)

    def test_they_are_never_flagged_for_their_owner(self):
        FollowUp.objects.filter(pk__in=[self.referral.pk, self.report.pk]).update(status="en_proceso")
        self.post(self.coord1, "requerido")
        self.post(self.coord1, "no_requerido")
        rows = list(FollowUp.objects.filter(pk__in=[self.referral.pk, self.report.pk]))
        self.assertEqual(decision_svc.waiting_on_owner(rows), {})
        self.assertTrue(all(decision_svc.note_for(row) is None for row in rows))


class FirstDecisionUnchangedTests(ChangeFixture):
    def setUp(self):
        super().setUp()
        Interview.objects.filter(pk=self.done.pk).update(follow_up_decision="por_evaluar")

    def test_por_decidir_is_decided_once_on_the_record_as_before(self):
        client = self.as_user(self.coord1)
        self.assertRedirects(client.get(self.page), self.record, fetch_redirect_response=False)
        self.assertRedirects(self.post(self.coord1, "requerido", client=client), self.record, fetch_redirect_response=False)
        self.assertEqual(self.decision(), "por_evaluar")  # the change page never decides a first time
        section = self.section(client.get(self.record).content.decode())
        self.assertIn('<button class="btn btn--primary" name="decision" value="requerido">Sí requiere seguimiento</button>', section)
        self.assertNotIn("Cambiar decisión de seguimiento", section)
        client.post(self.record, {"action": "decision", "decision": "requerido"})
        page = client.get(self.record)
        self.assertEqual([m.message for m in page.context["messages"]], ["Mariana López Ruiz necesita seguimiento."])
        self.assertEqual(self.ordinary().count(), 1)
        self.assertFalse(FollowUpDecisionChange.objects.exists())
        with self.assertRaisesMessage(svc.InterviewError, "El seguimiento de esta entrevista ya se decidió."):
            svc.update_follow_up_decision(self.access(self.coord1), self.done, "no_requerido")
        self.assertIn("Cambiar decisión de seguimiento", self.section(client.get(self.record).content.decode()))

    def test_the_service_refuses_por_decidir(self):
        with self.assertRaises(svc.InterviewError):
            decision_svc.change(self.access(self.coord1), self.done, "requerido", reason=REASON)
        self.assertEqual(self.decision(), "por_evaluar")


class StaleTabTests(ChangeFixture):
    def test_a_second_tab_sending_the_same_change_is_refused_in_words(self):
        client = self.as_user(self.coord1)
        self.post(self.coord1, "requerido", client=client)
        client.get(self.record)  # reads the first message
        response = self.post(self.coord1, "requerido", client=client)
        self.assertRedirects(response, self.record, fetch_redirect_response=False)
        page = client.get(self.record)
        self.assertEqual([m.message for m in page.context["messages"]], ["Ya estaba así: Mariana López Ruiz necesita seguimiento."])
        self.assertEqual(FollowUpDecisionChange.objects.count(), 1)


class BothModesTests(ChangeFixture):
    """Product behaviour: the action and the history are on the record with and without the redesign."""

    OLD_SECTION = (
        '\n  <h2 id="seg-titulo" class="heading">Seguimiento</h2>\n  <p>No se requiere seguimiento</p>\n  \n'
    )

    def test_without_the_redesign_a_reader_gets_the_section_byte_for_byte(self):
        html = self.as_user(self.tutor1).get(self.record).content.decode()
        self.assertIn(
            f'<section class="block" id="seguimiento" aria-labelledby="seg-titulo">{self.OLD_SECTION}</section>\n\n\n'
            '<section class="block" aria-labelledby="respuestas">',
            html,
        )

    def test_without_the_redesign(self):
        client = self.as_user(self.coord1)
        page = client.get(self.page).content.decode()
        self.assertIn('<div class="step__actions">', page)
        self.post(self.coord1, "requerido", client=client)
        html = client.get(self.record).content.decode()
        self.assertIn(f'<p class="more"><a href="{self.page}">Cambiar decisión de seguimiento</a></p>', self.section(html))
        self.assertIn(
            '<li class="row"><div class="row__main"><p class="row__title">De «no necesita seguimiento» a «necesita seguimiento»</p>',
            self.history(html),
        )

    def test_with_the_redesign(self):
        flags.set_enabled(self.church, "community", True)
        client = self.as_user(self.coord1)
        page = client.get(self.page)
        self.assertTrue(page.context["ui_v2"])
        self.assertIn('<div class="form-actions">', page.content.decode())
        self.post(self.coord1, "requerido", client=client)
        response = client.get(self.record)
        self.assertTrue(response.context["ui_v2"])
        section = self.section(response.content.decode())
        self.assertIn("Necesita seguimiento.", section)
        self.assertIn(f'<p class="more"><a href="{self.page}">Cambiar decisión de seguimiento</a></p>', section)
        history = self.history(response.content.decode())
        self.assertIn('<li class="row row--lead">', history)
        self.assertIn("De «no necesita seguimiento» a «necesita seguimiento»", history)
