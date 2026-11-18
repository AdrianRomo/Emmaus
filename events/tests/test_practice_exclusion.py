"""The practice campero never counts (issue #49): the proof.

One event with real interviews, follow-ups and reports is rendered for every role: every URL of ``events.urls`` (the
full page and the fragment a refresh asks for), every download, the payload of every scope, a cutoff of every scope
with its page, PDF and presentation, and the people "from event" import. Then the practice campero is added with an
interview, a confidential answer, an authorization, a capture and follow-ups of every kind, and everything is
rendered again. The two renders must be the same bytes, except on the screens that exist to show the practice
campero, and those must be the same once their practice block is taken out.

The URLs are read from ``events.urls``: a screen added later is rendered here without anybody remembering to.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.db import transaction
from django.test import Client
from django.urls import reverse

from events import urls as event_urls
from events.models import (
    PRACTICE_NAME,
    Answer,
    AuditEvent,
    ConsentRecord,
    Event,
    EventMembership,
    EventParticipant,
    FollowUp,
    Interview,
    InterviewStatus,
    MembershipRole,
    ParticipantSession,
    ReportSnapshot,
    Sede,
    hash_token,
)
from events.services import audit
from events.services.exports import answers_rows
from events.services.metrics import build_payload, coordinator_scope, event_scope, team_scope
from events.services.reports import generate_snapshot, scopes_for_event

from people import flags

from .base import PASSWORD, CampFixture, make_user

NOW = datetime(2026, 7, 15, 16, 30, tzinfo=ZoneInfo("America/Mexico_City"))
SECRET = "SECRETO-DE-LA-PRACTICA"  # the practice campero's confidential answer and the details of its follow-ups
MASKS = [
    (re.compile(rb'name="csrfmiddlewaretoken" value="[^"]+"'), b""),
    (re.compile(rb'data-idle-remaining="\d+"'), b""),
    (re.compile(rb'nonce="[^"]+"'), b""),
    # Rows written while rendering (the audit row of a download) carry the wall clock, not the stopped one
    (re.compile(rb"\b\d\d:\d\d(:\d\d)?\b"), b"HH:MM"),
    # reportlab stamps each PDF with the wall clock and a random id
    (re.compile(rb"/(CreationDate|ModDate) \(D:[^)]*\)"), b""),
    (re.compile(rb"/ID\s*\[<[0-9a-f]+><[0-9a-f]+>\]"), b""),
]
# Taken out of a screen that shows the practice campero before comparing it with the same screen without it.
PRACTICE_STEP = re.compile(r'<li class="row[^"]*"[^>]*>\s*<div class="row__main">\s*<p class="row__title">Práctica</p>.*?</li>', re.S)
RECENT_ACTIVITY = re.compile(r'<section aria-labelledby="actividad-admin">.*?</section>', re.S)
PRACTICE_CARD = re.compile(r'<section class="notice notice--warning" aria-labelledby="practica">.*?</section>\s*', re.S)
PRACTISED = re.compile(r"\. (Ya practicó|Aún no practica)")


def spaces(html: str) -> str:
    """A block taken out of a template leaves its indentation behind."""
    return re.sub(r"\s+", " ", html)


def masked(content: bytes) -> bytes:
    for pattern, replacement in MASKS:
        content = pattern.sub(replacement, content)
    return content


def options(question, *codes):
    by_code = {option.code: option for option in question.options.all()}
    return [by_code[code] for code in codes]


class PracticeExclusionTests(CampFixture):
    """``captured()`` renders everything inside a savepoint and rolls it back, with the clock stopped: the cutoffs it
    generates, the audit rows of its downloads and the sign-ins of its clients leave nothing behind, so the render
    before and the render after the practice data differ only by that data."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # An administrator who also holds confidential access: "Descargar las respuestas" and every confidential count
        cls.admin2 = make_user("admin2@prueba.mx", "Abel", "Admin")
        EventMembership.objects.create(event=cls.event, user=cls.admin2, role=MembershipRole.ADMIN, can_view_sensitive=True)
        cls.sede = Sede.objects.create(church=cls.church, name="Mérida", slug="merida")
        Event.objects.filter(pk=cls.event.pk).update(ends_on=date(2026, 7, 14), starts_on=date(2026, 7, 10))  # over: people "from event" opens
        cls.event.refresh_from_db()
        flags.set_enabled(cls.church, "people", True)  # "Personas" is a module of the church: on, so its import renders
        cls.q = {question.code: question for step in cls.steps for question in step.questions}
        # Real interviews: one completed with a follow-up of each kind, one waiting for its decision, one stalled
        cls.i1 = cls._interview(cls.p1, cls.coord1, {"salvo": ["si"], "apoyo": ["familia"]}, text="Algo real y confidencial", decision="requerido")
        cls.f_care = FollowUp.objects.create(event=cls.event, participant=cls.p1, interview=cls.i1, assigned_to=cls.coord1, created_by=cls.coord1)
        cls.f_confidential = FollowUp.objects.create(
            event=cls.event, participant=cls.p1, interview=cls.i1, confidential=True, reason=FollowUp.Reason.SUPPORT_REQUESTED
        )
        cls.f_safety = FollowUp.objects.create(
            event=cls.event, participant=cls.p2, kind=FollowUp.Kind.SAFETY, reason=FollowUp.Reason.SAFETY, details="Reporte real", created_by=cls.coord1
        )
        cls.i4 = cls._interview(cls.p4, cls.coord3, {"salvo": ["no"]}, decision="por_evaluar")
        cls.i3 = Interview.objects.create(
            event=cls.event, participant=cls.p3, version=cls.version, conducted_by=cls.coord2, station=cls.event.stations.get(number=2),
            started_at=NOW - timedelta(hours=3), last_activity_at=NOW - timedelta(hours=2),
        )
        EventParticipant.objects.filter(pk=cls.p3.pk).update(interview_status=InterviewStatus.IN_PROGRESS)

    @classmethod
    def _interview(cls, participant, user, answers, *, text="", decision="no_requerido", status=Interview.Status.COMPLETED, **fields):
        done = status == Interview.Status.COMPLETED
        interview = Interview.objects.create(
            event=cls.event, participant=participant, version=cls.version, status=status, conducted_by=user,
            station=cls.event.stations.get(number=1), started_at=NOW - timedelta(hours=5), last_activity_at=NOW - timedelta(hours=4),
            completed_at=NOW - timedelta(hours=4) if done else None, follow_up_decision=decision if done else "", **fields,
        )
        for code, values in answers.items():
            Answer.objects.create(interview=interview, question=cls.q[code]).options.set(options(cls.q[code], *values))
        if text:
            Answer.objects.create(interview=interview, question=cls.q["dudas_detalle"], text_value=text)
        EventParticipant.objects.filter(pk=participant.pk).update(
            interview_status=InterviewStatus.COMPLETED if done else InterviewStatus.IN_PROGRESS
        )
        return interview

    # ---- the practice data ----------------------------------------------------------------------------------

    def add_practice(self, *, completed: bool) -> EventParticipant:
        """The practice campero in coord1's list with everything a rehearsal can leave: a completed interview (or one
        left open on a computer, with the campero's own capture still running), a confidential answer, an
        authorization, and follow-ups of every kind."""
        practice = EventParticipant.objects.create(
            event=self.event, number=0, full_name=PRACTICE_NAME, age=18, room="De práctica", church_group="De práctica", coordinator=self.m_coord1, is_practice=True,
        )
        status = Interview.Status.COMPLETED if completed else Interview.Status.IN_PROGRESS
        lock = {} if completed else {"lock_token_hash": hash_token("x"), "lock_holder": self.coord1, "lock_expires_at": NOW + timedelta(hours=1)}
        interview = self._interview(
            practice, self.coord1, {"salvo": ["no"], "apoyo": ["sexualidad"], "decision": ["recibir_cristo"]}, text=SECRET,
            decision="por_evaluar", status=status, **lock,
        )
        ConsentRecord.objects.create(participant=practice, interview=interview, kind=ConsentRecord.Kind.SELF, recorded_by=self.coord1)
        FollowUp.objects.create(event=self.event, participant=practice, interview=interview, assigned_to=self.coord1, created_by=self.coord1, details=SECRET)
        FollowUp.objects.create(event=self.event, participant=practice, created_by=self.coord1, status=FollowUp.Status.DONE, details=SECRET)
        FollowUp.objects.create(event=self.event, participant=practice, interview=interview, confidential=True, reason=FollowUp.Reason.SUPPORT_REQUESTED)
        FollowUp.objects.create(event=self.event, participant=practice, kind=FollowUp.Kind.SAFETY, reason=FollowUp.Reason.SAFETY, details=SECRET)
        if completed:
            audit.record(self.coord1, self.event, audit.PRACTICE_COMPLETED, "Completó una entrevista de práctica", interview)
        else:
            ParticipantSession.objects.create(
                interview=interview, station=self.event.stations.get(number=1), started_by=self.coord1, token_hash=hash_token("y"),
                lock_token_hash=hash_token("z"), state=ParticipantSession.State.ACTIVE, expires_at=NOW + timedelta(hours=1), last_activity_at=NOW,
            )
        return practice

    # ---- rendering everything --------------------------------------------------------------------------------

    def roles(self) -> dict[str, User]:
        return {
            "administración": self.admin, "administración con acceso confidencial": self.admin2, "pastor": self.pastor,
            "pastor con acceso confidencial": self.care, "tutor 1": self.tutor1,
            "coordinador 1": self.coord1, "coordinador 2": self.coord2,
        }

    def urls(self, snapshots: list[ReportSnapshot]) -> list[tuple[str, bool]]:
        """Every URL of events.urls, with each real object its arguments can name: (url, also as a fragment)."""
        slug = self.event.slug
        question = self.q["salvo"]
        fixed = {
            "membership_id": [self.m_coord1.id, self.m_tutor1.id], "follow_up_id": [self.f_care.id, self.f_confidential.id, self.f_safety.id],
            "version_id": [self.version.id], "question_id": [question.id], "section_id": [question.section_id], "sede_id": [self.sede.id],
            "pk": [1], "number": [1], "participant_id": [self.p1.public_id, self.p2.public_id],
        }
        urls = []
        for pattern in event_urls.urlpatterns:
            names = list(pattern.pattern.converters)
            choices = [{}]
            for name in names:
                if name == "slug":
                    values = [slug]
                elif name == "public_id" and pattern.name.startswith("report"):
                    values = [snapshot.public_id for snapshot in snapshots]
                elif name == "public_id" and pattern.name.startswith("interview") and pattern.name != "interview_start":
                    values = [self.i1.public_id, self.i3.public_id, self.i4.public_id]
                elif name == "public_id":
                    values = [self.p1.public_id, self.p3.public_id]
                else:
                    values = fixed[name]  # a new kind of argument fails here until this test knows what to pass
                choices = [{**chosen, name: value} for chosen in choices for value in values]
            # The fragment too (what a refresh puts in the page) wherever the URL names the event alone: every live region
            urls += [(reverse(f"events:{pattern.name}", kwargs=kwargs), names == ["slug"]) for kwargs in choices]
        urls += [(url, False) for url in [
            f"{reverse('events:participants', args=[slug])}?participa=todos", f"{reverse('events:participants', args=[slug])}?q=practica",
            f"{reverse('events:participants', args=[slug])}?q=%230", f"{reverse('events:my_participants', args=[slug])}?q=practica",
            f"{reverse('events:interview_search', args=[slug])}?q=practica", f"{reverse('events:interview_search', args=[slug])}?q=0",
            f"{reverse('events:follow_ups', args=[slug])}?estado=todos", f"{reverse('events:follow_ups', args=[slug])}?responsable=ninguno",
            f"{reverse('events:follow_ups', args=[slug])}?responsable=mio", f"{reverse('events:follow_ups', args=[slug])}?estado=atendido",
            f"{reverse('events:presentation_live', args=[slug])}?alcance=equipo:{self.m_tutor1.id}",
            reverse("people:from_event_list"), reverse("people:from_event", args=[slug]), f"{reverse('people:from_event', args=[slug])}?q=practica",
        ]]
        return urls

    def render_everything(self) -> dict:
        surfaces = {}
        snapshots = []
        for scope in scopes_for_event(self.event):  # a cutoff of every scope: its stored numbers, then its page, PDF and slides
            snapshot = generate_snapshot(self.event, scope, trigger=ReportSnapshot.Trigger.MANUAL, cutoff_at=NOW, user=self.admin, now=NOW)
            ReportSnapshot.objects.filter(pk=snapshot.pk).update(public_id=uuid.uuid5(uuid.NAMESPACE_URL, scope.key))
            snapshot.refresh_from_db()
            snapshots.append(snapshot)
            self.assertEqual(snapshot.status, ReportSnapshot.Status.READY, snapshot.error)
            surfaces[("corte", scope.key)] = json.dumps(snapshot.data, sort_keys=True, default=str)
        scopes = [event_scope(self.event), team_scope(self.event, self.m_tutor1), team_scope(self.event, self.m_tutor2)]
        scopes += [coordinator_scope(self.event, m) for m in (self.m_coord1, self.m_coord2, self.m_coord3)]
        for scope in scopes:
            payload = build_payload(scope, include_safety=True, include_outcomes=True, now=NOW)
            surfaces[("métricas", scope.key)] = json.dumps(payload, sort_keys=True, default=str)
        surfaces[("respuestas", "todas")] = json.dumps(answers_rows(self.event))
        urls = self.urls(snapshots)
        for role, user in self.roles().items():
            client = Client()
            self.assertTrue(client.login(username=user.username, password=PASSWORD))
            client.post(self.url("choose_station"), {"station": self.event.stations.get(number=3).id, "siguiente": "/"})
            for url, live in urls:
                for fragment in (False, True) if live else (False,):
                    response = client.get(url, **({"HTTP_X_REQUESTED_WITH": "fetch"} if fragment else {}))
                    body = b"".join(response.streaming_content) if response.streaming else response.content
                    surfaces[(role, url, "fragmento" if fragment else "página")] = (
                        response.status_code, response.get("Location", ""), response.get("Content-Disposition", ""), masked(body),
                    )
        return surfaces

    _before = None

    def before(self) -> dict:
        """The event without practice data. Rendered once for the class: every render is rolled back, so each test
        starts from the same rows (``test_the_harness_renders_real_data_and_is_repeatable`` renders it twice)."""
        if PracticeExclusionTests._before is None:
            PracticeExclusionTests._before = self.captured()
        return PracticeExclusionTests._before

    def captured(self) -> dict:
        import reportlab.rl_config

        savepoint = transaction.savepoint()
        try:
            with mock.patch("django.utils.timezone.now", return_value=NOW), mock.patch.object(reportlab.rl_config, "invariant", 1):
                return self.render_everything()
        finally:
            transaction.savepoint_rollback(savepoint)

    # ---- what may differ -------------------------------------------------------------------------------------

    def allowed(self, practice) -> dict:
        """The screens that exist to show the practice campero, each with what to take out before comparing: after
        that, the screen must be the one the event had without it."""
        slug = self.event.slug

        def admin_home(html):
            return RECENT_ACTIVITY.sub("", PRACTICE_STEP.sub("", html))

        allowed = {}
        for role in ("administración", "administración con acceso confidencial"):
            for kind in ("página", "fragmento"):
                allowed[(role, reverse("events:admin_home", args=[slug]), kind)] = admin_home
                allowed[(role, reverse("events:practice", args=[slug]), kind)] = None  # the practice campero's own page
                allowed[(role, reverse("events:audit", args=[slug]), kind)] = None  # "audited": the log says somebody practised
                allowed[(role, reverse("events:team", args=[slug]), kind)] = lambda html: PRACTISED.sub("", html)
        for role in ("pastor", "pastor con acceso confidencial"):
            for kind in ("página", "fragmento"):
                allowed[(role, reverse("events:team", args=[slug]), kind)] = lambda html: PRACTISED.sub("", html)
        for kind in ("página", "fragmento"):
            for url in (reverse("events:my_participants", args=[slug]), f"{reverse('events:my_participants', args=[slug])}?q=practica"):
                allowed[("coordinador 1", url, kind)] = lambda html: PRACTICE_CARD.sub("", html)
        return allowed

    def assert_only_practice_screens_changed(self, before: dict, after: dict, practice, *, expect: set):
        self.assertEqual(set(before), set(after))
        allowed = self.allowed(practice)
        changed = {key for key in before if before[key] != after[key]}
        self.assertLessEqual(changed, set(allowed), f"the practice campero changed: {sorted(map(str, changed - set(allowed)))[:5]}")
        self.assertLessEqual(expect, changed, "a screen that should show the practice campero does not")
        for key in changed:
            strip = allowed[key]
            self.assertEqual(before[key][:3], after[key][:3], key)  # same status, redirect and file name
            if strip is not None:
                self.assertEqual(spaces(strip(before[key][3].decode())), spaces(strip(after[key][3].decode())), key)
        # Nowhere, allowed or not, does a word of what was answered in practice appear.
        for key, surface in after.items():
            body = surface[3] if isinstance(surface, tuple) else surface.encode()
            self.assertNotIn(SECRET.encode(), body, key)
            if key not in allowed:
                self.assertNotIn(PRACTICE_NAME.encode(), body, key)

    def test_the_harness_renders_real_data_and_is_repeatable(self):
        """Defect: a comparison that passes because both sides are empty or forbidden, or fails by itself."""
        first, second = self.before(), self.captured()
        self.assertEqual(set(first), set(second))
        self.assertEqual([key for key in first if first[key] != second[key]], [])
        slug = self.event.slug
        ok = {key for key, surface in first.items() if isinstance(surface, tuple) and surface[0] == 200}
        names = {pattern.name for pattern in event_urls.urlpatterns}
        self.assertGreaterEqual(len(names), 80)
        for name in ("admin_home", "pastor_home", "progress", "follow_ups", "pending", "interviews", "reports", "presentation_live",
                     "participants", "participant_export", "team", "sessions", "questionnaires", "audit", "backup_list", "coordinators"):
            self.assertIn(("administración con acceso confidencial", reverse(f"events:{name}", args=[slug]), "página"), ok, name)
        self.assertIn(("administración con acceso confidencial", reverse("events:answers_export", args=[slug]), "página"), ok)
        self.assertIn(("administración", reverse("people:from_event", args=[slug]), "página"), ok)
        for role, name in (("coordinador 1", "coord_home"), ("coordinador 1", "my_participants"), ("coordinador 1", "coord_progress"),
                           ("coordinador 1", "coord_pending"), ("tutor 1", "tutor_home"), ("pastor", "pastor_home")):
            self.assertIn((role, reverse(f"events:{name}", args=[slug]), "página"), ok, name)
        for kind in ("report_detail", "report_pdf", "report_presentation"):
            hits = [key for key in ok if f"/{slug}/reportes/" in key[1] and key[0] == "pastor" and key[2] == "página"]
            self.assertGreaterEqual(len(hits), 9, kind)  # three scopes, each with its page, PDF and presentation
        answers = first[("administración con acceso confidencial", reverse("events:answers_export", args=[slug]), "página")][3].decode()
        self.assertIn("Algo real y confidencial", answers)  # the real confidential answer IS in the download
        pdf = next(surface for key, surface in first.items() if isinstance(surface, tuple) and key[1].endswith("/pdf/") and surface[0] == 200)
        self.assertTrue(pdf[3].startswith(b"%PDF"))

    def test_a_completed_practice_interview_changes_nothing_but_the_practice_screens(self):
        before = self.before()
        practice = self.add_practice(completed=True)
        slug = self.event.slug
        self.assert_only_practice_screens_changed(before, self.captured(), practice, expect={
            ("administración", reverse("events:admin_home", args=[slug]), "página"),
            ("administración", reverse("events:admin_home", args=[slug]), "fragmento"),
            ("administración", reverse("events:practice", args=[slug]), "página"),
            ("administración", reverse("events:team", args=[slug]), "página"),
            ("pastor", reverse("events:team", args=[slug]), "página"),
            ("coordinador 1", reverse("events:my_participants", args=[slug]), "página"),
        })

    def test_a_practice_interview_left_open_changes_nothing_but_the_practice_screens(self):
        """Open on a computer, with the campero's own capture running: no "en curso", no claim, no stalled interview."""
        before = self.before()
        practice = self.add_practice(completed=False)
        slug = self.event.slug
        self.assert_only_practice_screens_changed(before, self.captured(), practice, expect={
            ("administración", reverse("events:admin_home", args=[slug]), "página"),
            ("administración", reverse("events:practice", args=[slug]), "página"),
            ("coordinador 1", reverse("events:my_participants", args=[slug]), "página"),
        })

    def test_only_the_administration_and_its_coordinador_can_open_the_practice_campero(self):
        practice = self.add_practice(completed=True)
        interview = practice.interviews.get()
        for user, expected in ((self.admin, 200), (self.coord1, 200), (self.coord2, 404), (self.tutor1, 404), (self.pastor, 404), (self.care, 404)):
            client = self.login(user, Client())
            self.assertEqual(client.get(self.url("participant_detail", practice.public_id)).status_code, expected, user)
            self.assertEqual(client.get(self.url("interview_detail", interview.public_id)).status_code, expected, user)
        # What belongs to a real campero is not offered for it, even to the administration
        client = self.login(self.admin, Client())
        for name in ("participant_edit", "participant_delete", "participant_data_export", "guardian_authorization", "safety_report"):
            self.assertEqual(client.get(self.url(name, practice.public_id)).status_code, 404, name)
        for follow_up in FollowUp.objects.filter(participant=practice):
            self.assertEqual(client.get(self.url("follow_up_detail", follow_up.id)).status_code, 404)
        self.assertFalse(AuditEvent.objects.filter(event=self.event, summary__icontains=SECRET).exists())
