"""Mi iglesia, for who guides a group: up to three requests are raised one by one and decided on their own card, in
place (the decision returns to the home through a safe ``siguiente``); more than three lead to the group's people."""

from __future__ import annotations

from django.urls import reverse

from groups import services
from groups.models import GroupJoinRequest, GroupMembership

from .base import CommunityFixture


class GuideDecidesOnTheHomeTests(CommunityFixture):
    def home(self):
        self.client.force_login(self.leader_user)
        return self.client.get(reverse("community:home")).content.decode()

    def test_one_request_is_answered_on_the_card(self):
        page = self.home()
        decide = reverse("community:request_decide", args=[self.group.pk, self.pending.pk])
        self.assertIn(f'<form class="sign__actions sign__actions--pair" method="post" action="{decide}" data-inline="principal">', page)
        self.assertIn('<input type="hidden" name="siguiente" value="/mi-iglesia/">', page)
        self.assertIn(">No aceptar<", page)
        self.assertIn(">Aceptar<", page)
        self.assertIn(f'href="{reverse("community:group_people", args=[self.group.pk])}">Ver solicitud<span class="visually-hidden"> de ', page)  # contact first, if wanted

    def test_accepting_from_the_card_returns_to_the_home(self):
        self.client.force_login(self.leader_user)
        decide = reverse("community:request_decide", args=[self.group.pk, self.pending.pk])
        response = self.client.post(decide, {"decision": "aceptar", "siguiente": reverse("community:home")})
        self.assertRedirects(response, reverse("community:home"), fetch_redirect_response=False)
        self.assertTrue(GroupMembership.objects.filter(group=self.group, person=self.asked, end_at__isnull=True).exists())
        self.assertNotIn("quiere unirse", self.home())

    def test_two_requests_are_two_cards_each_answered_on_its_own(self):
        second, _ = services.request_to_join(self.group, self.member, self.member_user)
        page = self.home()
        self.assertEqual(page.count("sign__actions--pair"), 2)
        for request in (self.pending, second):
            self.assertIn(f'action="{reverse("community:request_decide", args=[self.group.pk, request.pk])}" data-inline="principal"', page)
        self.assertEqual(page.count("btn--primary btn--large"), 1)  # one filled «Aceptar»: the first
        self.assertNotIn("personas quieren unirse", page)
        self.assertEqual(GroupJoinRequest.objects.filter(group=self.group, status="pendiente").count(), 2)


class ManyRequestsInSeveralGroupsTests(CommunityFixture):
    """More than three requests in more than one group: the card leads to each group's requests, not only the first
    group's (browser QA, 2026-11-18: the other group's were reachable only through «A tu cargo»)."""

    def test_each_group_has_its_own_way_to_its_requests(self):
        from events.tests.base import make_user

        second = self._group(self.church, "Estudio de Romanos")
        services.set_membership(second, self.leader, self.admin, role=GroupMembership.Role.LEADER)
        for n in range(4):
            user = make_user(f"pide{n}@prueba.mx", f"Pide{n}", "Prueba")
            person = self._person(self.church, user)
            services.request_to_join(second if n % 2 else self.group, person, user)
        self.client.force_login(self.leader_user)
        page = self.client.get(reverse("community:home")).content.decode()
        self.assertIn("5 personas quieren unirse a tus grupos", page)
        self.assertIn(f'href="{reverse("community:group_people", args=[second.pk])}">Ver 2 en Estudio de Romanos</a>', page)
        self.assertIn(f'href="{reverse("community:group_people", args=[self.group.pk])}">Ver 3 en Jóvenes Roca</a>', page)
