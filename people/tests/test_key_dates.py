"""Key dates: the administration writes three; the person writes the two that are theirs to say."""

from datetime import date, timedelta

from django.urls import reverse
from django.utils import timezone

from groups import services as groups
from groups.tests.base import CommunityFixture
from people import services
from people.models import ChurchAuditEvent, Person

BASE = {"first_name": "Nora", "last_name": "Sin Cuenta", "preferred_contact_method": "email"}


class AdministrationTests(CommunityFixture):
    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("people:edit", args=[self.no_account.pk])

    def test_the_three_dates_are_written_shown_and_cleared(self):
        self.client.post(self.url, {**BASE, "baptized_on": "14/03/2009", "arrived_on": "2005-01-02", "member_since": "01/06/2010"})
        nora = Person.objects.get(pk=self.no_account.pk)
        self.assertEqual((nora.baptized_on, nora.arrived_on, nora.member_since), (date(2009, 3, 14), date(2005, 1, 2), date(2010, 6, 1)))
        page = self.client.get(reverse("people:detail", args=[nora.pk]))
        for words in ("Se bautizó el", "14 de marzo de 2009", "Llegó a la iglesia el", "Es miembro desde", "1 de junio de 2010"):
            self.assertContains(page, words)
        self.assertContains(self.client.get(self.url), 'value="14/03/2009"')
        # The audit row says a person was modified, never a date.
        row = ChurchAuditEvent.objects.filter(action="persona_modificada").latest("pk")
        self.assertEqual(row.metadata, {"person_id": nora.pk})
        self.client.post(self.url, BASE)
        nora.refresh_from_db()
        self.assertEqual((nora.baptized_on, nora.arrived_on, nora.member_since), (None, None, None))
        self.assertNotContains(self.client.get(reverse("people:detail", args=[nora.pk])), "Se bautizó el")

    def test_a_date_that_has_not_happened_or_is_not_a_date_is_refused(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).strftime("%d/%m/%Y")
        for field in ("baptized_on", "arrived_on", "member_since"):
            for value, words in ((tomorrow, "tiene que ser un día que ya pasó"), ("01/01/1800", "tiene que ser un día que ya pasó"),
                                 ("<script>", "Escribe la fecha así")):
                response = self.client.post(self.url, {**BASE, field: value})
                self.assertContains(response, words)
                self.assertNotContains(response, "<script>")
        self.assertIsNone(Person.objects.get(pk=self.no_account.pk).baptized_on)

    def test_they_do_not_touch_the_birth_date_or_its_verification(self):
        services.verify_birth_date(self.no_account, self.admin, "documento", date(2010, 5, 5))
        self.client.post(self.url, {**BASE, "birth_date": "05/05/2010", "baptized_on": "14/03/2019"})
        nora = Person.objects.get(pk=self.no_account.pk)
        self.assertTrue(nora.birth_date_verified)
        self.assertEqual(nora.baptized_on, date(2019, 3, 14))


class MyDataTests(CommunityFixture):
    def setUp(self):
        self.client.force_login(self.member_user)
        self.url = reverse("community:profile")
        self.data = {"phone": "55 1111 2222", "email": "diego@prueba.mx", "preferred_contact_method": "email"}

    def test_the_person_says_their_baptism_and_arrival_and_never_their_membership(self):
        Person.objects.filter(pk=self.member.pk).update(member_since=date(2020, 1, 1))
        page = self.client.get(self.url)
        self.assertContains(page, 'name="baptized_on"')
        self.assertContains(page, 'name="arrived_on"')
        self.assertNotContains(page, "member_since")
        self.assertNotContains(page, "miembro desde")
        self.assertNotContains(page, 'name="stage"')
        self.client.post(self.url, {**self.data, "baptized_on": "14/03/2009", "arrived_on": "01/02/2003", "member_since": "01/01/1999",
                                    "stage": "miembro"})
        diego = Person.objects.get(pk=self.member.pk)
        self.assertEqual((diego.baptized_on, diego.arrived_on), (date(2009, 3, 14), date(2003, 2, 1)))
        self.assertEqual((diego.member_since, diego.stage), (date(2020, 1, 1), ""))  # not theirs to say

    def test_a_future_date_is_refused(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).strftime("%d/%m/%Y")
        self.assertContains(self.client.post(self.url, {**self.data, "baptized_on": tomorrow}), "tiene que ser un día que ya pasó")
        self.assertIsNone(Person.objects.get(pk=self.member.pk).baptized_on)

    def test_no_other_member_and_no_guide_reads_them(self):
        Person.objects.filter(pk=self.member.pk).update(baptized_on=date(2007, 3, 14), arrived_on=date(2001, 2, 1), member_since=date(2011, 6, 1))
        groups.set_membership(self.group, self.member, self.admin)
        groups.set_membership(self.group, self.asked, self.admin)
        pages = [reverse("community:group_detail", args=[self.group.pk]), reverse("community:home"), reverse("community:profile")]
        for user in (self.leader_user, self.asked_user):
            self.client.force_login(user)
            for url in pages:
                page = self.client.get(url)
                self.assertEqual(page.status_code, 200)
                for words in ("2007", "2001", "2011"):
                    self.assertNotContains(page, words, msg_prefix=f"{user.email} {url}")
        self.client.force_login(self.leader_user)
        page = self.client.get(reverse("community:group_people", args=[self.group.pk]))
        self.assertContains(page, "Diego")
        for words in ("14 de marzo", "2007", "2001", "2011", "bautiz", "miembro desde"):
            self.assertNotContains(page, words)
        # The person reads their own two dates in their own form, and still not the church's.
        self.client.force_login(self.member_user)
        page = self.client.get(reverse("community:profile"))
        self.assertContains(page, 'value="14/03/2007"')
        self.assertNotContains(page, "2011")
