from io import StringIO

from django.core.management import call_command

from access.models import RoleAssignment
from groups import services
from groups.tests.base import CommunityFixture
from people.services import grant_access


class ProjectionTests(CommunityFixture):
    def check(self) -> str:
        out = StringIO()
        call_command("rebuild_role_assignments", "--check", stdout=out)
        return out.getvalue()

    def test_services_keep_the_projection_equal_to_its_source(self):
        services.set_membership(self.group, self.member, self.admin)
        services.decide_request(self.pending, self.leader_user, accept=True)
        services.archive_group(self.other_group, self.admin)
        self.assertIn("Sin diferencias", self.check())

    def test_a_person_without_an_account_holds_no_row_until_they_get_one(self):
        self.no_account.email = "nora@prueba.mx"
        self.no_account.save()
        membership = services.set_membership(self.group, self.no_account, self.admin)
        self.assertFalse(RoleAssignment.objects.filter(source_id=membership.pk).exists())
        user, _ = grant_access(self.no_account, self.admin)
        row = RoleAssignment.objects.get(source_id=membership.pk)
        self.assertEqual((row.subject_user, row.role, row.status), (user, "integrante", "active"))
        self.assertIn("Sin diferencias", self.check())

    def test_check_finds_drift_and_apply_repairs_it(self):
        RoleAssignment.objects.filter(subject_user=self.leader_user).update(role="integrante")
        extra = RoleAssignment.objects.create(
            subject_user=self.member_user, church=self.church, scope_type="group", scope_id=self.group.pk,
            role="lider", source_table=services.SOURCE, source_id=999_999,
        )
        with self.assertRaises(SystemExit):
            self.check()
        out = StringIO()
        call_command("rebuild_role_assignments", "--apply", stdout=out)
        self.assertIn("2 diferencia", out.getvalue())
        self.assertFalse(RoleAssignment.objects.filter(pk=extra.pk).exists())
        self.assertEqual(RoleAssignment.objects.get(subject_user=self.leader_user).role, "lider")
        self.assertNotIn("Gala", out.getvalue())
