"""A simple event of the church: created in the agenda, an ``events.Event`` without the camp's tools."""

from datetime import timedelta

from django.urls import reverse

from agenda.models import EventPage
from events.access import accessible_events
from events.models import Event, InterviewStation
from events.services.join import find_event_by_key
from people.models import ChurchAuditEvent

from .test_agenda import AgendaFixture


class SimpleEventTests(AgendaFixture):
    def data(self, **extra):
        return {"name": "Noche de alabanza", "kind": "otro", "location": "Templo", "slug": "",
                "starts_on": (self.today + timedelta(days=7)).isoformat(), "ends_on": "", **extra}

    def create(self, **extra) -> Event:
        self.client.force_login(self.admin)
        response = self.client.post(reverse("agenda:create"), self.data(**extra))
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["form"].errors)
        return Event.objects.get(name=extra.get("name", "Noche de alabanza"), church=self.church)

    def test_the_administration_creates_one_and_lands_on_its_page(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("agenda:create"), self.data())
        event = Event.objects.get(slug="noche-de-alabanza")
        self.assertRedirects(response, reverse("agenda:page_edit", args=[event.slug]))
        self.assertFalse(event.camp_tools)
        self.assertFalse(event.join_by_name)
        self.assertEqual(event.church, self.church)
        self.assertFalse(EventPage.objects.get(event=event).published)
        self.assertFalse(InterviewStation.objects.filter(event=event).exists())
        self.assertFalse(list(event.questionnaire_versions()))
        self.assertTrue(ChurchAuditEvent.objects.filter(church=self.church, action="evento_sencillo_creado").exists())

    def test_a_member_cannot_create_one(self):
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(reverse("agenda:create")).status_code, 403)
        self.assertEqual(self.client.post(reverse("agenda:create"), self.data()).status_code, 403)
        self.assertFalse(Event.objects.filter(name="Noche de alabanza").exists())

    def test_the_day_is_required_and_the_last_day_is_not_before_it(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("agenda:create"), self.data(starts_on=""))
        self.assertContains(response, "Escribe qué día es.")
        response = self.client.post(reverse("agenda:create"), self.data(ends_on=self.today.isoformat()))
        self.assertContains(response, "El último día no puede ser antes del primero.")

    def test_a_name_that_repeats_gets_another_address(self):
        first = self.create()
        self.client.post(reverse("agenda:create"), self.data())
        self.assertEqual(Event.objects.filter(name="Noche de alabanza").count(), 2)
        self.assertEqual(Event.objects.filter(name="Noche de alabanza").exclude(pk=first.pk).get().slug, "noche-de-alabanza-2")

    def test_it_never_opens_the_camps_screens(self):
        event = self.create()
        self.assertNotIn(event, accessible_events(self.admin))
        for name in ("events:home", "events:admin_home", "events:participants"):
            self.assertEqual(self.client.get(reverse(name, args=[event.slug])).status_code, 404, name)
        self.assertIsNone(find_event_by_key(event.join_code))
        self.assertIsNone(find_event_by_key(event.name))

    def test_its_data_is_changed_in_the_agenda_and_a_camp_is_not(self):
        event = self.create()
        url = reverse("agenda:event_edit", args=[event.slug])
        response = self.client.post(url, self.data(name="Noche de adoración", slug=event.slug, location="Auditorio"))
        self.assertRedirects(response, reverse("agenda:admin_list"))
        event.refresh_from_db()
        self.assertEqual((event.name, event.location, event.slug), ("Noche de adoración", "Auditorio", "noche-de-alabanza"))
        self.assertEqual(self.client.get(reverse("agenda:event_edit", args=[self.retiro.slug])).status_code, 404)

    def test_another_church_cannot_change_it(self):
        event = self.create()
        self.client.force_login(self.admin_b)
        self.assertEqual(self.client.get(reverse("agenda:event_edit", args=[event.slug])).status_code, 404)

    def test_published_it_shows_to_a_member_who_signs_up(self):
        event = self.create()
        self.client.post(reverse("agenda:page_edit", args=[event.slug]), {"published": "on", "registration": "abierta", "description": "Ven."})
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(reverse("agenda:list")), "Noche de alabanza")
        self.assertEqual(self.client.post(reverse("agenda:register", args=[event.slug])).status_code, 302)
        self.assertEqual(event.registrations.count(), 1)
