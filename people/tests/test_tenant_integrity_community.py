"""``manage.py tenant_integrity`` over the church modules added after the camp (wave 7).

As in ``test_tenant_integrity``: each violation is one row written through the ORM, never through
the services, which would refuse it. Every test plants exactly one and expects it counted alone.
"""

import json
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from access.models import RoleAssignment
from agenda.models import EventRegistration, GuardianConsent
from care.models import FollowUp, FollowUpStep
from espacios.models import Booking, Space
from events.models import Sede
from feedback.models import FeedbackGiven
from groups.models import GroupAttendance, GroupJoinRequest, GroupMeeting, GroupMembership
from groups.tests.base import CommunityFixture
from notices.models import Announcement, AnnouncementRead, NoticeEmail
from people.management.commands.tenant_integrity import ID_LIMIT, camp_checks, checks, community_checks
from people.models import (
    CampusAffiliation, DuplicateDismissal, EventParticipantLink, PeopleImport, Person, PersonMerge, PersonTag, Tag,
)
from people.management.commands.tenant_integrity import PRIVATE_SIZES
from resources.models import (
    Devotional, DevotionalRead, MediaItem, ReadingPlan, ReadingPlanDay, ReadingPlanDayRead, ReadingPlanProgress,
)
from serving import services as serving
from serving.models import Assignment, PlanItem, RoleNeed, RotationEntry, ServiceDate, SwapRequest, TeamMembership, TeamRole
from sitio.models import PublicMedia

from .test_tenant_integrity import json_counts, mismatches, run_integrity

KEYS = [check.key for check in community_checks()]
ALL_KEYS = [check.key for check in checks()]


class CommunityIntegrityTests(CommunityFixture):
    """The community fixture (churches A and B, people, groups, a linked campero) plus one valid row
    for every check, so each of them reads something."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        today = timezone.localdate()
        cls.sede_a = Sede.objects.create(church=cls.church, name="Satélite", slug="satelite")
        cls.sede_b = Sede.objects.create(church=cls.church_b, name="Mérida", slug="merida")
        cls.old = Person.objects.create(church=cls.church, first_name="Dora", last_name="Duplicada", merged_into=cls.member)
        CampusAffiliation.objects.create(person=cls.member, sede=cls.sede_a)
        PersonMerge.objects.create(church=cls.church, survivor=cls.member, absorbed=cls.old)
        low, high = sorted([cls.leader, cls.asked], key=lambda person: person.pk)
        DuplicateDismissal.objects.create(church=cls.church, person_a=low, person_b=high)
        cls.tag_b = Tag.objects.create(church=cls.church_b, name="Coro B", normalized_name="coro b")
        PersonTag.objects.create(person=cls.leader, tag=Tag.objects.create(church=cls.church, name="Coro", normalized_name="coro"))
        cls.import_b = PeopleImport.objects.create(church=cls.church_b, created_count=1)
        Person.objects.create(church=cls.church, first_name="Iris", last_name="Importada",
                              imported_in=PeopleImport.objects.create(church=cls.church, created_count=1))
        type(cls.group).objects.filter(pk=cls.group.pk).update(sede=cls.sede_a)
        cls.meeting = GroupMeeting.objects.create(group=cls.group, date=today)
        cls.meeting_b = GroupMeeting.objects.create(group=cls.group_b, date=today)
        GroupAttendance.objects.create(meeting=cls.meeting, person=cls.leader)
        cls.notice = Announcement.objects.create(church=cls.church, title="Aviso", body="Texto", group=cls.group)
        cls.notice_b = Announcement.objects.create(church=cls.church_b, title="Aviso B", body="Texto")
        AnnouncementRead.objects.create(announcement=cls.notice, person=cls.leader)
        NoticeEmail.objects.create(announcement=cls.notice, version=1, person=cls.leader)
        cls.registration = EventRegistration.objects.create(event=cls.event, person=cls.leader)
        cls.registration_b = EventRegistration.objects.create(event=cls.event_b, person=cls.member_b)
        GuardianConsent.objects.create(registration=cls.registration, guardian_name="Mamá de prueba", relationship="madre", method="papel")
        FeedbackGiven.objects.create(event=cls.event, person=cls.leader)
        cls.devotional = Devotional.objects.create(church=cls.church, date=today, title="Hoy", body="Texto")
        cls.devotional_b = Devotional.objects.create(church=cls.church_b, date=today, title="Hoy B", body="Texto")
        DevotionalRead.objects.create(devotional=cls.devotional, person=cls.leader)
        cls.plan = ReadingPlan.objects.create(church=cls.church, title="Plan", status="publicado")
        cls.plan_b = ReadingPlan.objects.create(church=cls.church_b, title="Plan B", status="publicado")
        cls.plan_day = ReadingPlanDay.objects.create(plan=cls.plan, position=1, reference="Juan 15:1-8")
        cls.plan_day_b = ReadingPlanDay.objects.create(plan=cls.plan_b, position=1, reference="Juan 15:1-8")
        cls.plan_progress = ReadingPlanProgress.objects.create(plan=cls.plan, person=cls.leader)
        ReadingPlanDayRead.objects.create(progress=cls.plan_progress, day=cls.plan_day)
        cls.media = MediaItem.objects.create(church=cls.church, title="Predicación", url="https://videos.invalid/a")
        cls.media_b = MediaItem.objects.create(church=cls.church_b, title="Predicación B", url="https://videos.invalid/b")
        PublicMedia.objects.create(item=cls.media, church=cls.church)
        cls.team = serving.save_team(cls.church, cls.admin, name="Bienvenida")
        cls.team_b = serving.save_team(cls.church_b, cls.admin_b, name="Equipo B")
        serving.set_member(cls.team, cls.leader, cls.admin, role="coordina")
        serving.set_member(cls.team_b, cls.member_b, cls.admin_b, role="coordina")
        cls.day = serving.save_date(cls.team, cls.admin, date=today + timedelta(days=7), what="Domingo")
        cls.day_b = serving.save_date(cls.team_b, cls.admin_b, date=today + timedelta(days=7), what="Domingo B")
        cls.space = Space.objects.create(church=cls.church, name="Salón de prueba")
        cls.space_b = Space.objects.create(church=cls.church_b, name="Salón B")
        start = timezone.now() + timedelta(days=3)
        cls.booking = Booking.objects.create(
            space=cls.space, title="Ensayo", starts_at=start, ends_at=start + timedelta(hours=1), requested_by=cls.leader, event=cls.event,
        )
        cls.booking_b = Booking.objects.create(
            space=cls.space_b, title="Ensayo B", starts_at=start, ends_at=start + timedelta(hours=1), requested_by=cls.member_b, event=cls.event_b,
        )
        serving.assign(cls.day, cls.leader, cls.admin)
        # Wave 10 of Servir: a role of each team, and one valid row of each new table in church A.
        cls.team_role = TeamRole.objects.create(team=cls.team, name="Puerta")
        cls.team_role_b = TeamRole.objects.create(team=cls.team_b, name="Puerta B")
        serving.set_member(cls.team, cls.member, cls.admin)
        cls.assignment = Assignment.objects.get(service_date=cls.day, person=cls.leader)
        Assignment.objects.filter(pk=cls.assignment.pk).update(role=cls.team_role)
        cls.need = RoleNeed.objects.create(service_date=cls.day, role=cls.team_role, count=1)
        cls.rotation_entry = RotationEntry.objects.create(role=cls.team_role, person=cls.leader, position=1)
        cls.swap = SwapRequest.objects.create(assignment=cls.assignment, from_person=cls.leader, to_person=cls.member)
        cls.plan_item = PlanItem.objects.create(service_date=cls.day, position=1, title="Bienvenida", minutes=5, responsible=cls.leader)
        cls.care = FollowUp.objects.create(church=cls.church, person=cls.member, owner=cls.leader, reason="visita")
        cls.care_b = FollowUp.objects.create(church=cls.church_b, person=cls.member_b, reason="visita")
        FollowUpStep.objects.create(follow_up=cls.care, kind="nota", by=cls.leader)
        cls.form, cls.form_version, cls.form_answer = cls._form_answer(cls.church, cls.member)
        cls.form_b, cls.form_version_b, cls.form_answer_b = cls._form_answer(cls.church_b, cls.member_b)

    @staticmethod
    def _form_answer(church, person):
        from formularios.models import Form, FormVersion, Submission

        form = Form.objects.create(church=church, title="Convivio", purpose="Para organizar", opens_on=timezone.localdate(),
                                   closes_on=timezone.localdate())
        version = FormVersion.objects.create(form=form, number=1, title=form.title, purpose=form.purpose, schema=[])
        return form, version, Submission.objects.create(form=form, version=version, person=person, purpose_shown=form.purpose)

    # One builder per check: each returns the row that disagrees.
    def break_person_merged_into_church(self):
        return Person.objects.create(church=self.church, first_name="Otra", last_name="Duplicada", merged_into=self.member_b)

    def break_campus_affiliation_sede_church(self):
        return CampusAffiliation.objects.create(person=self.leader, sede=self.sede_b)

    def break_participant_link_person_church(self):
        return EventParticipantLink.objects.create(participant=self.p1, person=self.member_b, event=self.event)

    def break_participant_link_participant_event(self):
        # Same church, a campero of the church's other event: only the event pair disagrees.
        return EventParticipantLink.objects.create(participant=self.p3, person=self.leader, event=self.other_event)

    def break_person_merge_church(self):
        absorbed = Person.objects.create(church=self.church, first_name="Unida", last_name="Cruzada")
        return PersonMerge.objects.create(church=self.church, survivor=self.member_b, absorbed=absorbed)

    def break_person_tag_church(self):
        return PersonTag.objects.create(person=self.leader, tag=self.tag_b)

    def break_person_import_church(self):
        return Person.objects.create(church=self.church, first_name="Otra", last_name="Importada", imported_in=self.import_b)

    def break_duplicate_dismissal_church(self):
        low, high = sorted([self.member, self.member_b], key=lambda person: person.pk)
        return DuplicateDismissal.objects.create(church=self.church, person_a=low, person_b=high)

    def break_merged_person_group_membership(self):
        return GroupMembership.objects.create(group=self.other_group, person=self.old)

    def break_merged_person_join_request(self):
        return GroupJoinRequest.objects.create(group=self.other_group, person=self.old)

    def break_merged_person_registration(self):
        return EventRegistration.objects.create(event=self.other_event, person=self.old, status="pendiente", pending_reason="age")

    def break_merged_person_team_membership(self):
        return TeamMembership.objects.create(team=self.team, person=self.old)  # no service: no role row either

    def break_group_sede_church(self):
        type(self.other_group).objects.filter(pk=self.other_group.pk).update(sede=self.sede_b)
        return self.other_group

    def break_group_membership_person_church(self):
        return GroupMembership.objects.create(group=self.group, person=self.member_b)

    def break_group_join_request_person_church(self):
        return GroupJoinRequest.objects.create(group=self.group_b, person=self.member)

    def break_group_attendance_person_church(self):
        return GroupAttendance.objects.create(meeting=self.meeting_b, person=self.member)

    def break_announcement_group_church(self):
        return Announcement.objects.create(church=self.church, title="Cruzado", body="Texto", group=self.group_b)

    def break_announcement_read_person_church(self):
        return AnnouncementRead.objects.create(announcement=self.notice_b, person=self.member)

    def break_notice_email_person_church(self):
        return NoticeEmail.objects.create(announcement=self.notice, version=1, person=self.member_b)

    def break_registration_person_church(self):
        return EventRegistration.objects.create(event=self.other_event, person=self.member_b)

    def break_consent_registration_church(self):
        # The registration itself is the finding of the check above; here both are planted and both expected.
        registration = EventRegistration.objects.create(event=self.event, person=self.admin_b_person)
        return GuardianConsent.objects.create(registration=registration, guardian_name="Papá de prueba", relationship="padre", method="papel")

    def break_feedback_given_person_church(self):
        return FeedbackGiven.objects.create(event=self.event_b, person=self.member)

    def break_devotional_read_person_church(self):
        return DevotionalRead.objects.create(devotional=self.devotional_b, person=self.member)

    def break_reading_plan_progress_person_church(self):
        return ReadingPlanProgress.objects.create(plan=self.plan_b, person=self.member)

    def break_reading_plan_day_read_plan(self):
        # The person's own plan, a day of another plan (here another church's): only the pair of plans disagrees.
        progress = ReadingPlanProgress.objects.create(plan=self.plan, person=self.member)
        return ReadingPlanDayRead.objects.create(progress=progress, day=self.plan_day_b)

    def break_public_media_item_church(self):
        # Church A marks a resource of church B as public: the page of neither shows it, and this counts it.
        return PublicMedia.objects.create(item=self.media_b, church=self.church)

    def break_team_membership_person_church(self):
        return TeamMembership.objects.create(team=self.team_b, person=self.asked)  # no service: no role row either

    def break_assignment_person_church(self):
        return Assignment.objects.create(service_date=self.day_b, person=self.member)

    def break_assignment_role_team(self):
        Assignment.objects.filter(pk=self.assignment.pk).update(role=self.team_role_b)
        return self.assignment

    def break_role_need_role_team(self):
        RoleNeed.objects.filter(pk=self.need.pk).update(role=self.team_role_b)
        return self.need

    def break_rotation_entry_person_church(self):
        return RotationEntry.objects.create(role=self.team_role, person=self.member_b, position=2)

    def break_swap_request_from_person_church(self):
        SwapRequest.objects.filter(pk=self.swap.pk).update(from_person=self.member_b)
        return self.swap

    def break_swap_request_to_person_church(self):
        return SwapRequest.objects.create(assignment=self.assignment, from_person=self.leader, to_person=self.member_b, status="declinada")

    def break_plan_item_responsible_church(self):
        return PlanItem.objects.create(service_date=self.day, position=2, title="Cierre", minutes=5, responsible=self.member_b)

    def break_care_follow_up_person_church(self):
        return FollowUp.objects.create(church=self.church, person=self.member_b, reason="visita")

    def break_care_follow_up_owner_church(self):
        FollowUp.objects.filter(pk=self.care_b.pk).update(owner=self.leader)
        return self.care_b

    def break_care_step_by_church(self):
        return FollowUpStep.objects.create(follow_up=self.care_b, kind="nota", by=self.leader)

    def break_booking_requester_church(self):
        Booking.objects.filter(pk=self.booking_b.pk).update(requested_by=self.leader)
        return self.booking_b

    def break_booking_event_church(self):
        Booking.objects.filter(pk=self.booking.pk).update(event=self.event_b)
        return self.booking

    def break_form_submission_person_church(self):
        type(self.form_answer_b).objects.filter(pk=self.form_answer_b.pk).update(person=self.leader)
        return self.form_answer_b

    def break_form_submission_version_form(self):
        type(self.form_answer).objects.filter(pk=self.form_answer.pk).update(version=self.form_version_b)
        return self.form_answer

    def break_role_assignment_scope_church(self):
        return RoleAssignment.objects.create(
            subject_user=self.member_user, church=self.church, scope_type="group", scope_id=self.group_b.pk,
            role="lider", source_table="prueba.Cruzada", source_id=1,
        )

    def break_prayer_request_person_church(self):
        from oracion.models import PrayerRequest

        return PrayerRequest.objects.create(church=self.church, person=self.member_b, body="x", expires_at=timezone.now())

    def expected(self, key) -> dict:
        also = {"consent_registration_church": {"registration_person_church"}}.get(key, set())
        return {k: int(k == key or k in also) for k in ALL_KEYS}

    def assert_counted_alone_and_fails(self, key):
        row = getattr(self, f"break_{key}")()
        payload, code = json_counts()
        expected = self.expected(key)
        self.assertEqual(mismatches(payload), expected)
        errors = sum(expected.values())
        self.assertEqual((code, payload["ok"], payload["errors"], payload["warnings"]), (1, False, errors, 0))
        # --ids names the row by its primary key and by nothing else.
        with_ids = json.loads(run_integrity("--json", "--ids")[0])
        self.assertEqual(with_ids["checks"][key]["ids"], [row.pk])
        self.assertNotIn("ids", payload["checks"][key])
        return row

    # ---- a valid fixture --------------------------------------------------------------------------
    def test_valid_data_counts_zero_and_every_check_reads_rows(self):
        payload, code = json_counts()
        self.assertEqual(code, 0)
        self.assertEqual(mismatches(payload), dict.fromkeys(ALL_KEYS, 0))
        for key in KEYS:
            if key in PRIVATE_SIZES:  # what a person reads: the table's size is never printed
                self.assertIsNone(payload["checks"][key]["checked"], key)
                continue
            self.assertGreater(payload["checks"][key]["checked"], 0, key)
        self.assertEqual((payload["ok"], payload["errors"], payload["warnings"]), (True, 0, 0))
        out, message, code = run_integrity("--ids")
        self.assertEqual((message, code), ("", 0))
        self.assertIn("Resultado: sin discrepancias.", out)
        self.assertNotIn(", id", out)

    def test_there_is_a_builder_and_a_test_for_every_check(self):
        self.assertEqual(len(set(ALL_KEYS)), len(ALL_KEYS))
        self.assertEqual(ALL_KEYS, [check.key for check in camp_checks()] + KEYS)
        for key in KEYS:
            self.assertTrue(hasattr(self, f"break_{key}"), key)
            self.assertTrue(hasattr(self, f"test_{key}"), key)

    def test_it_writes_nothing(self):
        from django.apps import apps

        for key in KEYS:
            getattr(self, f"break_{key}")()
        models = [model for model in apps.get_models() if not model._meta.proxy]
        before = {model._meta.label: model._base_manager.count() for model in models}
        with self.assertNumQueries(len({check.model for check in checks()}) + len(KEYS)):
            run_integrity("--ids")
        self.assertEqual({model._meta.label: model._base_manager.count() for model in models}, before)

    # ---- one violation of each kind -----------------------------------------------------------------
    def test_person_merged_into_church(self):
        self.assert_counted_alone_and_fails("person_merged_into_church")

    def test_campus_affiliation_sede_church(self):
        self.assert_counted_alone_and_fails("campus_affiliation_sede_church")

    def test_participant_link_person_church(self):
        self.assert_counted_alone_and_fails("participant_link_person_church")

    def test_participant_link_participant_event(self):
        self.assert_counted_alone_and_fails("participant_link_participant_event")

    def test_person_merge_church(self):
        self.assert_counted_alone_and_fails("person_merge_church")

    def test_person_tag_church(self):
        self.assert_counted_alone_and_fails("person_tag_church")

    def test_person_import_church(self):
        self.assert_counted_alone_and_fails("person_import_church")

    def test_duplicate_dismissal_church(self):
        self.assert_counted_alone_and_fails("duplicate_dismissal_church")

    def test_merged_person_group_membership(self):
        row = self.assert_counted_alone_and_fails("merged_person_group_membership")
        GroupMembership.objects.filter(pk=row.pk).update(status="inactiva")  # history may stay on a merged record
        self.assertEqual(json_counts()[1], 0)

    def test_merged_person_join_request(self):
        row = self.assert_counted_alone_and_fails("merged_person_join_request")
        GroupJoinRequest.objects.filter(pk=row.pk).update(status="retirada")
        self.assertEqual(json_counts()[1], 0)

    def test_merged_person_registration(self):
        row = self.assert_counted_alone_and_fails("merged_person_registration")
        EventRegistration.objects.filter(pk=row.pk).update(status="cancelada", pending_reason="")
        self.assertEqual(json_counts()[1], 0)

    def test_merged_person_team_membership(self):
        row = self.assert_counted_alone_and_fails("merged_person_team_membership")
        TeamMembership.objects.filter(pk=row.pk).update(status="inactiva")
        self.assertEqual(json_counts()[1], 0)

    def test_group_sede_church(self):
        self.assert_counted_alone_and_fails("group_sede_church")

    def test_group_membership_person_church(self):
        self.assert_counted_alone_and_fails("group_membership_person_church")

    def test_group_join_request_person_church(self):
        self.assert_counted_alone_and_fails("group_join_request_person_church")

    def test_group_attendance_person_church(self):
        self.assert_counted_alone_and_fails("group_attendance_person_church")

    def test_announcement_group_church(self):
        self.assert_counted_alone_and_fails("announcement_group_church")

    def test_announcement_read_person_church(self):
        self.assert_counted_alone_and_fails("announcement_read_person_church")

    def test_notice_email_person_church(self):
        self.assert_counted_alone_and_fails("notice_email_person_church")

    def test_registration_person_church(self):
        self.assert_counted_alone_and_fails("registration_person_church")

    def test_consent_registration_church(self):
        self.assert_counted_alone_and_fails("consent_registration_church")

    def test_feedback_given_person_church(self):
        self.assert_counted_alone_and_fails("feedback_given_person_church")

    def test_devotional_read_person_church(self):
        self.assert_counted_alone_and_fails("devotional_read_person_church")

    def test_reading_plan_progress_person_church(self):
        self.assert_counted_alone_and_fails("reading_plan_progress_person_church")

    def test_reading_plan_day_read_plan(self):
        self.assert_counted_alone_and_fails("reading_plan_day_read_plan")

    def test_public_media_item_church(self):
        self.assert_counted_alone_and_fails("public_media_item_church")

    def test_team_membership_person_church(self):
        self.assert_counted_alone_and_fails("team_membership_person_church")

    def test_assignment_person_church(self):
        self.assert_counted_alone_and_fails("assignment_person_church")

    def test_assignment_role_team(self):
        self.assert_counted_alone_and_fails("assignment_role_team")

    def test_role_need_role_team(self):
        self.assert_counted_alone_and_fails("role_need_role_team")

    def test_rotation_entry_person_church(self):
        self.assert_counted_alone_and_fails("rotation_entry_person_church")

    def test_swap_request_from_person_church(self):
        self.assert_counted_alone_and_fails("swap_request_from_person_church")

    def test_swap_request_to_person_church(self):
        self.assert_counted_alone_and_fails("swap_request_to_person_church")

    def test_plan_item_responsible_church(self):
        self.assert_counted_alone_and_fails("plan_item_responsible_church")

    def test_care_follow_up_person_church(self):
        self.assert_counted_alone_and_fails("care_follow_up_person_church")

    def test_care_follow_up_owner_church(self):
        self.assert_counted_alone_and_fails("care_follow_up_owner_church")

    def test_care_step_by_church(self):
        self.assert_counted_alone_and_fails("care_step_by_church")

    def test_booking_requester_church(self):
        self.assert_counted_alone_and_fails("booking_requester_church")

    def test_booking_event_church(self):
        self.assert_counted_alone_and_fails("booking_event_church")

    def test_form_submission_person_church(self):
        self.assert_counted_alone_and_fails("form_submission_person_church")

    def test_form_submission_version_form(self):
        self.assert_counted_alone_and_fails("form_submission_version_form")

    def test_role_assignment_scope_church(self):
        self.assert_counted_alone_and_fails("role_assignment_scope_church")

    def test_prayer_request_person_church(self):
        self.assert_counted_alone_and_fails("prayer_request_person_church")

    def test_a_role_of_every_scope_type_that_points_outside_its_church(self):
        def role(scope_type, scope_id, source_id):
            return RoleAssignment.objects.create(
                subject_user=self.member_user, church=self.church, scope_type=scope_type, scope_id=scope_id,
                role="x", source_table="prueba.Cruzada", source_id=source_id,
            )

        good = [role("church", self.church.pk, 1), role("event", self.event.pk, 2), role("group", self.group.pk, 3),
                role("ministry", self.team.pk, 4)]
        self.assertEqual(mismatches(json_counts()[0])["role_assignment_scope_church"], 0)
        bad = [role("church", self.church_b.pk, 5), role("event", self.event_b.pk, 6), role("group", self.group_b.pk, 7),
               role("ministry", self.team_b.pk, 8), role("group", 999_999, 9)]  # the last one: a group that is gone
        payload = json.loads(run_integrity("--json", "--ids")[0])
        self.assertEqual(payload["checks"]["role_assignment_scope_church"]["ids"], [row.pk for row in bad])
        self.assertTrue(good)

    # ---- what it prints -----------------------------------------------------------------------------
    def test_every_violation_at_once_prints_counts_and_ids_and_never_a_name(self):
        rows = {key: getattr(self, f"break_{key}")() for key in KEYS}
        payload, code = json_counts()
        counts = mismatches(payload)
        self.assertEqual({k: counts[k] for k in KEYS}, {k: 2 if k == "registration_person_church" else 1 for k in KEYS})
        self.assertEqual((code, payload["ok"], payload["errors"]), (1, False, len(KEYS) + 1))
        plain, message, code = run_integrity()
        self.assertEqual(code, 1)
        self.assertEqual(plain.count("Revisar"), len(KEYS))
        self.assertNotIn(", id", plain)
        out, _, _ = run_integrity("--ids")
        for key, row in rows.items():
            self.assertIn(f"{row._meta.label}, id: ", out, key)
        json_out, _, _ = run_integrity("--json", "--ids")
        secrets = [
            *Person.objects.values_list("last_name", flat=True), *Person.objects.exclude(email="").values_list("email", flat=True),
            "55 1111 2222", self.church.name, self.church_b.name, self.group.name, self.group_b.name, "Equipo B", "Bienvenida",
            "Mamá de prueba", "Papá de prueba", "Aviso B", "Cruzado", "Domingo B", self.event.slug, self.event_b.name,
        ]
        for text in (plain, out, message, json_out):
            for secret in secrets:
                self.assertNotIn(secret, text)

    def test_ids_are_capped(self):
        ServiceDate.objects.filter(pk=self.day_b.pk).update(date=timezone.localdate())
        people = Person.objects.bulk_create(
            Person(church=self.church, first_name="Persona", last_name=f"Número {n}") for n in range(ID_LIMIT + 3)
        )
        Assignment.objects.bulk_create(Assignment(service_date=self.day_b, person=person) for person in people)
        payload = json.loads(run_integrity("--json", "--ids")[0])
        entry = payload["checks"]["assignment_person_church"]
        self.assertEqual((entry["mismatched"], len(entry["ids"])), (ID_LIMIT + 3, ID_LIMIT))
        self.assertIn(f"(los primeros {ID_LIMIT})", run_integrity("--ids")[0])


class EmptyDatabaseTests(TestCase):
    def test_a_clean_database_passes_with_nothing_read(self):
        payload, code = json_counts()
        self.assertEqual((code, payload["ok"], payload["errors"], payload["warnings"]), (0, True, 0, 0))
        self.assertEqual(list(payload["checks"]), ALL_KEYS)
        for key, entry in payload["checks"].items():
            self.assertEqual((entry["checked"] or 0, entry["mismatched"]), (0, 0), key)
        out, message, code = run_integrity()
        self.assertEqual((message, code), ("", 0))
        self.assertIn("Resultado: sin discrepancias.", out)


class CommunitySeedsHaveNoMismatchesTests(TestCase):
    def test_every_synthetic_seed_yields_zero_and_exercises_the_checks(self):
        with override_settings(DEBUG=True):
            call_command("seed_camp_demo", "--participants", "20", "--hours", "30", password="x", stdout=StringIO())
            call_command("seed_dev_data", password="x", stdout=StringIO())
            call_command("seed_second_church", password="x", stdout=StringIO())
            call_command("seed_community_demo", "--panorama", "--eventos", password="x", stdout=StringIO())
        payload, code = json_counts()
        self.assertEqual((code, payload["ok"]), (0, True))
        self.assertEqual(mismatches(payload), dict.fromkeys(ALL_KEYS, 0))
        read = {key for key in KEYS if payload["checks"][key]["checked"]}
        for key in ("group_membership_person_church", "team_membership_person_church", "assignment_person_church",
                    "registration_person_church", "role_assignment_scope_church", "participant_link_person_church"):
            self.assertIn(key, read)
        out = StringIO()
        call_command("rebuild_role_assignments", "--check", stdout=out)
        self.assertIn("Sin diferencias", out.getvalue())
