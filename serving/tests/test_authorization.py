"""Servir through ``core.authz.has_scope`` (decisions 0003 and 0016): who may manage a team is the
``ministry`` scope of ``access.RoleAssignment``, projected from ``serving.TeamMembership``."""

from datetime import timedelta
from io import StringIO

from django.core.management import call_command

from access.models import RoleAssignment
from core.authz import has_scope
from events.models import EventMembership, MembershipRole
from people.models import Person
from serving import services
from serving.access import managed_team_ids
from serving.models import TeamMembership

from .test_serving import ServingFixture as Fixture


class TeamScopeTests(Fixture):
    def manages(self, user, team, church=None) -> bool:
        return has_scope(user, church or self.church, "ministry", team.pk, "manage")

    def test_who_coordinates_manages_that_team_and_who_serves_does_not(self):
        self.assertTrue(self.manages(self.leader_user, self.team))
        self.assertTrue(has_scope(self.leader_user, self.church, "ministry", self.team.pk, "view"))
        self.assertFalse(self.manages(self.member_user, self.team))
        self.assertTrue(has_scope(self.member_user, self.church, "ministry", self.team.pk, "view"))
        self.assertFalse(has_scope(self.coord1, self.church, "ministry", self.team.pk, "view"))
        self.assertEqual(managed_team_ids(self.leader_user, self.church), [self.team.pk])
        self.assertEqual(managed_team_ids(self.member_user, self.church), [])

    def test_a_coordinator_cannot_manage_another_team(self):
        other_day = services.save_date(self.other_team, self.admin, date=self.week, what="Ensayo")
        self.assertFalse(self.manages(self.leader_user, self.other_team))
        self.client.force_login(self.leader_user)
        for url in (self.url("team", self.other_team.pk), self.url("date_create", self.other_team.pk),
                    self.url("date_detail", other_day.pk), self.url("date_delete", other_day.pk)):
            self.assertEqual(self.client.get(url).status_code, 403, url)
            self.assertEqual(self.client.post(url, self.date_post()).status_code, 403, url)
        self.assertTrue(type(other_day).objects.filter(pk=other_day.pk, what="Ensayo").exists())
        # Serving in the other team does not make them its coordinator either.
        services.set_member(self.other_team, self.leader, self.admin)
        self.assertFalse(self.manages(self.leader_user, self.other_team))
        self.assertEqual(self.client.get(self.url("team", self.other_team.pk)).status_code, 403)

    def test_a_coordinator_cannot_manage_a_team_of_another_church(self):
        for church in (self.church, self.church_b):
            self.assertFalse(self.manages(self.leader_user, self.team_b, church))
        self.assertFalse(self.manages(self.leader_user, self.team, self.church_b))
        self.assertFalse(self.manages(self.member_b_user, self.team, self.church))
        self.assertTrue(self.manages(self.member_b_user, self.team_b, self.church_b))
        self.client.force_login(self.leader_user)
        for url in (self.url("team", self.team_b.pk), self.url("date_create", self.team_b.pk),
                    self.url("date_detail", self.day_b.pk), self.url("date_delete", self.day_b.pk)):
            self.assertEqual(self.client.get(url).status_code, 404, url)
            self.assertEqual(self.client.post(url, self.date_post()).status_code, 404, url)

    def test_a_coordinator_cannot_manage_an_archived_team(self):
        services.archive_team(self.team, self.admin)
        self.assertFalse(self.manages(self.leader_user, self.team))
        self.assertFalse(has_scope(self.leader_user, self.church, "ministry", self.team.pk, "view"))
        self.assertEqual(managed_team_ids(self.leader_user, self.church), [])
        self.assertEqual(services.coordinated_teams(self.leader), [])
        self.client.force_login(self.leader_user)
        for url in (self.url("team", self.team.pk), self.url("date_create", self.team.pk),
                    self.url("date_detail", self.day.pk), self.url("date_delete", self.day.pk)):
            self.assertEqual(self.client.get(url).status_code, 404, url)
            self.assertEqual(self.client.post(url, self.date_post()).status_code, 404, url)
        self.assertTrue(type(self.day).objects.filter(pk=self.day.pk).exists())

    def test_an_event_role_never_grants_a_team(self):
        # Staff of the camp, an event administrator included, and people who only serve in the team.
        event_admin = self.asked_user
        EventMembership.objects.create(event=self.event, user=event_admin, role=MembershipRole.ADMIN)
        services.set_member(self.team, self.coord_person, self.admin)
        for user in (self.pastor, self.care, self.tutor1, self.coord1, self.coord2, self.outsider, event_admin):
            with self.subTest(user=user.email):
                self.assertFalse(self.manages(user, self.team))
                self.assertFalse(self.manages(user, self.other_team))
        for user in (self.coord1, event_admin):  # people of the church who serve in the team
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url("team", self.team.pk)).status_code, 403)
            self.assertEqual(self.client.post(self.url("date_create", self.team.pk), self.date_post()).status_code, 403)
            self.assertEqual(self.client.get(self.url("date_detail", self.day.pk)).status_code, 403)
        # And the other way: coordinating a team is no event role.
        self.assertFalse(has_scope(self.leader_user, self.church, "event", self.event.pk, "view"))
        self.assertFalse(has_scope(self.leader_user, self.church, "church", self.church.pk, "view"))
        # Nor a group role, even where a group and a team share an id (the scope type tells them apart).
        services.set_member(self.other_team, self.member, self.admin, role="coordina")
        for group in (self.group, self.other_group):
            self.assertFalse(has_scope(self.member_user, self.church, "group", group.pk, "view"))
        self.assertFalse(has_scope(self.leader_user, self.church, "ministry", self.other_team.pk, "view"))

    def test_the_administration_manages_every_team_of_its_church_through_its_church_role(self):
        # Nothing is implied into the team scope (as with groups): the views name the church role.
        self.assertFalse(self.manages(self.admin, self.team))
        self.assertFalse(RoleAssignment.objects.filter(subject_user=self.admin, scope_type="ministry").exists())
        self.client.force_login(self.admin)
        for team in (self.team, self.other_team):
            self.assertEqual(self.client.get(self.url("team", team.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("date_detail", self.day.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("team", self.team_b.pk)).status_code, 404)
        self.client.force_login(self.admin_b)
        self.assertEqual(self.client.get(self.url("team", self.team.pk)).status_code, 404)

    def test_taking_the_coordinator_role_away_revokes_at_once(self):
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.get(self.url("team", self.team.pk)).status_code, 200)
        services.set_member(self.team, self.leader, self.admin, role="sirve")
        self.assertFalse(self.manages(self.leader_user, self.team))
        self.assertEqual(self.client.get(self.url("team", self.team.pk)).status_code, 403)
        self.assertEqual(self.client.post(self.url("date_create", self.team.pk), self.date_post()).status_code, 403)
        self.assertEqual(self.client.get(self.url("date_detail", self.day.pk)).status_code, 403)
        self.assertNotContains(self.client.get(self.url("home")), self.url("team", self.team.pk))

        services.set_member(self.team, self.leader, self.admin, role="coordina")
        self.assertTrue(self.manages(self.leader_user, self.team))
        self.assertContains(self.client.get(self.url("home")), self.url("team", self.team.pk))
        services.remove_member(self.team, self.leader, self.admin)
        self.assertFalse(self.manages(self.leader_user, self.team))
        self.assertFalse(has_scope(self.leader_user, self.church, "ministry", self.team.pk, "view"))
        self.assertEqual(self.client.get(self.url("team", self.team.pk)).status_code, 403)

    def test_the_role_follows_the_account_of_the_person(self):
        membership = TeamMembership.objects.get(team=self.team, person=self.leader)
        self.leader.user = None
        self.leader.save()
        self.assertFalse(RoleAssignment.objects.filter(source_table=services.SOURCE, source_id=membership.pk).exists())
        self.assertFalse(self.manages(self.leader_user, self.team))
        self.assert_projection()
        self.leader.user = self.leader_user
        self.leader.save()
        self.assertTrue(self.manages(self.leader_user, self.team))
        self.leader_user.is_active = False
        self.leader_user.save()
        self.assertFalse(self.manages(self.leader_user, self.team))
        self.assertEqual(managed_team_ids(self.leader_user, self.church), [])

    def test_a_coordinator_without_an_account_holds_no_row(self):
        membership = services.set_member(self.other_team, self.no_account, self.admin, role="coordina")
        self.assertFalse(RoleAssignment.objects.filter(source_table=services.SOURCE, source_id=membership.pk).exists())
        self.assertEqual(services.coordinated_teams(self.no_account), [])


class ProjectionTests(Fixture):
    def snapshot(self) -> set:
        return set(
            RoleAssignment.objects.values_list(
                "subject_user_id", "church_id", "scope_type", "scope_id", "role", "status", "start_at", "end_at",
                "source_table", "source_id",
            )
        )

    def test_every_service_call_leaves_the_projection_equal_to_the_rows(self):
        self.assert_projection()
        team = services.save_team(self.church, self.admin, name="Estacionamiento")
        self.assert_projection()
        services.set_member(team, self.member, self.admin, role="coordina")
        self.assert_projection()
        services.set_member(team, self.coord_person, self.admin)
        self.assert_projection()
        services.set_member(team, self.no_account, self.admin, role="coordina")
        self.assert_projection()
        services.set_member(team, self.member, self.admin, role="sirve")
        self.assert_projection()
        services.set_member(team, self.coord_person, self.admin, role="coordina")
        self.assert_projection()
        services.save_team(self.church, self.admin, team=team, name="Estacionamiento y accesos")
        self.assert_projection()
        day = services.save_date(team, self.coord1, date=self.week, what="Acomodar coches")
        self.assert_projection()
        assignment = services.assign(day, self.member, self.coord1)
        services.respond(assignment, True, self.member_user)
        services.unassign(day, self.member, self.coord1)
        busy = services.set_unavailable(self.member, self.week + timedelta(days=2), self.member_user)
        services.clear_unavailable(busy, self.member_user)
        services.save_date(team, self.coord1, service_date=day, date=self.week + timedelta(days=1), what="Acomodar coches")
        self.assert_projection()
        services.remove_member(team, self.member, self.admin)
        self.assert_projection()
        services.remove_member(team, self.member, self.admin)  # twice changes nothing
        self.assert_projection()
        services.set_member(team, self.member, self.admin, role="coordina")  # back in
        self.assert_projection()
        services.delete_date(day, self.coord1)
        self.assert_projection()
        services.archive_team(team, self.admin)
        self.assert_projection()
        services.archive_team(team, self.admin)
        self.assert_projection()
        self.assertFalse(
            RoleAssignment.objects.filter(scope_type="ministry", scope_id=team.pk, status="active").exists()
        )
        self.assertEqual(RoleAssignment.objects.filter(scope_type="ministry", scope_id=team.pk).count(), 2)

    def test_a_rebuild_from_scratch_equals_what_the_services_maintained(self):
        services.set_member(self.other_team, self.member, self.admin, role="coordina")
        services.set_member(self.other_team, self.no_account, self.admin, role="coordina")
        services.set_member(self.other_team, self.asked, self.admin)
        services.set_member(self.team, self.leader, self.admin, role="sirve")
        services.remove_member(self.team, self.asked, self.admin)
        services.archive_team(self.other_team, self.admin)
        maintained = self.snapshot()
        self.assertTrue(any(row[2] == "ministry" for row in maintained))
        self.assertTrue(any(row[2] == "group" for row in maintained))

        RoleAssignment.objects.all().delete()
        out = StringIO()
        call_command("rebuild_role_assignments", "--apply", stdout=out)
        self.assertEqual(self.snapshot(), maintained)
        self.assertIn(f"Se corrigieron {len(maintained)} diferencia", out.getvalue())
        again = StringIO()
        call_command("rebuild_role_assignments", "--apply", stdout=again)
        self.assertIn("Se corrigieron 0 diferencia", again.getvalue())
        self.assertEqual(self.snapshot(), maintained)
        for name in Person.objects.values_list("first_name", flat=True):
            self.assertNotIn(name, out.getvalue())

    def test_check_finds_drift_in_a_team_role_and_apply_repairs_it(self):
        row = RoleAssignment.objects.get(subject_user=self.member_user, scope_type="ministry", scope_id=self.team.pk)
        RoleAssignment.objects.filter(pk=row.pk).update(role="coordina")
        stray = RoleAssignment.objects.create(
            subject_user=self.member_user, church=self.church, scope_type="ministry", scope_id=self.other_team.pk,
            role="coordina", source_table="nadie.Nada", source_id=1,
        )
        self.assertTrue(has_scope(self.member_user, self.church, "ministry", self.team.pk, "manage"))
        with self.assertRaises(SystemExit):
            call_command("rebuild_role_assignments", "--check", stdout=StringIO())
        out = StringIO()
        call_command("rebuild_role_assignments", "--apply", stdout=out)
        self.assertIn("2 diferencia", out.getvalue())
        self.assertFalse(RoleAssignment.objects.filter(pk=stray.pk).exists())
        for team in (self.team, self.other_team):
            self.assertFalse(has_scope(self.member_user, self.church, "ministry", team.pk, "manage"))
