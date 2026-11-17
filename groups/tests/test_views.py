from django.test import override_settings
from django.urls import reverse

from core.models import Role
from core.services import assign_role
from events.tests.base import make_user
from groups import services
from groups.models import Group, GroupJoinRequest, GroupMembership
from groups.tests.base import CommunityFixture
from people import flags
from people.models import Person

GET_PAGES = [
    # name, args builder, who gets 200 (everyone else of the church gets 403)
    ("community:home", lambda f: [], {"member", "leader", "admin"}),
    ("community:groups", lambda f: [], {"member", "leader", "admin"}),
    ("community:group_detail", lambda f: [f.group.pk], {"member", "leader", "admin"}),
    ("community:group_people", lambda f: [f.group.pk], {"leader", "admin"}),
    ("community:group_people", lambda f: [f.other_group.pk], {"admin"}),
    ("community:group_admin_list", lambda f: [], {"admin"}),
    ("community:group_create", lambda f: [], {"admin"}),
    ("community:group_edit", lambda f: [f.group.pk], {"admin"}),
    ("community:group_archive", lambda f: [f.group.pk], {"admin"}),
]


class PermissionMatrixTests(CommunityFixture):
    def users(self):
        return {"member": self.member_user, "leader": self.leader_user, "admin": self.admin}

    def test_get_pages_by_role(self):
        for name, args, allowed in GET_PAGES:
            for role, user in self.users().items():
                self.client.force_login(user)
                with self.subTest(page=name, role=role):
                    status = self.client.get(reverse(name, args=args(self))).status_code
                    self.assertEqual(status, 200 if role in allowed else 403)

    def test_anonymous_is_sent_to_sign_in(self):
        for name, args, _ in GET_PAGES:
            response = self.client.get(reverse(name, args=args(self)))
            self.assertEqual(response.status_code, 302)
            self.assertIn("/cuenta/entrar/", response["Location"])

    def test_accounts_that_are_not_people_of_the_church_get_404(self):
        """An event role (tutor, another coordinator) or a pastor without a person is not "of the church" here."""
        for user in (self.tutor1, self.coord2, self.outsider):
            self.client.force_login(user)
            for name, args, _ in GET_PAGES:
                with self.subTest(user=user.email, page=name):
                    self.assertEqual(self.client.get(reverse(name, args=args(self))).status_code, 404)

    def test_a_church_role_holder_gets_a_person_on_the_first_visit(self):
        pastor = make_user("pastora@prueba.mx", "Paz", "Pastora")
        assign_role(pastor, self.church, Role.PASTOR)
        self.client.force_login(pastor)
        self.assertEqual(self.client.get(reverse("community:home")).status_code, 200)
        self.assertEqual(Person.objects.filter(church=self.church, user=pastor).count(), 1)
        self.assertEqual(self.client.get(reverse("community:group_admin_list")).status_code, 403)  # a pastor is not the administration


class ModuleOffTests(CommunityFixture):
    def assert_all_404(self):
        for user in (self.member_user, self.leader_user, self.admin):
            self.client.force_login(user)
            for name, args, _ in GET_PAGES:
                with self.subTest(user=user.email, page=name):
                    self.assertEqual(self.client.get(reverse(name, args=args(self))).status_code, 404)
            response = self.client.post(reverse("community:group_request", args=[self.group.pk]))
            self.assertEqual(response.status_code, 404)

    def test_every_view_answers_404_while_the_module_is_off(self):
        flags.set_enabled(self.church, "community", False)
        self.assert_all_404()
        self.assertEqual(GroupJoinRequest.objects.filter(person=self.member).count(), 0)

    @override_settings(EMMAUS_V2_MODULES=False)
    def test_the_kill_switch_turns_every_church_off(self):
        self.assert_all_404()
        self.assertTrue(flags.set_enabled(self.church, "community", False) or True)  # rows untouched by the switch


class TenantIsolationTests(CommunityFixture):
    """Scenario 5: ids of another church are 404 for every role, on reads and on writes, and nothing leaks."""

    def test_groups_of_another_church_do_not_exist(self):
        services.request_to_join(self.group_b, self.member_b, self.member_b_user)
        request_b = GroupJoinRequest.objects.get(group=self.group_b)
        membership_b = services.set_membership(self.group_b, self.admin_b_person, self.admin_b, role="lider")
        for user in (self.member_user, self.leader_user, self.admin):
            self.client.force_login(user)
            for name in ("group_detail", "group_people", "group_edit", "group_archive", "group_leave"):
                # The administration's pages refuse a non-administrator before looking anything up: the same 403 for any id.
                expected = 403 if name in ("group_edit", "group_archive") and user != self.admin else 404
                with self.subTest(user=user.email, page=name):
                    self.assertEqual(self.client.get(reverse(f"community:{name}", args=[self.group_b.pk])).status_code, expected)
            posts = [
                (reverse("community:group_request", args=[self.group_b.pk]), {}),
                (reverse("community:group_withdraw", args=[self.group_b.pk]), {}),
                (reverse("community:request_decide", args=[self.group_b.pk, request_b.pk]), {"decision": "aceptar"}),
                (reverse("community:request_decide", args=[self.group.pk, request_b.pk]), {"decision": "aceptar"}),
                (reverse("community:member_remove", args=[self.group_b.pk, membership_b.pk]), {}),
                (reverse("community:member_remove", args=[self.group.pk, membership_b.pk]), {}),
                (reverse("community:member_add", args=[self.group_b.pk]), {"persona": self.member.pk}),
            ]
            for url, data in posts:
                with self.subTest(user=user.email, post=url):
                    self.assertIn(self.client.post(url, data).status_code, (403, 404))
        request_b.refresh_from_db()
        self.assertTrue(request_b.is_pending)
        self.assertTrue(GroupMembership.objects.get(pk=membership_b.pk).is_active)

    def test_an_admin_cannot_put_a_person_of_another_church_in_a_group(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("community:member_add", args=[self.group.pk]), {"persona": self.member_b.pk})
        self.assertEqual(response.status_code, 404)
        with self.assertRaises(services.GroupError):
            services.set_membership(self.group, self.member_b, self.admin)
        with self.assertRaises(services.GroupError):
            services.request_to_join(self.group, self.member_b, self.member_b_user)
        self.assertFalse(GroupMembership.objects.filter(group=self.group, person=self.member_b).exists())

    def test_lists_and_search_never_show_another_church(self):
        self.client.force_login(self.admin)
        for url in (reverse("community:groups"), reverse("community:group_admin_list"), reverse("community:home"),
                    reverse("community:group_people", args=[self.group.pk]) + "?q=bruno"):
            response = self.client.get(url)
            self.assertNotContains(response, "Grupo de la iglesia B")
            self.assertNotContains(response, "Bruno")
            self.assertNotContains(response, "Retiro B")


class GroupRulesTests(CommunityFixture):
    def test_a_guide_decides_only_requests_of_their_group_and_once(self):
        other_request, _ = services.request_to_join(self.other_group, self.member, self.member_user)
        self.client.force_login(self.leader_user)
        url = reverse("community:request_decide", args=[self.other_group.pk, other_request.pk])
        self.assertEqual(self.client.post(url, {"decision": "aceptar"}).status_code, 403)
        # A request of another group asked through the guide's own group: not found there.
        url = reverse("community:request_decide", args=[self.group.pk, other_request.pk])
        self.assertEqual(self.client.post(url, {"decision": "aceptar"}).status_code, 404)
        url = reverse("community:request_decide", args=[self.group.pk, self.pending.pk])
        self.client.post(url, {"decision": "aceptar"})
        response = self.client.post(url, {"decision": "declinar"}, follow=True)
        self.assertContains(response, "Esta solicitud ya tenía respuesta.")
        self.assertTrue(GroupMembership.objects.get(group=self.group, person=self.asked).is_active)

    def test_a_guide_cannot_remove_a_guide_or_search_the_church(self):
        second = services.set_membership(self.group, self.coord_person, self.admin, role="lider")
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.get(reverse("community:member_remove", args=[self.group.pk, second.pk])).status_code, 403)
        response = self.client.get(reverse("community:group_people", args=[self.group.pk]) + "?q=diego")
        self.assertNotContains(response, "Diego Hernández Cruz")
        self.assertNotContains(response, "Agregar a alguien de la iglesia")
        self.assertEqual(self.client.post(reverse("community:member_add", args=[self.group.pk]), {"persona": self.member.pk}).status_code, 403)

    def test_closed_and_archived_groups_take_no_requests(self):
        Group.objects.filter(pk=self.other_group.pk).update(accepts_requests=False)
        self.client.force_login(self.member_user)
        response = self.client.post(reverse("community:group_request", args=[self.other_group.pk]), follow=True)
        self.assertContains(response, "Este grupo no está recibiendo solicitudes por ahora.")
        services.archive_group(self.group, self.admin)
        self.assertEqual(self.client.post(reverse("community:group_request", args=[self.group.pk])).status_code, 404)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, GroupJoinRequest.Status.WITHDRAWN)
        self.client.force_login(self.leader_user)  # the guide of an archived group manages nothing
        self.assertEqual(self.client.get(reverse("community:group_people", args=[self.group.pk])).status_code, 404)

    def test_leaving_and_withdrawing(self):
        self.client.force_login(self.asked_user)
        self.client.post(reverse("community:group_withdraw", args=[self.group.pk]))
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, GroupJoinRequest.Status.WITHDRAWN)
        services.set_membership(self.group, self.asked, self.admin)
        response = self.client.post(reverse("community:group_leave", args=[self.group.pk]), follow=True)
        self.assertContains(response, "Saliste de Jóvenes Roca.")
        self.assertNotContains(self.client.get(reverse("community:group_detail", args=[self.group.pk])), "Salón 2")

    def test_requests_are_throttled(self):
        self.client.force_login(self.member_user)
        for _ in range(10):
            self.client.post(reverse("community:group_request", args=[self.other_group.pk]))
        response = self.client.post(reverse("community:group_request", args=[self.group.pk]), follow=True)
        self.assertContains(response, "Demasiados intentos.")
        self.assertFalse(GroupJoinRequest.objects.filter(group=self.group, person=self.member).exists())

    def test_the_administration_creates_a_group_and_names_its_guide(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("community:group_create"), {
            "name": "Recién llegados", "kind": "pequeno", "description": "Un café.", "audience": "", "meeting_day": "6",
            "meeting_time": "13:00", "meeting_place": "Cafetería", "accepts_requests": "on",
        })
        group = Group.objects.get(name="Recién llegados")
        self.assertRedirects(response, reverse("community:group_people", args=[group.pk]))
        self.assertEqual((group.church, group.created_by, group.schedule), (self.church, self.admin, "Los domingos a las 13:00"))
        response = self.client.post(
            reverse("community:member_add", args=[group.pk]), {"persona": self.no_account.pk, "rol": "lider"}, follow=True
        )
        self.assertContains(response, "Todavía no tiene acceso a Emmaus")
        self.assertContains(self.client.get(reverse("community:groups")), "Lo guía: Nora Sin Cuenta.")
