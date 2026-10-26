"""Administración B (ronda "cada rol sabe qué hacer", 2026-10-26): alta, equipo, importar, editor, listas.

The short name for the address forms itself from the name (UX-12, LANG-17); the person added to the team gets a
message ready to send (UX-24, A11Y-12); a pending import says so and still takes another file (UX-22); the
questionnaire editor folds its identifiers under "Datos técnicos" (UI-19, LANG-05, LANG-56); lists say "ya no
participa", "Computadora: Ninguna" and keep at most three data on a row's line (UI-09, LANG-42, LANG-43).
"""

import io
import re
from datetime import timedelta

from django.conf import settings
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.forms import ChurchSettingsForm, InitialSetupForm
from core.models import Church
from events.forms import EventForm, QuestionForm
from events.models import AuditEvent, Event, EventParticipant, Interview, InterviewStation
from events.services.questionnaire import duplicate_version

from .base import CampFixture
from .test_admin_screens import IMPORT_CSV
from .test_camp_visual import declarations, stylesheet

CONSOLE = "django.core.mail.backends.console.EmailBackend"
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
SETUP_PASSWORD = "StrongPass12345!"


def input_tag(html, element_id):
    match = re.search(rf'<input[^>]*\bid="{re.escape(element_id)}"[^>]*>', html)
    assert match, element_id
    return match.group(0)


def event_data(**changes):
    data = {"name": "Retiro de Jóvenes", "kind": "campamento", "slug": "", "status": "activo", "location": "", "starts_on": "", "ends_on": ""}
    data.update(changes)
    return data


# ---- 1. The short name for the address forms itself ----------------------------------------------------


class EventSlugTests(CampFixture):
    def test_a_blank_slug_is_formed_from_the_name(self):
        form = EventForm(data=event_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["slug"], "retiro-de-jovenes")

    def test_a_formed_slug_that_is_taken_gets_the_next_number(self):
        form = EventForm(data=event_data(name="Campamento A"))  # campamento-a exists
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["slug"], "campamento-a-2")
        Event.objects.create(church=self.church, name="Otro", slug="campamento-a-2")
        form = EventForm(data=event_data(name="Campamento A"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["slug"], "campamento-a-3")

    def test_a_typed_slug_that_is_taken_keeps_its_error(self):
        form = EventForm(data=event_data(slug=self.event.slug))
        self.assertEqual(form.errors["slug"], ["Ya existe un evento con ese nombre corto. Prueba con otro."])

    def test_a_typed_slug_without_letters_or_numbers_is_refused(self):
        form = EventForm(data=event_data(slug="---"))
        self.assertEqual(form.errors["slug"], ["Usa letras, números o guiones."])

    def test_a_name_without_letters_or_numbers_asks_for_the_slug(self):
        form = EventForm(data=event_data(name="¡¡!!"))
        self.assertEqual(form.errors["slug"], ["Escribe un nombre corto con letras, números o guiones."])

    def test_a_missing_name_carries_the_only_error(self):
        form = EventForm(data=event_data(name=""))
        self.assertEqual(form.errors["name"], ["Escribe el nombre del evento."])
        self.assertNotIn("slug", form.errors)

    def test_a_long_name_is_cut_to_the_column_without_a_dash_at_the_end(self):
        name = "Campamento " + "de " * 30 + "verano"
        form = EventForm(data=event_data(name=name[:160]))
        self.assertTrue(form.is_valid(), form.errors)
        slug = form.cleaned_data["slug"]
        self.assertLessEqual(len(slug), 80)
        self.assertFalse(slug.endswith("-"), slug)

    def test_the_slug_is_optional_and_says_where_it_comes_from(self):
        field = EventForm().fields["slug"]
        self.assertFalse(field.required)
        self.assertEqual(field.label, "Nombre corto para la dirección")
        self.assertEqual(field.help_text, "Se forma con el nombre; puedes cambiarlo. Solo minúsculas, números y guiones.")

    def test_the_source_is_the_bound_name_field_whatever_the_prefix(self):
        self.assertEqual(EventForm().fields["slug"].widget.attrs["data-slug-from"], "id_name")
        basics = EventForm(instance=self.event, prefix="basics")
        self.assertEqual(basics.fields["slug"].widget.attrs["data-slug-from"], "id_basics-name")

    def test_settings_with_the_slug_emptied_form_it_again_without_clashing_with_itself(self):
        data = {f"basics-{k}": v for k, v in event_data(name="Campamento A").items()}
        form = EventForm(data=data, instance=self.event, prefix="basics")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["slug"], "campamento-a")

    def test_the_create_page_ties_the_slug_to_the_name(self):
        html = self.login(self.admin).get(reverse("events:create")).content.decode()
        self.assertIn('data-slug-from="id_name"', input_tag(html, "id_slug"))
        self.assertIn("Se forma con el nombre; puedes cambiarlo.", html)

    def test_creating_with_a_blank_slug_creates_the_event_and_its_computers(self):
        response = self.login(self.admin).post(reverse("events:create"), event_data())
        event = Event.objects.get(slug="retiro-de-jovenes")
        self.assertRedirects(response, reverse("events:admin_home", args=[event.slug]), fetch_redirect_response=False)
        self.assertEqual(event.church, self.church)
        self.assertEqual(InterviewStation.objects.filter(event=event).count(), 10)
        self.assertTrue(AuditEvent.objects.filter(event=event, action="evento_creado").exists())

    def test_creating_with_a_taken_typed_slug_creates_nothing(self):
        response = self.login(self.admin).post(reverse("events:create"), event_data(slug=self.event.slug))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ya existe un evento con ese nombre corto. Prueba con otro.")
        self.assertFalse(Event.objects.filter(name="Retiro de Jóvenes").exists())

    # Refuter round 1 --------------------------------------------------------------------------------------

    def test_settings_with_name_and_slug_blank_says_the_name_and_keeps_the_address(self):
        """A blank slug next to a failed name went into the unsaved event as "": every link of the page raised
        NoReverseMatch (500). The saved address stays; the name's error is the one shown."""
        data = {"form": "basics", **{f"basics-{k}": v for k, v in event_data(name="").items()}}
        response = self.login(self.admin).post(self.url("settings"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Escribe el nombre del evento.")
        self.assertEqual(Event.objects.get(pk=self.event.pk).slug, "campamento-a")
        form = EventForm(data={f"basics-{k}": v for k, v in event_data(name="").items()}, instance=self.event, prefix="basics")
        self.assertFalse(form.is_valid())
        self.assertEqual(form.cleaned_data["slug"], "campamento-a")

    def test_creating_with_name_and_slug_blank_says_the_name(self):
        response = self.login(self.admin).post(reverse("events:create"), event_data(name=""))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Escribe el nombre del evento.")
        self.assertEqual(Event.objects.count(), 2)

    def test_addresses_the_site_already_uses_count_as_taken(self):
        """/eventos/nuevo/ is "Crear campamento" and /eventos/cuenta/… the session ping: both are routed before an
        event's address, so an event called "Nuevo" would open the create form."""
        for name in ("Nuevo", "Cuenta"):
            form = EventForm(data=event_data(name=name))
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data["slug"], f"{name.lower()}-2")
        for slug in ("nuevo", "cuenta"):
            form = EventForm(data=event_data(slug=slug))
            self.assertEqual(form.errors["slug"], ["Ya existe un evento con ese nombre corto. Prueba con otro."])

    def test_the_slug_the_script_formed_from_the_name_is_still_formed(self):
        """With JavaScript the mirrored slug arrives filled in: it is the name's, so a clash gets the next number."""
        form = EventForm(data=event_data(name="Campamento A", slug="campamento-a"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["slug"], "campamento-a-2")
        response = self.login(self.admin).post(reverse("events:create"), event_data(name="Campamento A", slug="campamento-a"))
        self.assertRedirects(response, reverse("events:admin_home", args=["campamento-a-2"]), fetch_redirect_response=False)

    def test_a_slug_that_is_not_the_names_is_typed_and_keeps_its_error(self):
        Event.objects.create(church=self.church, name="Otro", slug="otro-nombre")
        form = EventForm(data=event_data(name="Campamento A", slug="otro-nombre"))
        self.assertEqual(form.errors["slug"], ["Ya existe un evento con ese nombre corto. Prueba con otro."])


class FirstSetupSlugTests(TestCase):
    def data(self, **changes):
        data = {
            "church_name": "Iglesia de la Gracia", "church_slug": "", "admin_name": "Ana Administra",
            "admin_email": "ana@gracia.mx", "admin_password": SETUP_PASSWORD,
        }
        data.update(changes)
        return data

    def test_a_blank_slug_is_formed_from_the_church_name(self):
        form = InitialSetupForm(data=self.data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["church_slug"], "iglesia-de-la-gracia")

    def test_a_formed_slug_that_is_taken_gets_the_next_number(self):
        Church.objects.create(name="Otra", slug="iglesia-de-la-gracia")
        form = InitialSetupForm(data=self.data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["church_slug"], "iglesia-de-la-gracia-2")

    def test_a_typed_slug_that_is_taken_says_nombre_corto(self):
        Church.objects.create(name="Otra", slug="gracia")
        form = InitialSetupForm(data=self.data(church_slug="gracia"))
        self.assertEqual(form.errors["church_slug"], ["Ese nombre corto ya está en uso. Elige otro."])

    def test_a_formed_slug_fits_the_column(self):
        """Church.slug holds 50 characters (a SlugField without max_length): a longer one fails on PostgreSQL."""
        limit = Church._meta.get_field("slug").max_length
        form = InitialSetupForm(data=self.data(church_name="Iglesia Bautista Independiente Monte de Sion de Ciudad Satélite"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertLessEqual(len(form.cleaned_data["church_slug"]), limit)
        self.assertFalse(form.cleaned_data["church_slug"].endswith("-"))
        self.assertEqual(form.fields["church_slug"].max_length, limit)

    def test_the_field_is_optional_and_keeps_its_label(self):
        field = InitialSetupForm().fields["church_slug"]
        self.assertFalse(field.required)
        self.assertEqual(field.label, "Nombre corto de la iglesia para la dirección")
        self.assertEqual(field.help_text, "Se forma con el nombre; puedes cambiarlo.")

    def test_the_setup_page_ties_the_slug_to_the_name(self):
        html = self.client.get(reverse("initial_setup")).content.decode()
        self.assertIn('data-slug-from="id_church_name"', input_tag(html, "id_church_slug"))
        self.assertIn("<li>Escribe el nombre de tu iglesia; el nombre corto se forma solo.</li>", html)
        self.assertNotIn("identificador corto", html)

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_setting_up_with_a_blank_slug_creates_the_church(self):
        response = self.client.post(reverse("initial_setup"), self.data())
        self.assertRedirects(response, reverse("events:list"), fetch_redirect_response=False)
        self.assertEqual(Church.objects.get().slug, "iglesia-de-la-gracia")


# ---- 2. A message ready to send ------------------------------------------------------------------------


class MemberCreatedMessageTests(CampFixture):
    def add(self, role="coordinador"):
        self.login(self.admin)
        return self.client.post(self.url("member_add"), {"full_name": "Nadia Nueva", "email": "nadia@persona.mx", "role": role, "team_name": ""})

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_the_message_has_the_link_and_the_role(self):
        response = self.add()
        link = response.context["link"]
        html = response.content.decode()
        textarea = re.search(r'<textarea class="input" id="mensaje" rows="6" readonly>(.*?)</textarea>', html, re.S)
        self.assertIsNotNone(textarea)
        self.assertEqual(
            textarea.group(1),
            "Hola, Nadia: te agregaron al equipo de Campamento A en Emmaus, como coordinador. Crea tu contraseña con "
            f"este enlace (vence en unos días):\n{link}\nSi tienes dudas, avísame.",
        )
        self.assertContains(response, '<label class="label" for="mensaje">Mensaje listo para enviar</label>', html=True)
        self.assertContains(response, '<button class="btn btn--secondary" type="button" data-copy="mensaje" data-copy-done="Mensaje copiado">Copiar mensaje</button>', html=True)
        self.assertContains(response, f'value="{link}"')  # the link alone stays
        self.assertContains(response, 'data-copy="enlace"')
        self.assertEqual((response.context["first_name"], response.context["role_label"]), ("Nadia", "Coordinador"))

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_without_email_the_page_never_says_one_was_sent(self):
        response = self.add(role="tutor")
        self.assertFalse(response.context["email_sent"])
        self.assertNotContains(response, "Le enviamos un correo")
        self.assertNotContains(response, "También le llegó un correo.")
        self.assertContains(response, "como tutor.")

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_a_sent_email_is_said_once_on_the_page(self):
        response = self.add()
        self.assertTrue(response.context["email_sent"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertContains(response, "También le llegó un correo.", count=1)

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_the_link_on_the_person_page_says_it_was_copied(self):
        self.add()
        membership = self.event.memberships.get(user__email="nadia@persona.mx")
        response = self.client.post(self.url("member_edit", membership.id), {"action": "link"})
        self.assertContains(response, 'data-copy="enlace" data-copy-done="Enlace copiado"')


# ---- 3. Import: a file under review, and another one -----------------------------------------------------


class ImportPendingTests(CampFixture):
    def upload(self, text, name="camperos.csv"):
        upload = io.BytesIO(text.encode("utf-8"))
        upload.name = name
        return self.client.post(self.url("participant_import"), {"file": upload})

    def setUp(self):
        self.login(self.admin)
        self.upload(IMPORT_CSV)

    def test_the_review_says_a_file_is_pending_and_offers_another(self):
        response = self.client.get(self.url("participant_import"))
        html = response.content.decode()
        self.assertContains(response, '<h2 id="otro-archivo" class="heading">¿Otro archivo?</h2>', html=True)
        self.assertContains(response, "Tienes un archivo por revisar: camperos.csv. Si subes otro, reemplaza a este.")
        section = re.search(r'<section class="admin-upload" aria-labelledby="otro-archivo">(.*?)</section>', html, re.S).group(1)
        self.assertIn('<form method="post" enctype="multipart/form-data" novalidate>', section)
        self.assertIn('type="file"', input_tag(section, "id_file"))
        self.assertIn('<label class="label" for="id_file">', section)
        self.assertIn('<button class="btn btn--secondary" type="submit">Revisar otro archivo</button>', section)
        self.assertNotIn('name="action"', section)
        # The pinned counts of the review (test_admin_screens) do not move.
        self.assertEqual(html.count('name="include"'), 3)
        self.assertEqual(html.count(" checked>"), 1)
        self.assertEqual(html.count('name="action" value="confirm"'), 1)
        self.assertEqual(html.count("btn--primary"), 1)

    def test_a_wrong_second_file_says_why_and_keeps_the_pending_one(self):
        response = self.upload("cualquier cosa", name="lista.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El archivo debe ser .csv.")
        self.assertContains(response, 'href="#id_file"')
        self.assertContains(response, "<title>Error: ", html=False)
        self.assertContains(response, "Tienes un archivo por revisar: camperos.csv.")

    def test_a_second_file_with_problems_shows_them_with_the_pending_one(self):
        response = self.upload("Columna,Otra\n1,2\n", name="otro.csv")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hay un problema con el archivo")
        self.assertContains(response, "Faltan estas columnas:")
        self.assertContains(response, "Tienes un archivo por revisar: camperos.csv.")

    def test_a_good_second_file_replaces_the_pending_one(self):
        self.upload("Nombre,Edad,Cuarto,Iglesia o grupo\nNoemí Nueva Sala,16,3,Iglesia Centro\n", name="otros.csv")
        response = self.client.get(self.url("participant_import"))
        self.assertContains(response, "Tienes un archivo por revisar: otros.csv.")
        self.assertEqual(response.content.decode().count('name="include"'), 1)

    def test_without_a_pending_file_the_page_is_the_plain_upload(self):
        self.client.post(self.url("participant_import"), {"action": "cancel"})
        response = self.client.get(self.url("participant_import"))
        self.assertNotContains(response, "Tienes un archivo por revisar")
        self.assertContains(response, "Revisar archivo")


# ---- 4. Questionnaire editor: "Datos técnicos" -------------------------------------------------------------

FOLD = re.compile(r'<details class="fold fold--inline">\s*<summary>Datos técnicos</summary>\s*<div class="fold__body">(.*?)</div>\s*</details>', re.S)


class EditorTechnicalDataTests(CampFixture):
    def test_one_fold_per_question_and_codes_only_inside(self):
        html = self.login(self.admin).get(self.url("version_detail", self.version.id)).content.decode()
        folds = FOLD.findall(html)
        self.assertEqual(len(folds), sum(len(step.questions) for step in self.steps))
        self.assertTrue(any("<code>salvo</code>" in fold and "Sí, <code>si</code>" in fold for fold in folds))
        self.assertNotIn("<code>", re.sub(r"<details.*?</details>", "", html, flags=re.S))
        self.assertNotIn("Identificador interno (para quien edita el cuestionario)", html)
        self.assertNotIn('class="details"', html)

    def test_the_question_form_folds_its_identifiers_and_names_the_fold(self):
        draft = duplicate_version(self.version, self.admin)
        question = draft.sections.first().questions.first()
        response = self.login(self.admin).get(self.url("question_edit", draft.id, question.id))
        html = response.content.decode()
        folds = FOLD.findall(html)
        self.assertEqual(len(folds), 1)
        self.assertIn("<dt>¿Tienes dudas acerca de tu fe?</dt><dd>Sí, <code>si</code>; No, <code>no</code>;", folds[0])
        self.assertNotIn("<code>", re.sub(r"<details.*?</details>", "", html, flags=re.S))
        self.assertContains(response, "Están en “Datos técnicos”, en esta página y debajo de cada pregunta del cuestionario.")

    def test_the_type_of_answer_starts_with_a_sentence_not_dashes(self):
        draft = duplicate_version(self.version, self.admin)
        question = draft.sections.first().questions.first()
        self.login(self.admin)
        edit = self.client.get(self.url("question_edit", draft.id, question.id))
        self.assertContains(edit, '<option value="">Elige un tipo de respuesta</option>', html=False)
        self.assertNotContains(edit, "---------")
        new = self.client.get(self.url("question_add", draft.id, draft.sections.first().id))
        self.assertContains(new, "Elige un tipo de respuesta")
        self.assertNotContains(new, "---------")

    def test_a_missing_type_of_answer_says_what_to_do_in_tu(self):
        draft = duplicate_version(self.version, self.admin)
        form = QuestionForm(data={"text": "¿Algo más?", "help_text": "", "kind": "", "sensitivity": "general", "order": 1}, version=draft)
        self.assertEqual(form.errors["kind"], ["Elige un tipo de respuesta."])

    def test_the_help_names_the_fold(self):
        help_text = QuestionForm(version=self.version).fields["condition_codes"].help_text
        self.assertEqual(
            help_text,
            "Los identificadores internos de esas respuestas, separados por comas. Están en “Datos técnicos”, en esta "
            "página y debajo de cada pregunta del cuestionario. Por ejemplo: si",
        )

    def test_the_inline_fold_has_no_rule_above_and_a_short_gap(self):
        css = stylesheet()
        summary = declarations(css, ".fold--inline > summary")
        self.assertEqual((summary.get("border-top"), summary.get("font-size")), ("0", "var(--t-small)"))
        self.assertEqual(declarations(css, ".fold--inline > * + *").get("margin-top"), "var(--s-2)")
        self.assertTrue(declarations(css, ".details summary"))  # pinned by TouchTargetTests: kept


# ---- 5. Lists and words ----------------------------------------------------------------------------------


class ListWordingTests(CampFixture):
    def test_a_phone_row_has_two_short_lines_and_the_status_at_the_end(self):
        self.login(self.admin)
        html = self.client.get(self.url("participants")).content.decode()
        rows = re.search(r'<ul class="rows admin-people__rows">(.*?)</ul>', html, re.S).group(1)
        p = self.p1
        row = re.search(rf'<li class="row">((?:(?!</li>).)*?>{p.full_name}</a>.*?)</li>', rows, re.S).group(1)
        metas = re.findall(r'<p class="row__meta">(.*?)</p>', row)
        self.assertEqual(metas, [f"Cuarto {p.room}, {p.age} años", f"#{p.code}, {p.church_group}, con {p.coordinator.display_name}"])
        self.assertTrue(all(len(meta.split(", ")) <= 3 for meta in metas), metas)
        self.assertRegex(row, r'<div class="row__end">\s*<span class="status status--pending"><span class="status__mark" aria-hidden="true"></span>Sin entrevista</span>\s*</div>')
        self.assertEqual(rows.count('<li class="row">'), html.count('<tr><td class="num">'))

    def test_someone_who_left_says_ya_no_participa(self):
        EventParticipant.objects.filter(pk=self.p2.pk).update(is_active=False)
        self.login(self.admin)
        response = self.client.get(self.url("participants") + "?participa=no")
        html = response.content.decode()
        self.assertIn('<option value="no" selected>Ya no participan</option>', html)
        self.assertIn(f"<p class=\"row__meta\">#{self.p2.code}, Iglesia Centro, con {self.p2.coordinator.display_name}, ya no participa</p>", html)
        self.assertIn('<span class="table__note">Ya no participa</span>', html)
        self.assertNotIn("ado de baja", html)
        detail = self.client.get(self.url("participant_detail", self.p2.public_id)).content.decode()
        self.assertIn(f"#{self.p2.code}, ya no participa", detail)
        self.assertNotIn("dado de baja", detail)

    def test_the_filters_keep_their_button_on_their_row_from_900px(self):
        css = stylesheet()
        self.login(self.admin)
        html = self.client.get(self.url("participants")).content.decode()
        self.assertIn('class="filter-bar admin-people__filters"', html)
        self.assertIn('<section class="admin-people" aria-labelledby="resultados">', html)
        self.assertEqual(declarations(css, ".filter-bar.admin-people__filters", 900).get("flex-wrap"), "nowrap")
        self.assertNotIn("flex-wrap", declarations(css, ".filter-bar.admin-people__filters", 899))
        self.assertEqual(declarations(css, ".admin-people .pager").get("max-width"), "none")

    def test_sessions_name_the_computer_or_none(self):
        coordinator = self.login(self.coord1, Client())
        self.choose_station(coordinator)
        self.start(self.p2, client=coordinator)
        self.login(self.admin)
        live = self.client.get(self.url("sessions")).content.decode()
        claims = re.search(r'aria-labelledby="t-claims">(.*?)</section>', live, re.S).group(1)
        self.assertIn("<th>Computadora</th>", claims)
        self.assertNotIn("Abierta en", claims)
        self.assertIn("<td>Computadora 1</td>", claims)
        Interview.objects.filter(participant=self.p2).update(lock_expires_at=timezone.now() - timedelta(seconds=1))
        expired = self.client.get(self.url("sessions"))
        self.assertContains(expired, '<span class="muted">Ninguna</span>')
        self.assertNotContains(expired, "Libre")

    def test_church_colours_say_what_they_paint(self):
        fields = ChurchSettingsForm().fields
        labels = {name: fields[name].label for name in fields if name.startswith("theme_")}
        self.assertEqual(labels, {
            "theme_primary_color": "Color principal",
            "theme_accent_color": "Color de “estás aquí”",
            "theme_secondary_color": "Color secundario",
            "theme_background_color": "Color del fondo",
            "theme_surface_color": "Color de campos y paneles",
            "theme_text_color": "Color del texto",
            "theme_muted_text_color": "Color del texto tenue",
            "theme_border_color": "Color de los bordes",
        })
        self.assertTrue(fields["theme_primary_color"].help_text.startswith("Botones, enlaces y cifras. "))
        self.assertTrue(fields["theme_accent_color"].help_text.startswith("El punto de la computadora y el subrayado de la página actual. "))
        self.assertEqual(fields["theme_secondary_color"].help_text, "Por ahora no se usa en pantalla.")
        page = self.login(self.admin).get(reverse("church_settings"))
        self.assertContains(page, "Necesita un contraste de al menos 4.5 a 1 sobre blanco.")
        self.assertContains(page, "Necesita un contraste de al menos 3 a 1 sobre blanco.")
        self.assertContains(page, 'type="color"', count=8)
        for retired in (">Superficies", ">Color de acento", ">Fondo", ">Bordes"):
            self.assertNotContains(page, retired)
        self.assertContains(
            page,
            "El color principal pinta botones, enlaces y cifras; el de “estás aquí”, el punto de la computadora y la página "
            "actual. Al guardar, Emmaus revisa que los dos se lean sobre blanco, y que el principal y los textos se lean "
            "sobre el fondo y sobre los campos y paneles. Los bordes revísalos tú.",
        )
        self.assertNotContains(page, "el de acento")


# ---- 6. The two behaviours in emmaus.js ----------------------------------------------------------------


class ScriptTests(TestCase):
    def test_the_script_forms_the_slug_and_announces_the_copy(self):
        script = (settings.BASE_DIR / "static" / "js" / "emmaus.js").read_text()
        self.assertIn("input[data-slug-from]", script)
        self.assertIn('normalize("NFD")', script)
        self.assertIn("data-copy-status", script)
        self.assertIn("data-copy-done", script)
        self.assertIn("No se pudo copiar. Selecciona el texto y cópialo.", script)
        self.assertIn("3000", script)
        # Still one listener per copy button: the old block was changed, not doubled.
        self.assertEqual(script.count('$all("[data-copy]")'), 1)
