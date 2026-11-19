"""The merge as a rule about identity and authorization: who may be merged, what an account gains,
what happens to minors, and what two administrators at once (or a stale copy) can do."""

from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.utils import timezone

from access.models import RoleAssignment
from agenda.models import EventRegistration, GuardianConsent
from core.authz import Action, has_scope
from core.models import UserChurchRole
from events.tests.base import make_user
from groups import services as groups
from groups.models import GroupMembership
from people import duplicates, merge, services
from people.models import ChurchAuditEvent, DuplicateDismissal, Person, PersonMerge
from people.persons import own_person
from serving import services as serving

from .merge_base import MergeFixture


def rebuild_check():
    """``rebuild_role_assignments --check`` exits with 1 when the projection and its sources disagree."""
    call_command("rebuild_role_assignments", "--check", stdout=StringIO())


class WhoMayBeMergedTests(MergeFixture):
    def test_the_plain_case_hides_the_absorbed_record_and_writes_one_merge_and_one_audit_row(self):
        audited = ChurchAuditEvent.objects.count()
        record = self.do_merge()
        self.dup.refresh_from_db()
        self.keep.refresh_from_db()
        self.assertEqual((self.dup.merged_into_id, self.dup.user_id), (self.keep.pk, None))
        self.assertTrue(Person.objects.filter(pk=self.dup.pk).exists())  # hidden, never deleted
        self.assertEqual((self.dup.first_name, self.dup.email), ("Lia", "LIA@prueba.mx"))
        self.assertEqual((self.keep.full_name, self.keep.email, self.keep.phone), ("Lía Luna", "lia@prueba.mx", "55 3333 4444"))
        self.assertEqual((record.church, record.survivor, record.absorbed, record.merged_by), (self.church, self.keep, self.dup, self.admin))
        row = ChurchAuditEvent.objects.order_by("id").last()
        self.assertEqual(ChurchAuditEvent.objects.count(), audited + 1)
        self.assertEqual(row.action, "persona_union")
        self.assertEqual(row.metadata, {"person_id": self.keep.pk, "other_person_id": self.dup.pk, "merge_id": record.pk})
        for word in ("Lía", "Lia", "Luna", "prueba.mx"):  # ids only: not a name, not an address
            self.assertNotIn(word, row.summary + str(row.metadata))

    def test_another_church_is_refused_by_the_service_itself(self):
        twin = Person.objects.create(church=self.church_b, first_name="Lía", last_name="Luna", email="lia@prueba.mx")
        self.assertEqual(duplicates.signals_between(self.keep, twin), ["email"])  # they do look alike
        before = self.pointers()
        for church, survivor, absorbed in ((self.church, self.keep, twin), (self.church, twin, self.keep), (self.church_b, twin, self.keep)):
            token = merge._token(survivor, absorbed)
            with self.assertRaises(merge.MergeRefused):
                merge.merge(church, survivor.pk, absorbed.pk, self.admin, choices={}, token=token)
            self.assertFalse(merge.build_preview(church, survivor, absorbed).allowed)
        with self.assertRaises(merge.MergeRefused):
            merge.dismiss(self.church, self.keep.pk, twin.pk, self.admin)
        self.assertEqual(self.pointers(), before)
        twin.refresh_from_db()
        self.assertIsNone(twin.merged_into_id)
        self.assertEqual((PersonMerge.objects.count(), DuplicateDismissal.objects.count()), (0, 0))
        # And the review of each church never shows the other's person.
        for church in (self.church, self.church_b):
            for pair in duplicates.suggestions(church):
                self.assertEqual({pair.a.church_id, pair.b.church_id}, {church.pk})

    def test_no_signal_no_merge(self):
        Person.objects.filter(pk=self.dup.pk).update(email="otra@prueba.mx")
        self.assert_refused("Nada indica que sean la misma persona")

    def test_a_dismissed_pair_is_not_merged_until_somebody_reopens_it(self):
        row = merge.dismiss(self.church, self.dup.pk, self.keep.pk, self.admin)
        self.assertEqual((row.person_a_id, row.person_b_id), tuple(sorted([self.keep.pk, self.dup.pk])))
        self.assertEqual(merge.dismiss(self.church, self.keep.pk, self.dup.pk, self.admin).pk, row.pk)  # twice is one row
        self.assert_refused("no son la misma persona")
        merge.reopen(self.church, row.pk, self.admin)
        self.assertEqual(self.refusals(), [])
        actions = list(ChurchAuditEvent.objects.filter(action__startswith="persona_duplicado").order_by("id").values_list("action", "metadata"))
        ids = {"person_id": row.person_a_id, "other_person_id": row.person_b_id}
        self.assertEqual(actions, [("persona_duplicado_descartado", ids), ("persona_duplicado_reabierto", ids)])

    def test_the_same_record_twice_and_a_record_already_merged_are_refused(self):
        with self.assertRaises(merge.MergeRefused):
            merge.merge(self.church, self.keep.pk, self.keep.pk, self.admin, choices={}, token="")
        self.do_merge()
        third = Person.objects.create(church=self.church, first_name="Lia", last_name="Luna Ríos", email="lia@prueba.mx")
        with self.assertRaises(merge.MergeRefused):
            merge.merge(self.church, third.pk, self.dup.pk, self.admin, choices={}, token=merge._token(third, self.dup))


class ContactFieldTests(MergeFixture):
    def test_an_empty_field_is_filled_and_a_different_one_is_chosen_between_the_two(self):
        Person.objects.filter(pk=self.keep.pk).update(phone="")
        Person.objects.filter(pk=self.dup.pk).update(phone="55 9999 0000", preferred_contact_method="whatsapp")
        preview = merge.build_preview(self.church, Person.objects.get(pk=self.keep.pk), Person.objects.get(pk=self.dup.pk))
        self.assertEqual({row.name: row.state for row in preview.fields},
                         {"name": "keeps", "email": "same", "phone": "fills", "preferred_contact_method": "choose"})
        # A choice is one of the two records, never a value and never left out.
        for bad in ({}, {"preferred_contact_method": self.leader.pk}, {"preferred_contact_method": 0}):
            with self.assertRaises(merge.MergeRefused):
                self.do_merge(**bad)
        record = self.do_merge(preferred_contact_method=self.dup.pk)
        self.keep.refresh_from_db()
        self.assertEqual((self.keep.phone, self.keep.preferred_contact_method, self.keep.email), ("55 9999 0000", "whatsapp", "lia@prueba.mx"))
        self.assertEqual(record.manifest["survivor_before"], {"phone": "", "preferred_contact_method": "email"})
        merge.undo(self.church, record.pk, self.admin)
        self.keep.refresh_from_db()
        self.assertEqual((self.keep.phone, self.keep.preferred_contact_method), ("", "email"))
        record.refresh_from_db()
        self.assertEqual(record.manifest["survivor_before"], {})  # not needed any more: gone at once

    def test_the_previous_contact_values_are_cleared_when_the_undo_ends(self):
        Person.objects.filter(pk=self.dup.pk).update(phone="55 9999 0000")
        record = self.do_merge(phone=self.dup.pk)
        self.assertEqual(merge.clear_expired(apply=True), 0)  # still inside the window
        PersonMerge.objects.filter(pk=record.pk).update(merged_at=timezone.now() - timedelta(days=merge.UNDO_DAYS, minutes=1))
        out = StringIO()
        call_command("prune_person_merges", stdout=out)
        self.assertIn("por borrar: 1", out.getvalue())
        record.refresh_from_db()
        self.assertEqual(record.manifest["survivor_before"], {"phone": "55 3333 4444"})  # a rehearsal writes nothing
        out = StringIO()
        call_command("prune_person_merges", "--apply", stdout=out)
        self.assertIn(": 1", out.getvalue())
        self.assertNotIn("3333", out.getvalue())
        record.refresh_from_db()
        self.assertEqual(record.manifest["survivor_before"], {})
        self.assertEqual(record.manifest["fields_changed"], ["phone"])
        self.assertIsNotNone(record.contact_cleared_at)
        self.assertEqual(merge.clear_expired(apply=True), 0)  # again: nothing left
        with self.assertRaisesMessage(merge.MergeRefused, "ya no se puede deshacer"):
            merge.undo(self.church, record.pk, self.admin)
        self.dup.refresh_from_db()
        self.assertEqual(self.dup.merged_into_id, self.keep.pk)


class AccountAndRoleTests(MergeFixture):
    """A role is never copied: it is derived again from the membership and the account (decision 0016)."""

    def setUp(self):
        self.account = make_user("lia@prueba.mx", "Lía", "Luna")

    def give(self, person):
        Person.objects.filter(pk=person.pk).update(user=self.account)

    def manages(self, user, group) -> bool:
        return has_scope(user, self.church, "group", group.pk, Action.MANAGE)

    def test_two_accounts_are_never_merged(self):
        self.give(self.keep)
        Person.objects.filter(pk=self.dup.pk).update(user=make_user("lia2@prueba.mx"))
        self.assert_refused("Las dos tienen acceso a Emmaus")

    def test_a_group_guided_by_the_record_without_account_reaches_the_account_and_the_preview_says_so(self):
        self.give(self.keep)
        led = groups.set_membership(self.other_group, self.dup, self.admin, role="lider")
        team = serving.set_member(self.team, self.dup, self.admin, role="coordina")
        self.assertFalse(self.manages(self.account, self.other_group))
        self.assertFalse(RoleAssignment.objects.filter(subject_user=self.account).exists())
        preview = merge.build_preview(self.church, Person.objects.get(pk=self.keep.pk), self.dup)
        self.assertEqual((preview.account, preview.groups_gained, preview.teams_gained), ("survivor", 1, 1))

        record = self.do_merge()
        self.assertTrue(self.manages(self.account, self.other_group))
        self.assertTrue(has_scope(self.account, self.church, "ministry", self.team.pk, Action.MANAGE))
        self.assertFalse(self.manages(self.account, self.group))  # nothing beyond the memberships that moved
        self.assertEqual(
            sorted(RoleAssignment.objects.filter(subject_user=self.account).values_list("source_table", "source_id", "role", "status")),
            [("groups.GroupMembership", led.pk, "lider", "active"), ("serving.TeamMembership", team.pk, "coordina", "active")],
        )
        self.assertFalse(UserChurchRole.objects.filter(user=self.account).exists())  # and no church role from anywhere
        rebuild_check()

        merge.undo(self.church, record.pk, self.admin)
        self.assertFalse(self.manages(self.account, self.other_group))
        self.assertFalse(RoleAssignment.objects.filter(subject_user=self.account).exists())
        rebuild_check()

    def test_the_account_of_the_absorbed_record_moves_with_its_roles_and_comes_back(self):
        self.give(self.dup)
        groups.set_membership(self.other_group, Person.objects.get(pk=self.dup.pk), self.admin, role="lider")
        mine = groups.set_membership(self.group, self.keep, self.admin, role="lider")  # held by a record nobody signs in as
        self.assertTrue(self.manages(self.account, self.other_group))
        self.assertFalse(self.manages(self.account, self.group))
        preview = merge.build_preview(self.church, self.keep, Person.objects.get(pk=self.dup.pk))
        self.assertEqual((preview.account, preview.groups_gained), ("moves", 1))

        record = self.do_merge()
        self.assertEqual(record.manifest["moved_user_id"], self.account.pk)
        self.assertEqual(own_person(self.account, self.church), self.keep)
        self.assertEqual(Person.objects.filter(church=self.church, user=self.account).count(), 1)
        self.assertTrue(self.manages(self.account, self.other_group))
        self.assertTrue(self.manages(self.account, self.group))
        self.assertIn(("groups.GroupMembership", mine.pk), RoleAssignment.objects.filter(subject_user=self.account).values_list("source_table", "source_id"))
        rebuild_check()

        merge.undo(self.church, record.pk, self.admin)
        self.assertEqual(own_person(self.account, self.church), self.dup)
        self.assertTrue(self.manages(self.account, self.other_group))
        self.assertFalse(self.manages(self.account, self.group))
        rebuild_check()

    def test_an_ended_role_is_not_revived_by_a_merge(self):
        self.give(self.keep)
        ended = groups.set_membership(self.other_group, self.dup, self.admin, role="lider")
        groups.end_membership(ended, self.admin)
        self.do_merge()
        self.assertFalse(self.manages(self.account, self.other_group))
        self.assertEqual(GroupMembership.objects.get(pk=ended.pk).status, "inactiva")
        rebuild_check()

    def test_the_account_does_not_come_back_if_it_is_no_longer_on_the_survivor(self):
        self.give(self.dup)
        record = self.do_merge()
        Person.objects.filter(pk=self.keep.pk).update(user=None)  # somebody took the access away since
        undone = merge.undo(self.church, record.pk, self.admin)
        self.dup.refresh_from_db()
        self.assertIsNone(self.dup.user_id)
        self.assertTrue(undone.undo_report["account_not_restored"])
        self.assertTrue(any("acceso a Emmaus" in line for line in merge.undo_report_lines(undone)))


class MinorTests(MergeFixture):
    """A merge never changes who counts as a minor and never carries an authorization to another identity."""

    def setUp(self):
        self.minor = timezone.localdate() - timedelta(days=15 * 365)
        self.adult = timezone.localdate() - timedelta(days=30 * 365)

    def born(self, person, date):
        Person.objects.filter(pk=person.pk).update(birth_date=date)

    def test_different_dates_of_birth_refuse(self):
        self.born(self.keep, self.adult)
        self.born(self.dup, self.adult - timedelta(days=1))
        self.assert_refused("fechas de nacimiento distintas")

    def test_a_minor_is_merged_only_with_the_same_date_of_birth(self):
        for survivor, absorbed in ((self.keep, self.dup), (self.dup, self.keep)):
            self.born(survivor, self.minor)
            self.born(absorbed, None)
            self.assert_refused("menor de edad", survivor, absorbed)
        self.born(self.keep, self.minor)
        self.born(self.dup, self.minor)
        self.assertEqual(self.refusals(), [])

    def test_an_authorization_on_record_refuses_unless_the_dates_are_the_same(self):
        registration = EventRegistration.objects.create(event=self.other_event, person=self.dup)
        GuardianConsent.objects.create(registration=registration, guardian_name="Mamá de prueba", relationship="madre", method="papel",
                                       revoked_at=timezone.now())  # even a withdrawn one is a record about a minor
        self.assert_refused("autorización de su tutor")
        self.assert_refused("autorización de su tutor", self.dup, self.keep)
        self.born(self.dup, self.adult)
        self.assert_refused("autorización de su tutor")
        self.born(self.keep, self.adult)
        self.assertEqual(self.refusals(), [])

    def test_marked_as_a_minor_at_the_door_or_a_campero_under_age_refuse_without_a_date(self):
        registration = EventRegistration.objects.create(event=self.other_event, person=self.dup, guest_is_minor=True)
        self.assert_refused("menor de edad")
        registration.delete()
        services.link_participant(self.dup, self.p1, self.admin)  # 15 at the camp
        self.assert_refused("menor de edad")

    def test_a_merge_never_writes_a_date_of_birth_or_its_verification(self):
        self.born(self.dup, self.adult)
        self.assert_refused("Solo el registro que se uniría tiene fecha de nacimiento")
        self.assertEqual(self.refusals(self.dup, self.keep), [])  # the other way round is fine
        self.born(self.keep, self.adult)
        services.verify_birth_date(Person.objects.get(pk=self.dup.pk), self.admin, "documento")
        self.assert_refused("edad confirmada")
        services.verify_birth_date(Person.objects.get(pk=self.keep.pk), self.admin, "registro")
        before = Person.objects.filter(pk=self.keep.pk).values("birth_date", "birth_date_verified_at", "birth_date_verified_method").get()
        record = self.do_merge()
        self.assertEqual(Person.objects.filter(pk=self.keep.pk).values(*before).get(), before)
        merge.undo(self.church, record.pk, self.admin)
        self.assertEqual(Person.objects.filter(pk=self.keep.pk).values(*before).get(), before)
        self.assertTrue(Person.objects.get(pk=self.dup.pk).birth_date_verified)


class RaceTests(MergeFixture):
    def test_two_administrators_merging_the_same_pair_make_one_merge(self):
        second_admin = User.objects.get(pk=self.admin.pk)
        token = merge.build_preview(self.church, self.keep, self.dup).token  # both opened the same preview
        merge.merge(self.church, self.keep.pk, self.dup.pk, self.admin, choices={}, token=token)
        state = self.pointers()
        for survivor, absorbed in ((self.keep, self.dup), (self.dup, self.keep)):  # the same way, and the other way round
            with self.assertRaises(merge.MergeRefused) as caught:
                merge.merge(self.church, survivor.pk, absorbed.pk, second_admin, choices={}, token=token)
            self.assertIn("alguien más ya unió", caught.exception.message)
        self.assertEqual(PersonMerge.objects.count(), 1)
        self.assertEqual(ChurchAuditEvent.objects.filter(action="persona_union").count(), 1)
        self.assertEqual(self.pointers(), state)

    def test_a_preview_that_went_stale_merges_nothing(self):
        token = merge.build_preview(self.church, self.keep, self.dup).token
        Person.objects.filter(pk=self.dup.pk).update(phone="55 0000 1111")  # somebody edited one of them meanwhile
        before = self.pointers()
        with self.assertRaises(merge.StalePreview):
            merge.merge(self.church, self.keep.pk, self.dup.pk, self.admin, choices={"phone": self.dup.pk}, token=token)
        self.assertEqual(self.pointers(), before)
        self.assertEqual(PersonMerge.objects.count(), 0)

    def test_a_copy_read_before_the_merge_cannot_bring_the_absorbed_record_back(self):
        stale = Person.objects.get(pk=self.dup.pk)
        Person.objects.filter(pk=self.dup.pk).update(user=make_user("lia@prueba.mx"))
        stale_with_account = Person.objects.get(pk=self.dup.pk)
        self.do_merge()
        for copy in (stale, stale_with_account):
            copy.phone = "55 7777 8888"
            with self.assertRaises(services.PeopleError) as caught:
                services.save_person(copy, self.admin, created=False)
            self.assertEqual(caught.exception.code, "merged")
        self.dup.refresh_from_db()
        self.assertEqual((self.dup.merged_into_id, self.dup.user_id, self.dup.phone), (self.keep.pk, None, ""))
        # A stale copy of the survivor may still correct a phone; it never writes the account or the merge back.
        survivor = Person.objects.get(pk=self.keep.pk)
        account = survivor.user_id
        survivor.user_id, survivor.merged_into_id, survivor.phone = None, self.dup.pk, "55 1212 3434"
        services.save_person(survivor, self.admin, created=False)
        self.keep.refresh_from_db()
        self.assertEqual((self.keep.user_id, self.keep.merged_into_id, self.keep.phone), (account, None, "55 1212 3434"))

    def test_an_undo_asked_twice_is_one_undo(self):
        record = self.do_merge()
        merge.undo(self.church, record.pk, self.admin)
        state = self.pointers()
        with self.assertRaisesMessage(merge.MergeRefused, "ya se había deshecho"):
            merge.undo(self.church, record.pk, self.admin)
        self.assertEqual(self.pointers(), state)
        self.assertEqual(ChurchAuditEvent.objects.filter(action="persona_union_deshecha").count(), 1)
        with self.assertRaises(merge.MergeRefused):  # and never from another church
            merge.undo(self.church_b, self.do_merge().pk, self.admin_b)
        # After an undo the pair can be merged again: the earlier record does not block a new one.
        self.assertEqual(PersonMerge.objects.filter(undone_at__isnull=True).count(), 1)
