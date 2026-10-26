"""Fixes from the UI/UX and accessibility audit of 2026-10-26 (sandbox-docs/emmaus/auditoria-ux).

A11Y-01 sticky bar and focus, A11Y-02 "Menú" as a modal, A11Y-04 the staff idle notice, A11Y-07 the live status,
UI-01/UX-02 the pastor's figures, UI-02 the risk report link, UI-03/UX-11 the administration home, UI-07 status
marks, UI-21 "auth-card--left". What only a browser can show (focus, the inert page, timers) is checked with
Playwright; these tests pin the markup, the stylesheet rules and the server side.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.template import Context, Template
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event, EventMembership, Interview, InterviewStatus, MembershipRole, ReportSnapshot
from events.services.interviews import KIOSK_KEY
from events.templatetags.emmaus import FOLLOW_UP_MARKS, STATUS_MARKS, duration_short

from .base import PASSWORD, CampFixture

CSS = (Path(settings.BASE_DIR) / "static" / "css" / "rotulo.css").read_text(encoding="utf-8")


@dataclass
class Rule:
    media: str
    selector: str
    body: str


def css_rules(css: str) -> list[Rule]:
    """Every style rule with the at-rules around it ("@media (max-width: 600px)"). Enough for this stylesheet."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules, media, start, i = [], [], 0, 0
    while i < len(css):
        if css[i] == "{":
            prelude = " ".join(css[start:i].split())
            if prelude.startswith("@") and not prelude.startswith("@font-face"):
                media.append(prelude)
                start = i + 1
            else:
                end = css.index("}", i)
                rules.append(Rule(" ".join(media), prelude, " ".join(css[i + 1 : end].split())))
                i, start = end, end + 1
        elif css[i] == "}":
            if media:
                media.pop()
            start = i + 1
        i += 1
    return rules


RULES = css_rules(CSS)


def rules_for(selector: str) -> list[Rule]:
    return [r for r in RULES if selector in [s.strip() for s in r.selector.split(",")]]


def tag(html: str, pattern: str) -> str:
    """The opening tag that matches `pattern` (a regex on its attributes)."""
    match = re.search(r"<[a-z]+[^>]*" + pattern + r"[^>]*>", html)
    if not match:
        raise AssertionError(f"no tag matches {pattern!r}")
    return match.group(0)


def complete(event, participant, when=None):
    when = when or timezone.now()
    Interview.objects.create(
        event=event, participant=participant, version=event.questionnaire_version, status=Interview.Status.COMPLETED,
        completed_at=when, started_at=when - timedelta(minutes=10), follow_up_decision="no_requerido",
    )
    participant.interview_status = InterviewStatus.COMPLETED
    participant.save(update_fields=["interview_status"])


# ---- A11Y-01: the sticky "Continuar" bar never hides the focused option -------------------------------------


class StickyStepBarTests(SimpleTestCase):
    def sticky_rule(self):
        sticky = [r for r in rules_for(".step .step__actions") if "position: sticky" in r.body]
        self.assertEqual(len(sticky), 1, "one sticky rule for the step's bar")
        return sticky[0]

    def test_question_pages_reserve_the_bar_height_for_keyboard_focus(self):
        sticky = self.sticky_rule()
        self.assertIn("max-width: 600px", sticky.media)
        padding = [r for r in rules_for("html:has(.step .step__actions)") if r.media == sticky.media]
        self.assertEqual(len(padding), 1, "scroll-padding wherever the bar is sticky, and only there")
        # The bar is 12 + 60 + 16 px plus a 1px rule = 89px: reserve at least that
        match = re.search(r"scroll-padding-bottom: calc\(([\d.]+)rem \+ 1px\)", padding[0].body)
        self.assertIsNotNone(match, padding[0].body)
        self.assertGreaterEqual(float(match.group(1)) * 16 + 1, 89)

    def test_options_and_text_boxes_keep_a_gap_below_them(self):
        for selector in (".step .choice > input", ".step textarea"):
            with self.subTest(selector):
                self.assertTrue(any("scroll-margin" in r.body for r in rules_for(selector)), selector)

    def test_the_bar_is_not_sticky_in_a_short_window(self):
        # A phone on its side or 400 % zoom (320x256): an 89px bar would cover a third of the window
        self.assertIn("min-height", self.sticky_rule().media)


# ---- A11Y-02: "Menú" is a labelled modal dialog -----------------------------------------------------------------


class MenuPanelDialogTests(CampFixture):
    def assert_modal_panel(self, response):
        html = response.content.decode()
        panel = tag(html, r'id="menu-panel"')
        self.assertIn('role="dialog"', panel)
        self.assertIn('aria-modal="true"', panel)
        self.assertIn('aria-labelledby="menu-panel-title"', panel)
        self.assertRegex(html, r'<h2 id="menu-panel-title"[^>]*>Menú</h2>')

    def test_coordinator_shell(self):
        self.login(self.coord1)
        self.choose_station()
        self.assert_modal_panel(self.client.get(self.url("coord_home")))

    def test_leader_shell(self):
        self.login(self.admin)
        self.assert_modal_panel(self.client.get(self.url("admin_home")))

    def test_base_template(self):
        self.login(self.admin)
        self.assert_modal_panel(self.client.get(reverse("church_settings")))


# ---- A11Y-04: "¿Sigues aquí?" before the staff idle logout ------------------------------------------------------


class StaffIdleNoticeTests(CampFixture):
    PING = "/eventos/cuenta/sigo-aqui/"

    def test_the_ping_url(self):
        self.assertEqual(reverse("events:still_here"), self.PING)

    def test_still_here_answers_204_to_staff_and_touches_the_session(self):
        self.login(self.coord1)
        session = self.client.session
        # 20 s ago: the middleware alone refreshes last_seen only after 30 s, so this proves the view sets it
        session["last_seen"] = int(time.time()) - 20
        session.save()
        response = self.client.get(self.PING)
        self.assertEqual(response.status_code, 204)
        self.assertGreaterEqual(self.client.session["last_seen"], int(time.time()) - 2)

    def test_still_here_sends_anonymous_visitors_to_the_login_page(self):
        response = self.client.get(self.PING)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("login")))

    def test_still_here_does_not_save_a_session_that_already_expired(self):
        self.login(self.coord1)
        session = self.client.session
        session["last_seen"] = int(time.time()) - settings.EMMAUS_IDLE_LOGOUT_SECONDS - 5
        session.save()
        response = self.client.get(self.PING)
        self.assertEqual(response.status_code, 302)  # the middleware logs out first
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_still_here_is_get_only(self):
        self.login(self.coord1)
        self.assertEqual(self.client.post(self.PING).status_code, 405)

    def staff_pages(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        yield "coordinator shell", self.client.get(self.url("coord_home"))
        yield "interview shell", self.client.get(self.url("interview_step", interview.public_id, 1))
        self.login(self.admin)
        yield "leader shell", self.client.get(self.url("admin_home"))
        yield "base.html", self.client.get(reverse("church_settings"))

    def test_staff_shells_expose_the_idle_limit_and_the_ping_url(self):
        for name, response in self.staff_pages():
            with self.subTest(name):
                body = re.search(r"<body[^>]*>", response.content.decode()).group(0)
                self.assertIn(f'data-idle-seconds="{settings.EMMAUS_IDLE_LOGOUT_SECONDS}"', body)
                self.assertIn(f'data-idle-ping-url="{self.PING}"', body)

    @override_settings(EMMAUS_IDLE_LOGOUT_SECONDS=150)
    def test_the_limit_follows_the_setting(self):
        for name, response in self.staff_pages():
            with self.subTest(name):
                self.assertIn('data-idle-seconds="150"', response.content.decode())

    def test_the_tag_says_nothing_for_someone_signed_out(self):
        template = Template("{% load emmaus %}{% staff_idle_attrs %}")
        self.assertEqual(template.render(Context({"user": AnonymousUser()})), "")
        self.assertEqual(template.render(Context({})), "")
        self.assertIn('data-idle-seconds="', template.render(Context({"user": self.coord1})))

    @override_settings(EMMAUS_IDLE_LOGOUT_SECONDS=0)
    def test_nothing_when_the_idle_logout_is_off(self):
        template = Template("{% load emmaus %}{% staff_idle_attrs %}")
        self.assertEqual(template.render(Context({"user": self.coord1})), "")

    def last_seen(self, seconds_ago):
        session = self.client.session
        session["last_seen"] = int(time.time()) - seconds_ago
        session.save()

    def test_the_countdown_starts_from_the_servers_last_touch(self):
        # Refuter round 1: the middleware refreshes last_seen at most every 30 s, so a page opened 25 s after the last
        # touch must say 1775 s are left, not 1800, or "Seguir aquí" arrives after the server already logged out.
        self.login(self.coord1)
        self.choose_station()
        self.last_seen(25)
        body = re.search(r"<body[^>]*>", self.client.get(self.url("coord_home")).content.decode()).group(0)
        remaining = int(re.search(r'data-idle-remaining="(\d+)"', body).group(1))
        limit = settings.EMMAUS_IDLE_LOGOUT_SECONDS
        self.assertIn(remaining, range(limit - 27, limit - 24))

    def test_a_fresh_touch_leaves_the_whole_limit(self):
        self.login(self.coord1)
        self.choose_station()
        self.last_seen(40)  # over 30 s: this request refreshes last_seen
        body = re.search(r"<body[^>]*>", self.client.get(self.url("coord_home")).content.decode()).group(0)
        remaining = int(re.search(r'data-idle-remaining="(\d+)"', body).group(1))
        self.assertIn(remaining, range(settings.EMMAUS_IDLE_LOGOUT_SECONDS - 2, settings.EMMAUS_IDLE_LOGOUT_SECONDS + 1))

    def test_checking_does_not_touch_the_session(self):
        # "¿Sigue abierta?" before announcing the end: a background request (no middleware refresh) that the view
        # answers without setting last_seen, with the seconds that are really left.
        self.login(self.coord1)
        self.last_seen(100)
        before = self.client.session["last_seen"]
        response = self.client.get(self.PING + "?comprobar=1", HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.session["last_seen"], before)
        self.assertIn(int(response["X-Idle-Remaining"]), range(settings.EMMAUS_IDLE_LOGOUT_SECONDS - 102, settings.EMMAUS_IDLE_LOGOUT_SECONDS - 99))

    def test_checking_an_expired_session_redirects_to_the_login_page(self):
        self.login(self.coord1)
        self.last_seen(settings.EMMAUS_IDLE_LOGOUT_SECONDS + 5)
        response = self.client.get(self.PING + "?comprobar=1", HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("login")))

    def test_the_touch_says_the_whole_limit_is_left(self):
        self.login(self.coord1)
        self.last_seen(20)
        response = self.client.get(self.PING)
        self.assertEqual(int(response["X-Idle-Remaining"]), settings.EMMAUS_IDLE_LOGOUT_SECONDS)

    def test_a_confined_browser_never_starts_the_staff_notice(self):
        # The 404 and CSRF pages render base.html; on a kiosk browser they must not start "¿Sigues aquí?"
        self.login(self.coord1)
        session = self.client.session
        session[KIOSK_KEY] = {"x": 1}
        session.save()
        response = self.client.get("/estacion/no-existe/")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("data-idle-seconds", response.content.decode())

    def test_signed_out_and_kiosk_pages_have_no_staff_notice(self):
        self.assertNotIn("data-idle-seconds", self.client.get(reverse("login")).content.decode())
        self.login(self.coord1)
        self.choose_station(number=5)
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.assertIn(KIOSK_KEY, self.client.session)
        response = self.client.get(reverse("kiosk:step", args=[1]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("data-idle-seconds", response.content.decode())


# ---- A11Y-07: the update time is not announced; a lost connection is --------------------------------------------


class LiveStatusTests(CampFixture):
    def assert_silent_time_and_announcer(self, html, status_id="estado-actualizacion", element="p"):
        line = tag(html, f'id="{status_id}"')
        self.assertIn('aria-live="off"', line)
        self.assertNotIn("polite", line)
        self.assertRegex(
            html, rf'<{element} class="visually-hidden" role="status" aria-live="polite" data-live-announce="{status_id}"></{element}>'
        )

    def test_the_time_line_is_silent_and_a_separate_status_carries_the_alerts(self):
        self.login(self.admin)
        for name in ("pastor_home", "progress", "admin_home"):
            with self.subTest(name):
                self.assert_silent_time_and_announcer(self.client.get(self.url(name)).content.decode())

    def test_the_coordinator_and_tutor_dashboards_too(self):
        self.login(self.coord1)
        self.choose_station()
        for name in ("coord_home", "coord_progress"):
            with self.subTest(name):
                self.assert_silent_time_and_announcer(self.client.get(self.url(name)).content.decode())
        self.login(self.tutor1)
        self.assert_silent_time_and_announcer(self.client.get(self.url("tutor_home")).content.decode())

    def test_the_live_presentation_too(self):
        self.login(self.pastor)
        html = self.client.get(self.url("presentation_live")).content.decode()
        self.assert_silent_time_and_announcer(html, status_id="deck-estado", element="span")


# ---- UI-01 / UX-02: the pastor's figures fit their tiles --------------------------------------------------------


class FiguresTests(CampFixture):
    def test_duration_short(self):
        nb = " "
        cases = {375: f"6{nb}h 15{nb}min", 120: f"2{nb}h", 45: f"45{nb}min", 61: f"1{nb}h 1{nb}min", 0: f"0{nb}min", None: "", "": ""}
        for minutes, text in cases.items():
            with self.subTest(minutes):
                self.assertEqual(duration_short(minutes), text)

    def test_the_figure_is_short_and_the_sentence_keeps_the_words(self):
        for participant in (self.p1, self.p2, self.p3):
            complete(self.event, participant)  # 1.5 per hour, 2 pending: 80 minutes
        self.login(self.pastor)
        html = self.client.get(self.url("pastor_home")).content.decode()
        figure = re.search(r"<dt>Tiempo estimado</dt><dd class=\"figures__value\">([^<]*)</dd>", html)
        self.assertIsNotNone(figure)
        self.assertEqual(figure.group(1), "1 h 20 min")
        self.assertIn("alrededor de 1 hora 20 minutos al ritmo actual", html)

    def test_a_figure_value_wraps_inside_its_tile(self):
        values = rules_for(".figures__value")
        self.assertTrue(values)
        for rule in values:
            self.assertNotIn("nowrap", rule.body, rule.selector)
        self.assertTrue(any("overflow-wrap: anywhere" in r.body for r in values))
        self.assertTrue(any("min-width: 0" in r.body for r in rules_for(".figures > div")))


# ---- UI-02: "Reportar una situación de riesgo" is a chile secondary button --------------------------------------


class SafetyReportLinkTests(CampFixture):
    def test_the_review_draws_the_risk_report_apart_from_discard(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        html = self.client.get(self.url("interview_review", interview.public_id)).content.decode()
        risk = tag(html, r'href="/eventos/campamento-a/camperos/[^"]+/riesgo/')
        self.assertIn('class="btn btn--secondary btn--danger-outline"', risk)
        discard = tag(html, r'href="' + re.escape(self.url("interview_discard", interview.public_id)) + '"')
        self.assertNotIn("btn", discard)

    def test_the_camper_record_draws_the_risk_report_as_the_same_button(self):
        self.login(self.coord1)
        html = self.client.get(self.url("participant_detail", self.p2.public_id)).content.decode()
        risk = tag(html, r'href="' + re.escape(self.url("safety_report", self.p2.public_id)) + '"')
        self.assertIn('class="btn btn--secondary btn--danger-outline"', risk)
        self.assertRegex(html, r'<a class="btn btn--secondary btn--danger-outline" href="[^"]+">Reportar una situación de riesgo</a>')

    def test_the_outline_is_chile(self):
        rules = rules_for(".btn--danger-outline")
        self.assertTrue(rules)
        self.assertIn("color: var(--chile)", rules[0].body)
        self.assertIn("border-color: var(--chile)", rules[0].body)


# ---- UI-03 / UX-11: the administration home of an event with no camperos ----------------------------------------


class AdminHomeTests(CampFixture):
    SIGN = "Registra o importa a los camperos"

    def empty_event(self, **kw):
        return Event.objects.create(
            church=self.church, name="Retiro vacío", slug="retiro-vacio", questionnaire_version=self.version,
            procedures_approved_at=kw.get("procedures_approved_at", timezone.now()),
            starts_on=timezone.localdate(), ends_on=timezone.localdate(),
        )

    def home(self, event):
        self.login(self.admin)
        return self.client.get(reverse("events:admin_home", args=[event.slug])).content.decode()

    def team_row(self, html):
        rows = re.findall(r'<li class="row[^"]*"[^>]*>.*?</li>', html, flags=re.S)
        row = [r for r in rows if '<p class="row__title">Equipo</p>' in r]
        self.assertEqual(len(row), 1)
        return row[0]

    def test_no_camperos_asks_to_import_or_register_them(self):
        event = self.empty_event()
        html = self.home(event)
        self.assertEqual(len(re.findall(r'class="sign[ "]', html)), 1, "exactly one next-action panel")
        self.assertIn(f'<h2 id="que-sigue" class="sign__title">{self.SIGN}</h2>', html)
        self.assertIn("Puedes importar la lista desde una hoja de cálculo o registrarlos uno por uno.", html)
        importar = reverse("events:participant_import", args=[event.slug])
        self.assertIn(f'<a class="btn btn--primary btn--large" href="{importar}">Importar camperos</a>', html)
        self.assertIn(f'<a class="sign__link" href="{reverse("events:participant_create", args=[event.slug])}">Registrar uno</a>', html)

    def test_procedures_still_come_first(self):
        html = self.home(self.empty_event(procedures_approved_at=None))
        self.assertEqual(len(re.findall(r'class="sign[ "]', html)), 1)
        self.assertIn("Confirma los procedimientos de la iglesia", html)
        self.assertNotIn(self.SIGN, html)

    def test_no_panel_once_there_are_camperos(self):
        self.assertNotIn(self.SIGN, self.home(self.event))

    def test_team_is_missing_without_coordinators(self):
        row = self.team_row(self.home(self.empty_event()))
        self.assertIn("row--attention", row)
        self.assertIn('<span class="status status--pending"><span class="status__mark" aria-hidden="true"></span>Falta</span>', row)
        self.assertNotIn("Listo", row)
        self.assertNotIn("Cada coordinador tiene tutor", row)

    def test_team_needs_review_with_a_coordinator_without_tutor(self):
        event = self.empty_event()
        EventMembership.objects.create(event=event, user=self.coord1, role=MembershipRole.COORDINATOR)
        row = self.team_row(self.home(event))
        self.assertIn("row--attention", row)
        self.assertIn('<span class="status status--review"><span class="status__mark" aria-hidden="true"></span>Revisar</span>', row)

    def test_team_is_ready_when_every_coordinator_has_a_tutor(self):
        row = self.team_row(self.home(self.event))  # three coordinators, all with a tutor
        self.assertNotIn("row--attention", row)
        self.assertIn('<span class="status status--done"><span class="status__mark" aria-hidden="true"></span>Listo</span>', row)

    def cutoffs_row(self, html):
        rows = re.findall(r'<li class="row[^"]*"[^>]*>.*?</li>', html, flags=re.S)
        row = [r for r in rows if '<p class="row__title">Cortes</p>' in r]
        self.assertEqual(len(row), 1)
        return row[0]

    def test_cutoffs_wait_until_the_first_automatic_one(self):
        self.event.reports_enabled = True
        self.event.save()
        row = self.cutoffs_row(self.home(self.event))
        self.assertIn('<span class="status status--pending"><span class="status__mark" aria-hidden="true"></span>Esperando</span>', row)
        self.assertNotIn("Listo", row)
        self.assertNotIn("row--attention", row)

    def test_cutoffs_are_ready_after_the_first_automatic_one(self):
        self.event.reports_enabled = True
        self.event.save()
        ReportSnapshot.objects.create(
            event=self.event, trigger=ReportSnapshot.Trigger.AUTOMATIC, cutoff_at=timezone.now(), scope_key="evento",
            scope_label="Evento", interval_minutes=120, metrics_version="1", data={},
        )
        row = self.cutoffs_row(self.home(self.event))
        self.assertIn('<span class="status status--done"><span class="status__mark" aria-hidden="true"></span>Listo</span>', row)

    def test_team_state_in_the_context(self):
        self.login(self.admin)
        event = self.empty_event()
        url = reverse("events:admin_home", args=[event.slug])
        self.assertEqual(self.client.get(url).context["team_state"], "falta")
        EventMembership.objects.create(event=event, user=self.coord1, role=MembershipRole.COORDINATOR)
        self.assertEqual(self.client.get(url).context["team_state"], "revisar")
        self.assertEqual(self.client.get(self.url("admin_home")).context["team_state"], "listo")


# ---- UI-07: status marks mean one thing each --------------------------------------------------------------------


class StatusMarkTests(CampFixture):
    TERMINAL_OR_REVIEW = ("descartada", "retirada", "archivado", "revisar", "por_evaluar", "pasada_al_pastor", "publicada")

    def test_no_dead_keys(self):
        # Keys a template passes; "por_evaluar" is what Interview.follow_up_decision stores for "Por decidir"
        for key in ("vencida", "cancelado", "por_decidir"):
            self.assertNotIn(key, STATUS_MARKS)
        self.assertEqual(STATUS_MARKS["por_evaluar"], ("review", "Por decidir"))

    def test_an_archived_event_is_closed_in_the_event_list(self):
        # Only a superuser's list includes archived events (events.access.accessible_events)
        Event.objects.filter(pk=self.other_event.pk).update(status=Event.Status.ARCHIVED)
        root = User.objects.create_superuser("raiz@prueba.mx", "raiz@prueba.mx", PASSWORD)
        self.login(root)
        html = self.client.get(reverse("events:list") + "?todos=1").content.decode()
        self.assertIn("Campamento B", html)
        self.assertIn('<span class="status status--closed"><span class="status__mark" aria-hidden="true"></span>Archivado</span>', html)
        self.assertNotIn('status--pending"><span class="status__mark" aria-hidden="true"></span>Archivado', html)

    def test_no_two_different_statuses_share_mark_and_label(self):
        seen = {}
        for key, pair in [*STATUS_MARKS.items(), *FOLLOW_UP_MARKS.items()]:
            seen.setdefault(pair, set()).add(key)
        shared = {pair: keys for pair, keys in seen.items() if len(keys) > 1}
        self.assertEqual(shared, {})

    def test_a_label_always_has_the_same_mark(self):
        marks = {}
        for kind, label in [*STATUS_MARKS.values(), *FOLLOW_UP_MARKS.values()]:
            marks.setdefault(label, set()).add(kind)
        self.assertEqual({label: kinds for label, kinds in marks.items() if len(kinds) > 1}, {})

    def test_terminal_and_review_states_do_not_use_the_not_started_outline(self):
        for key in self.TERMINAL_OR_REVIEW:
            with self.subTest(key):
                self.assertNotEqual(STATUS_MARKS[key][0], "pending")
        self.assertNotEqual(FOLLOW_UP_MARKS["cancelado"][0], "pending")
        self.assertEqual(STATUS_MARKS["descartada"][0], "closed")
        self.assertEqual(STATUS_MARKS["revisar"][0], "review")
        self.assertEqual(STATUS_MARKS["publicada"][0], "done")

    def test_every_mark_has_its_own_drawing(self):
        bodies = {}
        for kind in ("done", "progress", "closed", "review"):
            rules = rules_for(f".status--{kind} .status__mark")
            self.assertTrue(rules, kind)
            bodies[kind] = rules[0].body
        self.assertEqual(len(set(bodies.values())), 4)
        self.assertIn("linear-gradient", bodies["review"])  # half filled

    def test_the_tag_renders_the_mark_and_the_word(self):
        html = Template('{% load emmaus %}{% status "descartada" %}|{% status "revisar" %}|{% follow_up_status "cancelado" %}').render(Context())
        self.assertEqual(
            html,
            '<span class="status status--closed"><span class="status__mark" aria-hidden="true"></span>Descartada</span>|'
            '<span class="status status--review"><span class="status__mark" aria-hidden="true"></span>Revisar</span>|'
            '<span class="status status--closed"><span class="status__mark" aria-hidden="true"></span>Cancelado</span>',
        )

    def test_the_admin_home_has_no_outlined_revisar(self):
        self.m_coord1.tutor_assignment.delete()  # a coordinator without a tutor: "Equipo" says "Revisar"
        self.p2.coordinator = None  # a campero without a coordinator: "Camperos" says "Revisar"
        self.p2.save(update_fields=["coordinator"])
        self.login(self.admin)
        html = self.client.get(self.url("admin_home")).content.decode()
        review = '<span class="status status--review"><span class="status__mark" aria-hidden="true"></span>Revisar</span>'
        self.assertEqual(html.count(review), 2)
        self.assertNotIn('status--pending"><span class="status__mark" aria-hidden="true"></span>Revisar', html)

    def test_a_published_questionnaire_version_is_not_drawn_as_not_started(self):
        self.login(self.admin)
        html = self.client.get(self.url("questionnaires")).content.decode()
        self.assertIn('<span class="status status--done"><span class="status__mark" aria-hidden="true"></span>Publicada</span>', html)


# ---- UI-21: "auth-card--left" has a rule ------------------------------------------------------------------------


class AuthCardLeftTests(SimpleTestCase):
    def test_the_left_card_shares_the_page_heads_left_edge(self):
        rules = rules_for(".auth-card--left")
        self.assertTrue(rules)
        self.assertIn("margin-inline: 0", rules[0].body)
