"""What the independent review of the merge reproduced, each as a test (wave 9).

None of these is a test of two transactions at once: SQLite has no row locks. What the suite can show
is the order in which the locks are asked for, and what a confirmation read earlier does later.
"""

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import OperationalError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from agenda import services as agenda
from agenda.models import GuardianConsent
from core import scheduler
from core.authz import Action, has_scope
from core.models import SchedulerHeartbeat
from events.models import Event
from events.tests.base import make_user
from groups import services as groups
from groups.models import GroupJoinRequest, GroupMembership
from people import duplicates, flags, merge
from people import services as people_services
from people.models import ChurchAuditEvent, Person, PersonMerge
from serving import services as serving

from .merge_base import MergeFixture


class ProtectedEvent(MergeFixture):
    """An event that asks for a guardian's authorization: who is confirmed there is ``agenda.services.standing``."""

    def setUp(self):
        today = timezone.localdate()
        self.protected = Event.objects.create(church=self.church, name="Retiro protegido", slug="retiro-protegido", kind=Event.Kind.RETREAT,
                                              starts_on=today + timedelta(days=10), ends_on=today + timedelta(days=11))
        self.page = agenda.save_page(self.protected, self.admin, published=True, registration="abierta", requires_guardian_consent=True)
        self.adult = today - timedelta(days=30 * 365)
        self.minor = today - timedelta(days=15 * 365)

    def stands(self, registration) -> tuple:
        registration.refresh_from_db()
        person = Person.objects.get(pk=registration.person_id)
        should = agenda.standing(self.protected, self.page, person=person, said_minor=registration.guest_is_minor,
                                 consent=GuardianConsent.objects.filter(registration=registration).first())
        self.assertEqual((registration.status, registration.pending_reason), tuple(should))  # the row says what the rule says
        return registration.person_id, registration.status, registration.pending_reason


class RegistrationsAreSettledAgainTests(ProtectedEvent):
    def test_an_age_confirmed_for_the_survivor_confirms_nothing_for_the_record_that_comes_back(self):
        registration = agenda.register(self.protected, self.dup, self.admin)
        self.assertEqual(self.stands(registration), (self.dup.pk, "pendiente", "age"))
        record = self.do_merge()
        registration.refresh_from_db()
        agenda.confirm_age(registration, self.admin, "documento", self.adult)  # the team saw the survivor's document
        self.assertEqual(self.stands(registration), (self.keep.pk, "inscrita", ""))

        undone = merge.undo(self.church, record.pk, self.admin)
        self.assertEqual(self.stands(registration), (self.dup.pk, "pendiente", "age"))  # back, and waiting again
        self.dup.refresh_from_db()
        self.assertEqual((self.dup.birth_date, self.dup.birth_date_verified), (None, False))
        self.assertTrue(Person.objects.get(pk=self.keep.pk).birth_date_verified)
        # And the screen does not say that everything simply came back.
        self.assertEqual(undone.undo_report["resettled"], 1)
        self.assertIn("1 inscripción a un evento cambió de estado", merge.undo_change_lines(undone)[0])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("people:merge_detail", args=[record.pk])), "Revisa las inscripciones")

    def test_an_authorization_given_after_the_merge_stays_with_the_survivor(self):
        registration = agenda.register(self.protected, self.dup, self.admin)
        other = self.row_for("agenda.EventRegistration.person", self.dup)  # an ordinary registration, for contrast
        record = self.do_merge()
        registration.refresh_from_db()
        agenda.confirm_age(registration, self.admin, "documento", self.minor)
        agenda.record_consent(registration, self.admin, guardian_name="Mamá de prueba", relationship="madre", method="papel",
                              allows_participation=True)
        self.assertEqual(self.stands(registration), (self.keep.pk, "inscrita", ""))

        undone = merge.undo(self.church, record.pk, self.admin)
        self.assertEqual(self.stands(registration), (self.keep.pk, "inscrita", ""))  # with the child the guardian authorized
        self.assertFalse(GuardianConsent.objects.filter(registration__person=self.dup).exists())
        other.refresh_from_db()
        self.assertEqual(other.person_id, self.dup.pk)
        self.assertEqual(undone.undo_report["consent_kept"], 1)
        lines = merge.undo_report_lines(undone)
        self.assertTrue(any("1 inscripción a un evento, porque después de la unión se registró la autorización" in line for line in lines), lines)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("people:merge_detail", args=[record.pk])), "No todo regresó")

    def test_a_row_that_arrives_stands_by_the_survivors_age_at_once(self):
        Person.objects.filter(pk__in=[self.keep.pk, self.dup.pk]).update(birth_date=self.adult)
        people_services.verify_birth_date(Person.objects.get(pk=self.keep.pk), self.admin, "documento")
        registration = agenda.register(self.protected, Person.objects.get(pk=self.dup.pk), self.admin)
        self.assertEqual(self.stands(registration), (self.dup.pk, "pendiente", "age"))  # an adult by a date nobody verified
        record = self.do_merge()
        self.assertEqual(self.stands(registration), (self.keep.pk, "inscrita", ""))
        self.assertEqual(record.manifest["resettled"], 1)
        merge.undo(self.church, record.pk, self.admin)
        self.assertEqual(self.stands(registration), (self.dup.pk, "pendiente", "age"))


class TheConfirmationIsOfWhatWasReadTests(MergeFixture):
    def test_a_role_that_appeared_after_the_preview_is_not_merged_with_the_old_confirmation(self):
        user = make_user("lia-cuenta@prueba.mx", "Lía", "Luna")
        Person.objects.filter(pk=self.keep.pk).update(user=user)
        self.keep.refresh_from_db()
        preview = merge.build_preview(self.church, self.keep, self.dup)
        self.assertEqual(preview.groups_gained, 0)
        groups.set_membership(self.other_group, self.dup, self.admin, role=GroupMembership.Role.LEADER)  # after the preview
        with self.assertRaises(merge.StalePreview):
            merge.merge(self.church, self.keep.pk, self.dup.pk, self.admin, choices={}, token=preview.token)
        self.assertFalse(has_scope(user, self.church, "group", self.other_group.pk, Action.MANAGE))
        self.assertEqual(PersonMerge.objects.count(), 0)
        # The screen shows it again, now with the warning; confirming THAT merges.
        self.client.force_login(self.admin)
        url = reverse("people:merge_preview", args=[self.keep.pk, self.dup.pk])
        response = self.client.post(url, {"revision": preview.token})
        self.assertContains(response, "No unimos nada")
        self.assertContains(response, "La cuenta gana permisos")
        self.assertEqual(PersonMerge.objects.count(), 0)

    def test_any_row_that_appears_or_goes_changes_the_confirmation(self):
        token = merge.build_preview(self.church, self.keep, self.dup).token
        row = self.row_for("care.FollowUp.person", self.dup)
        self.assertNotEqual(merge.build_preview(self.church, self.keep, self.dup).token, token)
        row.delete()
        self.assertEqual(merge.build_preview(self.church, self.keep, self.dup).token, token)
        self.row_for("resources.DevotionalRead.person", self.dup)  # a private mark too: uncounted, but confirmed
        self.assertNotEqual(merge.build_preview(self.church, self.keep, self.dup).token, token)


class LockOrderTests(MergeFixture):
    """The order the locks are asked in (SQLite takes none): teams, then people, as ``serving.services`` need."""

    def order_of(self, action) -> list[str]:
        asked = []
        teams, people = merge._lock_teams, merge._locked_pair
        with mock.patch.object(merge, "_lock_teams", lambda ids: asked.append("teams") or teams(ids)), \
                mock.patch.object(merge, "_locked_pair", lambda *args: asked.append("people") or people(*args)):
            action()
        return asked

    def test_teams_are_locked_before_people_in_a_merge_and_in_an_undo(self):
        serving.set_member(self.team, self.dup, self.admin)
        records = []
        self.assertEqual(self.order_of(lambda: records.append(self.do_merge())), ["teams", "people"])
        self.assertEqual(self.order_of(lambda: merge.undo(self.church, records[0].pk, self.admin)), ["teams", "people"])

    def test_a_team_that_turns_up_between_the_two_locks_writes_nothing_and_asks_again(self):
        token = merge.build_preview(self.church, self.keep, self.dup).token
        before = self.pointers()
        with mock.patch.object(merge, "_team_ids", side_effect=[[], [self.team.pk]]):
            with self.assertRaises(merge.Busy):
                merge.merge(self.church, self.keep.pk, self.dup.pk, self.admin, choices={}, token=token)
        self.assertEqual((self.pointers(), PersonMerge.objects.count()), (before, 0))

    def test_the_screen_says_try_again_for_a_deadlock_and_changes_nothing(self):
        self.client.force_login(self.admin)
        url = reverse("people:merge_preview", args=[self.keep.pk, self.dup.pk])
        for error in (merge.Busy(), OperationalError("deadlock detected")):
            with mock.patch.object(merge, "merge", side_effect=error):
                response = self.client.post(url, {"revision": "x"}, follow=True)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "inténtalo de nuevo")
            self.assertNotContains(response, "deadlock")
        record = self.do_merge()
        with mock.patch.object(merge, "undo", side_effect=OperationalError("deadlock detected")):
            response = self.client.post(reverse("people:merge_undo", args=[record.pk]), follow=True)
        self.assertContains(response, "inténtalo de nuevo")
        self.assertIsNone(PersonMerge.objects.get(pk=record.pk).undone_at)

    def test_the_rows_that_move_are_read_under_the_lock_not_from_the_screen(self):
        """A writer that read the person before the merge and wrote just before the lock: its row moves too."""
        people = merge._locked_pair

        def late_writer(*args):
            GroupMembership.objects.create(group=self.other_group, person=self.dup)  # no service: an unlocked copy passed its check
            return people(*args)

        preview = merge.build_preview(self.church, self.keep, self.dup)
        with mock.patch.object(merge, "_locked_pair", late_writer), self.assertRaises(merge.StalePreview):
            merge.merge(self.church, self.keep.pk, self.dup.pk, self.admin, choices={}, token=preview.token)
        self.do_merge()  # confirmed again with that row in sight
        self.assertFalse(GroupMembership.objects.filter(person=self.dup).exists())
        self.assert_integrity()
        # One written AFTER the merge is what the integrity check is for.
        GroupMembership.objects.create(group=self.group, person=self.dup)
        out = StringIO()
        with self.assertRaises(Exception):
            call_command("tenant_integrity", stdout=out, stderr=StringIO())
        self.assertIn("Integrantes activos de un grupo que son un registro ya unido a otro", out.getvalue())


class MergeableIsSuggestedTests(MergeFixture):
    def test_a_value_too_many_people_share_merges_nobody_even_by_hand(self):
        for n in range(duplicates.MAX_SHARED):
            Person.objects.create(church=self.church, first_name=f"Persona {n}", email="lia@prueba.mx")
        self.assertEqual(duplicates.suggestions(self.church), [])
        self.assertEqual(duplicates.signals_between(self.keep, self.dup), ["email"])  # alike, and still not a signal
        self.assert_refused("lo tienen muchas personas más")
        self.client.force_login(self.admin)
        response = self.client.get(reverse("people:merge_preview", args=[self.keep.pk, self.dup.pk]))
        self.assertContains(response, "Esta unión no se puede hacer")
        self.assertNotContains(response, "Unir estos registros</button>")

    def test_every_suggested_pair_carries_the_signals_of_the_list(self):
        Person.objects.filter(pk=self.dup.pk).update(phone="+52 55 3333 4444")
        (pair,) = duplicates.suggestions(self.church)
        for first, second in ((self.keep, self.dup), (self.dup, self.keep)):
            self.assertEqual(duplicates.suggested_signals(self.church, first.pk, second.pk), pair.signals)
        self.assertEqual(merge.build_preview(self.church, self.keep, Person.objects.get(pk=self.dup.pk)).signals, ["email", "phone"])
        self.assertEqual(duplicates.suggested_signals(self.church_b, self.keep.pk, self.dup.pk), [])  # asked through another church


class RequestToAGroupOneIsAlreadyInTests(MergeFixture):
    def check(self, member, asker):
        groups.set_membership(self.other_group, member, self.admin)
        request, _ = groups.request_to_join(self.other_group, asker, self.admin)
        preview = merge.build_preview(self.church, self.keep, self.dup)
        self.assertEqual((preview.refusals, preview.close_requests), ([], [request.pk]))
        self.assertIn("1 solicitud para unirse a un grupo", preview.stay_lines)
        self.assertNotIn("1 solicitud para unirse a un grupo", preview.move_lines)
        before = self.pointers()
        record = self.do_merge()
        request.refresh_from_db()
        self.assertEqual((request.status, request.person_id), ("retirada", asker.pk))  # closed where it was, never moved
        self.assertFalse(GroupJoinRequest.objects.filter(person=self.keep, status="pendiente").exists())
        self.assertEqual(self.membership(self.other_group, self.keep).status, "activa")
        self.assertEqual(record.manifest["closed_requests"], [request.pk])
        # The activity log says what happened, with ids only: nobody "withdrew" anything.
        self.assertFalse(ChurchAuditEvent.objects.filter(action="grupo_solicitud_retirada").exists())
        row = ChurchAuditEvent.objects.get(action="grupo_solicitud_cerrada_union")
        self.assertEqual(row.metadata, {"group_id": self.other_group.pk, "person_id": asker.pk})
        self.assertEqual((row.actor, row.object_id), (self.admin, str(request.pk)))
        for word in ("Lía", "Lia", "Luna", self.other_group.name):
            self.assertNotIn(word, row.summary)
        self.assert_integrity()
        undone = merge.undo(self.church, record.pk, self.admin)
        request.refresh_from_db()
        self.assertEqual((request.status, request.decided_at, request.person_id), ("pendiente", None, asker.pk))
        self.assertEqual((self.pointers(), merge.undo_report_lines(undone)), (before, []))

    def test_the_absorbed_record_asked_and_the_survivor_is_in(self):
        self.check(self.keep, self.dup)

    def test_the_survivor_asked_and_the_absorbed_record_is_in(self):
        self.check(self.dup, self.keep)

    def test_a_request_somebody_answered_meanwhile_is_not_reopened(self):
        groups.set_membership(self.other_group, self.keep, self.admin)
        request, _ = groups.request_to_join(self.other_group, self.dup, self.admin)
        record = self.do_merge()
        GroupJoinRequest.objects.filter(pk=request.pk).update(status="declinada", decided_by=self.admin)
        undone = merge.undo(self.church, record.pk, self.admin)
        self.assertEqual(GroupJoinRequest.objects.get(pk=request.pk).status, "declinada")
        self.assertEqual(undone.undo_report["not_restored"], {"groups.GroupJoinRequest.person": 1})


class SiblingsTests(ProtectedEvent):
    """Twins: the same date of birth and a parent's phone and e-mail pass every other guard."""

    def twins(self, first_a="Emma", first_b="Emma", last_b="Paz L."):
        Person.objects.filter(pk=self.keep.pk).update(first_name=first_a, last_name="Paz Luna", birth_date=self.minor, phone="55 6000 7000")
        Person.objects.filter(pk=self.dup.pk).update(first_name=first_b, last_name=last_b, birth_date=self.minor, phone="55 6000 7000")
        for person in (self.keep, self.dup):
            person.refresh_from_db()
            person.save()  # the normalised name

    def test_two_minors_with_different_given_names_are_refused(self):
        self.twins("Emma", "Elena", "Paz Luna")
        self.assert_refused("pueden ser hermanos")
        self.assert_refused("pueden ser hermanos", self.dup, self.keep)

    def test_the_same_given_name_needs_its_own_confirmation(self):
        self.twins()
        preview = merge.build_preview(self.church, self.keep, self.dup)
        self.assertEqual((preview.refusals, preview.sibling_warning, preview.signals), ([], True, ["email", "phone"]))
        before = self.pointers()
        with self.assertRaisesMessage(merge.MergeRefused, "Confirma que no son dos hermanos"):
            self.do_merge()
        self.assertEqual(self.pointers(), before)

        self.client.force_login(self.admin)
        url = reverse("people:merge_preview", args=[self.keep.pk, self.dup.pk])
        page = self.client.get(url)
        self.assertContains(page, "Dos hermanos pueden compartir teléfono y correo")
        self.assertContains(page, 'name="no_son_hermanos" value="si" required')
        for extra in ({}, {"no_son_hermanos": "no"}):
            self.client.post(url, {"revision": preview.token, **extra})
            self.assertEqual(PersonMerge.objects.count(), 0)
        self.assertEqual(self.client.post(url, {"revision": preview.token, "no_son_hermanos": "si"}).status_code, 302)
        self.assertEqual(PersonMerge.objects.count(), 1)

    def test_an_authorization_on_record_asks_the_same_of_adults_by_date(self):
        self.twins()
        Person.objects.filter(pk__in=[self.keep.pk, self.dup.pk]).update(birth_date=self.adult)
        self.assertFalse(merge.build_preview(self.church, Person.objects.get(pk=self.keep.pk), Person.objects.get(pk=self.dup.pk)).sibling_warning)
        registration = self.row_for("agenda.EventRegistration.person", self.dup)
        GuardianConsent.objects.create(registration=registration, guardian_name="Mamá de prueba", relationship="madre", method="papel")
        self.assertTrue(merge.build_preview(self.church, Person.objects.get(pk=self.keep.pk), Person.objects.get(pk=self.dup.pk)).sibling_warning)

    def test_the_same_full_name_and_date_is_one_person_twice_and_asks_nothing_more(self):
        self.twins("Emma", "Emma", "Paz Luna")
        preview = merge.build_preview(self.church, self.keep, self.dup)
        self.assertEqual((preview.refusals, preview.sibling_warning), ([], False))
        self.assertIn("name_birth", preview.signals)
        self.do_merge()

    def test_adults_are_not_asked(self):
        self.twins("Emma", "Elena", "Paz Luna")
        Person.objects.filter(pk__in=[self.keep.pk, self.dup.pk]).update(birth_date=self.adult)
        preview = merge.build_preview(self.church, Person.objects.get(pk=self.keep.pk), Person.objects.get(pk=self.dup.pk))
        self.assertEqual((preview.refusals, preview.sibling_warning), ([], False))


@override_settings(EMMAUS_SCHEDULED_COMMUNITY_JOBS=True)
class ScheduledRetentionTests(MergeFixture):
    """The job ``uniones_personas`` of the scheduler's pass: the 30 days are kept by something that runs."""

    def run_pass(self) -> str:
        out = StringIO()
        call_command("run_scheduled_jobs", stdout=out)
        return out.getvalue()

    def expired_merge(self) -> PersonMerge:
        Person.objects.filter(pk=self.dup.pk).update(phone="55 9999 0000")
        record = self.do_merge(phone=self.dup.pk)
        PersonMerge.objects.filter(pk=record.pk).update(merged_at=timezone.now() - timedelta(days=merge.UNDO_DAYS, minutes=1))
        return record

    def before(self, record) -> dict:
        return PersonMerge.objects.get(pk=record.pk).manifest["survivor_before"]

    def test_a_pass_empties_what_expired_once_and_nothing_younger(self):
        self.assertIn(scheduler.PERSON_MERGES, scheduler.JOBS)
        record = self.do_merge()
        self.assertIn("uniones de personas: 0 con datos anteriores borrados", self.run_pass())
        merge.undo(self.church, record.pk, self.admin)
        record = self.expired_merge()
        audited = ChurchAuditEvent.objects.count()
        printed = self.run_pass()
        self.assertIn("uniones de personas: 1 con datos anteriores borrados", printed)
        self.assertNotIn("3333", printed)
        self.assertEqual(self.before(record), {})
        self.assertIsNotNone(PersonMerge.objects.get(pk=record.pk).contact_cleared_at)
        self.assertTrue(Person.objects.filter(pk=self.dup.pk, merged_into=self.keep).exists())  # nothing else is touched
        self.assertEqual(ChurchAuditEvent.objects.count(), audited)
        self.assertIn("uniones de personas: 0 con datos anteriores borrados", self.run_pass())
        beat = SchedulerHeartbeat.objects.get(job=scheduler.PERSON_MERGES)
        self.assertEqual(beat.last_error, "")
        self.assertIsNotNone(beat.last_ok_at)

    def test_it_runs_for_every_church_whatever_its_module_flags(self):
        """It only deletes data nothing needs: a church that switched the module off does not keep it for that."""
        record = self.expired_merge()
        flags.set_enabled(self.church, "people", False)
        flags.set_enabled(self.church, "community", False)
        self.assertIn("uniones de personas: 1 con", self.run_pass())
        self.assertEqual(self.before(record), {})

    def test_the_switches_of_the_community_jobs_do_not_stop_it(self):
        """It only deletes what the product promised to delete: no switch keeps those values past the 30 days."""
        for off in ({"EMMAUS_SCHEDULED_COMMUNITY_JOBS": False}, {"EMMAUS_V2_MODULES": False}):
            record = self.expired_merge()
            with self.settings(**off):
                self.assertEqual(scheduler.skipped(scheduler.PERSON_MERGES), "")
                self.assertIn("uniones de personas: 1 con", self.run_pass())
            self.assertEqual(self.before(record), {})
            PersonMerge.objects.filter(pk=record.pk).update(undone_at=timezone.now())
            Person.objects.filter(pk=self.dup.pk).update(merged_into=None)

    def test_a_job_that_breaks_is_named_by_its_class_and_fails_the_pass(self):
        from django.core.management.base import CommandError

        with mock.patch.object(merge, "clear_expired", side_effect=ValueError("55 3333 4444")), \
                self.assertLogs("core.scheduler", "ERROR") as plain, self.assertLogs("emmaus.scheduler"), self.assertRaises(CommandError):
            call_command("run_scheduled_jobs", stdout=StringIO())
        self.assertEqual(SchedulerHeartbeat.objects.get(job=scheduler.PERSON_MERGES).last_error, "ValueError")
        self.assertNotIn("3333", "".join(plain.output))
