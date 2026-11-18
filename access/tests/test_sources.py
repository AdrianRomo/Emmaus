"""A role row never outlives, contradicts or lags behind the membership it came from (review of
wave 7; decision 0016, rules 5 and 7): deleted sources, rows that drifted, and the ``Person`` hook."""

from io import StringIO
from unittest import mock

from django.core.management import call_command

from access.models import RoleAssignment
from core.authz import has_scope
from groups import services as groups
from groups.access import managed_group_ids
from groups.models import Group, GroupMembership
from people.models import Person
from serving import services as serving
from serving.access import managed_team_ids
from serving.models import Team, TeamMembership
from serving.tests.test_serving import ServingFixture as Fixture


class Base(Fixture):
    """Gala (``leader``) guides ``group`` and coordinates ``team``."""

    def tearDown(self):
        pass  # these tests break the projection on purpose; each asserts its own end state

    def team_manage(self, user=None) -> bool:
        return has_scope(user or self.leader_user, self.church, "ministry", self.team.pk, "manage")

    def group_manage(self, user=None) -> bool:
        return has_scope(user or self.leader_user, self.church, "group", self.group.pk, "manage")

    def rows(self, source):
        return RoleAssignment.objects.filter(source_table=source, subject_user=self.leader_user)

    def assert_gala_holds_nothing(self):
        self.assertFalse(self.team_manage())
        self.assertFalse(self.group_manage())
        for scope, pk in (("ministry", self.team.pk), ("group", self.group.pk)):
            self.assertFalse(has_scope(self.leader_user, self.church, scope, pk, "view"))
        self.assertEqual(managed_team_ids(self.leader_user, self.church), [])
        self.assertEqual(managed_group_ids(self.leader_user, self.church), [])

    def assert_no_difference(self):
        out = StringIO()
        call_command("rebuild_role_assignments", "--check", stdout=out)
        self.assertIn("Sin diferencias", out.getvalue())


class DeletedSourceTests(Base):
    def test_a_deleted_team_membership_takes_its_role_with_it(self):
        self.assertTrue(self.team_manage())
        TeamMembership.objects.filter(team=self.team, person=self.leader).delete()
        self.assertFalse(self.rows(serving.SOURCE).exists())
        self.assertFalse(self.team_manage())
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.get(self.url("team", self.team.pk)).status_code, 403)
        self.assertEqual(self.client.post(self.url("date_create", self.team.pk), self.date_post()).status_code, 403)
        self.assert_no_difference()

    def test_a_deleted_group_membership_takes_its_role_with_it(self):
        self.assertTrue(self.group_manage())
        GroupMembership.objects.get(group=self.group, person=self.leader).delete()
        self.assertFalse(self.rows(groups.SOURCE).exists())
        self.assertFalse(self.group_manage())
        self.assert_no_difference()

    def test_undoing_link_people_leaves_no_role_for_the_person_created_again(self):
        """The documented undo deletes people; the memberships go by cascade. The account, with a
        person created again on its next visit, must not find the old roles waiting."""
        Person.objects.filter(pk=self.leader.pk).delete()
        self.assertFalse(TeamMembership.objects.filter(team=self.team, role="coordina").exists())
        self.assertFalse(RoleAssignment.objects.filter(subject_user=self.leader_user).exists())
        self._person(self.church, self.leader_user)
        self.assert_gala_holds_nothing()
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.get(self.url("home")).status_code, 200)
        self.assertEqual(self.client.get(self.url("team", self.team.pk)).status_code, 403)
        self.assertEqual(self.client.get(self.url("date_detail", self.day.pk)).status_code, 403)
        self.assert_no_difference()

    def test_a_deleted_team_or_group_leaves_no_row_for_its_id(self):
        team_id, group_id = self.team.pk, self.group.pk
        stray = RoleAssignment.objects.create(  # a row no membership stands for goes with the scope too
            subject_user=self.member_user, church=self.church, scope_type="ministry", scope_id=team_id, role="coordina",
            source_table="nadie.Nada", source_id=1,
        )
        Team.objects.filter(pk=team_id).delete()
        Group.objects.filter(pk=group_id).delete()
        self.assertFalse(RoleAssignment.objects.filter(scope_type="ministry", scope_id=team_id).exists())
        self.assertFalse(RoleAssignment.objects.filter(scope_type="group", scope_id=group_id).exists())
        self.assertFalse(RoleAssignment.objects.filter(pk=stray.pk).exists())
        self.assert_gala_holds_nothing()
        self.assert_no_difference()

    def test_a_deleted_account_leaves_no_row(self):
        user_id = self.leader_user.pk
        self.leader_user.delete()
        self.assertFalse(RoleAssignment.objects.filter(subject_user_id=user_id).exists())
        self.assertIsNone(Person.objects.get(pk=self.leader.pk).user_id)
        self.assert_no_difference()


class RowWithoutItsSourceTests(Base):
    """``has_scope`` itself: a row the receivers and the services failed to end grants nothing."""

    def restore(self, row_values):
        for values in row_values:
            RoleAssignment.objects.create(**values)

    def test_a_row_whose_membership_is_gone_grants_nothing(self):
        kept = list(
            RoleAssignment.objects.filter(subject_user=self.leader_user).values(
                "subject_user_id", "church_id", "scope_type", "scope_id", "role", "status", "start_at", "source_table", "source_id"
            )
        )
        self.assertEqual({row["scope_type"] for row in kept}, {"group", "ministry"})
        TeamMembership.objects.filter(person=self.leader).delete()
        GroupMembership.objects.filter(person=self.leader).delete()
        self.restore(kept)  # as if the delete had gone around the receivers (raw SQL, another process)
        self.assertEqual(RoleAssignment.objects.filter(subject_user=self.leader_user, status="active").count(), 2)
        self.assert_gala_holds_nothing()
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.get(self.url("team", self.team.pk)).status_code, 403)

    def test_a_row_still_active_for_a_membership_that_ended_grants_nothing(self):
        TeamMembership.objects.filter(person=self.leader).update(status="inactiva")
        GroupMembership.objects.filter(person=self.leader).update(status=GroupMembership.Status.INACTIVE)
        self.assertEqual(RoleAssignment.objects.filter(subject_user=self.leader_user, status="active").count(), 2)
        self.assert_gala_holds_nothing()

    def test_a_row_that_says_another_role_account_or_scope_than_its_membership_grants_nothing(self):
        TeamMembership.objects.filter(person=self.leader).update(role="sirve")
        GroupMembership.objects.filter(person=self.leader).update(role="integrante")
        self.assert_gala_holds_nothing()
        TeamMembership.objects.filter(person=self.leader).update(role="coordina")
        GroupMembership.objects.filter(person=self.leader).update(role="lider")
        self.assertTrue(self.team_manage() and self.group_manage())
        # The same rows handed to another account, or pointed at another team or group.
        RoleAssignment.objects.filter(subject_user=self.leader_user).update(subject_user=self.member_user)
        self.assertFalse(self.team_manage(self.member_user))
        self.assertFalse(self.group_manage(self.member_user))
        RoleAssignment.objects.filter(subject_user=self.member_user).update(subject_user=self.leader_user)
        RoleAssignment.objects.filter(subject_user=self.leader_user, scope_type="ministry").update(scope_id=self.other_team.pk)
        RoleAssignment.objects.filter(subject_user=self.leader_user, scope_type="group").update(scope_id=self.other_group.pk)
        self.assertFalse(has_scope(self.leader_user, self.church, "ministry", self.other_team.pk, "manage"))
        self.assertFalse(has_scope(self.leader_user, self.church, "group", self.other_group.pk, "manage"))

    def test_a_row_of_an_archived_team_grants_nothing(self):
        Team.objects.filter(pk=self.team.pk).update(status=Team.Status.ARCHIVED)
        self.assertFalse(self.team_manage())
        self.assertEqual(managed_team_ids(self.leader_user, self.church), [])

    def test_the_answer_is_still_one_query(self):
        for scope, pk in (("ministry", self.team.pk), ("group", self.group.pk)):
            with self.assertNumQueries(1):
                self.assertTrue(has_scope(self.leader_user, self.church, scope, pk, "manage"))


class PersonHookTests(Base):
    def test_saving_a_person_re_derives_roles_only_when_the_account_changed(self):
        person = Person.objects.get(pk=self.leader.pk)
        with mock.patch.object(serving, "sync_assignment") as team_sync, mock.patch.object(groups, "sync_assignment") as group_sync:
            person.phone = "55 0000 0000"
            person.save()
            person.save(update_fields=["phone"])
            person.user = self.leader_user  # the same account again
            person.save()
            self.assertEqual((team_sync.call_count, group_sync.call_count), (0, 0))
            person.user = None
            person.save(update_fields=["user"])
            self.assertEqual((team_sync.call_count, group_sync.call_count), (1, 1))
            person.user = self.leader_user
            person.save()
            self.assertEqual((team_sync.call_count, group_sync.call_count), (2, 2))

    def test_a_stale_copy_of_the_person_is_judged_against_the_database(self):
        stale = Person.objects.get(pk=self.leader.pk)   # loaded while the account was linked
        fresh = Person.objects.get(pk=self.leader.pk)
        fresh.user = None
        fresh.save()                                    # unlinked by another request: the roles are gone
        self.assertFalse(RoleAssignment.objects.filter(subject_user=self.leader_user).exists())
        stale.save()  # writes the account back: in memory nothing changed, in the database it did
        self.assertTrue(self.team_manage() and self.group_manage())
        self.assert_no_difference()

    def test_a_snapshot_taken_before_a_service_ended_the_role_cannot_write_it_back(self):
        """The reviewer's replay: the hook read the memberships, then a service ended the role, then
        the hook wrote. The write derives from the rows as they are when it happens."""
        team_snapshot = list(TeamMembership.objects.filter(person=self.leader).select_related("team", "person"))
        group_snapshot = list(GroupMembership.objects.filter(person=self.leader).select_related("group", "person"))
        serving.remove_member(self.team, self.leader, self.admin)
        groups.end_membership(group_snapshot[0], self.admin)
        for membership in team_snapshot:
            serving.sync_assignment(membership)
        for membership in group_snapshot:
            groups.sync_assignment(membership)
        self.assertFalse(RoleAssignment.objects.filter(subject_user=self.leader_user, status="active").exists())
        self.assert_gala_holds_nothing()
        self.assert_no_difference()

    def test_the_same_replay_for_a_role_changed_and_for_an_archived_team(self):
        snapshot = list(TeamMembership.objects.filter(person=self.leader).select_related("team", "person"))
        serving.set_member(self.team, self.leader, self.admin, role="sirve")
        serving.sync_assignment(snapshot[0])
        self.assertEqual(self.rows(serving.SOURCE).get().role, "sirve")
        serving.set_member(self.team, self.leader, self.admin, role="coordina")
        snapshot = list(TeamMembership.objects.filter(person=self.leader).select_related("team", "person"))
        serving.archive_team(self.team, self.admin)
        serving.sync_assignment(snapshot[0])
        self.assertEqual(self.rows(serving.SOURCE).get().status, "inactive")
        self.assert_no_difference()

    def test_a_membership_deleted_since_the_snapshot_leaves_no_row(self):
        snapshot = TeamMembership.objects.select_related("team", "person").get(team=self.team, person=self.leader)
        TeamMembership.objects.filter(pk=snapshot.pk).delete()
        self.assertIsNone(serving.sync_assignment(snapshot))
        self.assertFalse(self.rows(serving.SOURCE).exists())


class AccountLinkedMeanwhileTests(Base):
    def test_a_new_membership_reads_the_account_when_it_writes_not_when_the_view_loaded_the_person(self):
        loaded = Person.objects.get(pk=self.no_account.pk)  # what the view holds: no account yet
        Person.objects.filter(pk=loaded.pk).update(user=self.outsider)  # linked by another request meanwhile
        membership = serving.set_member(self.other_team, loaded, self.admin, role="coordina")
        row = RoleAssignment.objects.get(source_table=serving.SOURCE, source_id=membership.pk)
        self.assertEqual((row.subject_user, row.role, row.status), (self.outsider, "coordina", "active"))
        self.assertTrue(has_scope(self.outsider, self.church, "ministry", self.other_team.pk, "manage"))
        group_membership = groups.set_membership(self.other_group, loaded, self.admin, role="lider")
        self.assertTrue(RoleAssignment.objects.filter(source_table=groups.SOURCE, source_id=group_membership.pk).exists())
        self.assert_no_difference()
