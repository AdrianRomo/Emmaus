"""Every follow-up has an owner (issue #46).

A new follow-up goes to the campero's coordinador, or to that coordinador's tutor when the coordinador cannot attend
it, and stays unassigned when neither can: an owner is only ever somebody who could already open it. The tutor's and the
pastor's homes say how many nobody attends, the list assigns several at once with one audit row each, the reminder
e-mail carries no name, reason or answer, and a finished event leaves its follow-ups workable.
"""

from __future__ import annotations

from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from django.urls import reverse

from events.access import resolve_access
from events.models import AuditEvent, Event, EventMembership, FollowUp, Interview, TutorAssignment
from events.services import follow_ups as follow_up_svc
from events.services import interviews as interview_svc

from .base import PASSWORD, CampFixture
from .test_ux_seguimiento import FollowUpBase

CARE = FollowUp.Kind.CARE
SAFETY = FollowUp.Kind.SAFETY
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
BASE_URL = "https://emmaus.example.org"
UNASSIGNED_QUERY = "?estado=abiertos&amp;responsable=ninguno"


class DefaultOwnerTests(CampFixture):
    def new(self, participant, **fields):
        return follow_up_svc.create(event=self.event, participant=participant, **fields)

    def test_a_follow_up_decided_at_the_end_of_an_interview_goes_to_the_camperos_coordinador(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        self.answer(interview, "salvo", "si")
        interview.refresh_from_db()
        self.client.post(self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "requerido"})
        follow_up = FollowUp.objects.get(interview=interview)
        self.assertEqual(follow_up.assigned_to, self.coord1)

    def test_a_follow_up_decided_later_goes_to_the_camperos_coordinador(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        self.answer(interview, "salvo", "si")
        interview.refresh_from_db()
        self.client.post(self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "por_evaluar"})
        self.assertFalse(FollowUp.objects.filter(interview=interview).exists())
        self.client.post(self.url("interview_detail", interview.public_id), {"action": "decision", "decision": "requerido"})
        self.assertEqual(FollowUp.objects.get(interview=interview).assigned_to, self.coord1)

    def test_the_owner_is_the_camperos_coordinador_whoever_creates_it(self):
        # An administrator finishes the interview of a campero of coord3: the follow-up is coord3's.
        self.assertEqual(self.new(self.p4, created_by=self.admin).assigned_to, self.coord3)

    def test_a_coordinador_who_left_the_team_passes_it_to_their_tutor(self):
        EventMembership.objects.filter(pk=self.m_coord1.pk).update(is_active=False)
        self.assertEqual(self.new(self.p1).assigned_to, self.tutor1)

    def test_a_coordinador_whose_account_is_off_passes_it_to_their_tutor(self):
        self.coord1.is_active = False
        self.coord1.save()
        self.assertEqual(self.new(self.p1).assigned_to, self.tutor1)

    def test_without_coordinador_or_with_nobody_able_it_stays_unassigned(self):
        self.p2.coordinator = None
        self.p2.save()
        self.assertIsNone(self.new(self.p2).assigned_to)
        EventMembership.objects.filter(pk__in=[self.m_coord1.pk, self.m_tutor1.pk]).update(is_active=False)
        self.assertIsNone(self.new(self.p1).assigned_to)
        TutorAssignment.objects.filter(coordinator=self.m_coord3).delete()
        EventMembership.objects.filter(pk=self.m_coord3.pk).update(is_active=False)
        self.assertIsNone(self.new(self.p4).assigned_to)

    def test_an_owner_chosen_by_the_caller_is_kept(self):
        self.assertEqual(self.new(self.p1, assigned_to=self.pastor).assigned_to, self.pastor)

    # ---- confidential follow-ups and safety reports keep their rules --------------------------------

    def test_a_confidential_follow_up_never_goes_to_somebody_who_may_not_see_it(self):
        follow_up = self.new(self.p1, confidential=True, reason=FollowUp.Reason.SUPPORT_REQUESTED)
        self.assertIsNone(follow_up.assigned_to)  # neither the coordinador nor the tutor holds confidential access
        for user in (self.coord1, self.tutor1, self.pastor, self.admin):
            self.assertFalse(resolve_access(user, self.event).can_view_follow_up(follow_up), user.email)
        self.assertTrue(resolve_access(self.care, self.event).can_view_follow_up(follow_up))

    def test_a_confidential_follow_up_goes_up_to_the_tutor_who_may_see_it(self):
        EventMembership.objects.filter(pk=self.m_tutor1.pk).update(can_view_sensitive=True)
        follow_up = self.new(self.p1, confidential=True)
        self.assertEqual(follow_up.assigned_to, self.tutor1)
        self.assertFalse(resolve_access(self.coord1, self.event).can_view_follow_up(follow_up))

    def test_a_confidential_follow_up_goes_to_the_coordinador_who_may_see_it(self):
        EventMembership.objects.filter(pk=self.m_coord1.pk).update(can_view_sensitive=True)
        self.assertEqual(self.new(self.p1, confidential=True).assigned_to, self.coord1)

    def test_the_confidential_referral_of_a_real_interview_stays_unassigned_and_hidden(self):
        self.login(self.coord1)
        self.choose_station(number=5)
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.answer(interview, "apoyo", ["familia"], url_name="kiosk")
        self.client.post(reverse("kiosk:finish"))
        self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        interview.refresh_from_db()
        self.client.post(self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "requerido"})
        referral = FollowUp.objects.get(interview=interview, confidential=True)
        self.assertIsNone(referral.assigned_to)
        self.assertEqual(FollowUp.objects.get(interview=interview, confidential=False).assigned_to, self.coord1)
        self.assertEqual(self.client.get(self.url("follow_up_detail", referral.id)).status_code, 404)

    def test_a_safety_report_is_not_given_to_the_coordinador_who_reported_it(self):
        report = interview_svc.report_safety_concern(resolve_access(self.coord1, self.event), self.p1, details="Algo que vio el coordinador.")
        self.assertIsNone(report.follow_up.assigned_to)  # they see that it exists, they may not attend it
        self.assertFalse(resolve_access(self.coord1, self.event).can_manage_follow_up(report.follow_up))


class UnassignedOnTheHomesTests(FollowUpBase):
    def setUp(self):
        self.unassigned = [self.follow_up(self.p1), self.follow_up(self.p3)]  # the team of tutor1
        self.follow_up(self.p4)  # the team of tutor2
        self.follow_up(self.p2, assigned_to=self.coord1)
        self.follow_up(self.p1, status=FollowUp.Status.DONE)  # closed: nobody has to attend it

    def home(self, user, name):
        response = self.login(user, Client()).get(self.url(name))
        self.assertEqual(response.status_code, 200)
        return response

    def test_the_tutors_home_counts_the_unassigned_of_their_team_and_links_the_filter(self):
        response = self.home(self.tutor1, "tutor_home")
        self.assertEqual(response.context["follow_ups_unassigned"], 2)
        self.assertContains(response, f'<a href="{self.url("follow_ups")}{UNASSIGNED_QUERY}">2 sin responsable: asígnalos</a>', html=True)
        self.assertEqual(self.home(self.tutor2, "tutor_home").context["follow_ups_unassigned"], 1)
        listed = self.listed(self.login(self.tutor1, Client()), "?estado=abiertos&responsable=ninguno")
        self.assertCountEqual(self.ids(listed), [f.id for f in self.unassigned])  # the link shows what the home counted

    def test_the_tutors_home_says_so_when_every_follow_up_has_an_owner(self):
        FollowUp.objects.filter(assigned_to__isnull=True).update(assigned_to=self.tutor1)
        response = self.home(self.tutor1, "tutor_home")
        self.assertContains(response, "Todos tienen responsable.")
        self.assertNotContains(response, "sin responsable")

    def test_the_live_fragment_of_the_tutors_home_carries_the_count(self):
        response = self.login(self.tutor1, Client()).get(self.url("tutor_home"), headers={"X-Requested-With": "fetch"})
        self.assertContains(response, "2 sin responsable")

    def test_the_pastors_home_counts_the_unassigned_of_the_event_and_links_the_filter(self):
        for user in (self.pastor, self.admin):
            response = self.home(user, "pastor_home")
            self.assertEqual(response.context["follow_ups_unassigned"], 3, user.email)
            self.assertContains(response, '<dd class="figures__value" data-count="sin-responsable">3</dd>', html=True)
            self.assertContains(response, f'href="{self.url("follow_ups")}{UNASSIGNED_QUERY}"')

    def test_the_count_never_includes_what_the_reader_may_not_see(self):
        self.follow_up(self.p1, confidential=True)
        self.follow_up(self.p1, kind=SAFETY)
        self.assertEqual(self.home(self.pastor, "pastor_home").context["follow_ups_unassigned"], 3)
        self.assertEqual(self.home(self.tutor1, "tutor_home").context["follow_ups_unassigned"], 2)
        self.assertEqual(self.home(self.care, "pastor_home").context["follow_ups_unassigned"], 5)


class BulkAssignTests(FollowUpBase):
    def setUp(self):
        self.a = self.follow_up(self.p1)
        self.b = self.follow_up(self.p3)
        self.c = self.follow_up(self.p4)
        self.closed = self.follow_up(self.p2, status=FollowUp.Status.DONE)
        self.client = self.login(self.admin, Client())

    def bulk(self, client, items, assigned_to, **extra):
        return client.post(self.url("follow_ups_bulk_assign"), {"seguimiento": [f.id for f in items], "assigned_to": assigned_to, **extra})

    def owner(self, item):
        item.refresh_from_db()
        return item.assigned_to

    def test_the_list_offers_one_form_and_a_labelled_checkbox_per_assignable_row(self):
        html = self.listed(self.client, "?estado=todos").content.decode()
        self.assertIn(f'<form method="post" action="{self.url("follow_ups_bulk_assign")}" id="asignar-varios"', html)
        self.assertIn('<label class="hint" for="asignar-varios-a">Asignar los marcados a</label>', html)
        self.assertEqual(html.count('form="asignar-varios" name="seguimiento"'), 3)  # the closed row has none
        self.assertIn(f'<label class="check" for="marcar-{self.a.id}">', html)
        self.assertIn(f"Marcar el seguimiento de {self.p1.full_name}", html)
        self.assertNotIn(f'id="marcar-{self.closed.id}"', html)

    def test_someone_who_may_assign_nothing_gets_no_bulk_form(self):
        self.follow_up(self.p3, kind=SAFETY, created_by=self.coord2)
        FollowUp.objects.filter(pk__in=[self.a.pk, self.b.pk, self.c.pk]).update(status=FollowUp.Status.DONE)
        html = self.listed(self.client, "?estado=todos").content.decode()
        self.assertIn("asignar-varios", html)  # the administrator may attend the safety report
        html = self.listed(self.login(self.coord2, Client()), "?estado=todos").content.decode()
        self.assertNotIn("asignar-varios", html)

    def test_several_rows_are_assigned_and_each_one_is_audited(self):
        response = self.bulk(self.client, [self.a, self.b, self.c], self.tutor1.id)
        self.assertRedirects(response, self.url("follow_ups"), fetch_redirect_response=False)
        # Teresa supervises the coordinadores of p1 and p3, not p4's: that one is not hers to open, so it stays as it was
        self.assertEqual(self.flashed(response), ["Se asignaron 2 seguimientos. 1 se quedó como estaba."])
        self.assertIsNone(self.owner(self.c))
        self.assertFalse(self.audit_rows(self.c).exists())
        for item in (self.a, self.b):
            self.assertEqual(self.owner(item), self.tutor1)
            row = self.audit_rows(item).get()  # one row per follow-up
            self.assertEqual((row.actor, row.object_type, row.object_id), (self.admin, "FollowUp", str(item.id)))

    def test_the_way_back_is_kept_and_only_inside_the_site(self):
        back = self.url("follow_ups") + "?estado=abiertos&responsable=ninguno"
        self.assertRedirects(self.bulk(self.client, [self.a], self.tutor1.id, siguiente=back), back, fetch_redirect_response=False)
        response = self.bulk(self.client, [self.b], self.tutor1.id, siguiente="https://evil.example/")
        self.assertRedirects(response, self.url("follow_ups"), fetch_redirect_response=False)

    def test_su_coordinador_o_su_tutor_gives_each_row_its_default_owner(self):
        EventMembership.objects.filter(pk=self.m_coord2.pk).update(is_active=False)
        response = self.bulk(self.client, [self.a, self.b, self.c], "predeterminado")
        self.assertEqual(self.flashed(response), ["Se asignaron 3 seguimientos."])
        self.assertEqual([self.owner(self.a), self.owner(self.b), self.owner(self.c)], [self.coord1, self.tutor1, self.coord3])

    def test_rows_that_cannot_change_are_left_as_they_were_and_counted(self):
        FollowUp.objects.filter(pk=self.b.pk).update(assigned_to=self.tutor1)
        response = self.bulk(self.client, [self.a, self.b, self.closed], self.tutor1.id)
        self.assertEqual(self.flashed(response), ["Se asignó 1 seguimiento. 2 se quedaron como estaban."])
        self.assertIsNone(self.owner(self.closed))
        self.assertEqual(self.audit_rows(self.b).count(), 0)
        self.assertEqual(self.audit_rows(self.closed).count(), 0)
        # A coordinador is offered only for their own camperos: coord1 is refused on the row of coord3's campero.
        response = self.bulk(self.client, [self.c], self.coord1.id)
        self.assertEqual(self.flashed(response)[-1], "No se asignó ninguno: ya tenían a esa persona, ya están cerrados o esa persona no puede atenderlos.")
        self.assertIsNone(self.owner(self.c))

    def test_nothing_marked_or_nobody_chosen_changes_nothing(self):
        self.assertEqual(self.flashed(self.bulk(self.client, [], self.tutor1.id)), ["Marca al menos un seguimiento."])
        self.assertEqual(self.flashed(self.bulk(self.client, [self.a], ""))[-1], "Elige a una persona del equipo.")
        self.assertEqual(self.flashed(self.bulk(self.client, [self.a], self.outsider.id))[-1][:21], "No se asignó ninguno:")
        self.assertIsNone(self.owner(self.a))
        self.assertFalse(AuditEvent.objects.filter(action="seguimiento").exists())

    def test_a_guessed_id_refuses_the_whole_request(self):
        other_team = self.c  # a campero of tutor2's team
        confidential = self.follow_up(self.p1, confidential=True)
        other_event = FollowUp.objects.create(event=self.other_event, participant=self.p_other_event)
        for stranger in (other_team, confidential, other_event):
            response = self.bulk(self.login(self.tutor1, Client()), [self.a, stranger], self.tutor1.id)
            self.assertEqual(response.status_code, 403, stranger.id)
        self.assertIsNone(self.owner(self.a))  # the row they may assign was not written either
        self.assertFalse(AuditEvent.objects.filter(action="seguimiento").exists())
        response = self.login(self.tutor1, Client()).post(self.url("follow_ups_bulk_assign"), {"seguimiento": ["1 OR 1=1"], "assigned_to": self.tutor1.id})
        self.assertEqual(response.status_code, 403)

    def test_a_coordinador_cannot_assign_a_safety_report_they_only_reported(self):
        report = self.follow_up(self.p1, kind=SAFETY, created_by=self.coord1)
        response = self.bulk(self.login(self.coord1, Client()), [report], "predeterminado")
        self.assertEqual(response.status_code, 403)

    def test_it_needs_a_session_a_post_and_a_role_in_the_event(self):
        url = self.url("follow_ups_bulk_assign")
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client().post(url, {"seguimiento": [self.a.id]}).status_code, 302)  # to the login
        self.assertEqual(self.login(self.outsider, Client()).post(url, {"seguimiento": [self.a.id]}).status_code, 403)


@override_settings(EMAIL_BACKEND=LOCMEM)
class ReminderTests(FollowUpBase):
    def setUp(self):
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.CLOSED)
        self.event.refresh_from_db()
        self.follow_up(self.p1, assigned_to=self.coord1, reason=FollowUp.Reason.SPIRITUAL_DECISION, details="Habló de su familia.")
        self.follow_up(self.p2, assigned_to=self.coord1, status=FollowUp.Status.IN_PROGRESS)
        self.follow_up(self.p4, assigned_to=self.tutor2, reason=FollowUp.Reason.TALK_TO_LEADER)
        self.follow_up(self.p3, assigned_to=self.coord2, status=FollowUp.Status.DONE)  # closed: no reminder
        self.follow_up(self.p3)  # nobody attends it: nobody to remind

    def run_command(self, *args):
        out = StringIO()
        call_command("remind_follow_up_owners", *args, stdout=out)
        return out.getvalue()

    def test_without_apply_it_sends_nothing_and_says_how_many_would_go(self):
        out = self.run_command()
        self.assertEqual(mail.outbox, [])
        self.assertIn("[simulación] Campamento A: saldrían 2 correos por 3 seguimientos abiertos.", out)
        self.assertFalse(AuditEvent.objects.filter(action="recordatorio_seguimiento").exists())
        for private in ("Mariana", "Carla", "coord1@prueba.mx"):
            self.assertNotIn(private, out)

    def test_apply_sends_one_mail_to_each_owner_with_no_name_reason_or_answer(self):
        out = self.run_command("--apply", "--base-url", BASE_URL)
        self.assertIn("Campamento A: se enviaron 2 correos.", out)
        self.assertCountEqual([message.to for message in mail.outbox], [[self.coord1.email], [self.tutor2.email]])
        link = f"{BASE_URL}{self.url('follow_ups')}?estado=abiertos&responsable=mio"
        expected = (
            "Hola:\n\n"
            "Tienes seguimientos abiertos a tu cargo en Iglesia de prueba. Entra a Emmaus para ver cuáles son y atenderlos:\n\n"
            f"{link}\n\n"
            "Por privacidad, este correo no incluye nombres, motivos ni respuestas."
        )
        private = [p.full_name for p in (self.p1, self.p2, self.p3, self.p4)]
        private += [word for name in private for word in name.split()]
        private += [str(label) for _, label in FollowUp.Reason.choices] + ["Habló de su familia", "Carla", "Tomás", "Campamento A"]
        for message in mail.outbox:
            self.assertEqual(message.body, expected)  # the whole body: nothing of the camperos or the follow-ups
            self.assertEqual(message.subject, "Emmaus: tienes seguimientos por atender")
            for text in private:
                self.assertNotIn(text, message.subject + message.body, text)
            self.assertEqual((message.cc, message.bcc), ([], []))
        row = AuditEvent.objects.get(action="recordatorio_seguimiento")
        self.assertEqual((row.event, row.actor_label, row.metadata), (self.event, "Sistema", {"sent": 2}))

    def test_the_link_opens_the_owners_own_open_follow_ups(self):
        response = self.login(self.coord1, Client()).get(self.url("follow_ups") + "?estado=abiertos&responsable=mio")
        self.assertEqual(len(self.ids(response)), 2)

    def test_only_somebody_who_can_still_open_them_is_reminded(self):
        EventMembership.objects.filter(pk=self.m_coord1.pk).update(is_active=False)  # left the team
        self.follow_up(self.p3, assigned_to=self.tutor2)  # a campero of another team: tutor2 cannot open it
        self.follow_up(self.p1, assigned_to=self.pastor, confidential=True)  # no confidential access: cannot open it
        self.assertEqual([(user, count) for user, count in follow_up_svc.owners_to_remind(self.event)], [(self.tutor2, 1)])
        self.run_command("--apply", "--base-url", BASE_URL)
        self.assertEqual([message.to for message in mail.outbox], [[self.tutor2.email]])

    def test_only_finished_events_unless_one_is_named(self):
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.ACTIVE)
        self.assertIn("No hay eventos terminados.", self.run_command("--apply", "--base-url", BASE_URL))
        self.assertEqual(mail.outbox, [])
        self.run_command("--event", self.event.slug, "--apply", "--base-url", BASE_URL)
        self.assertEqual(len(mail.outbox), 2)
        with self.assertRaises(CommandError):
            self.run_command("--event", "no-existe")

    def test_apply_needs_the_public_address(self):
        for args in (("--apply",), ("--apply", "--base-url", "emmaus.example.org"), ("--apply", "--base-url", "javascript:alert(1)")):
            with self.assertRaises(CommandError):
                self.run_command(*args)
        self.assertEqual(mail.outbox, [])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_without_configured_mail_it_says_so_and_records_nothing(self):
        out = self.run_command("--apply", "--base-url", BASE_URL)
        self.assertIn("no hay correo configurado, no se envió nada", out)
        self.assertFalse(AuditEvent.objects.filter(action="recordatorio_seguimiento").exists())


class AfterTheEventTests(FollowUpBase):
    """``status=cerrado``: the owner still opens, reassigns and resolves their follow-ups."""

    def setUp(self):
        self.item = self.follow_up(self.p1, assigned_to=self.coord1)
        self.other = self.follow_up(self.p4, assigned_to=self.tutor2)
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.CLOSED)

    def test_the_owner_opens_and_resolves_their_follow_up_after_the_event_closed(self):
        for user, item in ((self.coord1, self.item), (self.tutor2, self.other)):
            client = self.login(user, Client())
            self.assertIn(item.id, self.ids(self.listed(client, "?estado=abiertos&responsable=mio")))
            detail = self.url("follow_up_detail", item.id)
            self.assertEqual(client.get(detail).status_code, 200)
            response = client.post(detail, {"status": FollowUp.Status.DONE, "assigned_to": user.id})
            self.assertRedirects(response, self.url("follow_ups"), fetch_redirect_response=False)
            item.refresh_from_db()
            self.assertEqual(item.status, FollowUp.Status.DONE)
            self.assertIsNotNone(item.resolved_at)

    def test_assigning_still_works_after_the_event_closed(self):
        client = self.login(self.pastor, Client())
        client.post(self.assign_url(self.item), {"assigned_to": self.tutor1.id})
        client.post(self.url("follow_ups_bulk_assign"), {"seguimiento": [self.other.id], "assigned_to": self.pastor.id})
        self.item.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual((self.item.assigned_to, self.other.assigned_to), (self.tutor1, self.pastor))

    def test_the_homes_keep_counting_after_the_event_closed(self):
        FollowUp.objects.filter(pk=self.item.pk).update(assigned_to=None)
        response = self.login(self.tutor1, Client()).get(self.url("tutor_home"))
        self.assertEqual(response.context["follow_ups_unassigned"], 1)
