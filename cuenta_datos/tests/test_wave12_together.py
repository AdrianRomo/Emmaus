"""What only exists once the four slices of wave 12 are together (docs/v2/wave12/): «Mis datos en Emmaus» and its
download read the directory's real table, the bookings that repeat and what a person answered when signing up;
«Pendientes» counts a repeating request once and nothing of the directory or of a visitor; the seed runs with every
flag, twice."""

import json
from datetime import time, timedelta
from io import StringIO

from django.apps import apps
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from agenda import headcounts, questions, visitors
from agenda import services as agenda
from agenda.models import EventHeadcount, VisitorRequest
from agenda.simple import create_simple_event
from core.models import Role, UserChurchRole
from cuenta_datos import registry, services
from espacios import repeticion
from espacios.models import Space
from events.models import Event
from formularios import queue
from people import directory, minors
from people import services as people_services
from people.models import DirectoryEntry, Person

from .base import DataFixture, url

BOOKING_TITLE = "ENSAYO-QUE-SE-REPITE"
OTHER_PHONE = "55 9999 0001"


class TogetherFixture(DataFixture):
    """The guide (Gala) is an adult member with a phone; the pastor switched the directory on."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Person.objects.filter(pk=cls.leader.pk).update(phone="55 7777 0002")
        Person.objects.filter(pk=cls.admin_person.pk).update(phone=OTHER_PHONE)
        people_services.set_stage(cls.church, cls.leader.pk, Person.Stage.MEMBER, cls.admin, seen=cls.leader.stage)
        cls.leader.refresh_from_db()
        directory.switch_on(cls.church, cls.pastor_user, accepted=True, wording=directory.WORDING_VERSION)
        cls.room = Space.objects.create(church=cls.church, name="Salón 2", capacity=40)

    def appear(self, person, fields=("phone", "groups")):
        token = directory.preview_token(person, fields, directory.preview(self.church, person, fields))
        return directory.opt_in(self.church, person, person.user, fields=fields, token=token)

    def ctx(self, person, texts=()):
        return registry.Ctx(self.church, person, person.user, frozenset(texts))

    def summary(self, person, key):
        return next(item for item in registry.summaries(self.ctx(person)) if item.category.key == key)

    def category(self, person, key, texts=()):
        title = registry.BY_KEY[key].title
        return next(one["datos"] for one in registry.export(self.ctx(person, texts))["categorías"] if one["categoría"] == title)


class DirectoryInMyDataTests(TogetherFixture):
    def test_the_directory_is_read_from_its_real_table_and_nothing_is_found_by_an_app_label(self):
        self.assertEqual(registry.BY_KEY["directorio"].relations, (("people.DirectoryEntry", "person"),))
        self.assertFalse(hasattr(registry, "DIRECTORY_APPS"))
        self.assertNotIn("directorio", [config.label for config in apps.get_app_configs()])  # it lives in ``people``

    def test_before_choosing_the_page_says_nothing_is_held_and_leads_to_the_choice(self):
        item = self.summary(self.leader, "directorio")
        self.assertEqual((item.count, item.words), (0, []))
        self.assertEqual(item.where, ("Aparecer en el directorio", reverse("directorio:mine")))
        self.assertEqual(self.category(self.leader, "directorio"), {})

    def test_the_page_says_what_the_person_chose_and_since_when_and_never_a_value(self):
        entry = self.appear(self.leader)
        item = self.summary(self.leader, "directorio")
        since = registry.day(timezone.localtime(entry.accepted_at, registry.church_tz(self.church)))
        self.assertEqual(item.words, [f"Elegiste aparecer con tu nombre, tu teléfono y tus grupos, desde el {since}."])
        self.assertEqual(item.count, 1)
        self.client.force_login(self.leader_user)
        page = self.client.get(url("home"))
        self.assertContains(page, item.words[0])
        self.assertContains(page, f'href="{reverse("directorio:mine")}">Aparecer en el directorio</a>', html=False)
        self.assertNotContains(page, "55 7777 0002")
        self.assertNotContains(page, "Lo que elegiste para el directorio: ")  # the sentence stands for the count of one row

    def test_the_download_holds_the_four_choices_of_this_person_only(self):
        self.appear(self.leader, ("phone",))
        rows = self.category(self.leader, "directorio")["Lo que elegiste para el directorio"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            {key: value for key, value in rows[0].items() if key.startswith("elegiste")},
            {"elegiste aparecer con tu nombre": "Sí", "elegiste mostrar tu teléfono": "Sí", "elegiste mostrar tu correo": "No",
             "elegiste mostrar tus grupos": "No", "elegiste mostrar tus equipos": "No"},
        )
        self.assertTrue(rows[0]["lo aceptaste"])
        # Nobody else's download holds it, and the pastor's acceptance for the church is not a row of his own data.
        for other in (self.admin_person, self.pastor_person, self.member):
            self.assertEqual(self.category(other, "directorio"), {}, other.full_name)
        self.assertNotIn("directorio", json.dumps(self.category(self.pastor_person, "cuenta"), ensure_ascii=False).lower())

    def test_somebody_who_left_reads_that_they_do_not_appear_and_keeps_the_way_back_only_while_offered(self):
        self.appear(self.leader)
        directory.opt_out(self.church, self.leader, self.leader_user)
        item = self.summary(self.leader, "directorio")
        self.assertEqual(item.words, ["Elegiste no aparecer. No se muestra nada tuyo."])
        row = self.category(self.leader, "directorio")["Lo que elegiste para el directorio"][0]
        self.assertEqual((row["elegiste aparecer con tu nombre"], row["elegiste mostrar tu teléfono"], row["lo aceptaste"]), ("No", "No", ""))
        directory.switch_off(self.church, self.pastor_user)
        self.assertIsNone(self.summary(self.leader, "directorio").where)  # the page would answer 404: no dead link

    def test_who_is_not_offered_the_directory_gets_no_link_and_the_administration_never_a_count(self):
        self.assertIsNone(self.summary(self.pastor_person, "directorio").where)  # no stage: not offered
        self.appear(self.leader)
        counts = dict((category.key, count) for category, count in registry.staff_counts(self.ctx(self.leader)))
        self.assertIsNone(counts["directorio"])

    def test_one_rule_says_who_is_a_minor(self):
        from formularios import services as forms
        from oracion import access as prayer

        self.assertIs(prayer.known_minor, minors.known_minor)
        Person.objects.filter(pk=self.leader.pk).update(birth_date=timezone.localdate() - timedelta(days=365 * 15))
        self.leader.refresh_from_db()
        self.assertTrue(minors.known_minor(self.leader))
        self.assertTrue(forms.is_known_minor(self.leader))
        self.assertFalse(services.may_download(self.leader))
        self.assertIsNone(self.summary(self.leader, "formularios").where)
        self.assertIsNone(self.summary(self.leader, "directorio").where)


class OtherSlicesInMyDataTests(TogetherFixture):
    def series(self, person=None, **extra):
        day = timezone.localdate() + timedelta(days=2)
        person = person or self.leader
        return repeticion.request_series(self.room, person, person.user, title=BOOKING_TITLE, starts_on=day,
                                         ends_on=day + timedelta(weeks=10), starts_at=time(18), ends_at=time(20), **extra)

    def test_a_booking_that_repeats_is_in_the_download_of_who_asked_without_who_decided_or_why(self):
        series = self.series()
        repeticion.reject_series(series, self.admin, "MOTIVO-DE-LA-ADMINISTRACION")
        rows = self.category(self.leader, "espacios")["Reservas que se repiten que pediste"]
        self.assertEqual([(row["espacio"], row["para qué (lo escribiste tú)"], row["cada cuántas semanas"]) for row in rows],
                         [("Salón 2", BOOKING_TITLE, 1)])
        text = json.dumps(registry.export(self.ctx(self.leader)), ensure_ascii=False)
        self.assertNotIn("MOTIVO-DE-LA-ADMINISTRACION", text)
        self.assertNotIn(self.admin.get_full_name(), text)
        self.assertNotIn(BOOKING_TITLE, json.dumps(registry.export(self.ctx(self.admin_person)), ensure_ascii=False))
        self.assertEqual(dict(self.summary(self.leader, "espacios").parts)["Reservas que se repiten que pediste"], 1)

    def breakfast(self):
        day = timezone.localdate() + timedelta(days=5)
        event = create_simple_event(self.church, self.admin, name="Desayuno de prueba", slug="desayuno-de-prueba", kind=Event.Kind.OTHER,
                                    location="Salón 2", starts_on=day, ends_on=day)
        agenda.save_page(event, self.admin, published=True, public=True, registration="abierta", starts_at=time(9, 30), visitor_signup=True)
        return event

    def test_what_a_person_answered_when_signing_up_is_theirs_and_its_words_wait_for_the_box(self):
        event = self.breakfast()
        asked = questions.save_question(event, self.admin, text="¿Cómo te enteraste?", kind="texto")
        mine = agenda.register(event, self.leader, self.leader_user)
        questions.save_answers(mine, {str(asked.pk): "RESPUESTA-DE-GALA"})
        other = agenda.register(event, self.asked, self.asked_user)
        questions.save_answers(other, {str(asked.pk): "RESPUESTA-DE-PIA"})
        row = self.category(self.leader, "eventos")["Inscripciones a eventos"][0]
        self.assertEqual(row["preguntas que respondiste al inscribirte"], 1)
        self.assertNotIn("lo que respondiste al inscribirte", row)
        with_words = self.category(self.leader, "eventos", {registry.TEXT_FORMS})["Inscripciones a eventos"][0]
        self.assertEqual(with_words["lo que respondiste al inscribirte"], [{"pregunta": "¿Cómo te enteraste?", "respuesta": "RESPUESTA-DE-GALA"}])
        everything = json.dumps(registry.export(self.ctx(self.leader, set(registry.TEXT_KINDS))), ensure_ascii=False)
        self.assertNotIn("RESPUESTA-DE-PIA", everything)
        self.client.force_login(self.leader_user)
        self.assertNotContains(self.client.get(url("home")), "RESPUESTA-DE-GALA")  # the page never copies a text

    def test_a_headcount_and_a_visitors_request_are_in_nobodys_download(self):
        event = self.breakfast()
        Event.objects.filter(pk=event.pk).update(starts_on=timezone.localdate(), ends_on=timezone.localdate())
        event.refresh_from_db()
        headcounts.record(event, self.admin, total=4321, adults=4000, children=321)
        visitors.request(event, first_name="Visita", last_name="SIN-CUENTA-DEMO", email="visita.prueba@demo.invalid")
        self.assertEqual((EventHeadcount.objects.count(), VisitorRequest.objects.count()), (1, 1))
        for person in (self.admin_person, self.leader, self.pastor_person):
            text = json.dumps(registry.export(self.ctx(person, set(registry.TEXT_KINDS))), ensure_ascii=False)
            for word in ("4321", "321", "SIN-CUENTA-DEMO", "visita.prueba"):
                self.assertNotIn(word, text, (person.full_name, word))
        self.assertIn(("agenda.EventHeadcount", "recorded_by"), registry.NOT_THEIRS)
        self.assertIn(("serving.PlanTemplate", "created_by"), registry.NOT_THEIRS)


class PendientesTogetherTests(TogetherFixture):
    def everything(self, user, person, *, admin=False):
        """Every row of «Pendientes» for this account, the badge's count held to them, without the request to join
        a group the fixture leaves for its guide."""
        rows = queue.rows(user, self.church, person, is_church_admin=admin)
        self.assertEqual(queue.count(user, self.church, person, is_church_admin=admin), sum(row.count for row in rows), user.email)
        return [row for row in rows if row.kind != "Grupos"]

    def test_a_repeating_request_and_a_removal_request_wait_for_the_administration_and_nothing_else_of_the_wave_does(self):
        day = timezone.localdate() + timedelta(days=2)
        self.assertEqual(self.everything(self.admin, self.admin_person, admin=True), [])
        # None of these is a task of the staff: a directory switched on, somebody in it, a visitor who has not confirmed.
        token = directory.preview_token(self.leader, ("phone",), directory.preview(self.church, self.leader, ("phone",)))
        directory.opt_in(self.church, self.leader, self.leader_user, fields=("phone",), token=token)
        event = create_simple_event(self.church, self.admin, name="Desayuno de prueba", slug="desayuno-de-prueba", kind=Event.Kind.OTHER,
                                    location="Salón 2", starts_on=day, ends_on=day)
        agenda.save_page(event, self.admin, published=True, public=True, registration="abierta", starts_at=time(9, 30), visitor_signup=True)
        visitors.request(event, first_name="Visita", last_name="Demo", email="visita.prueba@demo.invalid")
        self.assertEqual((DirectoryEntry.objects.count(), VisitorRequest.objects.count()), (1, 1))
        for user, person, admin in ((self.admin, self.admin_person, True), (self.pastor_user, self.pastor_person, False),
                                    (self.leader_user, self.leader, False)):
            self.assertEqual(self.everything(user, person, admin=admin), [], user.email)

        # One series that waits is ONE decision, however many dates it would write; with it, a request about data.
        repeticion.request_series(self.room, self.leader, self.leader_user, title=BOOKING_TITLE, starts_on=day,
                                  ends_on=day + timedelta(weeks=10), starts_at=time(18), ends_at=time(20))
        services.ask_removal(self.church, self.leader, self.leader_user, "todo")
        rows = self.everything(self.admin, self.admin_person, admin=True)
        self.assertEqual(
            sorted((row.kind, row.title, row.url, row.count) for row in rows),
            [("Datos personales", "1 solicitud sobre datos personales", url("requests"), 1),
             ("Espacios", "1 solicitud de reserva por decidir", reverse("espacios:queue"), 1)],
        )
        for row in rows:
            for word in (BOOKING_TITLE, "Gala", "Salón 2"):
                self.assertNotIn(word, row.title)
        for user, person in ((self.pastor_user, self.pastor_person), (self.leader_user, self.leader), (self.member_user, self.member)):
            self.assertEqual(self.everything(user, person), [], user.email)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("formularios:queue"))
        self.assertContains(page, "1 solicitud de reserva por decidir")
        self.assertNotContains(page, BOOKING_TITLE)


class PowersTogetherTests(TogetherFixture):
    def test_a_church_without_a_pastor_reads_that_the_directory_and_the_prayer_switch_are_blocked(self):
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(url("powers")), "no tiene ninguna cuenta con rol de pastor")
        UserChurchRole.objects.filter(church=self.church, role=Role.PASTOR).delete()
        page = self.client.get(url("powers"))
        self.assertContains(page, "no tiene ninguna cuenta con rol de pastor")
        self.assertContains(page, "nadie puede encender ni apagar las peticiones de oración ni el directorio entre miembros")
        # And it is true: the administration cannot move the switch (the page that does answers 404 to it).
        self.assertEqual(self.client.get(reverse("directorio:policy")).status_code, 404)
        self.assertFalse(directory.is_pastor(self.admin, self.church))


class WhoSeesMyContactTests(TogetherFixture):
    def test_mis_datos_names_everybody_who_is_shown_a_phone_or_an_e_mail(self):
        """The guide of the group (groups/group_people.html), who coordinates the team (serving/team.html), the owner of
        a follow-up (care/_row.html, care/detail.html) and the administration (Personas): the sentence names the four."""
        self.client.force_login(self.leader_user)
        page = self.client.get(reverse("community:profile"))
        self.assertContains(page, "solo los ven quien guía tu grupo, quien coordina un equipo en el que sirves, la persona a quien tu "
                                  "iglesia le encargó buscarte y la administración de tu iglesia.")
        self.assertNotContains(page, "quienes ven el directorio")
        token = directory.preview_token(self.leader, ("phone",), directory.preview(self.church, self.leader, ("phone",)))
        directory.opt_in(self.church, self.leader, self.leader_user, fields=("phone",), token=token)
        self.assertContains(self.client.get(reverse("community:profile")), "quienes ven el directorio de tu iglesia")


ALL_FLAGS = ("--panorama", "--eventos", "--duplicados", "--formularios", "--rotas", "--oracion", "--dos-iglesias", "--directorio",
             "--repeticion", "--visitas")


class SeedWithEveryFlagTests(DataFixture):
    def rows(self) -> dict:
        skip = {"people.ChurchAuditEvent", "sessions.Session", "admin.LogEntry"}
        return {model._meta.label: model._base_manager.count() for model in apps.get_models() if model._meta.label not in skip}

    def test_every_flag_together_runs_twice_and_the_second_run_adds_nothing(self):
        with self.settings(DEBUG=True):
            call_command("seed_community_demo", *ALL_FLAGS, password="Clave-de-prueba-1", stdout=StringIO())
            first = self.rows()
            call_command("seed_community_demo", *ALL_FLAGS, password="Clave-de-prueba-1", stdout=StringIO())
        second = self.rows()
        self.assertEqual({label: (first[label], count) for label, count in second.items() if first[label] != count}, {})
        for label in ("people.DirectoryEntry", "espacios.BookingSeries", "agenda.VisitorRequest", "agenda.EventHeadcount",
                      "serving.PlanTemplate", "oracion.PrayerRequest", "formularios.Form"):
            self.assertGreater(second[label], 0, label)
        out = StringIO()
        call_command("tenant_integrity", stdout=out)
        self.assertIn("Resultado: sin discrepancias.", out.getvalue())
