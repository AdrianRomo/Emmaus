"""The slice's acceptance scenarios, end to end through the screens (docs/v2/EMMAUS_V2_CLAUDE_CODE_PROMPT.md)."""

from django.core import mail
from django.test import override_settings
from django.urls import reverse

from access.models import RoleAssignment
from events.access import resolve_access
from events.models import Answer, EventParticipant, Interview
from groups.models import GroupJoinRequest, GroupMembership
from groups.tests.base import CommunityFixture
from people.models import ChurchAuditEvent, EventParticipantLink, Person


class CampToGroupJourneyTests(CommunityFixture):
    # The default backend of a deployment without SMTP: nothing can be delivered, and the screens say so.
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_a_former_campero_joins_a_group_under_the_same_person(self):
        """Scenario 1: the camp record stays as it was, and the same person asks, is accepted and sees the meeting."""
        campero_before = EventParticipant.objects.values().get(pk=self.p2.pk)
        self.client.force_login(self.member_user)

        home = self.client.get(reverse("dashboard"), follow=True)
        self.assertEqual(home.redirect_chain[-1][0], reverse("community:home"))
        self.assertContains(home, '<h1 class="voice hoy__titulo">Hoy</h1>')
        self.assertContains(home, "Donde has estado")
        self.assertContains(home, "Campamento A")
        self.assertContains(home, "Encuentra tu grupo")

        listing = self.client.get(reverse("community:groups"))
        self.assertContains(listing, "Jóvenes Roca")
        self.assertContains(listing, "Gala Guía")
        detail = self.client.get(reverse("community:group_detail", args=[self.group.pk]))
        self.assertContains(detail, "¿Quieres unirte a este grupo?")
        self.assertNotContains(detail, "Salón 2")  # where it meets is for members

        response = self.client.post(reverse("community:group_request", args=[self.group.pk]), follow=True)
        self.assertContains(response, "Quien lo guía verá tu solicitud cuando entre a Emmaus.")
        self.assertContains(response, "Ya pediste unirte.")
        self.client.post(reverse("community:group_request", args=[self.group.pk]))  # twice: still one request
        request = GroupJoinRequest.objects.get(group=self.group, person=self.member)
        self.assertContains(self.client.get(reverse("community:home")), "Por decidir")

        self.client.force_login(self.leader_user)
        leader_home = self.client.get(reverse("community:home"))
        self.assertContains(leader_home, "2 personas quieren unirse a tus grupos")
        people = self.client.get(reverse("community:group_people", args=[self.group.pk]))
        self.assertContains(people, "Diego Hernández Cruz")
        self.assertContains(people, "55 1111 2222")
        response = self.client.post(
            reverse("community:request_decide", args=[self.group.pk, request.pk]), {"decision": "aceptar"}, follow=True
        )
        self.assertContains(response, "Diego Hernández Cruz ya es parte de Jóvenes Roca. Lo verá cuando entre a Emmaus.")

        self.client.force_login(self.member_user)
        home = self.client.get(reverse("community:home"))
        self.assertContains(home, "Jóvenes Roca")
        self.assertContains(home, "Se reúne el jueves")
        self.assertContains(self.client.get(reverse("community:group_detail", args=[self.group.pk])), "Salón 2")

        # One person, one account, the same camp record; and the camp record did not change.
        self.assertEqual(Person.objects.filter(user=self.member_user).count(), 1)
        self.assertEqual(EventParticipantLink.objects.get(participant=self.p2).person, self.member)
        self.assertEqual(EventParticipant.objects.values().get(pk=self.p2.pk), campero_before)
        row = RoleAssignment.objects.get(subject_user=self.member_user)
        self.assertEqual((row.scope_type, row.scope_id, row.role), ("group", self.group.pk, "integrante"))
        actions = set(ChurchAuditEvent.objects.filter(church=self.church).values_list("action", flat=True))
        self.assertLessEqual({"persona_vinculada", "grupo_solicitud", "grupo_solicitud_aceptada"}, actions)

    def test_a_former_coordinator_holds_nothing_over_a_group(self):
        """Scenario 2: coord1 coordinated Diego at the camp; in the church they are one more person."""
        self.assertIsNotNone(resolve_access(self.coord1, self.event))  # the event role is still theirs, in its event
        self.client.force_login(self.coord1)
        self.assertEqual(self.client.get(reverse("community:groups")).status_code, 200)
        for name, args in (
            ("community:group_people", [self.group.pk]), ("community:group_edit", [self.group.pk]),
            ("community:group_admin_list", []), ("community:group_create", []), ("people:list", []),
            ("people:detail", [self.member.pk]),
        ):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403)
        response = self.client.post(
            reverse("community:request_decide", args=[self.group.pk, self.pending.pk]), {"decision": "aceptar"}
        )
        self.assertEqual(response.status_code, 403)
        self.pending.refresh_from_db()
        self.assertTrue(self.pending.is_pending)

    def test_a_group_guide_cannot_open_anything_of_the_camp(self):
        """J2.7: guiding the group of a former campero opens no camp screen, and no group screen shows an answer."""
        from django.urls import get_resolver

        interview = Interview.objects.create(event=self.event, participant=self.p2, conducted_by=self.coord1, version=self.version)
        self.client.force_login(self.leader_user)
        patterns = get_resolver().namespace_dict["events"][1].url_patterns
        checked = 0
        for pattern in patterns:
            converters = pattern.pattern.converters
            if "slug" not in converters:
                continue
            kwargs = {"slug": self.event.slug}
            for key, converter in converters.items():
                if key == "slug":
                    continue
                kind = type(converter).__name__
                kwargs[key] = {"UUIDConverter": self.p2.public_id if "participant" in pattern.name or "campero" in pattern.name else interview.public_id,
                               "IntConverter": 1}.get(kind, "x")
            url = reverse(f"events:{pattern.name}", kwargs=kwargs)
            with self.subTest(url=pattern.name):
                self.assertIn(self.client.get(url).status_code, (403, 404, 405))
                checked += 1
        self.assertGreater(checked, 40)
        for model in (Interview, Answer):
            self.assertNotIn(model.__name__, open(__import__("groups.views").views.__file__).read())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_notices_say_an_email_went_out_only_when_one_did(self):
        """Scenario 8, and invariant 7: the notice names nobody and says nothing of the decision."""
        self.client.force_login(self.member_user)
        response = self.client.post(reverse("community:group_request", args=[self.group.pk]), follow=True)
        self.assertContains(response, "Le avisamos por correo a quien lo guía.")
        self.assertEqual(mail.outbox[0].to, [self.leader_user.email])
        self.assertNotIn("Diego", mail.outbox[0].body + mail.outbox[0].subject)
        request = GroupJoinRequest.objects.get(group=self.group, person=self.member)
        self.client.force_login(self.leader_user)
        response = self.client.post(
            reverse("community:request_decide", args=[self.group.pk, request.pk]), {"decision": "declinar"}, follow=True
        )
        self.assertContains(response, "Le avisamos por correo.")
        self.assertEqual(mail.outbox[1].to, [self.member_user.email])
        self.assertNotIn("Jóvenes Roca", mail.outbox[1].body)
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(reverse("community:home")), "Habla con quien lo guía o busca otro grupo.")
        self.assertFalse(GroupMembership.objects.filter(person=self.member).exists())
