"""An event's recent notice under its line (a decision under the owner's delegation): a change to an event is
published as its public notice (#101), and wherever a member sees the event listed («Mi iglesia», Eventos) the row
says a notice of the last days is there, linked to the event's notices. Older ones and other events say nothing."""

from __future__ import annotations

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from agenda.models import EventNotice

from .test_agenda import AgendaFixture


class RecentNoticeOnTheEventsRowTests(AgendaFixture):
    def notice(self, title, days_ago=0):
        return EventNotice.objects.create(event=self.retiro, title=title, body="Cambió la hora.", published_at=timezone.now() - timedelta(days=days_ago))

    def pages(self):
        self.client.force_login(self.member_user)
        return (self.client.get(reverse("community:home")).content.decode(), self.client.get(reverse("agenda:list")).content.decode())

    def test_a_recent_notice_is_said_under_the_event_and_leads_to_it(self):
        self.notice("Ahora empezamos a las 18:00")
        home, listing = self.pages()
        detail = reverse("agenda:detail", args=[self.retiro.slug])
        self.assertIn(f'href="{detail}?desde=inicio#avisos">', home)
        self.assertIn(f'href="{detail}#avisos">', listing)
        for page in (home, listing):
            self.assertIn("Ahora empezamos a las 18:00</a>", page)

    def test_an_old_notice_or_none_says_nothing(self):
        self.notice("Aviso de hace dos semanas", days_ago=14)
        home, listing = self.pages()
        for page in (home, listing):
            self.assertNotIn("aviso-reciente", page)
