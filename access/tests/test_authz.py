from datetime import timedelta

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.utils import timezone

from access.models import RoleAssignment
from core.access import get_user_role
from core.authz import SESSION_KEY, current_church, has_scope, switch_church
from core.models import Role, UserChurchRole
from core.services import assign_role
from events.access import resolve_access
from events.models import EventMembership, MembershipRole
from events.tests.base import make_user
from groups import services
from groups.models import GroupMembership
from groups.tests.base import CommunityFixture


class ParityTests(CommunityFixture):
    """Church and event scopes answer exactly what the legacy chokepoints answer (decision 0003, rule 8)."""

    def users(self):
        church_pastor = make_user("pastor-iglesia@prueba.mx")
        assign_role(church_pastor, self.church, Role.PASTOR)
        removed_pastor = make_user("pastor-fuera@prueba.mx")
        assign_role(removed_pastor, self.church, Role.PASTOR)
        EventMembership.objects.create(event=self.event, user=removed_pastor, role=MembershipRole.PASTOR, is_active=False)
        root = make_user("root@prueba.mx")
        root.is_superuser = True
        root.save()
        inactive = make_user("inactivo@prueba.mx")
        assign_role(inactive, self.church, Role.CHURCH_ADMIN)
        inactive.is_active = False
        inactive.save()
        return [
            self.admin, self.pastor, self.care, self.tutor1, self.tutor2, self.coord1, self.coord2, self.coord3,
            self.outsider, self.other_coord, self.member_user, self.leader_user, church_pastor, removed_pastor,
            root, inactive, AnonymousUser(),
        ]

    def test_event_scope_equals_resolve_access(self):
        for user in self.users():
            for event in (self.event, self.other_event):
                access = resolve_access(user, event)
                with self.subTest(user=getattr(user, "email", "anon"), event=event.slug):
                    self.assertEqual(has_scope(user, self.church, "event", event.pk, "view"), access is not None)
                    self.assertEqual(
                        has_scope(user, self.church, "event", event.pk, "manage"), bool(access and access.can_manage_event)
                    )

    def test_church_scope_equals_the_church_role(self):
        for user in self.users():
            usable = getattr(user, "is_authenticated", False) and user.is_active
            record = get_user_role(user) if usable else None
            with self.subTest(user=getattr(user, "email", "anon")):
                self.assertEqual(has_scope(user, self.church, "church", self.church.pk, "view"), record is not None)
                self.assertEqual(
                    has_scope(user, self.church, "church", self.church.pk, "manage"),
                    bool(record and record.role == Role.CHURCH_ADMIN),
                )


class DeniedPathTests(CommunityFixture):
    def test_closed_vocabulary(self):
        with self.assertRaises(ValueError):
            has_scope(self.admin, self.church, "sede", 1, "view")
        with self.assertRaises(ValueError):
            has_scope(self.admin, self.church, "church", self.church.pk, "delete")

    def test_scope_of_another_church_is_denied_never_an_error(self):
        self.assertFalse(has_scope(self.admin, self.church, "church", self.church_b.pk, "view"))
        self.assertFalse(has_scope(self.admin, self.church_b, "church", self.church_b.pk, "view"))
        self.assertFalse(has_scope(self.admin, self.church, "event", self.event_b.pk, "view"))
        self.assertFalse(has_scope(self.admin_b, self.church_b, "event", self.event.pk, "manage"))
        self.assertFalse(has_scope(self.leader_user, self.church_b, "group", self.group.pk, "manage"))
        self.assertFalse(has_scope(self.leader_user, self.church, "group", self.group_b.pk, "view"))
        self.assertFalse(has_scope(self.admin, None, "church", self.church.pk, "view"))
        self.assertFalse(has_scope(self.admin, self.church, "group", "no-es-un-id", "view"))
        self.assertFalse(has_scope(self.admin, self.church, "event", 999_999, "view"))

    def test_nothing_is_implied_into_a_group(self):
        """A church administrator, a pastor, a tutor and a former coordinator hold nothing in a group."""
        for user in (self.admin, self.pastor, self.care, self.tutor1, self.coord1, self.member_user):
            with self.subTest(user=user.email):
                self.assertFalse(has_scope(user, self.church, "group", self.group.pk, "view"))
                self.assertFalse(has_scope(user, self.church, "group", self.group.pk, "manage"))

    def test_a_group_role_implies_nothing_outside_its_group(self):
        self.assertTrue(has_scope(self.leader_user, self.church, "group", self.group.pk, "manage"))
        self.assertFalse(has_scope(self.leader_user, self.church, "group", self.other_group.pk, "view"))
        self.assertFalse(has_scope(self.leader_user, self.church, "church", self.church.pk, "view"))
        self.assertFalse(has_scope(self.leader_user, self.church, "event", self.event.pk, "view"))
        self.assertIsNone(resolve_access(self.leader_user, self.event))
        self.assertFalse(UserChurchRole.objects.filter(user=self.leader_user).exists())

    def test_member_views_and_does_not_manage(self):
        services.set_membership(self.group, self.member, self.admin)
        self.assertTrue(has_scope(self.member_user, self.church, "group", self.group.pk, "view"))
        self.assertFalse(has_scope(self.member_user, self.church, "group", self.group.pk, "manage"))

    def test_ended_expired_and_future_roles_grant_nothing(self):
        row = RoleAssignment.objects.get(subject_user=self.leader_user, scope_id=self.group.pk)
        now = timezone.now()
        for change in ({"status": "inactive"}, {"end_at": now - timedelta(minutes=1)}, {"start_at": now + timedelta(days=1)}):
            RoleAssignment.objects.filter(pk=row.pk).update(status="active", end_at=None, start_at=now - timedelta(days=1))
            self.assertTrue(has_scope(self.leader_user, self.church, "group", self.group.pk, "manage"))
            RoleAssignment.objects.filter(pk=row.pk).update(**change)
            with self.subTest(change=list(change)):
                self.assertFalse(has_scope(self.leader_user, self.church, "group", self.group.pk, "view"))

    def test_leaving_the_group_ends_the_role_at_once(self):
        membership = GroupMembership.objects.get(group=self.group, person=self.leader)
        services.end_membership(membership, self.admin)
        self.assertFalse(has_scope(self.leader_user, self.church, "group", self.group.pk, "view"))

    def test_inactive_account_is_denied(self):
        self.leader_user.is_active = False
        self.leader_user.save()
        self.assertFalse(has_scope(self.leader_user, self.church, "group", self.group.pk, "manage"))


class J3Tests(CommunityFixture):
    def test_one_user_with_roles_in_two_churches_and_one_event_sees_no_leakage(self):
        x = make_user("equis@prueba.mx", "Equis", "Dos Iglesias")
        assign_role(x, self.church, Role.PARTICIPANT)  # the older row
        assign_role(x, self.church_b, Role.CHURCH_ADMIN)
        membership = EventMembership.objects.create(event=self.other_event, user=x, role=MembershipRole.COORDINATOR)
        a, b = self.church, self.church_b

        self.assertTrue(has_scope(x, b, "church", b.pk, "view"))
        self.assertTrue(has_scope(x, b, "church", b.pk, "manage"))
        self.assertTrue(has_scope(x, a, "church", a.pk, "view"))
        self.assertFalse(has_scope(x, a, "church", a.pk, "manage"))
        self.assertTrue(has_scope(x, a, "event", self.other_event.pk, "view"))
        self.assertFalse(has_scope(x, a, "event", self.other_event.pk, "manage"))
        self.assertFalse(has_scope(x, a, "event", self.event.pk, "view"))
        self.assertFalse(has_scope(x, b, "event", self.other_event.pk, "view"))  # asked under the wrong church
        self.assertTrue(has_scope(x, b, "event", self.event_b.pk, "manage"))
        self.assertFalse(has_scope(x, a, "event", self.event_b.pk, "manage"))
        self.assertFalse(has_scope(x, a, "group", self.group.pk, "view"))
        self.assertFalse(has_scope(x, b, "group", self.group_b.pk, "view"))

        request = RequestFactory().get("/")
        request.user, request.session = x, {}
        self.assertEqual(current_church(request), a)
        self.assertEqual(switch_church(request, b.pk), b)
        self.assertEqual(current_church(request), b)
        other = type(a).objects.create(name="Iglesia C", slug="iglesia-c")
        self.assertIsNone(switch_church(request, other.pk))
        request.session[SESSION_KEY] = other.pk  # forged
        self.assertEqual(current_church(request), a)
        self.assertNotIn(SESSION_KEY, request.session)

        membership.is_active = False
        membership.save()
        self.assertFalse(has_scope(x, a, "event", self.other_event.pk, "view"))
        self.assertTrue(has_scope(x, a, "church", a.pk, "view"))
        self.assertTrue(has_scope(x, b, "church", b.pk, "manage"))
        self.assertEqual(UserChurchRole.objects.filter(user=x).count(), 2)
        self.assertEqual(get_user_role(x).church, a)

    def test_a_person_without_a_role_belongs_to_their_church_only(self):
        request = RequestFactory().get("/")
        request.user, request.session = self.member_user, {SESSION_KEY: self.church_b.pk}
        self.assertEqual(current_church(request), self.church)
        request.user = self.coord2  # an event role and no person: no church for the new modules
        self.assertIsNone(current_church(request))
