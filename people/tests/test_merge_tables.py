"""One decision and one test for every foreign key to ``Person`` (docs/v2/wave9/personas.md, 1.5)."""

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.db.models import ProtectedError
from django.utils import timezone

from agenda.models import EventRegistration, GuardianConsent
from feedback import services as feedback
from feedback.models import FeedbackGiven
from groups import services as groups
from groups.models import GroupAttendance, GroupJoinRequest, GroupMembership
from notices.models import EmailPreference
from people import merge, services
from people.models import CampusAffiliation, ChurchAuditEvent, DuplicateDismissal, EventParticipantLink, Person, PersonMerge
from resources.models import ReadingPlanDay, ReadingPlanDayRead, ReadingPlanProgress
from serving import services as serving
from serving.models import Assignment, ServiceDate, Unavailable

from .merge_base import MergeFixture


class InventoryTests(MergeFixture):
    def test_every_relation_to_a_person_has_a_decision(self):
        """A new foreign key to ``Person`` fails here until somebody decides what a merge does with it."""
        self.assertEqual(merge.undecided_relations(), [])
        decided = {(table.model, table.field) for table in merge.TABLES} | merge.HANDLED_ELSEWHERE
        self.assertEqual(sorted(decided), merge.relations_to_person())
        self.assertGreaterEqual(len(merge.relations_to_person()), 24)
        for table in merge.TABLES:
            self.assertIsNotNone(self.row_for(table.key, self.dup).pk, table.key)  # and a builder for each

    def test_a_table_nobody_decided_about_refuses_the_merge_while_it_has_rows(self):
        known = merge.TABLES
        merge.TABLES = tuple(table for table in known if table.key != "serving.Unavailable.person")
        self.addCleanup(setattr, merge, "TABLES", known)
        self.assertEqual(merge.undecided_relations(), [("serving.Unavailable", "person")])
        self.assertEqual(self.refusals(), [])  # no rows there: nothing to get wrong
        Unavailable.objects.create(person=self.keep, date=self.day.date)
        self.assert_refused("todavía no sabemos unir")


def _moves(key):
    def test(self):
        row = self.row_for(key, self.dup)
        table = merge._BY_KEY[key]
        record = self.do_merge()
        self.assertEqual(getattr(table.rows().get(pk=row.pk), f"{table.field}_id"), self.keep.pk)
        self.assertEqual(record.manifest["moved"], {key: [row.pk]})
        self.assert_integrity()
        merge.undo(self.church, record.pk, self.admin)
        self.assertEqual(getattr(table.rows().get(pk=row.pk), f"{table.field}_id"), self.dup.pk)
        self.assert_integrity()

    return test


class EveryTableMovesTests(MergeFixture):
    """``test_moves_<table>``: a row of the absorbed record ends on the survivor, and the undo takes it back."""


for _table in merge.TABLES:
    setattr(EveryTableMovesTests, "test_moves_" + _table.key.lower().replace(".", "_"), _moves(_table.key))


class RoundTripTests(MergeFixture):
    def test_merge_and_undo_leave_every_foreign_key_as_it_was(self):
        for table in merge.TABLES:
            self.row_for(table.key, self.dup)
        self.row_for("care.FollowUp.person", self.keep)  # the survivor's own rows are never touched
        other = CampusAffiliation.objects.create(person=self.keep, sede=self.sede.__class__.objects.create(church=self.church, name="Norte", slug="norte"), is_primary=True)
        CampusAffiliation.objects.filter(person=self.dup).update(is_primary=True)
        # The campero was 15: a link like this one moves only when both records say the same date of birth.
        Person.objects.filter(pk__in=[self.keep.pk, self.dup.pk]).update(birth_date="1990-01-01")
        services.link_participant(self.dup, self.p1, self.admin)
        before = self.pointers()

        record = self.do_merge()
        self.dup.refresh_from_db()
        self.assertEqual(self.dup.merged_into_id, self.keep.pk)
        for table in merge.TABLES:
            self.assertFalse(table.rows().filter(**{table.field: self.dup.pk}).exists(), table.key)
        self.assertEqual(EventParticipantLink.objects.get(participant=self.p1).person_id, self.keep.pk)
        # One primary sede: the survivor's. The one that arrived is there, demoted.
        self.assertEqual(list(CampusAffiliation.objects.filter(person=self.keep, is_primary=True)), [other])
        self.assertEqual(CampusAffiliation.objects.filter(person=self.keep).count(), 2)
        self.assert_integrity()
        call_command("rebuild_role_assignments", "--check", stdout=StringIO())

        undone = merge.undo(self.church, record.pk, self.admin)
        self.assertEqual(self.pointers(), before)
        self.assertEqual(merge.undo_report_lines(undone), [])
        self.assert_integrity()
        call_command("rebuild_role_assignments", "--check", stdout=StringIO())

    def test_the_manifest_holds_ids_and_the_previous_contact_values_and_nothing_else(self):
        for table in merge.TABLES:
            self.row_for(table.key, self.dup)
        Person.objects.filter(pk=self.dup.pk).update(phone="55 9999 0000")
        record = self.do_merge(phone=self.dup.pk)
        manifest = record.manifest
        self.assertEqual(
            sorted(manifest), ["camp_participants", "demoted_affiliations", "fields_changed", "moved", "moved_user_id", "survivor_before", "version"]
        )
        self.assertEqual(manifest["survivor_before"], {"phone": "55 3333 4444"})
        for ids in manifest["moved"].values():
            self.assertTrue(all(isinstance(pk, int) for pk in ids))
        text = str(manifest)
        for word in ("Lía", "Luna", "lia@", "Texto", "visita", "9999"):
            self.assertNotIn(word, text)

    def test_what_the_survivor_changed_after_the_merge_is_not_undone_and_is_reported(self):
        membership = self.row_for("groups.GroupMembership.person", self.dup)
        gone = self.row_for("serving.Unavailable.person", self.dup)
        elsewhere = self.row_for("care.FollowUp.owner", self.dup)
        private = self.row_for("resources.DevotionalRead.person", self.dup)
        Person.objects.filter(pk=self.dup.pk).update(phone="55 9999 0000")
        record = self.do_merge(phone=self.dup.pk)
        Unavailable.objects.filter(pk=gone.pk).delete()
        type(elsewhere).objects.filter(pk=elsewhere.pk).update(owner=self.leader)
        type(private).objects.filter(pk=private.pk).delete()
        Person.objects.filter(pk=self.keep.pk).update(phone="55 1234 5678")
        later = groups.set_membership(self.group, Person.objects.get(pk=self.keep.pk), self.admin)

        undone = merge.undo(self.church, record.pk, self.admin)
        self.assertEqual(GroupMembership.objects.get(pk=membership.pk).person_id, self.dup.pk)  # still there: back
        self.assertEqual(type(elsewhere).objects.get(pk=elsewhere.pk).owner_id, self.leader.pk)  # points elsewhere: left alone
        self.assertEqual(GroupMembership.objects.get(pk=later.pk).person_id, self.keep.pk)  # gained later: stays
        self.assertEqual(Person.objects.get(pk=self.keep.pk).phone, "55 1234 5678")  # changed later: not overwritten
        self.assertEqual(undone.undo_report["not_restored"], {"serving.Unavailable.person": 1, "care.FollowUp.owner": 1})
        self.assertEqual(undone.undo_report["fields_not_restored"], ["phone"])
        lines = merge.undo_report_lines(undone)
        self.assertIn("1 día marcado como «no puedo»", lines)
        self.assertIn("1 seguimiento a su cargo", lines)
        # A private mark that could not return is said without a number and without which one.
        self.assertTrue(undone.undo_report["private_not_restored"])
        self.assertNotIn("resources.DevotionalRead.person", undone.undo_report["not_restored"])
        self.assertIn("algunas de sus marcas privadas de lectura", lines)


class ConflictTests(MergeFixture):
    """The survivor already has the equivalent row: it stays behind when that is harmless, and the merge is refused when it is not."""

    def test_a_live_membership_of_the_same_group_refuses_and_a_past_one_stays(self):
        groups.set_membership(self.other_group, self.keep, self.admin)
        mine = groups.set_membership(self.other_group, self.dup, self.admin, role="lider")
        self.assert_refused("Las dos están en un mismo grupo")
        groups.end_membership(mine, self.admin)
        self.assertEqual(self.refusals(), [])
        record = self.do_merge()
        self.assertEqual(GroupMembership.objects.get(pk=mine.pk).person_id, self.dup.pk)
        self.assertNotIn("groups.GroupMembership.person", record.manifest["moved"])
        self.assertEqual(self.membership(self.other_group, self.keep).role, "integrante")  # the role of the row left behind is not copied

    def test_a_live_membership_of_the_same_team_refuses_and_a_past_one_stays(self):
        serving.set_member(self.team, self.keep, self.admin)
        serving.set_member(self.team, self.dup, self.admin, role="coordina")
        self.assert_refused("Las dos están en un mismo equipo")
        serving.remove_member(self.team, self.dup, self.admin)
        self.assertEqual(self.refusals(), [])
        self.do_merge()
        self.assertEqual(self.team_membership(self.team, self.dup).status, "inactiva")
        self.assertEqual(self.team_membership(self.team, self.keep).role, "sirve")

    def test_two_pending_requests_to_one_group_refuse(self):
        for person in (self.keep, self.dup):
            GroupJoinRequest.objects.create(group=self.group, person=person)
        self.assert_refused("pidieron unirse al mismo grupo")
        GroupJoinRequest.objects.filter(person=self.keep).update(status="declinada")
        self.assertEqual(self.refusals(), [])  # only a pending pair collides

    def test_a_registration_to_the_same_event_refuses_whatever_its_state(self):
        EventRegistration.objects.create(event=self.other_event, person=self.keep, status="cancelada")
        EventRegistration.objects.create(event=self.other_event, person=self.dup)
        self.assert_refused("inscripción al mismo evento")

    def test_an_open_request_to_serve_the_same_day_refuses_and_a_past_one_stays(self):
        for person in (self.keep, self.dup):
            Assignment.objects.create(service_date=self.day, person=person)
        self.assert_refused("servir en una misma fecha")
        ServiceDate.objects.filter(pk=self.day.pk).update(date=timezone.localdate() - timedelta(days=1))
        self.assertEqual(self.refusals(), [])
        self.do_merge()
        self.assertEqual(Assignment.objects.filter(person=self.dup).count(), 1)

    def test_the_same_fact_twice_stays_on_the_absorbed_record(self):
        for key in ("groups.GroupAttendance.person", "notices.AnnouncementRead.person", "notices.NoticeEmail.person",
                    "resources.DevotionalRead.person", "resources.ReadingPlanProgress.person", "serving.Unavailable.person",
                    "people.CampusAffiliation.person"):
            self.row_for(key, self.keep)
            self.row_for(key, self.dup)
        EmailPreference.objects.create(person=self.keep, choice="none")
        EmailPreference.objects.create(person=self.dup, choice="all")
        preview = merge.build_preview(self.church, self.keep, self.dup)
        self.assertEqual(preview.refusals, [])
        self.assertIn("1 asistencia a una reunión de grupo", preview.stay_lines)
        record = self.do_merge()
        self.assertEqual(record.manifest["moved"], {})
        self.assertEqual(EmailPreference.objects.get(person=self.keep).choice, "none")  # the survivor's own choice wins
        self.assertEqual(GroupAttendance.objects.filter(meeting=self.meeting).count(), 2)  # nothing is deleted

    def test_reading_marks_move_with_their_plan_and_are_never_counted(self):
        progress = ReadingPlanProgress.objects.create(plan=self.plan, person=self.dup)
        mark = ReadingPlanDayRead.objects.create(progress=progress, day=ReadingPlanDay.objects.create(plan=self.plan, position=1, reference="Juan 15"))
        self.row_for("resources.DevotionalRead.person", self.dup)
        self.row_for("feedback.FeedbackGiven.person", self.dup)
        preview = merge.build_preview(self.church, self.keep, self.dup)
        self.assertEqual(preview.move_lines, [])  # not a number, not a word
        self.do_merge()
        self.assertEqual(ReadingPlanDayRead.objects.get(pk=mark.pk).progress.person_id, self.keep.pk)

    def test_an_opinion_token_is_neither_read_nor_copied(self):
        feedback.FeedbackGiven.objects.create(event=self.other_event, token=feedback.token_for(self.other_event, self.dup))
        before = list(FeedbackGiven.objects.values_list("event_id", "token", "person_id"))
        self.do_merge()
        self.assertEqual(list(FeedbackGiven.objects.values_list("event_id", "token", "person_id")), before)


class CampLinkTests(MergeFixture):
    def test_the_link_follows_through_its_own_services_and_comes_back(self):
        Person.objects.filter(pk__in=[self.keep.pk, self.dup.pk]).update(birth_date="1990-01-01")
        old = services.link_participant(self.dup, self.p1, self.admin)
        answers = self.p1.interviews.count()
        audited = ChurchAuditEvent.objects.count()
        record = self.do_merge()
        link = EventParticipantLink.objects.get(participant=self.p1)
        self.assertEqual((link.person_id, link.linked_by), (self.keep.pk, self.admin))
        self.assertNotEqual(link.pk, old.pk)  # undone and made again, as an administrator would
        actions = list(ChurchAuditEvent.objects.order_by("id").values_list("action", flat=True))[audited:]
        self.assertEqual(actions, ["persona_desvinculada", "persona_vinculada", "persona_union"])
        self.assertEqual(record.manifest["camp_participants"], [self.p1.pk])
        self.assertEqual(self.p1.interviews.count(), answers)
        self.assertIn("1 vínculo con un evento en el que participó", merge.Preview._counted({merge.CAMP_KEY: 1}))
        merge.undo(self.church, record.pk, self.admin)
        self.assertEqual(EventParticipantLink.objects.get(participant=self.p1).person_id, self.dup.pk)

    def test_two_camperos_of_one_event_are_never_one_person(self):
        Person.objects.filter(pk__in=[self.keep.pk, self.dup.pk]).update(birth_date="1990-01-01")
        services.link_participant(self.keep, self.p3, self.admin)
        services.link_participant(self.dup, self.p1, self.admin)
        self.assert_refused("campero del mismo evento")

    def test_a_link_to_an_event_that_is_running_again_refuses(self):
        Person.objects.filter(pk__in=[self.keep.pk, self.dup.pk]).update(birth_date="1990-01-01")
        services.link_participant(self.dup, self.p1, self.admin)
        type(self.event).objects.filter(pk=self.event.pk).update(status="active", ends_on=timezone.localdate() + timedelta(days=3))
        self.assert_refused("no ha terminado")


class EarlierMergeTests(MergeFixture):
    def test_a_record_that_received_a_merge_cannot_be_absorbed_while_that_can_be_undone(self):
        third = Person.objects.create(church=self.church, first_name="Lía", last_name="L.", email="lia@prueba.mx")
        first = self.do_merge()  # dup -> keep
        self.assert_refused_pair("recibió otra unión", third, self.keep)
        PersonMerge.objects.filter(pk=first.pk).update(merged_at=timezone.now() - timedelta(days=merge.UNDO_DAYS + 1))
        second = self.do_merge(third, self.keep)
        self.dup.refresh_from_db()
        self.assertEqual(self.dup.merged_into_id, self.keep.pk)  # a chain inside the church; the old id stays hidden
        self.assert_integrity()
        # The middle record is hidden now: the second merge is undone before anything else about it.
        with self.assertRaises(merge.MergeRefused):
            merge.undo(self.church, first.pk, self.admin)
        merge.undo(self.church, second.pk, self.admin)

    def assert_refused_pair(self, words, survivor, absorbed):
        reasons = self.refusals(survivor, absorbed)
        self.assertTrue(any(words in reason for reason in reasons), reasons)
        with self.assertRaises(merge.MergeRefused):
            self.do_merge(survivor, absorbed)

    def test_the_undo_waits_for_a_later_merge_of_the_survivor(self):
        third = Person.objects.create(church=self.church, first_name="Lía", last_name="L.", email="lia@prueba.mx")
        first = self.do_merge()  # dup -> keep
        PersonMerge.objects.filter(pk=first.pk).update(merged_at=timezone.now() - timedelta(days=merge.UNDO_DAYS - 1))
        # keep cannot be absorbed (its merge can still be undone), so the later merge is forced the plain way:
        Person.objects.filter(pk=self.keep.pk).update(merged_into=third)
        with self.assertRaisesMessage(merge.MergeRefused, "fue unida después a otra"):
            merge.undo(self.church, first.pk, self.admin)

    def test_people_in_a_merge_are_not_deleted_from_under_it(self):
        self.do_merge()
        for person in (self.keep, self.dup):
            with self.assertRaises(ProtectedError):
                person.delete()

    def test_a_dismissal_is_about_that_pair_of_records_and_does_not_move(self):
        low, high = sorted([self.dup.pk, self.leader.pk])
        row = DuplicateDismissal.objects.create(church=self.church, person_a_id=low, person_b_id=high)
        self.do_merge()
        row.refresh_from_db()
        self.assertEqual((row.person_a_id, row.person_b_id), (low, high))


class ConsentFollowsItsRegistrationTests(MergeFixture):
    def test_with_the_same_date_of_birth_the_authorization_moves_with_its_registration_and_only_there(self):
        born = timezone.localdate() - timedelta(days=15 * 365)
        Person.objects.filter(pk__in=[self.keep.pk, self.dup.pk]).update(birth_date=born)
        registration = EventRegistration.objects.create(event=self.other_event, person=self.dup, status="pendiente", pending_reason="consent")
        consent = GuardianConsent.objects.create(registration=registration, guardian_name="Mamá de prueba", relationship="madre", method="papel")
        self.assertEqual(self.refusals(), [])
        self.do_merge()
        registration.refresh_from_db()
        self.assertEqual((registration.person_id, registration.status, registration.pending_reason), (self.keep.pk, "pendiente", "consent"))
        self.assertEqual(GuardianConsent.objects.get().pk, consent.pk)
        self.assertEqual(GuardianConsent.objects.get().registration_id, registration.pk)
        self.keep.refresh_from_db()
        self.assertEqual(self.keep.birth_date, born)
