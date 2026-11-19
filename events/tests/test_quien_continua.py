"""Who goes on with an interview in progress (browser QA, owner decisions, 2026-11-18).

A. The computer that lost it. Ana's interview with Mariana is open on her computer; the administration gives Mariana
   to Carlos, who takes it over. Ana's browser had it (its session holds or held the claim's token), so instead of a
   404 it is sent to its home, told the interview is no longer in its hands and that the answers are kept, without a
   word about the campero she may no longer see; nothing it posts is written. Any other browser keeps the 404.

B. Whoever cannot continue it cannot take it. The rule an interview already follows when it starts: whoever starts it
   conducts it (``conducted_by``), and an administrador reads the personal answers of an interview she conducts
   (``EventAccess.can_view_answer``). Taking it over does not make her its conductor, so an administrador without
   confidential access would land on screens she cannot read: the take-over page does not offer it to her and says who
   can, and the server refuses it (``EventAccess.can_continue_interview``, ``svc.claim``), so the lock never moves.
   The two texts that were false in that case are true now: the step's toast never says the campero answered, and
   «… las respondió por su cuenta» only when the campero answered on their own.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from events.access import resolve_access
from events.models import AuditEvent, EventMembership, Interview, MembershipRole
from events.services import audit
from events.services import interviews as svc
from people import flags

from .base import PASSWORD, CampFixture, make_user

NO_LONGER_HERE = "Esta entrevista ya no está a tu cargo. Lo que se respondió sigue guardado."
WHO_CAN = "Esta entrevista tiene preguntas que no puedes ver: pueden continuarla su coordinador o alguien de la administración con acceso confidencial."
ANSWERED_ALONE = "las respondió por su cuenta"


def flashed(response) -> list[str]:
    """The message this response added (the earlier ones, such as the computer chosen, were not shown yet)."""
    return [message.message for message in get_messages(response.wsgi_request)][-1:]


class TheComputerThatLostItTests(CampFixture):
    """A: Mariana (p1) goes from Carla (coord1, «Ana») to César (coord2, «Carlos»)."""

    def setUp(self):
        self.ana = self.login(self.coord1, Client())
        self.choose_station(self.ana, number=3)
        self.start(self.p1, client=self.ana)
        self.interview = Interview.objects.get(participant=self.p1)
        self.answer(self.interview, "salvo", "si", client=self.ana)
        self.p1.coordinator = self.m_coord2
        self.p1.save(update_fields=["coordinator"])

    def carlos_takes_it(self):
        carlos = self.login(self.coord2, Client())
        self.choose_station(carlos, number=4)
        taken = carlos.post(self.url("interview_conflict", self.interview.public_id), {"identity": "on"})
        self.assertRedirects(taken, self.url("interview_resume", self.interview.public_id), fetch_redirect_response=False)
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.lock_holder_id, self.coord2.id)
        return carlos

    def capture_urls(self):
        public_id = self.interview.public_id
        return [
            self.url("interview_resume", public_id), self.url("interview_step", public_id, self.step("dudas").number),
            self.url("interview_review", public_id), self.url("interview_conflict", public_id),
            self.url("interview_handoff", public_id), self.url("interview_discard", public_id), self.url("interview_done", public_id),
        ]

    def test_it_is_sent_home_told_it_is_no_longer_in_its_hands_without_a_word_about_the_campero(self):
        self.carlos_takes_it()
        for module in (False, True):
            flags.set_enabled(self.church, "community", module)
            for url in self.capture_urls():
                with self.subTest(module=module, url=url):
                    response = self.ana.get(url)
                    self.assertRedirects(response, self.url("coord_home"), fetch_redirect_response=False)
                    self.assertEqual(flashed(response), [NO_LONGER_HERE])
        home = self.ana.get(self.url("interview_step", self.interview.public_id, 2), follow=True)
        self.assertContains(home, NO_LONGER_HERE)
        self.assertNotContains(home, self.p1.first_name)  # nothing about her: Ana may no longer see her

    def test_nothing_it_posts_is_written(self):
        self.carlos_takes_it()
        self.interview.refresh_from_db()
        before = (self.interview.revision, self.interview.lock_token_hash, self.interview.status, self.interview.answers.count())
        dudas = self.step("dudas")
        posts = [
            (self.url("interview_step", self.interview.public_id, dudas.number),
             {"revision": self.interview.revision, "direction": "next", f"q_{dudas.question.id}": "no"}),
            (self.url("interview_review", self.interview.public_id), {"revision": self.interview.revision, "follow_up_decision": "no_requerido"}),
            (self.url("interview_conflict", self.interview.public_id), {"identity": "on"}),
            (self.url("interview_handoff", self.interview.public_id), {}),
            (self.url("interview_discard", self.interview.public_id), {"reason": "repetir"}),
        ]
        for url, data in posts:
            with self.subTest(url=url):
                response = self.ana.post(url, data)
                self.assertRedirects(response, self.url("coord_home"), fetch_redirect_response=False)
                self.interview.refresh_from_db()
                after = (self.interview.revision, self.interview.lock_token_hash, self.interview.status, self.interview.answers.count())
                self.assertEqual(after, before)

    def test_before_carlos_takes_it_her_save_is_refused_too_and_her_claim_stays_where_it_was(self):
        self.interview.refresh_from_db()
        lock = (self.interview.lock_token_hash, self.interview.lock_holder_id, self.interview.revision)
        dudas = self.step("dudas")
        response = self.ana.post(
            self.url("interview_step", self.interview.public_id, dudas.number),
            {"revision": self.interview.revision, "direction": "next", f"q_{dudas.question.id}": "no"},
        )
        self.assertRedirects(response, self.url("coord_home"), fetch_redirect_response=False)
        self.assertEqual(flashed(response), [NO_LONGER_HERE])
        self.interview.refresh_from_db()
        self.assertEqual((self.interview.lock_token_hash, self.interview.lock_holder_id, self.interview.revision), lock)
        self.assertFalse(self.interview.answers.filter(question=dudas.question).exists())

    def test_a_browser_that_never_had_it_keeps_the_404(self):
        self.carlos_takes_it()
        cora = self.login(self.coord3, Client())  # another coordinador, never had it
        other_ana = self.login(self.coord1, Client())  # Ana herself, on a browser that never held it
        for client in (cora, other_ana):
            for url in self.capture_urls():
                with self.subTest(url=url):
                    self.assertEqual(client.get(url).status_code, 404)
        # Ana's browser on an interview it never held, of a campero she does not see: the 404 as before
        sofia = svc.start_interview(
            resolve_access(self.coord2, self.event), self.p3, mode="asistida", station=None, confirmed_consents={"assent"}, session={}
        )
        self.assertEqual(self.ana.get(self.url("interview_resume", sofia.public_id)).status_code, 404)

    def test_carlos_goes_on_where_it_was(self):
        carlos = self.carlos_takes_it()
        resumed = carlos.get(self.url("interview_resume", self.interview.public_id))
        self.assertRedirects(resumed, self.url("interview_step", self.interview.public_id, self.step("dudas").number), fetch_redirect_response=False)


class WhoCannotContinueCannotTakeItTests(CampFixture):
    """B: Carla (coord1, «Ana») started Mariana's (p1) assisted interview on Computadora 3."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.laura = make_user("laura@prueba.mx", "Laura", "Administración")
        EventMembership.objects.create(event=cls.event, user=cls.laura, role=MembershipRole.ADMIN)
        cls.ines = make_user("ines@prueba.mx", "Inés", "Cuidado")
        cls.m_ines = EventMembership.objects.create(event=cls.event, user=cls.ines, role=MembershipRole.ADMIN, can_view_sensitive=True)

    def setUp(self):
        self.ana = self.login(self.coord1, Client())
        self.choose_station(self.ana, number=3)
        self.start(self.p1, client=self.ana)
        self.interview = Interview.objects.get(participant=self.p1)
        self.answer(self.interview, "salvo", "si", client=self.ana)
        self.conflict = self.url("interview_conflict", self.interview.public_id)

    def browser(self, user, number=5):
        client = self.login(user, Client())
        self.choose_station(client, number=number)
        return client

    def lock(self):
        self.interview.refresh_from_db()
        return self.interview.lock_token_hash, self.interview.lock_holder_id, self.interview.lock_expires_at

    def nothing_moved(self, lock):
        self.assertEqual(self.lock(), lock)
        rows = AuditEvent.objects.filter(object_id=str(self.interview.public_id), actor=self.laura)
        self.assertFalse(rows.filter(action__in=["entrevista_control", "entrevista_retomada", audit.IDENTITY_VERIFIED]).exists())

    def test_the_page_offers_her_nothing_to_take_and_says_who_can(self):
        laura = self.browser(self.laura)
        for module in (False, True):
            flags.set_enabled(self.church, "community", module)
            with self.subTest(module=module):
                page = laura.get(self.conflict)
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, WHO_CAN, count=1)
                for offer in ("Continuar aquí", "Tomar el control aquí", 'name="identity"', 'type="submit">Continuar<'):
                    self.assertNotContains(page, offer)

    def test_the_server_refuses_her_take_over_and_the_lock_never_moves(self):
        laura = self.browser(self.laura)
        lock = self.lock()
        for module in (False, True):
            flags.set_enabled(self.church, "community", module)
            with self.subTest(module=module):
                refused = laura.post(self.conflict, {"identity": "on"})
                self.assertContains(refused, WHO_CAN)
                self.nothing_moved(lock)
        with self.assertRaisesMessage(svc.NotAllowed, WHO_CAN):
            svc.claim(resolve_access(self.laura, self.event), self.interview, {}, take_over=True)
        self.nothing_moved(lock)

    def test_every_way_in_ends_on_that_page(self):
        laura = self.browser(self.laura)
        lock = self.lock()
        public_id = self.interview.public_id
        for url in (self.url("interview_resume", public_id), self.url("interview_step", public_id, self.step("dudas").number),
                    self.url("interview_review", public_id), self.url("interview_handoff", public_id)):
            with self.subTest(url=url):
                response = laura.get(url, follow=True)
                self.assertEqual(response.redirect_chain[-1][0], self.conflict)
                self.assertContains(response, WHO_CAN)
        self.nothing_moved(lock)

    def test_a_free_claim_is_not_picked_up_either(self):
        Interview.objects.filter(pk=self.interview.pk).update(lock_expires_at=timezone.now() - timedelta(minutes=1))
        lock = self.lock()
        laura = self.browser(self.laura)
        self.assertContains(laura.get(self.url("interview_resume", self.interview.public_id), follow=True), WHO_CAN)
        self.nothing_moved(lock)
        # Someone who already ticked «Sí, es …» (before this rule) is not asked again: the claim itself refuses her
        audit.record(self.laura, self.event, audit.IDENTITY_VERIFIED, "Verificó la identidad", self.interview)
        response = laura.get(self.url("interview_resume", self.interview.public_id))
        self.assertRedirects(response, self.url("home"), fetch_redirect_response=False)
        self.assertEqual(flashed(response), [WHO_CAN])
        self.assertEqual(self.lock(), lock)

    def test_she_still_discards_an_abandoned_one(self):
        Interview.objects.filter(pk=self.interview.pk).update(lock_expires_at=timezone.now() - timedelta(minutes=1))
        laura = self.browser(self.laura)
        self.assertEqual(laura.get(self.url("interview_discard", self.interview.public_id)).status_code, 200)
        laura.post(self.url("interview_discard", self.interview.public_id), {"reason": "repetir"})
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.DISCARDED)

    def test_an_admin_with_confidential_access_takes_it_over(self):
        ines = self.browser(self.ines)
        for module in (False, True):
            flags.set_enabled(self.church, "community", module)
            with self.subTest(module=module):
                page = ines.get(self.conflict)
                self.assertNotContains(page, WHO_CAN)
                self.assertContains(page, 'name="identity"')
                self.assertContains(page, "Continuar aquí" if module else "Tomar el control aquí")
        ines.post(self.conflict, {"identity": "on"})
        self.assertEqual(self.lock()[1], self.ines.id)

    def test_the_admin_who_started_it_goes_on_with_it_on_another_computer(self):
        """The rule at the start: Laura starts Diego's (p2) interview, so she conducts it and reads every question."""
        first = self.browser(self.laura, number=6)
        self.start(self.p2, client=first)
        diego = Interview.objects.get(participant=self.p2)
        second = self.browser(self.laura, number=7)
        page = second.get(self.url("interview_conflict", diego.public_id))
        self.assertNotContains(page, WHO_CAN)
        second.post(self.url("interview_conflict", diego.public_id))
        self.assertEqual(second.get(self.url("interview_step", diego.public_id, self.step("salvo").number)).status_code, 200)
        diego.refresh_from_db()
        self.assertEqual(diego.lock_holder_id, self.laura.id)
        self.assertTrue(resolve_access(self.laura, self.event).can_continue_interview(diego))

    def test_its_new_coordinador_and_its_own_can_continue_it(self):
        self.assertTrue(resolve_access(self.coord1, self.event).can_continue_interview(self.interview))
        self.assertTrue(resolve_access(self.ines, self.event).can_continue_interview(self.interview))
        self.assertFalse(resolve_access(self.laura, self.event).can_continue_interview(self.interview))
        self.assertFalse(resolve_access(self.coord2, self.event).can_continue_interview(self.interview))  # not hers
        self.assertFalse(resolve_access(self.care, self.event).can_continue_interview(self.interview))  # a pastor never interviews


class NoFalseTextTests(CampFixture):
    """B, the two texts: what the step and the review say when someone holding the interview does not read a question."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ines = make_user("ines@prueba.mx", "Inés", "Cuidado")
        cls.m_ines = EventMembership.objects.create(event=cls.event, user=cls.ines, role=MembershipRole.ADMIN, can_view_sensitive=True)

    def test_an_assisted_interview_never_says_the_campero_answered(self):
        """Inés took Ana's assisted interview over; then her confidential access was withdrawn while she had it."""
        ana = self.login(self.coord1, Client())
        self.choose_station(ana, number=3)
        self.start(self.p1, client=ana)
        interview = Interview.objects.get(participant=self.p1)
        ines = self.login(self.ines, Client())
        self.choose_station(ines, number=5)
        ines.post(self.url("interview_conflict", interview.public_id), {"identity": "on"})
        EventMembership.objects.filter(pk=self.m_ines.pk).update(can_view_sensitive=False)
        for module in (False, True):
            flags.set_enabled(self.church, "community", module)
            for code, said in (("dudas", "Esa pregunta es sensible y no puedes verla."), ("impedimento", "Esa pregunta es confidencial y no puedes verla.")):
                with self.subTest(module=module, code=code):
                    response = ines.get(self.url("interview_step", interview.public_id, self.step(code).number))
                    self.assertRedirects(response, self.url("interview_review", interview.public_id), fetch_redirect_response=False)
                    self.assertEqual(flashed(response), [said])
            with self.subTest(module=module, page="review"):
                review = ines.get(self.url("interview_review", interview.public_id))
                self.assertContains(review, "Algunas preguntas son confidenciales y solo el equipo de cuidado autorizado puede leerlas.")
                self.assertNotContains(review, ANSWERED_ALONE)
                self.assertNotContains(review, "la respondió el campero")

    def supervised(self, *answers):
        """Carla hands the computer to Mariana, who answers ``answers`` alone and ends; Carla recovers it."""
        self.login(self.coord1)
        self.choose_station(number=3)
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", interview.public_id))
        for code, value in answers:
            self.assertEqual(self.answer(interview, code, value, url_name="kiosk").status_code, 302)
        self.client.post(reverse("kiosk:finish"))
        recovered = self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        self.assertRedirects(recovered, self.url("interview_review", interview.public_id), fetch_redirect_response=False)
        return interview

    def test_the_campero_who_answered_alone_is_said_to(self):
        interview = self.supervised(("salvo", "si"), ("impedimento", "Nada"))
        for module in (False, True):
            flags.set_enabled(self.church, "community", module)
            with self.subTest(module=module):
                review = self.client.get(self.url("interview_review", interview.public_id))
                self.assertContains(review, f"{self.p1.first_name} {ANSWERED_ALONE} y solo el equipo de cuidado autorizado puede leerlas.")
                # The step of a confidential question she cannot read: true, and the same answered or not
                response = self.client.get(self.url("interview_step", interview.public_id, self.step("impedimento").number))
                self.assertEqual(flashed(response), ["Esa pregunta es confidencial y no puedes verla."])

    def test_a_campero_who_answered_nothing_alone_is_not_said_to(self):
        interview = self.supervised()
        for module in (False, True):
            flags.set_enabled(self.church, "community", module)
            with self.subTest(module=module):
                review = self.client.get(self.url("interview_review", interview.public_id))
                self.assertContains(review, "Algunas preguntas son confidenciales y solo el equipo de cuidado autorizado puede leerlas.")
                self.assertNotContains(review, ANSWERED_ALONE)
