"""Two records of one human in church A, joined by the same e-mail, and the helpers the merge tests share."""

import json
from datetime import timedelta

from django.utils import timezone

from agenda.models import EventRegistration
from care.models import FollowUp, FollowUpStep
from espacios.models import Booking, Space
from events.models import Sede
from feedback.models import FeedbackGiven
from groups import services as groups
from groups.models import GroupAttendance, GroupJoinRequest, GroupMeeting, GroupMembership
from groups.tests.base import CommunityFixture
from notices.models import Announcement, AnnouncementRead, EmailPreference, NoticeEmail
from people import merge
from people.models import CampusAffiliation, Person, PersonTag, Tag
from resources.models import Devotional, DevotionalRead, ReadingPlan, ReadingPlanProgress
from serving import services as serving
from serving.models import Assignment, PlanItem, RotationEntry, ServingPreference, SwapRequest, TeamMembership, TeamRole, Unavailable

from .test_tenant_integrity import json_counts


class MergeFixture(CommunityFixture):
    """``keep`` and ``dup`` share an e-mail and nothing else; neither has an account, an age or a row anywhere."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        today = timezone.localdate()
        cls.keep = Person.objects.create(church=cls.church, first_name="Lía", last_name="Luna", email="lia@prueba.mx", phone="55 3333 4444")
        cls.dup = Person.objects.create(church=cls.church, first_name="Lia", last_name="Luna Ríos", email="LIA@prueba.mx")
        cls.sede = Sede.objects.create(church=cls.church, name="Satélite", slug="satelite")
        cls.meeting = GroupMeeting.objects.create(group=cls.group, date=today)
        cls.notice = Announcement.objects.create(church=cls.church, title="Aviso", body="Texto")
        cls.devotional = Devotional.objects.create(church=cls.church, date=today, title="Hoy", body="Texto")
        cls.plan = ReadingPlan.objects.create(church=cls.church, title="Plan", status="publicado")
        cls.team = serving.save_team(cls.church, cls.admin, name="Bienvenida")
        cls.day = serving.save_date(cls.team, cls.admin, date=today + timedelta(days=7), what="Domingo")

    # One builder per table of ``merge.TABLES``: a row of ``person`` there, made the plainest way.
    def row_for(self, key: str, person):
        builders = {
            "people.CampusAffiliation.person": lambda: CampusAffiliation.objects.create(person=person, sede=self.sede),
            "groups.GroupMembership.person": lambda: groups.set_membership(self.other_group, person, self.admin),
            "groups.GroupJoinRequest.person": lambda: GroupJoinRequest.objects.create(group=self.group, person=person),
            "groups.GroupAttendance.person": lambda: GroupAttendance.objects.create(meeting=self.meeting, person=person),
            "notices.AnnouncementRead.person": lambda: AnnouncementRead.objects.create(announcement=self.notice, person=person),
            "notices.EmailPreference.person": lambda: EmailPreference.objects.create(person=person, choice="all"),
            "notices.NoticeEmail.person": lambda: NoticeEmail.objects.create(announcement=self.notice, version=1, person=person),
            "agenda.EventRegistration.person": lambda: EventRegistration.objects.create(event=self.other_event, person=person),
            "resources.DevotionalRead.person": lambda: DevotionalRead.objects.create(devotional=self.devotional, person=person),
            "resources.ReadingPlanProgress.person": lambda: ReadingPlanProgress.objects.create(plan=self.plan, person=person),
            "serving.TeamMembership.person": lambda: serving.set_member(self.team, person, self.admin),
            "serving.Assignment.person": lambda: Assignment.objects.create(service_date=self.day, person=person),
            "serving.Unavailable.person": lambda: Unavailable.objects.create(person=person, date=self.day.date),
            "serving.RotationEntry.person": lambda: RotationEntry.objects.create(
                role=TeamRole.objects.get_or_create(team=self.team, name="Sonido")[0], person=person, position=1
            ),
            # A swap that already happened: the date is now the teammate's, and the row remembers who asked.
            "serving.SwapRequest.from_person": lambda: SwapRequest.objects.create(
                assignment=Assignment.objects.get_or_create(service_date=self.day, person=self.member)[0], from_person=person,
                to_person=self.member, status="hecha",
            ),
            "serving.SwapRequest.to_person": lambda: SwapRequest.objects.create(
                assignment=Assignment.objects.get_or_create(service_date=self.day, person=self.leader)[0], from_person=self.leader, to_person=person
            ),
            "serving.PlanItem.responsible": lambda: PlanItem.objects.create(
                service_date=self.day, position=1, title="Bienvenida", minutes=5, responsible=person
            ),
            "serving.ServingPreference.person": lambda: ServingPreference.objects.create(person=person, reminders=False),
            "care.FollowUp.person": lambda: FollowUp.objects.create(church=self.church, person=person, reason="visita"),
            "care.FollowUp.owner": lambda: FollowUp.objects.create(church=self.church, person=self.member, owner=person, reason="visita"),
            "care.FollowUpStep.by": lambda: FollowUpStep.objects.create(
                follow_up=FollowUp.objects.create(church=self.church, person=self.member, reason="visita"), kind="llamada", by=person
            ),
            "espacios.Booking.requested_by": lambda: Booking.objects.create(
                space=Space.objects.get_or_create(church=self.church, name="Salón 1")[0], title="Ensayo", requested_by=person,
                starts_at=timezone.now() + timedelta(days=3), ends_at=timezone.now() + timedelta(days=3, hours=1),
            ),
            "espacios.BookingSeries.requested_by": lambda: self._booking_series(person),
            "feedback.FeedbackGiven.person": lambda: FeedbackGiven.objects.create(event=self.other_event, person=person),
            "people.PersonTag.person": lambda: PersonTag.objects.create(
                person=person, tag=Tag.objects.get_or_create(church=self.church, name="Coro", normalized_name="coro")[0]
            ),
            "formularios.Submission.person": lambda: self._form_submission(person),
        }
        return builders[key]()

    def _booking_series(self, person):
        from datetime import time

        from espacios.models import BookingSeries

        day = timezone.localdate() + timedelta(days=3)
        return BookingSeries.objects.create(
            space=Space.objects.get_or_create(church=self.church, name="Salón 1")[0], title="Ensayo", requested_by=person,
            starts_on=day, ends_on=day + timedelta(weeks=8), starts_at=time(18), ends_at=time(20),
        )

    def _form_submission(self, person):
        from formularios.models import Form, FormVersion, Submission

        form = Form.objects.create(church=self.church, title="Convivio", purpose="Para organizar", opens_on=timezone.localdate(),
                                   closes_on=timezone.localdate())
        version = FormVersion.objects.create(form=form, number=1, title=form.title, purpose=form.purpose, schema=[])
        return Submission.objects.create(form=form, version=version, person=person, purpose_shown=form.purpose)

    def do_merge(self, survivor=None, absorbed=None, actor=None, not_siblings=False, **choices):
        survivor, absorbed = survivor or self.keep, absorbed or self.dup
        survivor.refresh_from_db()
        absorbed.refresh_from_db()
        token = merge.build_preview(survivor.church, survivor, absorbed).token
        return merge.merge(survivor.church, survivor.pk, absorbed.pk, actor or self.admin, choices=choices, token=token,
                           confirmed_siblings=not_siblings)

    def refusals(self, survivor=None, absorbed=None) -> list[str]:
        survivor, absorbed = survivor or self.keep, absorbed or self.dup
        survivor.refresh_from_db()
        absorbed.refresh_from_db()
        return merge.build_preview(survivor.church, survivor, absorbed).refusals

    def assert_refused(self, words: str, survivor=None, absorbed=None):
        """The preview says why, and the service refuses even when asked directly; nothing is written."""
        reasons = self.refusals(survivor, absorbed)
        self.assertTrue(any(words in reason for reason in reasons), reasons)
        before = self.pointers()
        with self.assertRaises(merge.MergeRefused):
            self.do_merge(survivor, absorbed)
        self.assertEqual(self.pointers(), before)
        self.assertFalse(Person.objects.filter(merged_into__isnull=False, pk__in=[self.keep.pk, self.dup.pk]).exists())

    def pointers(self) -> str:
        """Every foreign key to the two people, by table, as ids: what "the same as before" means, byte for byte."""
        state = {}
        for table in merge.TABLES:
            rows = table.rows().filter(**{f"{table.field}__in": [self.keep.pk, self.dup.pk]})
            state[table.key] = sorted(rows.values_list("pk", f"{table.field}_id"))
        people = Person.objects.filter(pk__in=[self.keep.pk, self.dup.pk]).order_by("pk")
        state["people"] = list(people.values_list("pk", "user_id", "merged_into_id", "email", "phone", "preferred_contact_method"))
        from people.models import EventParticipantLink

        state["camp"] = sorted(
            EventParticipantLink.objects.filter(person__in=[self.keep, self.dup]).values_list("participant_id", "person_id", "event_id")
        )
        state["primary"] = sorted(CampusAffiliation.objects.filter(person__in=[self.keep, self.dup], is_primary=True).values_list("pk", flat=True))
        return json.dumps(state, sort_keys=True, default=str)

    def assert_integrity(self):
        payload, code = json_counts()
        self.assertEqual((code, payload["ok"], payload["errors"]), (0, True, 0))

    @staticmethod
    def membership(group, person) -> GroupMembership:
        return GroupMembership.objects.get(group=group, person=person)

    @staticmethod
    def team_membership(team, person) -> TeamMembership:
        return TeamMembership.objects.get(team=team, person=person)
