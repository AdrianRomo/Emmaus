"""Where an ordinary sign-in lands for a camp administrator (owner decision): exactly one camp they administer
underway → its administration home; several → «¿A qué campamento entras?»; none → the usual home. A direct link is
honoured first; visiting the home later is not redirected; other roles keep their landing."""

from __future__ import annotations

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from events.models import Event

from .base import PASSWORD, CampFixture


class CampAdministratorLandingTests(CampFixture):
    def sign_in(self, user, next_url=""):
        url = reverse("login") + (f"?next={next_url}" if next_url else "")
        data = {"username": user.email, "password": PASSWORD}
        if next_url:
            data["next"] = next_url
        return self.client.post(url, data)

    def only_a_is_live(self):
        Event.objects.filter(pk=self.other_event.pk).update(status=Event.Status.CLOSED)
        Event.objects.filter(pk=self.event.pk).update(kind=Event.Kind.CAMP, status=Event.Status.ACTIVE)

    def test_one_camp_underway_opens_its_administration_home(self):
        self.only_a_is_live()
        response = self.sign_in(self.admin)
        self.assertRedirects(response, self.url("admin_home"), fetch_redirect_response=False)

    def test_a_direct_link_is_honoured(self):
        self.only_a_is_live()
        target = reverse("events:list") + "?todos=1"
        self.assertRedirects(self.sign_in(self.admin, target), target, fetch_redirect_response=False)

    def test_several_camps_underway_ask_which(self):
        Event.objects.filter(pk__in=[self.event.pk, self.other_event.pk]).update(kind=Event.Kind.CAMP, status=Event.Status.ACTIVE)
        response = self.sign_in(self.admin)
        self.assertRedirects(response, reverse("events:live_camps"), fetch_redirect_response=False)
        page = self.client.get(reverse("events:live_camps")).content.decode()
        self.assertIn("¿A qué campamento entras?", page)
        for event in (self.event, self.other_event):
            self.assertIn(f'href="{reverse("events:admin_home", args=[event.slug])}">{event.name}</a>', page)
        self.assertIn(f'href="{reverse("events:list")}?todos=1">Todos los eventos</a>', page)

    def test_no_camp_underway_keeps_the_usual_home(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        Event.objects.filter(pk=self.event.pk).update(kind=Event.Kind.CAMP, status=Event.Status.ACTIVE, starts_on=tomorrow, ends_on=tomorrow)
        Event.objects.filter(pk=self.other_event.pk).update(kind=Event.Kind.RETREAT, status=Event.Status.ACTIVE)  # live, but not a camp
        self.assertRedirects(self.sign_in(self.admin), reverse("dashboard"), fetch_redirect_response=False)
        self.client.logout()
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.DRAFT, starts_on=timezone.localdate())
        self.assertRedirects(self.sign_in(self.admin), reverse("dashboard"), fetch_redirect_response=False)

    def test_the_chooser_with_one_or_none_left_goes_on(self):
        self.only_a_is_live()
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(reverse("events:live_camps")), self.url("admin_home"), fetch_redirect_response=False)
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.CLOSED)
        self.assertRedirects(self.client.get(reverse("events:live_camps")), reverse("dashboard"), fetch_redirect_response=False)

    def test_a_coordinator_keeps_their_landing(self):
        self.only_a_is_live()
        self.assertRedirects(self.sign_in(self.coord1), reverse("dashboard"), fetch_redirect_response=False)

    def test_only_the_sign_in_redirects_the_home_does_not(self):
        self.only_a_is_live()
        self.client.force_login(self.admin)
        self.assertNotEqual(self.client.get(reverse("dashboard")).get("Location"), self.url("admin_home"))
