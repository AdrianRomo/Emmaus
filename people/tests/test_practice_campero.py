"""The practice campero of an event (issue #49) is not somebody to bring into the church's people."""

from django.urls import reverse

from events.services import practice as practice_svc
from groups.tests.base import CommunityFixture
from people.models import EventParticipantLink, Person


class PracticeCamperoIsNotAPersonTests(CommunityFixture):
    def setUp(self):
        self.login(self.admin)
        self.list_url = reverse("people:from_event_list")
        self.event_url = reverse("people:from_event", args=[self.event.slug])

    def row(self):
        return next(row for row in self.client.get(self.list_url).context["rows"] if row["event"] == self.event)

    def test_it_is_not_counted_listed_or_linkable(self):
        before = self.row()
        names = [campero.full_name for campero in self.client.get(self.event_url).context["page"]]
        practice = practice_svc.create(self.event, actor=self.admin, coordinator=self.m_coord1)
        after = self.row()
        self.assertEqual((after["total"], after["linked"], after["missing"]), (before["total"], before["linked"], before["missing"]))
        page = self.client.get(self.event_url)
        self.assertEqual([campero.full_name for campero in page.context["page"]], names)
        self.assertNotContains(page, practice.full_name)
        self.assertNotContains(self.client.get(f"{self.event_url}?q=practica"), practice.full_name)
        people = Person.objects.count()
        link = reverse("people:from_event_link", args=[self.event.slug, practice.public_id])
        self.assertEqual(self.client.post(link, {"persona": "nueva"}).status_code, 404)
        self.assertEqual(Person.objects.count(), people)
        self.assertFalse(EventParticipantLink.objects.filter(participant=practice).exists())
