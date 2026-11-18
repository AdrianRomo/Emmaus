"""Issue #48: "Publicar" did not put a version in use; that took a second action nobody was told about.

Publishing now offers "Publicar y usar en entrevistas nuevas" (one step, two audit rows, one transaction) and
"Solo publicar" (which says the version is not in use yet). "En uso" marks the version the event's new interviews
get, in the list and on the version page, and the administration home warns while a newer published version is
not in use. An interview keeps the version it started with.

Each test names the defect it would catch.
"""

from __future__ import annotations

import re
from unittest import mock

from django.test import Client

from events.models import AuditEvent, EventQuestionnaire, Interview, QuestionnaireVersion
from events.services import event_questionnaires as eq
from events.services.questionnaire import create_questionnaire_from_definition, duplicate_version, publish_version

from .base import CampFixture
from .test_multi_questionnaires import definition
from .test_multi_questionnaires_admin import AdminHelpers

PUBLISHED = QuestionnaireVersion.Status.PUBLISHED
DRAFT = QuestionnaireVersion.Status.DRAFT
IN_USE_MARK = '<span class="status status--done"><span class="status__mark" aria-hidden="true"></span>En uso</span>'
WARNING = "más nueva que no está en uso"


class PublishFixture(AdminHelpers, CampFixture):
    def draft(self):
        return duplicate_version(self.version, self.admin)

    def post(self, version, action, client=None):
        client = client or self.login(self.admin, Client())
        response = client.post(self.url("version_detail", version.id), {"action": action})
        self.assertEqual(response.status_code, 302)
        return client.get(response["Location"]).content.decode()

    def summaries(self):
        return list(AuditEvent.objects.filter(event=self.event).order_by("id").values_list("summary", flat=True))

    def in_use(self):
        return EventQuestionnaire.objects.get(event=self.event, questionnaire=self.version.questionnaire).version


class PublishAndUseTests(PublishFixture):
    def test_the_status_mark_exists(self):
        """Guard for IN_USE_MARK: if the mark's markup changes, the badge tests below must not pass by accident."""
        self.assertIn(IN_USE_MARK, self.page("questionnaires"))

    def test_a_draft_offers_the_two_actions_and_the_filled_one_also_uses(self):
        """Defect: one "Publicar versión" button that publishes and leaves the old version in use, saying nothing."""
        html = self.page("version_detail", self.draft().id)
        primary = re.findall(r'<button class="btn btn--primary"[^>]*>(.*?)</button>', html)
        self.assertEqual(primary, ["Publicar y usar en entrevistas nuevas"])
        self.assertRegex(html, r'<button class="btn btn--secondary" name="action" value="publish"[^>]*>Solo publicar</button>')
        self.assertIn('name="action" value="publish_use"', html)
        self.assertNotIn("Publicar versión", html)

    def test_the_primary_action_publishes_uses_and_audits_both(self):
        """Defect: "Publicó" recorded without "Usará" (what the production log showed the evening before the camp)."""
        draft = self.draft()
        before = len(self.summaries())
        html = self.post(draft, "publish_use")
        draft.refresh_from_db()
        self.assertEqual(draft.status, PUBLISHED)
        self.assertEqual(self.in_use(), draft)
        self.assertEqual(self.summaries()[before:], ["Publicó la versión 2", "Usará la versión 2 en entrevistas nuevas"])
        self.assertIn("Versión 2 publicada y en uso.", html)
        self.assertIn("las que ya empezaron conservan la suya", html)
        rows = AuditEvent.objects.filter(event=self.event).order_by("-id")[:2]
        self.assertEqual({row.action for row in rows}, {"cuestionario"})
        self.assertEqual({row.object_id for row in rows}, {str(draft.id)})
        self.assertEqual({row.actor for row in rows}, {self.admin})

    def test_publish_and_use_is_one_transaction(self):
        """Defect: the version published and "Publicó" recorded while putting it in use failed: the same half-done
        state the issue is about, now by accident."""
        draft = self.draft()
        before = self.summaries()
        with mock.patch.object(eq, "use_version", side_effect=RuntimeError("falla")):
            with self.assertRaises(RuntimeError):
                eq.publish_for_event(self.event, draft, self.admin, use=True)
        draft.refresh_from_db()
        self.assertEqual(draft.status, DRAFT)
        self.assertIsNone(draft.published_at)
        self.assertEqual(self.in_use(), self.version)
        self.assertEqual(self.summaries(), before)

    def test_the_second_audit_row_failing_undoes_everything(self):
        """Defect: the version in use with no "Usará" row: the log could not say when it changed."""
        from events.services import audit

        draft = self.draft()
        before = self.summaries()
        real = audit.record

        def record(actor, event, action, summary, *args, **kwargs):
            if summary.startswith("Usará"):
                raise RuntimeError("falla")
            return real(actor, event, action, summary, *args, **kwargs)

        with mock.patch.object(audit, "record", side_effect=record):
            with self.assertRaises(RuntimeError):
                eq.publish_for_event(self.event, draft, self.admin, use=True)
        draft.refresh_from_db()
        self.assertEqual(draft.status, DRAFT)
        self.assertEqual(self.in_use(), self.version)
        self.assertEqual(self.summaries(), before)

    def test_only_publishing_says_it_is_not_in_use(self):
        """Defect: "Versión 2 publicada. Ya no se puede modificar." and nothing about the version 1 still in use."""
        draft = self.draft()
        before = len(self.summaries())
        html = self.post(draft, "publish")
        draft.refresh_from_db()
        self.assertEqual(draft.status, PUBLISHED)
        self.assertEqual(self.in_use(), self.version)
        self.assertEqual(self.summaries()[before:], ["Publicó la versión 2"])
        self.assertIn("Todavía no está en uso: las entrevistas nuevas siguen con la versión 1.", html)
        self.assertIn("notice--warning", html)

    def test_a_draft_that_cannot_be_published_changes_nothing(self):
        """Defect: an empty draft put in use, or an audit row for a publication that did not happen."""
        version = create_questionnaire_from_definition(self.church, definition("Vacío", "Vacío", "vacio"))
        empty = duplicate_version(version, self.admin)
        empty.sections.all().delete()
        before = self.summaries()
        html = self.post(empty, "publish_use")
        empty.refresh_from_db()
        self.assertEqual(empty.status, DRAFT)
        self.assertEqual(self.summaries(), before)
        self.assertIn("Agrega al menos una pregunta antes de publicar.", html)
        self.assertFalse(EventQuestionnaire.objects.filter(event=self.event, version=empty).exists())

    def test_a_published_version_is_not_published_again(self):
        """Defect: a second "Publicó" row (or a silent swap) from a stale tab that still shows the draft's buttons."""
        draft = self.draft()
        publish_version(draft, self.admin)
        before = self.summaries()
        client = self.login(self.admin, Client())
        client.post(self.url("version_detail", draft.id), {"action": "publish_use"})
        self.assertEqual(self.summaries(), before)
        self.assertEqual(self.in_use(), self.version)
        with self.assertRaises(ValueError):
            eq.publish_for_event(self.event, draft, self.admin, use=True)

    def test_a_first_questionnaire_published_and_used_is_added_to_the_event(self):
        """Defect: publish-and-use working only to replace a version, not for a questionnaire the event did not use."""
        version = create_questionnaire_from_definition(self.church, definition("Servicio", "Cuestionario de servicio", "servir"))
        draft = duplicate_version(version, self.admin)
        html = self.post(draft, "publish_use")
        self.assertEqual([(link.version, link.order) for link in eq.links_for(self.event)], [(self.version, 1), (draft, 2)])
        self.assertIn("publicada y en uso en este evento", html)

    def test_only_an_administrator_of_this_church_publishes(self):
        """Defect: a coordinator, or another church's administrator, publishing and swapping the event's version."""
        from core.models import Church, Role
        from core.services import assign_role

        from .base import make_user

        draft = self.draft()
        client = self.login(self.coord1, Client())
        self.assertEqual(client.post(self.url("version_detail", draft.id), {"action": "publish_use"}).status_code, 403)
        other_church = Church.objects.create(name="Otra iglesia", slug="otra-iglesia")
        stranger = make_user("admin@otra.mx")
        assign_role(stranger, other_church, Role.CHURCH_ADMIN)
        client = self.login(stranger, Client())
        self.assertEqual(client.post(self.url("version_detail", draft.id), {"action": "publish_use"}).status_code, 403)
        draft.refresh_from_db()
        self.assertEqual(draft.status, DRAFT)
        self.assertEqual(self.in_use(), self.version)


class InUseBadgeTests(PublishFixture):
    def badges(self):
        """{version id: has the "En uso" mark} for every row of "Versiones" in the list."""
        html = self.page("questionnaires")
        versions = html[html.index('id="versiones"'):]
        rows = re.findall(r'<li class="row">(.*?)</li>', versions, re.S)
        found = {}
        for row in rows:
            version_id = int(re.search(r"/cuestionarios/(\d+)/", row).group(1))
            found[version_id] = IN_USE_MARK in row
        return found

    def assert_badges_match_links(self):
        linked = set(EventQuestionnaire.objects.filter(event=self.event).values_list("version_id", flat=True))
        badges = self.badges()
        self.assertEqual({version_id for version_id, marked in badges.items() if marked}, linked)
        for version_id in badges:
            page = self.page("version_detail", version_id)
            self.assertEqual(IN_USE_MARK in page, version_id in linked, version_id)

    def test_the_badge_follows_the_events_version_for_each_questionnaire(self):
        """Defect: the mark on the newest or the last published version instead of EventQuestionnaire.version."""
        second = self.second()
        newer = self.draft()
        publish_version(newer, self.admin)  # published, not in use: version 1 keeps the mark
        self.assert_badges_match_links()
        self.assertTrue(self.badges()[self.version.id])
        self.assertFalse(self.badges()[newer.id])
        self.assertTrue(self.badges()[second.id])

        eq.use_version(self.event, newer)
        self.assert_badges_match_links()
        self.assertFalse(self.badges()[self.version.id])
        self.assertTrue(self.badges()[newer.id])

        eq.stop_using(self.event, second.questionnaire_id)
        self.assert_badges_match_links()
        self.assertFalse(self.badges()[second.id])

    def test_the_badge_is_per_event(self):
        """Defect: "En uso" because another event of the church uses that version."""
        newer = self.draft()
        publish_version(newer, self.admin)
        eq.use_version(self.other_event, newer)
        self.assertFalse(self.badges()[newer.id])
        self.assertNotIn(IN_USE_MARK, self.page("version_detail", newer.id))

    def test_a_draft_never_has_the_badge(self):
        draft = self.draft()
        self.assertFalse(self.badges()[draft.id])
        self.assertNotIn(IN_USE_MARK, self.page("version_detail", draft.id))


class NewerVersionWarningTests(PublishFixture):
    def home(self):
        return self.page("admin_home")

    def test_no_warning_when_the_newest_published_version_is_in_use(self):
        """Defect: a warning on every administration home."""
        self.assertEqual(eq.newer_unused_versions(self.event), [])
        self.assertNotIn(WARNING, self.home())

    def test_a_draft_does_not_warn(self):
        """Defect: warning about a version that cannot be used yet."""
        self.draft()
        self.assertNotIn(WARNING, self.home())

    def test_it_warns_only_while_the_newer_published_version_is_unused(self):
        """Defect: the first camp day: version 2 published, never in use, and no screen saying so."""
        draft = self.draft()
        self.post(draft, "publish")
        html = self.home()
        self.assertIn("Hay una versión más nueva que no está en uso", html)
        self.assertIn("las entrevistas nuevas usan la versión 1. La versión 2 ya está publicada, pero no se usa.", html)
        self.assertIn(f'href="{self.url("version_detail", draft.id)}"', html)

        self.post(draft, "use")
        self.assertEqual(eq.newer_unused_versions(self.event), [])
        self.assertNotIn(WARNING, self.home())

    def test_publish_and_use_never_warns(self):
        self.post(self.draft(), "publish_use")
        self.assertNotIn(WARNING, self.home())

    def test_it_names_the_newest_of_several_and_ignores_older_ones(self):
        """Defect: warning about version 2 when 3 is the newest, or about an older version left behind on purpose."""
        second = self.draft()
        publish_version(second, self.admin)
        third = duplicate_version(second, self.admin)
        publish_version(third, self.admin)
        [item] = eq.newer_unused_versions(self.event)
        self.assertEqual((item.link.version, item.newest), (self.version, third))
        eq.use_version(self.event, third)
        self.assertEqual(eq.newer_unused_versions(self.event), [])  # versions 1 and 2 are older: not a warning

    def test_a_questionnaire_the_event_does_not_use_does_not_warn(self):
        """Defect: a warning for every questionnaire of the church."""
        create_questionnaire_from_definition(self.church, definition("Servicio", "Cuestionario de servicio", "servir"))
        self.assertNotIn(WARNING, self.home())

    def test_the_warning_is_per_event(self):
        """Defect: the other camp of the church warned because this one moved on (or the reverse)."""
        newer = self.draft()
        publish_version(newer, self.admin)
        eq.use_version(self.event, newer)
        self.assertEqual(eq.newer_unused_versions(self.event), [])
        [item] = eq.newer_unused_versions(self.other_event)
        self.assertEqual(item.newest, newer)

    def test_several_questionnaires_each_get_a_line(self):
        second = self.second()
        for version in (self.version, second):
            publish_version(duplicate_version(version, self.admin), self.admin)
        html = self.home()
        self.assertIn("Hay versiones más nuevas que no están en uso", html)
        self.assertEqual(len(eq.newer_unused_versions(self.event)), 2)

    def test_the_other_roles_homes_do_not_get_it(self):
        """The warning is the administration's: only it can put a version in use."""
        publish_version(self.draft(), self.admin)
        self.assertNotIn(WARNING, self.page("pastor_home", user=self.pastor))


class ExistingInterviewsKeepTheirVersionTests(PublishFixture):
    def test_an_interview_keeps_its_version_and_a_new_one_gets_the_new(self):
        """The unchanged rule (AGENTS.md): publish-and-use reaches only the interviews that start afterwards."""
        client = self.login(self.coord1, Client())
        self.choose_station(client)
        self.start(self.p2, client=client)
        started = Interview.objects.get(participant=self.p2)
        self.assertEqual(started.version, self.version)

        draft = self.draft()
        self.post(draft, "publish_use")

        started.refresh_from_db()
        self.assertEqual(started.version, self.version)
        self.assertEqual(started.status, Interview.Status.IN_PROGRESS)
        self.start(self.p1, client=client)
        self.assertEqual(Interview.objects.get(participant=self.p1).version, draft)
        # the page of the old version still says how many interviews it has
        self.assertEqual(Interview.objects.filter(version=self.version).count(), 1)
