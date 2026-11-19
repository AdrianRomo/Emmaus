"""«Cambiar decisión de seguimiento» (services.follow_up_decision): the owner's decisions of 2026-11-18.

A decision already taken («necesita seguimiento» / «no necesita seguimiento») is changed by the campero's coordinador or
an administrador of the event, on its own page, with a brief reason. Who, when, both decisions and the reason are kept
and shown on the interview's record; the activity log keeps all but the reason. To «necesita»: the ordinary care task is
created once, or reopened when it was closed (attended or cancelled), its closing kept in its history. To «no necesita»:
an open task is never closed by the change; it is marked «Decisión cambiada» in every list of follow-ups and on its page,
with «Revisar» for its owner, who closes it with its own form. Nothing other roles see depends on a confidential note.
Confidential referrals and safety reports are never touched. The first decision of «Por decidir» stays as it was."""

from __future__ import annotations

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from events.access import resolve_access
from events.models import AuditEvent, FollowUp, FollowUpDecisionChange, Interview, InterviewStatus
from events.services import follow_up_decision as decision_svc
from events.services import follow_ups
from events.services import interviews as svc
from people import flags
from people.tests.test_screens_unchanged import masked

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
            ["Cambiaste la decisión de Mariana López Ruiz: ya no necesita seguimiento. Su seguimiento sigue abierto hasta que quien lo atiende lo revise y lo cierre."],
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
        self.assertIn("Su seguimiento siguió abierto para que quien lo atiende lo revise y lo cierre.", tutor)
        self.assertIn("Se creó su seguimiento.", tutor)
        self.assertNotIn("Cambiar decisión de seguimiento", tutor)
        coordinator = self.section(self.as_user(self.coord1).get(self.record).content.decode())
        self.assertIn(f'<p class="more"><a href="{self.page}">Cambiar decisión de seguimiento</a></p>', coordinator)


class CareFollowUpTests(ChangeFixture):
    def close(self, follow_up, status, user=None):
        """Its owner (Carla, its default owner) closes it with its own form, as on its page."""
        return self.as_user(user or self.coord1).post(
            self.url("follow_up_detail", follow_up.pk), {"status": status, "assigned_to": follow_up.assigned_to_id or ""}
        )

    def test_to_required_creates_the_ordinary_task_once_and_never_two(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        self.assertEqual(
            (follow_up.status, follow_up.reason, follow_up.created_by, follow_up.assigned_to, follow_up.participant),
            ("pendiente", "criterio_coordinador", self.coord1, self.coord1, self.p1),  # its default owner, as the first decision
        )
        self.post(self.coord1, "requerido")  # a second tab sending the same change
        self.post(self.coord1, "no_requerido")
        self.post(self.coord1, "requerido")
        self.assertEqual(list(self.ordinary().values_list("pk", "status")), [(follow_up.pk, "pendiente")])
        self.assertEqual(FollowUpDecisionChange.objects.first().care, "sin_cambio")

    def test_to_not_required_never_closes_an_open_task_whatever_was_done_on_it(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        for status in ("pendiente", "en_proceso"):
            FollowUp.objects.filter(pk=follow_up.pk).update(status=status)
            self.post(self.admin, "no_requerido")
            follow_up.refresh_from_db()
            self.assertEqual(follow_up.status, status)
            self.assertEqual(FollowUpDecisionChange.objects.first().care, "abierto")
            self.assertIn(follow_up.pk, decision_svc.waiting_on_owner([follow_up]))
            self.post(self.admin, "requerido")
        self.assertFalse(AuditEvent.objects.filter(action="seguimiento", object_id=str(follow_up.pk)).exists())

    def test_marked_for_its_owner_who_reviews_and_closes_it(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        owner = self.as_user(self.coord1)  # Carla attends it: its default owner
        detail = self.url("follow_up_detail", follow_up.pk)
        self.post(self.admin, "no_requerido", reason="Ya lo acompaña su pastor.")
        listed = owner.get(self.url("follow_ups") + "?responsable=mio").content.decode()
        self.assertIn("Decisión cambiada", listed)
        self.assertIn("Su entrevista cambió a «no necesita seguimiento»: Ada Admin,", listed)
        # The reason, free text about a campero, is read on the task and the record, never in a list (a decision under
        # the owner's delegation, like the reason to continue it)
        self.assertNotIn("Ya lo acompaña su pastor.", listed)
        self.assertIn(f'href="{detail}?siguiente=', listed)
        self.assertIn('#decision-cambiada">Revisar<span class="visually-hidden"> el seguimiento de Mariana López Ruiz</span></a>', listed)
        page = owner.get(detail).content.decode()
        self.assertIn('<div class="notice notice--warning" id="decision-cambiada">', page)
        self.assertIn("Su entrevista ya no necesita seguimiento", page)
        self.assertIn("Ada Admin cambió la decisión el", page)
        self.assertIn("Motivo: Ya lo acompaña su pastor.", page)
        self.assertIn("Revísalo. Si ya no hace falta, ciérralo: en «Estado» elige «Atendido» o «Cancelado» y guarda. Si todavía hace falta, continúalo.", page)
        self.assertIn(f'href="{self.url("follow_up_continue", follow_up.pk)}">Continuar seguimiento</a>', page)
        # Closed with its own form, audited as every update of a follow-up.
        self.close(follow_up, "atendido")
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, "atendido")
        self.assertEqual(
            list(AuditEvent.objects.filter(action="seguimiento", object_id=str(follow_up.pk)).values_list("actor", "metadata")),
            [(self.coord1.pk, {"status": "atendido"})],
        )
        self.assertNotIn("Decisión cambiada", owner.get(self.url("follow_ups") + "?estado=todos").content.decode())
        self.assertNotIn("decision-cambiada", owner.get(detail).content.decode())

    def test_somebody_else_sees_the_mark_without_the_next_action(self):
        self.post(self.coord1, "requerido")
        self.post(self.coord1, "no_requerido")
        listed = self.as_user(self.tutor1).get(self.url("follow_ups")).content.decode()
        self.assertIn("Decisión cambiada", listed)
        self.assertNotIn(">Revisar<", listed)
        from django.template.loader import render_to_string

        follow_up = self.ordinary().get()
        html = render_to_string(
            "events/supervision/_decision_change_note.html",
            {"decision_note": decision_svc.note_for(follow_up), "can_manage": False, "event": self.event, "follow_up": follow_up},
        )
        self.assertIn("Carla Uno cambió la decisión el", html)
        self.assertNotIn("elige «Atendido»", html)
        self.assertNotIn("Continuar seguimiento", html)

    def test_back_to_required_clears_the_mark(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        self.post(self.coord1, "no_requerido")
        self.assertIn(follow_up.pk, decision_svc.waiting_on_owner([follow_up]))
        self.post(self.coord1, "requerido")
        follow_up.refresh_from_db()
        self.assertEqual(decision_svc.waiting_on_owner([follow_up]), {})
        self.assertNotIn("Decisión cambiada", self.as_user(self.coord1).get(self.url("follow_ups")).content.decode())

    def test_needed_again_after_atendido_reopens_the_same_task_and_keeps_its_closing(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        self.close(follow_up, "atendido")
        FollowUp.objects.filter(pk=follow_up.pk).update(resolution_note="Nota sintética del equipo de cuidado.")
        follow_up.refresh_from_db()
        closed_at = follow_up.resolved_at
        self.post(self.coord1, "no_requerido")  # closed: it stays as it is
        self.assertEqual(FollowUpDecisionChange.objects.first().care, "sin_cambio")
        client = self.as_user(self.admin)
        page = client.get(self.page).content.decode()
        self.assertIn("Su seguimiento está atendido: si la cambias, se reabrirá y volverá a la lista de quien lo atiende.", page)
        client.post(self.page, {"decision": "requerido", "reason": "Volvió a pedir hablar con un líder."})
        self.assertEqual(
            [m.message for m in client.get(self.record).context["messages"]],
            ["Cambiaste la decisión de Mariana López Ruiz: ahora necesita seguimiento. Se reabrió su seguimiento."],
        )
        follow_up.refresh_from_db()
        self.assertEqual(self.ordinary().count(), 1)  # the same task, never a second one
        self.assertEqual(
            (follow_up.status, follow_up.assigned_to, follow_up.resolved_at, follow_up.resolution_note),
            ("pendiente", self.coord1, None, "Nota sintética del equipo de cuidado."),  # its owner's queue; the note stays
        )
        self.assertEqual(FollowUpDecisionChange.objects.first().care, "reabierto")
        reopened = AuditEvent.objects.filter(action="seguimiento", object_id=str(follow_up.pk)).first()
        self.assertEqual(
            (reopened.actor, reopened.metadata),
            (self.admin, {"status": "pendiente", "reabierto": "atendido", "cerrado": closed_at.isoformat()}),
        )
        self.assertIn(
            "Se reabrió su seguimiento y volvió a la lista de quien lo atiende.",
            self.history(self.as_user(self.tutor1).get(self.record).content.decode()),
        )
        owner = self.as_user(self.coord1)
        self.assertIn("Mariana López Ruiz", owner.get(self.url("follow_ups") + "?responsable=mio").content.decode())  # back in the queue
        detail = owner.get(self.url("follow_up_detail", follow_up.pk)).content.decode()
        self.assertIn(f"{decision_svc.reopened_label(FollowUpDecisionChange.objects.first())}: su entrevista volvió a necesitar seguimiento", detail)
        self.assertIn("Motivo: Volvió a pedir hablar con un líder.", detail)
        history = detail.split('aria-labelledby="historial-seguimiento"')[1].split("</section>")[0]
        titles = [chunk.split("</p>")[0] for chunk in history.split('<p class="row__title">')[1:]]
        self.assertEqual(titles, ["Registrado", "Atendido", "Reabierto: su entrevista volvió a necesitar seguimiento (estaba atendido)"])
        self.assertIn("Carla Uno", history.split("Atendido")[1])  # who closed it
        self.assertNotIn("Nota sintética", detail)  # Carla has no confidential access: the note stays confidential

    def test_needed_again_after_its_owner_cancelled_it_reopens_it(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        self.post(self.coord1, "no_requerido")
        self.close(follow_up, "cancelado")
        self.post(self.coord1, "requerido")
        follow_up.refresh_from_db()
        self.assertEqual(list(self.ordinary().values_list("pk", "status")), [(follow_up.pk, "pendiente")])
        self.assertEqual(FollowUpDecisionChange.objects.first().care, "reabierto")
        self.assertEqual(
            [h["label"] for h in decision_svc.task_history(follow_up)],
            ["Registrado", "Cancelado", "Reabierto: su entrevista volvió a necesitar seguimiento (estaba cancelado)"],
        )

    def test_a_closing_nobody_audited_is_still_in_the_history(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        closed = timezone.now()
        FollowUp.objects.filter(pk=follow_up.pk).update(status="atendido", resolved_at=closed)  # as the demo seed leaves it
        self.post(self.coord1, "no_requerido")
        self.post(self.coord1, "requerido")
        follow_up.refresh_from_db()
        history = decision_svc.task_history(follow_up)
        self.assertEqual([h["label"] for h in history][:2], ["Registrado", "Atendido"])
        self.assertEqual((history[1]["when"], history[1]["who"]), (closed, ""))

    def test_a_task_never_reopened_shows_no_history(self):
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        self.close(follow_up, "atendido")
        self.assertEqual(decision_svc.task_history(follow_up), [])
        self.assertNotIn("historial-seguimiento", self.as_user(self.coord1).get(self.url("follow_up_detail", follow_up.pk)).content.decode())

    def test_what_the_change_page_says_it_will_do(self):
        client = self.as_user(self.coord1)
        self.assertContains(client.get(self.page), "Si la cambias, se creará su seguimiento.")
        self.post(self.coord1, "requerido")
        self.assertContains(client.get(self.page), "Su seguimiento sigue abierto: si la cambias, quien lo atiende lo revisará y lo cerrará.")
        self.close(self.ordinary().get(), "atendido")
        self.assertContains(client.get(self.page), "Su seguimiento ya está cerrado y seguirá como está.")

    def test_a_cancelled_one_is_not_reopened_beside_another(self):
        # Data from before tasks were reopened: a cancelled one and a newer one of the same interview.
        self.post(self.coord1, "requerido")
        first = self.ordinary().get()
        FollowUp.objects.filter(pk=first.pk).update(status="cancelado")
        follow_ups.create(event=self.event, participant=self.p1, interview=self.done, kind=CARE, created_by=self.coord1)
        response = self.close(first, "pendiente")
        self.assertContains(response, "Su entrevista ya tiene otro seguimiento. Continúa en ese.")
        first.refresh_from_db()
        self.assertEqual(first.status, "cancelado")
        self.assertEqual(self.ordinary().exclude(status="cancelado").count(), 1)


class ConfidentialNoteTests(ChangeFixture):
    """Owner decision 2: whether a confidential note exists has no effect on what other roles see."""

    def pages(self, follow_up):
        """What the people without confidential access see of this campero's task, its interview and their lists."""
        views = [
            (self.coord1, self.url("follow_up_detail", follow_up.pk)), (self.coord1, self.url("follow_ups")),
            (self.coord1, self.url("coord_home")), (self.coord1, self.record), (self.coord1, self.url("participant_detail", self.p1.public_id)),
            (self.tutor1, self.url("follow_up_detail", follow_up.pk)), (self.tutor1, self.url("tutor_home")), (self.tutor1, self.url("pending")),
            (self.pastor, self.url("follow_ups")), (self.admin, self.url("follow_up_detail", follow_up.pk)), (self.admin, self.page),
        ]
        rendered = []
        for user, url in views:
            response = self.viewers[user].get(url)
            self.assertEqual(response.status_code, 200, url)
            rendered.append(masked(response))
        return rendered

    def note(self, follow_up, text):
        """The care team writes (or clears) the closing note, leaving the status and the owner as they are."""
        follow_up.refresh_from_db()
        self.as_user(self.care).post(
            self.url("follow_up_detail", follow_up.pk),
            {"status": follow_up.status, "assigned_to": follow_up.assigned_to_id, "resolution_note": text},
        )
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.resolution_note, text)

    def test_a_note_never_changes_the_change_nor_what_others_see(self):
        self.viewers = {user: self.as_user(user) for user in (self.coord1, self.tutor1, self.pastor, self.admin)}
        self.post(self.coord1, "requerido")
        follow_up = self.ordinary().get()
        self.note(follow_up, "Nota sintética: hablamos el viernes.")
        self.post(self.coord1, "no_requerido")  # with a note: the same as without one
        self.assertEqual(FollowUpDecisionChange.objects.first().care, "abierto")
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, "pendiente")
        with_note = self.pages(follow_up)
        self.note(follow_up, "")
        self.assertEqual(self.pages(follow_up), with_note)
        # Reopened: the same, with and without a note
        self.close_as_owner(follow_up)
        self.post(self.coord1, "requerido")
        without = self.pages(follow_up)
        self.note(follow_up, "Otra nota sintética.")
        self.assertEqual(self.pages(follow_up), without)
        self.assertFalse(any("nota sintética" in page.lower() for page in without + self.pages(follow_up)))

    def close_as_owner(self, follow_up):
        self.as_user(self.coord1).post(self.url("follow_up_detail", follow_up.pk), {"status": "atendido", "assigned_to": self.coord1.pk})


class ListsMarkTests(ChangeFixture):
    """Owner decision 1: «Decisión cambiada» on every list of follow-ups a role already sees, both modes; «Revisar»
    for the task's owner only."""

    def setUp(self):
        super().setUp()
        self.post(self.coord1, "requerido")
        self.follow_up = self.ordinary().get()  # Carla attends it
        self.post(self.admin, "no_requerido")
        self.detail = self.url("follow_up_detail", self.follow_up.pk)

    def review(self, back):
        return (
            f'<a class="btn btn--secondary btn--small" href="{self.detail}?siguiente={back}#decision-cambiada">Revisar'
            '<span class="visually-hidden"> el seguimiento de Mariana López Ruiz</span></a>'
        )

    def check(self):
        coord = self.as_user(self.coord1)
        tutor = self.as_user(self.tutor1)
        for user, client, url, back in (  # the way back is the list it was opened from
            (self.coord1, coord, self.url("coord_home"), self.url("coord_home")),
            (self.coord1, coord, self.url("participant_detail", self.p1.public_id), self.url("participant_detail", self.p1.public_id)),
            (self.coord1, coord, self.url("follow_ups"), self.url("follow_ups")),
        ):
            html = client.get(url).content.decode()
            self.assertIn("Decisión cambiada", html, url)
            self.assertIn(self.review(back), html, url)  # the owner's next action
        for url in (self.url("tutor_home"), self.url("pending"), self.url("follow_ups"), self.url("participant_detail", self.p1.public_id)):
            html = tutor.get(url).content.decode()
            self.assertIn("Decisión cambiada", html, url)
            self.assertNotIn(">Revisar<", html, url)  # not theirs
        # Given to the tutor, the next action is theirs
        FollowUp.objects.filter(pk=self.follow_up.pk).update(assigned_to=self.tutor1)
        html = tutor.get(self.url("tutor_home")).content.decode()
        self.assertIn(self.review(self.url("tutor_home")), html)
        self.assertNotIn(">Revisar<", coord.get(self.url("coord_home")).content.decode())

    def test_without_the_redesign(self):
        self.check()

    def test_with_the_redesign(self):
        flags.set_enabled(self.church, "community", True)
        self.assertTrue(self.as_user(self.coord1).get(self.url("coord_home")).context["ui_v2"])
        self.check()

    def test_the_owners_review_comes_first_on_a_short_list(self):
        # Six newer open tasks of Carla's camperos: «Tus seguimientos» shows five, the newest; hers to review is first.
        for participant in (self.p2, self.p_minor_no_auth) * 3:
            follow_ups.create(event=self.event, participant=participant, kind=CARE, created_by=self.coord1)
        html = self.as_user(self.coord1).get(self.url("coord_home")).content.decode()
        self.assertIn(">Revisar<", html)
        rows = html.split('id="seguimientos"')[1].split("</ul>")[0]  # «Esperan algo de ti» without the redesign
        self.assertLess(rows.index("Mariana López Ruiz"), rows.index("Diego Hernández Cruz"))
        self.assertEqual(rows.count('<li class="row">'), 5)

    def test_nothing_is_marked_while_the_decision_needs_it(self):
        self.post(self.admin, "requerido")
        for user, url in ((self.coord1, self.url("coord_home")), (self.tutor1, self.url("tutor_home")), (self.tutor1, self.url("pending")),
                          (self.coord1, self.url("follow_ups")), (self.coord1, self.url("participant_detail", self.p1.public_id))):
            self.assertNotIn("Decisión cambiada", self.as_user(user).get(url).content.decode(), url)


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


class ReopenedMarkTests(ChangeFixture):
    """Owner decision of 2026-11-18 (second round): a reopened task is «Pendiente», same owner, its history kept, and
    says «Reabierto el …» on its page and on every list row until its owner moves it on or closes it."""

    def setUp(self):
        super().setUp()
        self.post(self.coord1, "requerido")
        self.follow_up = self.ordinary().get()  # Carla attends it
        self.detail = self.url("follow_up_detail", self.follow_up.pk)
        self.owner = self.as_user(self.coord1)
        self.owner.post(self.detail, {"status": "atendido", "assigned_to": self.coord1.pk})
        self.post(self.coord1, "no_requerido")
        self.post(self.admin, "requerido", reason="Volvió a pedir hablar con un líder.")
        self.label = decision_svc.reopened_label(FollowUpDecisionChange.objects.first())

    def lists(self):
        return [
            (self.owner, self.url("coord_home")), (self.owner, self.url("follow_ups")),
            (self.owner, self.url("participant_detail", self.p1.public_id)),
            (self.as_user(self.tutor1), self.url("tutor_home")), (self.as_user(self.tutor1), self.url("pending")),
        ]

    def check_marked(self):
        self.assertTrue(self.label.startswith("Reabierto el "))
        self.assertNotIn(":", self.label)  # the day, not the time
        for client, url in self.lists():
            self.assertIn(f'<span class="status__mark" aria-hidden="true"></span>{self.label}</span>', client.get(url).content.decode(), url)
        page = self.owner.get(self.detail).content.decode()
        self.assertIn(f"{self.label}: su entrevista volvió a necesitar seguimiento", page)
        self.assertIn("Cuando retomes el trabajo, en «Estado» elige «En proceso» y guarda.", page)

    def test_every_list_and_its_page_say_reabierto_with_the_day_both_modes(self):
        self.follow_up.refresh_from_db()
        self.assertEqual((self.follow_up.status, self.follow_up.assigned_to), ("pendiente", self.coord1))
        self.check_marked()
        flags.set_enabled(self.church, "community", True)
        self.check_marked()

    def test_its_owner_moves_it_to_en_proceso_with_its_own_audited_form(self):
        response = self.owner.post(self.detail, {"status": "en_proceso", "assigned_to": self.coord1.pk})
        self.assertEqual(response.status_code, 302)
        self.follow_up.refresh_from_db()
        self.assertEqual(self.follow_up.status, "en_proceso")
        row = AuditEvent.objects.filter(action="seguimiento", object_id=str(self.follow_up.pk)).first()
        self.assertEqual((row.actor, row.metadata), (self.coord1, {"status": "en_proceso"}))
        for client, url in self.lists():
            self.assertNotIn("Reabierto el", client.get(url).content.decode(), url)
        page = self.owner.get(self.detail).content.decode()
        self.assertNotIn("decision-cambiada", page)
        self.assertIn("Reabierto: su entrevista volvió a necesitar seguimiento (estaba atendido)", page)  # the history stays
        # Back to «Pendiente» by hand: moved on, so not «Reabierto» again
        self.owner.post(self.detail, {"status": "pendiente", "assigned_to": self.coord1.pk})
        self.assertNotIn("Reabierto el", self.owner.get(self.url("follow_ups")).content.decode())

    def test_closing_it_ends_the_mark(self):
        self.owner.post(self.detail, {"status": "atendido", "assigned_to": self.coord1.pk})
        self.assertNotIn("Reabierto el", self.owner.get(self.url("follow_ups") + "?estado=todos").content.decode())

    def test_a_note_or_a_new_owner_is_not_moving_it_on(self):
        self.as_user(self.care).post(self.detail, {"status": "pendiente", "assigned_to": self.tutor1.pk, "resolution_note": "Nota sintética."})
        self.assertIn(self.label, self.as_user(self.tutor1).get(self.url("tutor_home")).content.decode())


class ContinueTests(ChangeFixture):
    """Owner decision of 2026-11-18 (second round): «Continuar seguimiento» keeps a task open that the interview's decision
    («no necesita seguimiento») left for review, with a brief reason; the interview's decision and record do not change."""

    def setUp(self):
        super().setUp()
        self.post(self.coord1, "requerido")
        self.follow_up = self.ordinary().get()  # Carla attends it
        self.post(self.admin, "no_requerido")
        self.continue_url = self.url("follow_up_continue", self.follow_up.pk)
        self.detail = self.url("follow_up_detail", self.follow_up.pk)

    def go_on(self, user, reason="Pidió seguir viéndonos los domingos.", client=None):
        return (client or self.as_user(user)).post(self.continue_url, {"reason": reason})

    def change(self):
        return FollowUpDecisionChange.objects.first()

    def test_whoever_may_manage_the_task_may(self):
        # Its owner, and whoever may already manage it (can_manage_follow_up): an administrador, a pastor, the campero's
        # coordinador's tutor and the care team (confidential access) may too.
        for user in (self.coord1, self.admin, self.pastor, self.tutor1, self.care):
            with self.subTest(user=user.email):
                client = self.as_user(user)
                self.assertEqual(client.get(self.continue_url).status_code, 200)
                self.assertRedirects(self.go_on(user, client=client), self.detail, fetch_redirect_response=False)
                change = self.change()
                self.assertEqual(change.continued_by, user)
                FollowUpDecisionChange.objects.filter(pk=change.pk).update(continued_by=None, continued_at=None, continued_reason="")

    def test_everybody_else_is_refused(self):
        for user, status in ((self.coord2, 404), (self.tutor2, 404), (self.outsider, 403), (self.other_coord, 403)):
            client = self.as_user(user)
            self.assertEqual(client.get(self.continue_url).status_code, status, user)
            self.assertEqual(self.go_on(user, client=client).status_code, status, user)
        anonymous = Client()
        self.assertTrue(anonymous.post(self.continue_url, {"reason": "x"})["Location"].startswith(reverse("login")))
        self.assertEqual(self.as_user(self.coord1).put(self.continue_url).status_code, 405)
        self.assertIsNone(self.change().continued_at)
        with self.assertRaises(svc.NotAllowed):
            decision_svc.continue_follow_up(self.access(self.coord2), self.follow_up, reason="x")

    def test_a_reason_is_required_and_short(self):
        for reason, error in (("", "Escribe brevemente por qué continúas el seguimiento."), ("a" * 201, "Escribe el motivo en 200 caracteres o menos.")):
            response = self.go_on(self.coord1, reason=reason)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, error)
        self.assertIsNone(self.change().continued_at)
        page = self.as_user(self.coord1).get(self.continue_url).content.decode()
        self.assertIn('<label class="label" for="id_reason">¿Por qué continúas el seguimiento?</label>', page)
        self.assertIn('maxlength="200"', page)

    def test_the_mark_goes_the_task_stays_open_and_the_decision_stays(self):
        client = self.as_user(self.coord1)
        self.go_on(self.coord1, client=client)
        self.assertEqual([m.message for m in client.get(self.detail).context["messages"]], ["El seguimiento de Mariana López Ruiz sigue abierto."])
        self.follow_up.refresh_from_db()
        self.assertEqual((self.follow_up.status, self.follow_up.assigned_to), ("pendiente", self.coord1))
        self.assertEqual(decision_svc.waiting_on_owner([self.follow_up]), {})
        for url in (self.url("coord_home"), self.url("follow_ups") + "?responsable=mio", self.url("participant_detail", self.p1.public_id)):
            html = client.get(url).content.decode()
            self.assertNotIn("Decisión cambiada", html, url)
            self.assertIn("Mariana López Ruiz", html, url)  # still in her queue
        self.assertEqual(self.decision(), "no_requerido")
        self.assertIn("No se requiere seguimiento", self.section(client.get(self.record).content.decode()))
        self.assertEqual(self.change().care, "abierto")  # the record's history is as it was

    def test_audited_who_and_when_never_the_reason(self):
        self.go_on(self.coord1, reason="Pidió seguir viéndonos los domingos.")
        row = AuditEvent.objects.get(action="seguimiento_continuado")
        self.assertEqual((row.actor, row.object_type, row.object_id, row.metadata), (self.coord1, "FollowUp", str(self.follow_up.pk), {"status": "pendiente"}))
        for logged in AuditEvent.objects.all():
            self.assertNotIn("domingos", logged.summary + str(logged.metadata))
        self.assertIn("Continuó un seguimiento", self.as_user(self.admin).get(self.url("audit")).content.decode())

    def test_the_reason_is_read_on_the_task_by_whoever_may_manage_it_and_nowhere_else(self):
        self.go_on(self.coord1, reason="Pidió seguir viéndonos los domingos.")
        for user in (self.coord1, self.tutor1, self.admin):
            page = self.as_user(user).get(self.detail).content.decode()
            self.assertIn("Sigue abierto aunque su entrevista ya no lo pide", page)
            self.assertIn("Carla Uno decidió continuarlo el", page)
            self.assertIn("Motivo: Pidió seguir viéndonos los domingos.", page)
        for user, url in ((self.tutor1, self.url("follow_ups")), (self.tutor1, self.url("tutor_home")), (self.coord1, self.url("coord_home")),
                          (self.tutor1, self.record), (self.pastor, self.url("follow_ups"))):
            self.assertNotIn("domingos", self.as_user(user).get(url).content.decode(), url)
        # Whoever may see the task but not manage it reads that it continues, not why. (Every role that sees an ordinary
        # task may manage it today, so this is the template given such a viewer.)
        from django.template.loader import render_to_string

        html = render_to_string(
            "events/supervision/_decision_change_note.html",
            {"decision_note": decision_svc.note_for(self.follow_up), "can_manage": False, "event": self.event, "follow_up": self.follow_up},
        )
        self.assertIn("Carla Uno decidió continuarlo el", html)
        self.assertNotIn("domingos", html)

    def test_nothing_to_review_nothing_to_continue(self):
        self.go_on(self.coord1)
        client = self.as_user(self.coord1)
        self.assertRedirects(client.get(self.continue_url), self.detail, fetch_redirect_response=False)
        self.go_on(self.coord1, reason="Otra vez.", client=client)
        self.assertEqual(AuditEvent.objects.filter(action="seguimiento_continuado").count(), 1)
        self.assertNotIn("Otra vez", self.change().continued_reason)

    def test_a_new_change_to_no_asks_for_a_fresh_review(self):
        self.go_on(self.coord1)
        self.post(self.admin, "requerido")
        self.post(self.admin, "no_requerido")
        self.follow_up.refresh_from_db()
        self.assertIn(self.follow_up.pk, decision_svc.waiting_on_owner([self.follow_up]))

    def test_the_page_in_both_modes(self):
        page = self.as_user(self.coord1).get(self.continue_url).content.decode()
        self.assertIn('<div class="step__actions">', page)
        self.assertIn("El seguimiento sigue abierto con quien lo atiende y la entrevista no cambia.", page)
        flags.set_enabled(self.church, "community", True)
        response = self.as_user(self.coord1).get(self.continue_url)
        self.assertTrue(response.context["ui_v2"])
        self.assertIn('<div class="form-actions">', response.content.decode())

    def test_a_confidential_note_changes_neither_the_flow_nor_what_others_see(self):
        self.as_user(self.care).post(
            self.detail, {"status": "pendiente", "assigned_to": self.coord1.pk, "resolution_note": "Nota sintética del equipo de cuidado."}
        )
        viewers = {user: self.as_user(user) for user in (self.coord1, self.tutor1, self.pastor, self.admin)}
        urls = [self.detail, self.url("follow_ups"), self.continue_url]

        def pages():
            return [masked(client.get(url)) for client in viewers.values() for url in urls]

        marked = pages()
        self.assertTrue(all("Nota sintética" not in page for page in marked))
        self.as_user(self.care).post(self.detail, {"status": "pendiente", "assigned_to": self.coord1.pk, "resolution_note": ""})
        self.assertEqual(pages(), marked)
        self.go_on(self.coord1)
        continued = pages()
        self.as_user(self.care).post(self.detail, {"status": "pendiente", "assigned_to": self.coord1.pk, "resolution_note": "Otra nota."})
        self.assertEqual(pages(), continued)
