"""The second half of receiving a visitor: once the administration added them to the church's
people, one action asks that somebody walks with them. It creates one ``care.FollowUp`` with the
fixed reason "Vino por primera vez", nobody responsible yet and no free text, at most one while a
follow-up of that person is open; only the church's administration sees or uses it, and never the
person it is about."""

from django.urls import reverse

from agenda import services
from agenda.models import EventRegistration
from agenda.tests.test_event_extras import ExtrasFixture
from care import services as care
from care.models import FollowUp
from events.tests.base import make_user
from people.models import ChurchAuditEvent, Person

ASK = "Pedir que alguien le acompañe"
OPEN = "Ya hay un seguimiento abierto"


class VisitFixture(ExtrasFixture):
    def setUp(self):
        self.start()
        self.client.force_login(self.admin)
        self.guest = services.register_walkup(self.retiro, self.admin, first_name="Vera", last_name="Visita", phone="55 7777 0000")

    def promote(self) -> Person:
        return services.promote_guest(self.guest, self.admin)


class OfferTests(VisitFixture):
    def test_adding_the_visitor_to_the_people_leads_to_the_one_question(self):
        response = self.client.post(self.url("guest_promote", self.guest.pk))
        self.assertRedirects(response, self.url("visit_follow_up", self.guest.pk), fetch_redirect_response=False)
        page = self.client.get(response.url)
        self.assertContains(page, "Agregaste a Vera Visita a las personas de la iglesia.")
        self.assertContains(page, "¿Quieres pedir que alguien acompañe a Vera Visita?")
        self.assertContains(page, "Sí, pedir que alguien le acompañe")
        self.assertContains(page, "Ahora no")
        # Nothing to type: the only fields of the form are the token and the button.
        for field in ("<textarea", "<select", 'type="text"', 'name="reason"', 'name="motivo"'):
            self.assertNotContains(page, field)
        self.assertFalse(FollowUp.objects.exists())  # asking is not creating

    def test_yes_creates_one_follow_up_with_the_fixed_reason_and_nobody_responsible(self):
        person = self.promote()
        response = self.client.post(self.url("visit_follow_up", self.guest.pk), {"reason": "otro", "owner": self.leader.pk})
        follow_up = FollowUp.objects.get()
        self.assertRedirects(response, reverse("care:detail", args=[follow_up.pk]))
        self.assertEqual((follow_up.church, follow_up.person, follow_up.reason, follow_up.owner, follow_up.due_on),
                         (self.church, person, "visita", None, None))
        self.assertEqual((follow_up.status, follow_up.created_by), ("pendiente", self.admin))
        # Audited where every follow-up is: the person's id and a fixed sentence, no reason, no name.
        audit = ChurchAuditEvent.objects.filter(action="acompanar_creado").get()
        self.assertEqual((audit.actor, audit.summary, audit.metadata), (self.admin, "Creó un seguimiento de persona", {"person_id": person.pk}))

    def test_while_one_is_open_no_other_is_created_and_the_list_leads_to_it(self):
        person = self.promote()
        listing = self.client.get(self.url("registrations"))
        self.assertContains(listing, ASK)
        self.assertContains(listing, self.url("visit_follow_up", self.guest.pk))
        self.assertNotContains(listing, OPEN)
        self.client.post(self.url("visit_follow_up", self.guest.pk))
        follow_up = FollowUp.objects.get()
        listing = self.client.get(self.url("registrations"))
        self.assertContains(listing, OPEN)
        self.assertContains(listing, reverse("care:detail", args=[follow_up.pk]))
        self.assertNotContains(listing, ASK)
        asked = self.client.get(self.url("visit_follow_up", self.guest.pk))
        self.assertContains(asked, OPEN)
        self.assertContains(asked, reverse("care:detail", args=[follow_up.pk]))
        self.assertNotContains(asked, "Sí, pedir")
        again = self.client.post(self.url("visit_follow_up", self.guest.pk), follow=True)
        self.assertContains(again, "Ya hay un seguimiento abierto de Vera Visita. No se creó otro.")
        self.assertEqual(FollowUp.objects.count(), 1)
        self.assertEqual(services.ask_to_accompany(self.guest, self.admin), (follow_up, False))
        # A follow-up of any other reason that is open counts too; once it is closed, the action is back.
        care.set_status(follow_up, self.admin, "atendido")
        other = care.create(self.church, self.admin, person=person, reason="ausencia")
        self.assertContains(self.client.get(self.url("registrations")), reverse("care:detail", args=[other.pk]))
        care.set_status(other, self.admin, "cancelado")
        self.assertContains(self.client.get(self.url("registrations")), ASK)
        self.client.post(self.url("visit_follow_up", self.guest.pk))
        self.assertEqual(FollowUp.objects.filter(status="pendiente", reason="visita").count(), 1)
        self.assertEqual(FollowUp.objects.count(), 3)

    def test_only_for_who_came_as_a_visitor(self):
        own = services.register_walkup(self.retiro, self.admin, person=self.leader)
        recognized = services.register_walkup(self.retiro, self.admin, first_name="Nora", last_name="Sin Cuenta")
        services.promote_guest(recognized, self.admin, existing=self.no_account)  # a person the church already had
        listing = self.client.get(self.url("registrations"))
        self.assertNotContains(listing, ASK)
        for row in (own, recognized, self.guest):  # the last one is still a visitor, not a person
            self.assertEqual(self.client.get(self.url("visit_follow_up", row.pk)).status_code, 404)
            self.assertEqual(self.client.post(self.url("visit_follow_up", row.pk)).status_code, 404)
        with self.assertRaises(services.AgendaError) as caught:
            services.ask_to_accompany(own, self.admin)
        self.assertEqual(caught.exception.code, "not_visitor")
        # Recognizing the visitor as somebody the church already had leads back to the list, with no question.
        third = services.register_walkup(self.retiro, self.admin, first_name="Pía", last_name="Pidió")
        response = self.client.post(self.url("guest_promote", third.pk), {"persona": self.asked.pk})
        self.assertRedirects(response, self.url("registrations"))
        self.assertFalse(FollowUp.objects.exists())

    def test_an_annulled_row_offers_nothing(self):
        self.promote()
        services.annul_walkup(self.guest, self.admin, "error")
        self.assertEqual(self.client.post(self.url("visit_follow_up", self.guest.pk)).status_code, 404)
        self.assertFalse(FollowUp.objects.exists())


class WhoMayTests(VisitFixture):
    def test_only_the_administration_of_this_church(self):
        self.promote()
        for user, status in ((self.member_user, 403), (self.leader_user, 403), (self.admin_b, 404), (self.member_b_user, 403)):
            self.client.force_login(user)
            for method in (self.client.get, self.client.post):
                self.assertEqual(method(self.url("visit_follow_up", self.guest.pk)).status_code, status, user.email)
        self.client.logout()
        self.assertEqual(self.client.post(self.url("visit_follow_up", self.guest.pk)).status_code, 302)
        self.assertFalse(FollowUp.objects.exists())

    def test_tenant_isolation_the_follow_up_is_of_the_events_church_and_invisible_to_another(self):
        person = self.promote()
        self.client.post(self.url("visit_follow_up", self.guest.pk))
        follow_up = FollowUp.objects.get()
        self.assertEqual((follow_up.church_id, person.church_id), (self.church.pk, self.church.pk))
        self.client.force_login(self.admin_b)
        self.assertEqual(self.client.get(reverse("care:detail", args=[follow_up.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("care:admin_list")), "Vera")
        # Church B's own visitor, in church B's event: church A's administration reaches nothing of it.
        self.move(self.retiro_b, 0, 0)
        guest_b = services.register_walkup(self.retiro_b, self.admin_b, first_name="Beto", last_name="Visita B")
        services.promote_guest(guest_b, self.admin_b)
        self.client.force_login(self.admin)
        url_b = reverse("agenda:visit_follow_up", args=[self.retiro_b.slug, guest_b.pk])
        self.assertEqual(self.client.post(url_b).status_code, 404)
        # ...not even by putting church B's row under church A's event.
        self.assertEqual(self.client.post(self.url("visit_follow_up", guest_b.pk)).status_code, 404)
        self.assertEqual(FollowUp.objects.filter(church=self.church_b).count(), 0)
        self.assertEqual(open_ids := services.open_follow_ups(self.church_b, [person.pk]), {}, open_ids)

    def test_the_person_followed_never_sees_it(self):
        person = self.promote()
        self.client.post(self.url("visit_follow_up", self.guest.pk))
        follow_up = FollowUp.objects.get()
        # The visitor gets an account later, as any person of the church.
        user = make_user("vera@prueba.mx", "Vera", "Visita")
        Person.objects.filter(pk=person.pk).update(user=user, email=user.email)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("care:detail", args=[follow_up.pk])).status_code, 403)
        self.assertEqual(self.client.get(self.url("registrations")).status_code, 403)
        self.assertEqual(self.client.get(self.url("visit_follow_up", self.guest.pk)).status_code, 403)
        for path in (reverse("care:mine"), reverse("community:home"), self.url(), reverse("agenda:list")):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200, path)
            for words in ("seguimiento", "acompañe", "Vino por primera vez"):
                self.assertNotContains(page, words)
        # Even as an administrator of the church: the row about them offers nothing and says nothing.
        from core.models import Role
        from core.services import assign_role

        assign_role(user, self.church, Role.CHURCH_ADMIN)
        listing = self.client.get(self.url("registrations"))
        self.assertEqual(listing.status_code, 200)
        self.assertNotContains(listing, OPEN)
        self.assertNotContains(listing, ASK)
        self.assertNotContains(listing, reverse("care:detail", args=[follow_up.pk]))
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(self.url("visit_follow_up", self.guest.pk)).status_code, 404)
        care.set_status(follow_up, self.admin, "atendido")
        self.assertNotContains(self.client.get(self.url("registrations")), ASK)
        with self.assertRaises(services.AgendaError) as caught:
            services.ask_to_accompany(EventRegistration.objects.get(pk=self.guest.pk), user)
        self.assertEqual(caught.exception.code, "self")
        self.assertEqual(FollowUp.objects.count(), 1)
