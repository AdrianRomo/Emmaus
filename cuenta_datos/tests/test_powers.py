"""«Quién puede qué»: who reads it, what it lists, what it flags, and that it cannot drift from
what authorization answers."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from access.models import RoleAssignment
from core.authz import Action, has_scope
from core.models import Invitation, Role, UserChurchRole
from core.services import assign_role
from cuenta_datos import powers
from events.access import ROLE_LABELS, resolve_access
from events.models import Event, EventMembership, MembershipRole
from events.tests.base import make_user
from formularios import services as formularios
from formularios.access import can_read
from formularios.models import Form
from groups import services as groups
from groups.models import Group, GroupMembership
from people.models import Person
from serving import services as serving
from serving.models import Team, TeamMembership

from .base import DataFixture, url

User = get_user_model()


class PowersFixture(DataFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.team = serving.save_team(cls.church, cls.admin, name="Equipo de Sonido")
        serving.set_member(cls.team, cls.coord_person, cls.admin, role=TeamMembership.Role.COORDINATOR)
        serving.set_member(cls.team, cls.member, cls.admin)
        build = cls("setUp")
        cls.form = build.member_form(reader=True)  # read by who guides ``group``
        cls.root = User.objects.create_superuser("root@prueba.mx", "root@prueba.mx", "Clave-Root-2026")
        User.objects.exclude(pk=cls.mentor_user.pk).update(last_login=timezone.now())

    def setUp(self):
        self.client.force_login(self.admin)

    def review(self, can_change=True):
        return powers.build(self.church, can_change=can_change)

    def section(self, key, review=None):
        return next(section for section in (review or self.review()).sections if section.key == key)


class AccessTests(PowersFixture):
    def test_the_administration_and_the_pastors_read_it(self):
        for user in (self.admin, self.pastor_user):
            self.client.force_login(user)
            response = self.client.get(url("powers"))
            self.assertEqual(response.status_code, 200, user.email)
            self.assertContains(response, "Quién puede qué")
            self.assertContains(response, "Gala Guía")

    def test_nobody_else_and_the_page_does_not_say_it_exists(self):
        for user in (self.member_user, self.leader_user, self.coord1, self.mentor_user, self.coord2, self.pastor, self.care):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url("powers")).status_code, 404, user.email)
        self.client.logout()
        self.assertEqual(self.client.get(url("powers")).status_code, 302)

    def test_the_administration_of_another_church_gets_404_in_this_church_and_reads_only_its_own(self):
        """An account that administers church B and is only a person of church A: in A it is nobody special."""
        Person.objects.create(church=self.church, user=self.admin_b, first_name="Berta", last_name="Admin")
        self.client.force_login(self.admin_b)
        own = self.client.get(url("powers"))  # the church of the request is B: B's accounts, none of A's
        self.assertEqual(own.status_code, 200)
        for name in ("Gala Guía", "Pablo Pastor", "Jóvenes Roca", "Equipo de Sonido", self.event.name):
            self.assertNotContains(own, name)
        self.assertContains(own, "Berta Admin")
        self.assertEqual(self.client.post(reverse("switch_church"), {"church": self.church.pk}).status_code, 302)
        self.assertEqual(self.client.get(url("powers")).status_code, 404)

    def test_it_is_read_only_and_there_is_no_file_of_it(self):
        response = self.client.get(url("powers"))
        with open(response.templates[0].origin.name, encoding="utf-8") as source:  # the page's own template: no form, no button
            markup = source.read()
        self.assertEqual(response.templates[0].name, "cuenta_datos/powers.html")
        self.assertNotIn("<form", markup)
        self.assertNotIn("<button", markup)
        self.assertNotIn("Content-Disposition", response)
        self.assertEqual(self.client.post(url("powers")).status_code, 200)  # a POST changes nothing: it is the same page
        self.assertEqual(self.client.get(url("powers") + "?formato=csv")["Content-Type"], "text/html; charset=utf-8")

    def test_a_pastor_reads_the_rows_without_the_ways_to_change_them(self):
        self.client.force_login(self.pastor_user)
        response = self.client.get(url("powers"))
        self.assertNotContains(response, reverse("community:group_people", args=[self.group.pk]))
        self.assertNotContains(response, reverse("serving:team", args=[self.team.pk]))
        self.client.force_login(self.admin)
        response = self.client.get(url("powers"))
        self.assertContains(response, reverse("community:group_people", args=[self.group.pk]))
        self.assertContains(response, reverse("serving:team", args=[self.team.pk]))
        self.assertContains(response, reverse("formularios:readers", args=[self.form.pk]))

    def test_names_are_escaped_and_superusers_are_a_count(self):
        Person.objects.filter(pk=self.leader.pk).update(first_name="<b>Gala</b>")
        response = self.client.get(url("powers"))
        self.assertNotContains(response, "<b>Gala</b>")
        self.assertContains(response, "Hay 1 cuenta de superusuario")
        self.assertContains(response, "la persona que opera el servidor")
        self.assertNotContains(response, "root@prueba.mx")


class ContentTests(PowersFixture):
    def test_each_kind_lists_who_holds_it_since_when_and_who_gave_it(self):
        review = self.review()
        church = {holder.name: holder for holder in self.section("iglesia", review).holders}
        self.assertEqual(church[self.admin_person.full_name].what, "Administración de la iglesia")
        self.assertEqual(church["Pablo Pastor"].what, "Pastor de la iglesia")
        self.assertEqual(church["Memo Mentor"].what, "Mentor")
        self.assertEqual(church["Pablo Pastor"].since, timezone.localdate())
        guide = self.section("grupos", review).holders
        self.assertEqual([(holder.name, holder.what) for holder in guide], [("Gala Guía", "Guía de Jóvenes Roca")])
        self.assertEqual(guide[0].granted_by, self.admin_person.full_name)  # GroupMembership.added_by
        self.assertEqual(guide[0].url, reverse("community:group_people", args=[self.group.pk]))
        team = self.section("equipos", review).holders
        self.assertEqual([(holder.user_id, holder.what) for holder in team], [(self.coord1.pk, "Coordina Equipo de Sonido")])
        self.assertEqual(team[0].granted_by, "")  # the table does not keep who
        readers = self.section("formularios", review).holders
        self.assertEqual([holder.user_id for holder in readers], [self.leader_user.pk])
        self.assertIn("«Convivio de aniversario» por guiar Jóvenes Roca", readers[0].what)
        events = {(holder.user_id, holder.what) for holder in self.section("eventos", review).holders}
        self.assertIn((self.coord1.pk, f"Coordinador en {self.event.name}"), events)
        self.assertEqual(review.superusers, 1)
        # Who only takes part holds nothing: the member serves in the team and is in no section.
        self.assertNotIn(self.member_user.pk, review.user_ids())
        self.assertNotIn(self.asked_user.pk, review.user_ids())

    def test_only_the_church_of_the_request(self):
        review = self.review()
        self.assertNotIn(self.admin_b.pk, review.user_ids())
        self.assertNotIn(self.member_b_user.pk, review.user_ids())  # a coordinator of church B's event
        other = powers.build(self.church_b, can_change=True)
        self.assertEqual(other.user_ids(), {self.admin_b.pk, self.member_b_user.pk})

    def test_who_invited_a_church_role_is_named_when_an_accepted_invitation_says_so(self):
        Invitation.objects.create(church=self.church, email="PASTOR-sin-admin@prueba.mx", role=Role.PASTOR, invited_by=self.admin,
                                  accepted_at=timezone.now())
        Invitation.objects.create(church=self.church_b, email="mentor@prueba.mx", role=Role.MENTOR, invited_by=self.admin_b,
                                  accepted_at=timezone.now())  # another church's invitation says nothing here
        church = {holder.name: holder for holder in self.section("iglesia").holders}
        self.assertEqual(church["Pablo Pastor"].granted_by, self.admin_person.full_name)
        self.assertEqual(church["Memo Mentor"].granted_by, "")

    def test_an_ended_role_leaves_the_screen(self):
        groups.set_membership(self.group, self.leader, self.admin, role=GroupMembership.Role.MEMBER)
        serving.remove_member(self.team, self.coord_person, self.admin)
        EventMembership.objects.filter(event=self.event, user=self.coord2).update(is_active=False)
        User.objects.filter(pk=self.mentor_user.pk).update(is_active=False)
        review = self.review()
        self.assertEqual(self.section("grupos", review).holders, [])
        self.assertEqual(self.section("equipos", review).holders, [])
        self.assertEqual(self.section("formularios", review).holders, [])
        self.assertNotIn(self.coord2.pk, review.user_ids())
        self.assertNotIn(self.mentor_user.pk, review.user_ids())
        self.assertContains(self.client.get(url("powers")), "Ningún grupo tiene guía con cuenta.")


class FlagTests(PowersFixture):
    def test_an_account_with_a_role_and_no_sign_in_in_180_days(self):
        User.objects.filter(pk=self.leader_user.pk).update(last_login=timezone.now() - timedelta(days=181))
        User.objects.filter(pk=self.pastor_user.pk).update(last_login=timezone.now() - timedelta(days=179))
        review = self.review()
        self.assertEqual(self.section("grupos", review).holders[0].flags, ["No entra desde hace más de 180 días"])
        church = {holder.name: holder for holder in self.section("iglesia", review).holders}
        self.assertEqual(church["Pablo Pastor"].flags, [])
        self.assertEqual(church["Memo Mentor"].flags, ["Nunca ha entrado"])
        page = self.client.get(url("powers"))
        self.assertContains(page, "No entra desde hace más de 180 días.")
        self.assertContains(page, "row--attention")
        # The date only, never the hour.
        self.assertEqual(church["Pablo Pastor"].last_login, (timezone.now() - timedelta(days=179)).astimezone(
            powers.church_tz(self.church)).date())

    def test_a_role_held_by_an_account_whose_record_was_joined_away(self):
        before = {holder.user_id: holder for holder in self.section("iglesia").holders}
        self.assertEqual(before[self.pastor_user.pk].flags, [])
        Person.objects.filter(pk=self.pastor_person.pk).update(merged_into=self.admin_person.pk)
        church = {holder.user_id: holder for holder in self.section("iglesia").holders}
        self.assertEqual(church[self.pastor_user.pk].flags, ["Su registro de persona se unió a otro"])
        self.assertEqual(church[self.pastor_user.pk].name, "Pablo Pastor")  # the account's own name: the hidden record is not read
        self.assertContains(self.client.get(url("powers")), "Su registro de persona se unió a otro.")

    def test_a_church_without_a_pastor_says_what_that_blocks(self):
        self.assertFalse(self.review().no_pastor)
        self.assertNotContains(self.client.get(url("powers")), "no tiene ninguna cuenta con rol de pastor")
        UserChurchRole.objects.filter(church=self.church, role=Role.PASTOR).delete()
        self.assertTrue(self.review().no_pastor)
        page = self.client.get(url("powers"))
        self.assertContains(page, "no tiene ninguna cuenta con rol de pastor")
        for blocked in ("aprobar", "peticiones de oración", "directorio"):
            self.assertContains(page, blocked)

    def test_a_deactivated_pastor_is_no_pastor(self):
        User.objects.filter(pk=self.pastor_user.pk).update(is_active=False)
        self.assertTrue(self.review().no_pastor)

    def test_the_only_pastor_named_by_the_administration(self):
        self.assertFalse(self.review().lone_pastor_named_by_admin)  # nothing says who named the pastor
        Invitation.objects.create(church=self.church, email=self.pastor_user.email, role=Role.PASTOR, invited_by=self.admin,
                                  accepted_at=timezone.now())
        self.assertTrue(self.review().lone_pastor_named_by_admin)
        self.assertContains(self.client.get(url("powers")), "La única cuenta con rol de pastor la nombró la administración.")
        second = make_user("otra-pastora@prueba.mx", "Paz", "Pastora")
        assign_role(second, self.church, Role.PASTOR)
        self.assertFalse(self.review().lone_pastor_named_by_admin)  # two pastors: no longer the only one


class DriftTests(PowersFixture):
    """The screen is built from the tables authorization reads. Each kind it lists is checked against its own
    source of truth, account by account, both ways (nobody missing, nobody extra): church roles against the legacy
    role table behind ``has_scope`` (church); guides and coordinators against ``has_scope`` (group / ministry,
    manage), which reads ``access.RoleAssignment``; form readers against ``formularios.access.can_read``; event
    teams against ``events.access.resolve_access`` and the membership it answers from. One more test asks
    ``has_scope`` about every scope and fails when somebody who may manage something is on no section."""

    def more_of_every_kind(self) -> dict:
        """A second guide, a team that reads a form, an event administrator and the team of an archived event."""
        groups.set_membership(self.other_group, self.asked, self.admin, role=GroupMembership.Role.LEADER)
        formularios.add_reader(self.form, self.admin, f"ministry:{self.team.pk}")
        event_admin = make_user("admin-evento@prueba.mx", "Eva", "Evento")
        EventMembership.objects.create(event=self.event, user=event_admin, role=MembershipRole.ADMIN, created_by=self.admin)
        old = Event.objects.create(church=self.church, name="Retiro viejo", slug="retiro-viejo", status=Event.Status.ARCHIVED)
        old_admin = make_user("admin-viejo@prueba.mx", "Vera", "Vieja")
        EventMembership.objects.create(event=old, user=old_admin, role=MembershipRole.ADMIN)
        return {"event_admin": event_admin, "old_admin": old_admin}

    def listed(self, key, by="what") -> set:
        return {(holder.user_id, getattr(holder, by)) for holder in self.section(key).holders}

    def test_church_roles_are_the_role_table_behind_has_scope(self):
        self.more_of_every_kind()
        expected = set()
        for user in User.objects.all():
            if not has_scope(user, self.church, "church", self.church.pk, Action.VIEW):  # holds a usable church role
                continue
            row = UserChurchRole.objects.get(church=self.church, user=user)
            if row.role not in powers.NOT_A_POWER:
                expected.add((user.pk, powers.CHURCH_ROLE_WORDS.get(row.role, row.get_role_display())))
        self.assertEqual(self.listed("iglesia"), expected)
        self.assertEqual({what for _, what in expected}, set(powers.CHURCH_ROLE_WORDS.values()))  # each role is in the fixture

    def test_guides_are_who_has_scope_says_manages_a_group(self):
        self.more_of_every_kind()
        expected = {
            (user.pk, reverse("community:group_people", args=[group.pk]))
            for group in Group.objects.filter(church=self.church) for user in User.objects.all()
            if has_scope(user, self.church, "group", group.pk, Action.MANAGE)
        }
        self.assertEqual(self.listed("grupos", by="url"), expected)
        self.assertEqual(len(expected), 2)

    def test_coordinators_are_who_has_scope_says_manages_a_team(self):
        self.more_of_every_kind()
        expected = {
            (user.pk, reverse("serving:team", args=[team.pk]))
            for team in Team.objects.filter(church=self.church) for user in User.objects.all()
            if has_scope(user, self.church, "ministry", team.pk, Action.MANAGE)
        }
        self.assertEqual(self.listed("equipos", by="url"), expected)
        self.assertTrue(expected)

    def test_form_readers_are_who_can_read_says_reads_beyond_the_administration(self):
        self.more_of_every_kind()
        expected = {
            (user.pk, reverse("formularios:readers", args=[form.pk]))
            for form in Form.objects.filter(church=self.church) for user in User.objects.all()
            if can_read(user, form, is_church_admin=False)  # through a group or a team, never the church role
        }
        self.assertEqual(self.listed("formularios", by="url"), expected)
        self.assertEqual({user_id for user_id, _ in expected}, {self.leader_user.pk, self.coord1.pk})  # a guide and a coordinator

    def test_event_teams_are_who_resolve_access_lets_in_by_a_membership(self):
        self.more_of_every_kind()
        expected = set()
        for event in Event.objects.filter(church=self.church):
            for user in User.objects.all():
                access = resolve_access(user, event)
                if access is None or access.membership is None:  # none, or only through a church role
                    continue
                role = access.membership.role
                archived = " (archivado)" if event.status == Event.Status.ARCHIVED else ""
                expected.add((user.pk, f"{ROLE_LABELS.get(role, role)} en {event.name}{archived}"))
        self.assertEqual(self.listed("eventos"), expected)
        self.assertGreaterEqual(len({what.split(" en ")[0] for _, what in expected}), 2)  # more than one kind of role

    def managers(self, church) -> set[int]:
        scopes = [("church", church.pk)]
        scopes += [("group", pk) for pk in Group.objects.filter(church=church).values_list("pk", flat=True)]
        scopes += [("ministry", pk) for pk in Team.objects.filter(church=church).values_list("pk", flat=True)]
        scopes += [("event", pk) for pk in Event.objects.filter(church=church).values_list("pk", flat=True)]
        found = set()
        for user in User.objects.all():
            if any(has_scope(user, church, kind, pk, Action.MANAGE) for kind, pk in scopes):
                found.add(user.pk)
        return found

    def test_everybody_who_may_manage_something_is_on_the_screen(self):
        more = self.more_of_every_kind()
        event_admin, old_admin = more["event_admin"], more["old_admin"]
        for church in (self.church, self.church_b):
            managers = self.managers(church)
            review = powers.build(church, can_change=True)
            superusers = set(User.objects.filter(is_superuser=True, is_active=True).values_list("pk", flat=True))
            self.assertEqual(managers - superusers - review.user_ids(), set(), church.name)
            self.assertEqual(len(superusers), review.superusers)  # counted, never named
            self.assertTrue(superusers <= managers or not Event.objects.filter(church=church).exists())
        managers = self.managers(self.church)
        for user in (self.admin, self.leader_user, self.asked_user, self.coord1, event_admin, old_admin):
            self.assertIn(user.pk, managers, user.email)
        # And the page itself names each of them.
        page = self.client.get(url("powers")).content.decode()
        for user_id in managers - {self.root.pk}:
            holder = next(h for s in powers.build(self.church, can_change=True).sections for h in s.holders if h.user_id == user_id)
            self.assertIn(holder.name.split()[0], page)

    def test_the_test_would_fail_if_a_kind_were_dropped(self):
        """Without the guides' rows, ``has_scope`` still says the guide manages a group: the gap shows."""
        managers = self.managers(self.church)
        review = self.review()
        review.sections = [section for section in review.sections if section.key not in ("grupos", "formularios")]
        self.assertIn(self.leader_user.pk, managers - review.user_ids())

    def test_a_role_row_that_outlived_its_membership_is_not_listed_as_it_grants_nothing(self):
        GroupMembership.objects.filter(group=self.group, person=self.leader).update(status=GroupMembership.Status.INACTIVE)
        self.assertTrue(RoleAssignment.objects.filter(subject_user=self.leader_user, scope_type="group").exists())
        self.assertFalse(has_scope(self.leader_user, self.church, "group", self.group.pk, Action.MANAGE))
        self.assertEqual(self.section("grupos").holders, [])
