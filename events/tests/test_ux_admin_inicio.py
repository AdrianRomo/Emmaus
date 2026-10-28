"""Administración, inicio y configuración (round "cada rol sabe qué hacer", 2026-10-26).

"Preparación" says how many steps are ready and shows what is pending first; the ready steps are folded under an id
that keeps the reader's choice across the 30 s refresh (audit UI-06, LANG-24, UX-11). "Cortes" is one row: the
schedule, the last cutoff and whether the scheduler runs. "Configuración" stacks its sections in reading order under
an index, and each save comes back to the section it was made in (UX-14, UI-23, UI-26).
"""

import re
from datetime import date, datetime, time, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django import forms
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from events.forms import EventForm, EventSettingsForm
from events.models import Event, ReportSnapshot

from .base import CampFixture
from .test_camp_visual import declarations, stylesheet
from .test_ui import ENGLISH, analyse

MEXICO = ZoneInfo("America/Mexico_City")
PREPARATION = re.compile(r'<section aria-labelledby="preparacion">.*?</section>', re.S)
ROW = re.compile(r'<li class="row[^"]*"[^>]*>.*?</li>', re.S)
TITLE = re.compile(r'<p class="row__title">(.*?)</p>')
FOLD = '<details class="fold admin-steps__done" id="preparacion-listos">'
STEPS = ["Procedimientos de la iglesia", "Cuestionario", "Camperos", "Equipo", "Computadoras", "Cortes"]
LISTO = '<span class="status status--done"><span class="status__mark" aria-hidden="true"></span>Listo</span>'
REVISAR = '<span class="status status--review"><span class="status__mark" aria-hidden="true"></span>Revisar</span>'
APAGADOS = '<span class="status status--pending"><span class="status__mark" aria-hidden="true"></span>Apagados</span>'
# The questionnaires are chosen in Cuestionarios (several per event): Configuración only links there.
SECTIONS = ["procedimientos", "datos", "cortes", "privacidad", "exportar", "computadoras"]  # "exportar": Ronda 20
GROUPS = {
    "cortes": {"time_zone", "reports_enabled", "report_interval_minutes", "report_window_start", "report_window_end"},
    "privacidad": {"adult_age", "requires_guardian_authorization", "min_group_size", "retention_days", "privacy_notice", "safeguarding_instructions"},
}


def local(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MEXICO)


def text_of(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


class HomeHelpers(CampFixture):
    def automatic_cutoff(self, at=None, status=ReportSnapshot.Status.READY):
        return ReportSnapshot.objects.create(
            event=self.event, scope_key="evento", scope_label="Todo el campamento", trigger=ReportSnapshot.Trigger.AUTOMATIC,
            status=status, cutoff_at=at or timezone.now(), interval_minutes=120, metrics_version="1",
        )

    def new_event(self, **fields):
        """An event as "Nuevo evento" leaves it before anything else: no procedures, questionnaire, people or computers."""
        return Event.objects.create(
            church=self.church, name="Retiro nuevo", slug="retiro-nuevo", starts_on=timezone.localdate(), ends_on=timezone.localdate(), **fields
        )

    def pages(self, event=None, at=None):
        """The administration home: the full page and the fragment the 30 s refresh puts in its place."""
        url = reverse("events:admin_home", args=[(event or self.event).slug])
        with mock.patch("django.utils.timezone.now", return_value=at or timezone.now()):
            client = self.login(self.admin, Client())
            page = client.get(url)
            fragment = client.get(url, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(fragment.status_code, 200)
        return page, fragment

    def preparation(self, response):
        match = PREPARATION.search(response.content.decode())
        self.assertIsNotNone(match, "no Preparación section")
        return match.group(0)

    def split(self, section):
        """The rows before the fold of ready steps and the rows inside it."""
        pending, _, done = section.partition(FOLD)
        return ROW.findall(pending), ROW.findall(done)

    def titles(self, rows):
        return [TITLE.search(row).group(1) for row in rows]

    def cutoffs_row(self, section):
        rows = [row for row in ROW.findall(section) if '<p class="row__title">Cortes</p>' in row]
        self.assertEqual(len(rows), 1, "one Cortes row")
        return rows[0]


# ---- 1, 2. "N de M pasos listos", what is pending first ----------------------------------------------------------


class PreparationCountTests(HomeHelpers):
    def test_nothing_ready_counts_zero_of_six_and_folds_nothing(self):
        page, fragment = self.pages(self.new_event())
        section = self.preparation(page)
        self.assertIn('<p class="admin-steps__summary">0 de 6 pasos listos.</p>', section)
        self.assertNotIn('id="preparacion-listos"', page.content.decode())
        pending, done = self.split(section)
        self.assertEqual(self.titles(pending), STEPS)
        self.assertEqual(done, [])
        self.assertNotIn("Listo", section)
        self.assertEqual((page.context["steps_done"], page.context["steps_total"]), (0, 6))
        self.assertEqual([step["key"] for step in page.context["steps"]], ["procedimientos", "cuestionario", "camperos", "equipo", "computadoras", "cortes"])
        self.assertEqual(self.preparation(fragment), section)

    def test_one_ready_step_is_singular(self):
        page, _ = self.pages(self.new_event(procedures_approved_at=timezone.now()))
        section = self.preparation(page)
        self.assertIn('<p class="admin-steps__summary">1 de 6 pasos listos.</p>', section)
        self.assertIn(f"{FOLD}<summary>1 paso listo</summary>", section)

    def test_the_count_matches_the_rows(self):
        page, _ = self.pages()  # every step ready but cutoffs, still waiting for the first automatic one
        section = self.preparation(page)
        self.assertIn('<p class="admin-steps__summary">5 de 6 pasos listos.</p>', section)
        self.assertIn(f"{FOLD}<summary>5 pasos listos</summary>", section)
        pending, done = self.split(section)
        self.assertEqual(self.titles(pending), ["Cortes"])
        self.assertEqual(len(done), 5)
        self.assertEqual((page.context["steps_done"], page.context["steps_total"]), (5, 6))
        self.assertEqual(sum(step["state"] == "listo" for step in page.context["steps"]), 5)

    def test_everything_ready_says_todo_listo(self):
        self.automatic_cutoff()
        page, fragment = self.pages()
        section = self.preparation(page)
        self.assertIn('<p class="admin-steps__summary">Todo listo: 6 de 6 pasos.</p>', section)
        self.assertNotIn("pasos listos.", section)
        pending, done = self.split(section)
        self.assertEqual(pending, [])
        self.assertEqual(self.titles(done), STEPS)
        self.assertNotIn("<li", section.partition(FOLD)[0], "no empty list of pending steps")
        self.assertEqual(self.preparation(fragment), section)


class PreparationOrderTests(HomeHelpers):
    def setUp(self):
        self.p2.coordinator = None  # "Camperos" needs a look
        self.p2.save(update_fields=["coordinator"])

    def test_pending_rows_come_before_the_fold_and_ready_rows_are_inside_it(self):
        page, _ = self.pages()
        section = self.preparation(page)
        self.assertIn("4 de 6 pasos listos.", section)
        head, _, _ = section.partition(FOLD)
        self.assertIn('<h2 id="preparacion" class="heading">Preparación</h2>', head)
        pending, done = self.split(section)
        self.assertEqual(self.titles(pending), ["Camperos", "Cortes"])  # in the order of the steps
        self.assertEqual(self.titles(done), ["Procedimientos de la iglesia", "Cuestionario", "Equipo", "Computadoras"])
        for row in pending:
            self.assertNotIn("Listo", row)
        for row in done:
            self.assertIn(LISTO, row)
            self.assertNotIn("row--attention", row)

    def test_the_fold_starts_closed_and_the_fragment_carries_the_same_markup(self):
        page, fragment = self.pages()
        section = self.preparation(page)
        self.assertIn(f"{FOLD}<summary>4 pasos listos</summary>", section)  # no "open": closed by default
        self.assertIn('id="preparacion-listos"', fragment.content.decode())
        self.assertEqual(self.preparation(fragment), section)

    def test_the_team_row_keeps_its_id_where_it_is_listed(self):
        page, _ = self.pages()
        _, done = self.split(self.preparation(page))
        team = [row for row in done if '<p class="row__title">Equipo</p>' in row]
        self.assertEqual(len(team), 1)
        self.assertTrue(team[0].startswith('<li class="row" id="preparacion-equipo">'), team[0][:80])
        self.assertIn("Cada coordinador tiene tutor.", team[0])


# ---- 3. One "Cortes" row ------------------------------------------------------------------------------------------


class CutoffsRowTests(HomeHelpers):
    DAY = date(2026, 11, 5)

    def setUp(self):
        Event.objects.filter(pk=self.event.pk).update(
            starts_on=self.DAY, ends_on=self.DAY + timedelta(days=2), report_window_start=time(8), report_window_end=time(20),
            report_interval_minutes=120, reports_enabled=True, status=Event.Status.ACTIVE,
            updated_at=local(self.DAY, 0) - timedelta(days=3), procedures_approved_at=local(self.DAY, 0) - timedelta(days=3),
        )
        self.event.refresh_from_db()

    def test_one_row_with_the_schedule_the_last_cutoff_and_the_scheduler(self):
        self.automatic_cutoff(local(self.DAY, 14))
        page, _ = self.pages(at=local(self.DAY, 14, 20))
        html = page.content.decode()
        self.assertNotIn('<p class="row__title">Cortes automáticos</p>', html)
        section = self.preparation(page)
        row = self.cutoffs_row(section)
        self.assertTrue(row.startswith('<li class="row" id="cortes-automaticos">'), row[:80])
        self.assertIn(LISTO, row)
        text = text_of(row)
        self.assertIn("Cada 2 horas, de 08:00 a 20:00.", text)
        self.assertIn("Último corte: 5 nov, 14:00.", text)
        self.assertIn("Último corte automático: hoy 14:00.", text)
        self.assertIn(row, section.partition(FOLD)[2], "ready: inside the fold")

    def test_the_scheduler_id_only_while_the_scheduler_has_work(self):
        page, fragment = self.pages(at=local(self.DAY, 7, 50))  # before the first slot: not late
        self.assertIn('<li class="row" id="cortes-automaticos">', page.content.decode())
        self.assertIn('id="cortes-automaticos"', fragment.content.decode())
        for status in (Event.Status.DRAFT, Event.Status.CLOSED):
            Event.objects.filter(pk=self.event.pk).update(status=status)
            page, fragment = self.pages(at=local(self.DAY, 9))
            self.assertNotIn('id="cortes-automaticos"', page.content.decode(), status)
            self.assertNotIn('id="cortes-automaticos"', fragment.content.decode(), status)
            self.cutoffs_row(self.preparation(page))  # the row stays, without the scheduler sentence
            self.assertNotIn("Todavía no hay cortes automáticos.", page.content.decode())

    def test_cutoffs_turned_off_say_apagados_and_wait_in_the_pending_list(self):
        Event.objects.filter(pk=self.event.pk).update(reports_enabled=False)
        page, _ = self.pages(at=local(self.DAY, 9))
        self.assertNotIn('id="cortes-automaticos"', page.content.decode())
        section = self.preparation(page)
        row = self.cutoffs_row(section)
        self.assertIn(APAGADOS, row)
        self.assertIn("Los cortes automáticos están apagados.", text_of(row))
        self.assertNotIn("Listo", row)
        self.assertNotIn("row--attention", row)
        self.assertIn(row, section.partition(FOLD)[0])
        self.assertEqual([s["state"] for s in page.context["steps"] if s["key"] == "cortes"], ["apagados"])

    def test_a_late_scheduler_marks_the_row_and_says_what_to_do(self):
        self.automatic_cutoff(local(self.DAY, 12))
        page, fragment = self.pages(at=local(self.DAY, 14, 16))
        section = self.preparation(page)
        row = self.cutoffs_row(section)
        self.assertTrue(row.startswith('<li class="row row--attention" id="cortes-automaticos">'), row[:80])
        self.assertIn(REVISAR, row)
        self.assertIn("El programador de cortes no ha corrido desde las 12:00. Puedes generar un corte a mano en Reportes.", text_of(row))
        self.assertIn("Generar un corte", row)
        self.assertNotIn("Listo", row)
        self.assertIn(row, section.partition(FOLD)[0], "pending: before the fold")
        self.assertEqual(self.preparation(fragment), section)

    def test_a_failed_cutoff_is_said_without_marking_the_row(self):
        self.automatic_cutoff(local(self.DAY, 12))
        self.automatic_cutoff(local(self.DAY, 14), status=ReportSnapshot.Status.FAILED)
        page, _ = self.pages(at=local(self.DAY, 14, 40))
        section = self.preparation(page)
        row = self.cutoffs_row(section)
        self.assertTrue(row.startswith('<li class="row" id="cortes-automaticos">'), row[:80])
        self.assertIn("<strong>1 corte no se pudo generar.</strong>", row)
        self.assertIn("Último corte automático: hoy 14:00.", text_of(row))
        self.assertIn(REVISAR, row)
        self.assertNotIn("Listo", row)
        self.assertIn(row, section.partition(FOLD)[0], "pending: before the fold")
        step = [s for s in page.context["steps"] if s["key"] == "cortes"][0]
        self.assertEqual((step["state"], step["attention"]), ("revisar", False))


# ---- 4. Configuración by sections -----------------------------------------------------------------------------------


def form_data(event, form_class, prefix, which, **changes):
    """A settings form as a person posts it: every field with its current value, some changed."""
    form = form_class(instance=Event.objects.get(pk=event.pk), prefix=prefix)
    data = {"form": which}
    for name, field in form.fields.items():
        value = changes.get(name, form[name].value())
        if isinstance(field, forms.BooleanField):
            if value:
                data[f"{prefix}-{name}"] = "on"
            continue
        if isinstance(value, time):
            value = value.strftime("%H:%M")
        elif isinstance(value, date):
            value = value.isoformat()
        data[f"{prefix}-{name}"] = "" if value is None else value
    return data


class SettingsSectionsTests(CampFixture):
    def page(self):
        self.login(self.admin)
        response = self.client.get(self.url("settings"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def forms(self, html):
        """{discriminator or action: (opening tag, body)} of every form on the page."""
        found = {}
        for tag, body in re.findall(r"(<form[^>]*>)(.*?)</form>", html, re.S):
            which = re.search(r'name="form" value="(\w+)"', body) or re.search(r'name="action" value="(\w+)"', body)
            found.setdefault(which.group(1) if which else "other", []).append((tag, body))  # "other": the bar's "Cerrar sesión"
        return found

    def test_the_sections_follow_the_index(self):
        html = self.page()
        sections = re.findall(r'<section class="admin-section[^"]*" id="([^"]+)" aria-labelledby="([^"]+)">\s*<h2 id="([^"]+)" class="heading">', html)
        self.assertEqual([section for section, _, _ in sections], SECTIONS)
        for section, label, heading in sections:
            self.assertEqual(label, heading, section)
        nav = re.search(r'<nav class="page-toc" aria-label="Secciones">\s*<ul>(.*?)</ul>\s*</nav>', html, re.S)
        self.assertIsNotNone(nav)
        self.assertEqual(re.findall(r'<li><a href="#([^"]+)">', nav.group(1)), SECTIONS)
        self.assertEqual(
            re.findall(r'<a href="#[^"]+">([^<]+)</a>', nav.group(1)),
            ["Procedimientos", "Datos del evento", "Cortes", "Privacidad y protección", "Exportar datos", "Computadoras"],
        )
        self.assertNotIn("admin-split", html)  # one column

    def test_the_settings_form_holds_two_fieldsets_and_every_field_once(self):
        html = self.page()
        (tag, body), = self.forms(html)["settings"]
        self.assertIn("data-guard", tag)
        self.assertEqual(body.count("<fieldset"), 2)
        self.assertEqual(html.count('name="cfg-'), len(EventSettingsForm(instance=self.event).fields))
        for section, names in GROUPS.items():
            match = re.search(rf'<section class="admin-section[^"]*" id="{section}"[^>]*>(.*?)</section>', body, re.S)
            self.assertIsNotNone(match, f"{section} inside the settings form")
            self.assertIn("<fieldset", match.group(1))
            self.assertEqual(set(re.findall(r'name="cfg-(\w+)"', match.group(1))), names, section)
        self.assertEqual(body.count("btn--primary"), 1)
        self.assertIn('<button class="btn btn--primary" type="submit">Guardar cortes y privacidad</button>', body)

    def test_the_event_data_form_is_guarded_and_the_computers_are_not(self):
        Event.objects.filter(pk=self.event.pk).update(procedures_approved_at=None)
        found = self.forms(self.page())
        (tag, body), = found["basics"]
        self.assertIn("data-guard", tag)
        self.assertIn('<button class="btn btn--primary" type="submit">Guardar datos del evento</button>', body)
        for kind in ("procedures", "toggle", "add"):
            for tag, _ in found[kind]:
                self.assertNotIn("data-guard", tag, kind)

    def test_saving_the_event_data_comes_back_to_its_section(self):
        self.login(self.admin)
        response = self.client.post(self.url("settings"), form_data(self.event, EventForm, "basics", "basics", location="Rancho El Encino"))
        self.assertRedirects(response, self.url("settings") + "#datos", fetch_redirect_response=False)
        self.assertContains(self.client.get(response["Location"]), "Datos del evento guardados.")
        self.assertEqual(Event.objects.get(pk=self.event.pk).location, "Rancho El Encino")

    def test_a_new_slug_comes_back_to_the_section_at_the_new_address(self):
        self.login(self.admin)
        response = self.client.post(self.url("settings"), form_data(self.event, EventForm, "basics", "basics", slug="campamento-nuevo"))
        self.assertRedirects(response, reverse("events:settings", args=["campamento-nuevo"]) + "#datos", fetch_redirect_response=False)

    def test_saving_the_settings_comes_back_to_cortes_and_keeps_every_field(self):
        self.login(self.admin)
        changes = {
            "time_zone": "America/Cancun", "reports_enabled": False, "report_interval_minutes": 240,
            "report_window_start": time(9), "report_window_end": time(19), "adult_age": 21, "requires_guardian_authorization": False,
            "min_group_size": 7, "retention_days": 90, "privacy_notice": "Aviso nuevo.", "safeguarding_instructions": "Llama a Ana.",
        }
        self.assertEqual(set(changes), set(EventSettingsForm(instance=self.event).fields))
        response = self.client.post(self.url("settings"), form_data(self.event, EventSettingsForm, "cfg", "settings", **changes))
        self.assertRedirects(response, self.url("settings") + "#cortes", fetch_redirect_response=False)
        self.assertContains(self.client.get(response["Location"]), "Configuración guardada.")
        event = Event.objects.get(pk=self.event.pk)
        for name, value in changes.items():
            self.assertEqual(getattr(event, name), value, name)

    def test_confirming_the_procedures_comes_back_to_them(self):
        Event.objects.filter(pk=self.event.pk).update(procedures_approved_at=None)
        self.login(self.admin)
        response = self.client.post(self.url("settings"), {"form": "procedures", "confirm": "on"})
        self.assertRedirects(response, self.url("settings") + "#procedimientos", fetch_redirect_response=False)
        self.assertContains(self.client.get(response["Location"]), "Configuración guardada.")

    def test_an_invalid_form_stays_on_the_page(self):
        self.login(self.admin)
        response = self.client.post(self.url("settings"), form_data(self.event, EventSettingsForm, "cfg", "settings", min_group_size=1))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hay un problema")
        self.assertEqual(Event.objects.get(pk=self.event.pk).min_group_size, self.event.min_group_size)

    def test_an_invalid_new_slug_leaves_every_link_on_the_stored_address(self):
        """A bound form writes its clean values into its instance even when the form is invalid: the page was
        drawn with the unsaved slug, so its links and the computers' forms pointed at an event that does not exist."""
        self.login(self.admin)
        response = self.client.post(self.url("settings"), form_data(self.event, EventForm, "basics", "basics", slug="otro", name=""))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hay un problema")
        self.assertEqual(Event.objects.get(pk=self.event.pk).slug, self.event.slug)
        html = response.content.decode()
        self.assertIn('value="otro"', html)  # what the person typed stays in the field
        urls = re.findall(r'(?:href|action)="(/eventos/[^"?#]+/)[^"]*"', html)
        self.assertTrue(urls)
        for url in urls:
            self.assertTrue(url.startswith(f"/eventos/{self.event.slug}/"), url)
        self.assertIn(f'action="{reverse("events:stations", args=[self.event.slug])}"', html)


# ---- 5. One title, Spanish, every control labelled; the new rules live in "G3 Administración" ----------------------


class LanguageAndLabelTests(HomeHelpers):
    def check(self, response, name):
        html = response.content.decode()
        self.assertEqual(html.count("<h1"), 1, name)
        parsed = analyse(html)
        match = ENGLISH.search(" ".join(parsed.text))
        self.assertIsNone(match, f"{name}: texto en inglés «{match.group(0) if match else ''}»")
        for tag, attrs in parsed.controls:
            if "visually-hidden" in attrs.get("class", ""):
                continue
            self.assertTrue(attrs.get("id") in parsed.labels_for or attrs.get("aria-label"), f"{name}: control sin etiqueta {tag} {attrs.get('name')}")

    def test_the_home_in_every_state(self):
        self.check(self.pages(self.new_event())[0], "inicio sin nada listo")
        self.automatic_cutoff()
        self.check(self.pages()[0], "inicio todo listo")

    def test_the_settings_page_also_after_an_error(self):
        self.login(self.admin)
        self.check(self.client.get(self.url("settings")), "configuración")
        response = self.client.post(self.url("settings"), form_data(self.event, EventSettingsForm, "cfg", "settings", retention_days=1))
        self.check(response, "configuración con error")

    def test_the_new_rules_are_in_the_administration_block(self):
        css = stylesheet()
        g3 = css[css.index("/* ---------- G3 Administración ---------- */"):css.index("/* ---------- G4 Público ---------- */")]
        for selector in (".admin-steps__summary", ".admin-steps__done > summary", ".page-toc ul", ".page-toc a"):
            self.assertIn(selector + " {", g3, selector)
            self.assertEqual(css.count(selector + " {"), 1, selector)
        self.assertEqual(declarations(css, ".page-toc a", 390).get("min-height"), "2.75rem")  # 44 px to tap
