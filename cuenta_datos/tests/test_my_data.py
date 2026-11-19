"""«Mis datos en Emmaus» and its download: only the person's own rows, only the church of the request,
never another person's name or contact, never what the staff wrote, behind the password, twice a day."""

import json
from datetime import timedelta

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from agenda.models import EventRegistration
from care.models import FollowUp
from core.models import PastoralNote
from cuenta_datos import registry, services
from espacios.models import Booking, Space
from events.models import Event
from groups import services as groups
from groups.models import GroupAttendance, GroupMeeting, GroupMembership
from notices.models import Announcement, AnnouncementRead
from oracion.models import PrayerRequest
from people import flags
from people.models import ChurchAuditEvent, Person, PersonTag, Tag
from resources.models import ReadingPlan, ReadingPlanDay, ReadingPlanDayRead, ReadingPlanProgress
from serving import services as serving
from serving.models import TeamMembership

from .base import PASSWORD, DataFixture, url

MY_PRAYER = "MI-PETICION-PRIVADA sobre mi familia"
# Strings that are somebody else's, or the staff's: none may ever be in the member's download or on their page.
FOREIGN = [
    "Gala", "guia@prueba.mx", "55 9999 0001",  # who guides the member's group and coordinates their team
    "Pía", "pidio@prueba.mx", "RESPUESTA-DE-PIA", "PETICION-DE-PIA",  # another member, her answer and her prayer
    "Pablo", "pastor-sin-admin@prueba.mx", "RESPUESTA-DEL-PASTOR",  # the pastor and what he answered
    "ETIQUETA-INTERNA", "NOTA-PASTORAL-INTERNA", "RAZON-DE-LA-ADMINISTRACION",  # written by the staff
    "Hace tiempo que no viene", "ausencia",  # the reason of a follow-up about the member
    "Ya no asiste", "ya_no_asiste",  # the stage the administration gave them
    "Bárbara", "Campera",  # a campero of another church
    "tutor", "Tutora Externa",  # nothing of a guardian
]


class RichFixture(DataFixture):
    """Church A where the member (Diego) has a row of every kind, beside rows of other people and of the staff."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        now, today = timezone.now(), timezone.localdate()
        member, church = cls.member, cls.church
        Person.objects.filter(pk=cls.leader.pk).update(phone="55 9999 0001")
        Person.objects.filter(pk=member.pk).update(stage="ya_no_asiste")
        member.refresh_from_db()

        groups.set_membership(cls.group, member, cls.admin)
        meeting = GroupMeeting.objects.create(group=cls.group, date=today, recorded_by=cls.leader_user)
        GroupAttendance.objects.create(meeting=meeting, person=member)
        GroupAttendance.objects.create(meeting=meeting, person=cls.leader)

        cls.team = serving.save_team(church, cls.admin, name="Equipo de Alabanza")
        serving.set_member(cls.team, cls.leader, cls.admin, role=TeamMembership.Role.COORDINATOR)
        serving.set_member(cls.team, member, cls.admin)
        cls.service_date = serving.save_date(cls.team, cls.admin, date=today + timedelta(days=7), what="Reunión general")
        serving.assign(cls.service_date, member, cls.admin)
        serving.assign(cls.service_date, cls.leader, cls.admin)

        cls.simple = Event.objects.create(church=church, name="Convivio de otoño", slug="convivio-otono", camp_tools=False,
                                          starts_on=today + timedelta(days=3), ends_on=today + timedelta(days=3))
        EventRegistration.objects.create(event=cls.simple, person=member)
        EventRegistration.objects.create(event=cls.simple, person=cls.asked)
        EventRegistration.objects.create(event=cls.simple, guest_first_name="Tutora Externa", guest_last_name="Visita")

        notice = Announcement.objects.create(church=church, title="Cambio de horario", body="El domingo empezamos 11:00.", created_by=cls.admin)
        AnnouncementRead.objects.create(announcement=notice, person=member, read_at=now)

        plan = ReadingPlan.objects.create(church=church, title="Plan de Juan", created_by=cls.admin)
        first = ReadingPlanDay.objects.create(plan=plan, position=1, reference="Juan 1")
        progress = ReadingPlanProgress.objects.create(plan=plan, person=member)
        ReadingPlanDayRead.objects.create(progress=progress, day=first)

        cls.prayer = PrayerRequest.objects.create(
            church=church, person=member, author=cls.member_user, body=MY_PRAYER, expires_at=now + timedelta(days=30),
            reply="RESPUESTA-DEL-PASTOR: oramos por ti", replied_at=now, read_at=now, restricted=True, only_reader=cls.pastor_user,
        )
        PrayerRequest.objects.create(church=church, person=cls.asked, author=cls.asked_user, body="PETICION-DE-PIA",
                                     expires_at=now + timedelta(days=30))

        build = cls("setUp")  # the forms fixture's builders are instance methods that read class attributes
        cls.form = build.member_form()
        build.answer(cls.form)  # the member's: "No puedo los jueves"
        build.answer(cls.form, data=build.member_data(cls.form, **{cls.keys(cls.form)[2]: "RESPUESTA-DE-PIA"}), person=cls.asked)

        space = Space.objects.create(church=church, name="Salón Azul", created_by=cls.admin)
        Booking.objects.create(space=space, title="Ensayo de mi grupo", starts_at=now, ends_at=now + timedelta(hours=1),
                               blocked_from=now, blocked_until=now + timedelta(hours=1), requested_by=member,
                               status=Booking.Status.REJECTED, decided_by=cls.admin, decision_reason="RAZON-DE-LA-ADMINISTRACION")

        tag = Tag.objects.create(church=church, name="ETIQUETA-INTERNA", normalized_name="etiqueta-interna", created_by=cls.admin)
        PersonTag.objects.create(person=member, tag=tag, added_by=cls.admin)
        FollowUp.objects.create(church=church, person=member, reason="ausencia", owner=cls.leader, created_by=cls.admin)
        PastoralNote.objects.create(church=church, participant=cls.member_user, author=cls.pastor_user, body="NOTA-PASTORAL-INTERNA")

        # The same account is also a person of church B, in a group there.
        cls.member_in_b = Person.objects.create(church=cls.church_b, user=cls.member_user, first_name="Diego", last_name="Hernández Cruz",
                                                phone="55 0000 2222")
        groups.set_membership(cls.group_b, cls.member_in_b, cls.admin_b)

    def setUp(self):
        self.client.force_login(self.member_user)

    def post_download(self, client=None, **data):
        return (client or self.client).post(url("download"), {"password": PASSWORD, "formato": "json", **data})

    def downloaded(self, **data) -> tuple[dict, str]:
        response = self.post_download(**data)
        self.assertEqual(response.status_code, 200, response.content[:300])
        text = response.content.decode()
        return json.loads(text), text


class PageTests(RichFixture):
    def test_the_page_counts_each_category_and_never_copies_a_text_or_another_person(self):
        response = self.client.get(url("home"))
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        by_key = {item.category.key: item for item in response.context["summaries"]}
        self.assertEqual(dict(by_key["grupos"].parts)["Grupos en los que estás o estuviste"], 1)
        self.assertEqual(by_key["asistencia"].count, 1)
        self.assertEqual(by_key["eventos"].count, 1)
        self.assertEqual(dict(by_key["servir"].parts)["Fechas en las que te pidieron servir"], 1)
        self.assertEqual(by_key["oracion"].count, 1)
        self.assertEqual(by_key["formularios"].count, 1)
        self.assertEqual(by_key["espacios"].count, 1)
        self.assertEqual(by_key["campamentos"].count, 1)
        self.assertEqual(dict(by_key["lectura"].parts)["Planes de lectura que empezaste"], 1)
        self.assertEqual((by_key["directorio"].count, by_key["directorio"].where), (0, None))  # the church's switch is off
        self.assertIn("Descargar mis datos", page)
        self.assertIn("Lo que no está aquí", page)
        for text in [MY_PRAYER, "No puedo los jueves", *FOREIGN]:
            self.assertNotIn(text, page, text)
        # Every category says where it is seen, and the way goes to a screen of the person's own.
        self.assertEqual(by_key["oracion"].where[1], "/mi-iglesia/oracion/")
        self.assertEqual(by_key["perfil"].where[1], "/mi-iglesia/mis-datos/")

    def test_a_person_with_nothing_reads_nothing_saved(self):
        self.client.force_login(self.asked_user)
        other = self.client.get(url("home"))
        self.assertEqual(other.status_code, 200)
        self.client.force_login(self.mentor_user)  # an account with a church role and no row anywhere
        response = self.client.get(url("home"))
        self.assertContains(response, "Nada guardado.")
        self.assertNotContains(response, "Diego")

    def test_what_a_person_typed_is_escaped(self):
        Person.objects.filter(pk=self.member.pk).update(first_name="<script>alert(1)</script>")
        Booking.objects.filter(requested_by=self.member).update(title="<b>mío</b>")
        self.assertNotContains(self.client.get(url("home")), "<script>alert(1)")
        response = self.client.post(url("download"), {"password": PASSWORD, "formato": "pagina"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<script>alert(1)")
        self.assertNotContains(response, "<b>mío</b>")
        self.assertContains(response, "&lt;b&gt;mío&lt;/b&gt;")

    def test_denials(self):
        self.client.logout()
        for name in ("home", "download", "ask_removal"):
            self.assertEqual(self.client.get(url(name)).status_code, 302, name)  # to the sign-in page
        self.assertEqual(self.client.post(url("download"), {"password": PASSWORD}).status_code, 302)
        # An account that is nobody in a church with the module on (an event role gives nothing).
        self.client.force_login(self.coord2)
        for name in ("home", "download", "ask_removal"):
            self.assertEqual(self.client.get(url(name)).status_code, 404, name)

    def test_module_off_answers_404(self):
        flags.set_enabled(self.church, "community", False)
        flags.set_enabled(self.church_b, "community", False)
        for name in ("home", "download", "ask_removal", "requests", "powers"):
            self.assertEqual(self.client.get(url(name)).status_code, 404, name)
        self.assertEqual(self.post_download().status_code, 404)


class DownloadTests(RichFixture):
    def test_the_file_is_an_attachment_that_nobody_stores(self):
        response = self.post_download()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json; charset=utf-8")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("mis-datos-iglesia-prueba-", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_it_holds_the_persons_own_rows_in_plain_words(self):
        data, text = self.downloaded()
        self.assertEqual(data["iglesia"], "Iglesia de prueba")
        by_title = {category["categoría"]: category["datos"] for category in data["categorías"]}
        profile = by_title["Tu perfil y cómo contactarte"]["Tu registro en esta iglesia"][0]
        self.assertEqual(profile["nombre"], "Diego Hernández Cruz")
        self.assertEqual(profile["teléfono"], "55 1111 2222")
        self.assertEqual(by_title["Grupos y solicitudes"]["Grupos en los que estás o estuviste"][0]["grupo"], "Jóvenes Roca")
        self.assertEqual(by_title["Tu asistencia a tu grupo"]["Reuniones de tus grupos a las que fuiste"][0]["fuiste"], "Sí")
        self.assertEqual(by_title["Inscripciones a eventos"]["Inscripciones a eventos"][0]["evento"], "Convivio de otoño")
        serving_rows = by_title["Equipos, fechas y cambios para servir"]
        self.assertEqual(serving_rows["Equipos en los que sirves o serviste"][0]["equipo"], "Equipo de Alabanza")
        self.assertEqual(serving_rows["Fechas en las que te pidieron servir"][0]["tu respuesta"], "Por confirmar")
        self.assertEqual(by_title["Avisos leídos y tu elección de correos"]["Avisos que leíste"][0]["aviso"], "Cambio de horario")
        self.assertEqual(by_title["Lo que has leído"]["Planes de lectura que empezaste"][0]["días que marcaste como leídos"][0]["día"], 1)
        self.assertEqual(by_title["Reservas de espacios"]["Reservas de espacios que pediste"][0]["estado"], "Rechazada")
        self.assertEqual(by_title["Eventos en los que participaste"]["Eventos en los que participaste"][0]["evento"], self.event.name)
        account = by_title["Tu cuenta para entrar"]["Tu cuenta para entrar"][0]
        self.assertEqual(account["correo con el que entras"], "diego@prueba.mx")
        self.assertNotIn("pbkdf2", text)  # never the password's hash
        self.assertNotIn("password", text)

    def test_it_never_holds_another_persons_name_or_contact_nor_anything_the_staff_wrote(self):
        _data, text = self.downloaded(oracion="on", formularios="on")  # even with every box ticked
        for foreign in FOREIGN:
            self.assertNotIn(foreign, text, foreign)
        # Nobody else of the church, by any name or address the fixture knows.
        for person in Person.objects.exclude(user=self.member_user):
            for value in (person.email, person.phone, person.first_name):
                if value and value not in ("Diego",):
                    self.assertNotIn(value, text, value)
        for key in ("etapa", "etiqueta", "seguimiento", "nota", "decidió", "motivo"):
            self.assertNotIn(f'"{key}', text, key)

    def test_the_texts_the_person_wrote_come_only_behind_their_own_box(self):
        _data, text = self.downloaded()
        self.assertNotIn("MI-PETICION-PRIVADA", text)
        self.assertNotIn("No puedo los jueves", text)
        self.assertIn('"textos que pediste incluir": [\n    "Ninguno"', text)
        ChurchAuditEvent.objects.filter(action=services.DOWNLOAD_ACTION).delete()
        _data, text = self.downloaded(oracion="on")
        self.assertIn(MY_PRAYER, text)
        self.assertNotIn("No puedo los jueves", text)
        ChurchAuditEvent.objects.filter(action=services.DOWNLOAD_ACTION).delete()
        _data, text = self.downloaded(formularios="on")
        self.assertNotIn("MI-PETICION-PRIVADA", text)
        self.assertIn("No puedo los jueves", text)
        self.assertIn("¿Algo más?", text)

    def test_an_answer_to_a_public_form_tied_to_the_record_later_is_not_in_it(self):
        public = self.public_form()
        self.answer(public, data=self.public_data(public, text="ESCRITO-POR-QUIEN-SEA"))
        self.assertEqual(public.submissions.update(person=self.member), 1)
        _data, text = self.downloaded(formularios="on")
        self.assertNotIn("ESCRITO-POR-QUIEN-SEA", text)
        self.assertNotIn("visita@demo.invalid", text)

    def test_a_two_church_account_downloads_only_the_church_of_the_request(self):
        _data, in_a = self.downloaded()
        self.assertIn("Jóvenes Roca", in_a)
        self.assertNotIn("Grupo de la iglesia B", in_a)
        self.assertNotIn("55 0000 2222", in_a)
        self.assertNotIn("Iglesia B", in_a)
        self.assertEqual(self.client.post(reverse("switch_church"), {"church": self.church_b.pk}).status_code, 302)
        data, in_b = self.downloaded()
        self.assertEqual(data["iglesia"], "Iglesia B")
        self.assertIn("Grupo de la iglesia B", in_b)
        self.assertIn("55 0000 2222", in_b)
        for only_a in ("Jóvenes Roca", "55 1111 2222", "Equipo de Alabanza", "Convivio de otoño", "Salón Azul", "Cambio de horario", "Plan de Juan"):
            self.assertNotIn(only_a, in_b, only_a)
        # Each church's activity log holds its own download, and nothing of the other's.
        self.assertEqual(ChurchAuditEvent.objects.filter(church=self.church, action=services.DOWNLOAD_ACTION).count(), 1)
        self.assertEqual(ChurchAuditEvent.objects.filter(church=self.church_b, action=services.DOWNLOAD_ACTION).count(), 1)

    def test_a_row_of_another_persons_record_never_leaks_through_a_shared_account_column(self):
        """A record joined into another is hidden: its rows are not this person's any more."""
        hidden = Person.objects.create(church=self.church, first_name="Registro", last_name="Unido", phone="55 7777 0000",
                                       merged_into=self.leader)
        groups_before = GroupMembership.objects.filter(person=self.member).count()
        GroupMembership.objects.create(group=self.other_group, person=hidden)
        _data, text = self.downloaded()
        self.assertNotIn("55 7777 0000", text)
        self.assertNotIn("Estudio de Romanos", text)
        self.assertEqual(GroupMembership.objects.filter(person=self.member).count(), groups_before)

    def test_the_password_is_asked_again(self):
        for wrong in ({}, {"password": ""}, {"password": "otra-clave"}):
            response = self.client.post(url("download"), {"formato": "json", **wrong})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("Content-Disposition", response)
            self.assertNotContains(response, "Jóvenes Roca")
        self.assertContains(self.client.post(url("download"), {"password": "otra-clave"}), "Esa no es tu contraseña")
        self.assertFalse(ChurchAuditEvent.objects.filter(action=services.DOWNLOAD_ACTION).exists())
        self.assertEqual(self.client.get(url("download")).status_code, 200)  # a GET never gives the file
        self.assertNotIn("Content-Disposition", self.client.get(url("download")))

    def test_wrong_passwords_are_counted_and_then_even_the_right_one_waits(self):
        for _ in range(services.PASSWORD_LIMIT["limit"]):
            self.assertEqual(self.client.post(url("download"), {"password": "no"}).status_code, 200)
        self.assertEqual(self.client.post(url("download"), {"password": "no"}).status_code, 429)
        blocked = self.post_download()
        self.assertEqual(blocked.status_code, 429)
        self.assertNotIn("Content-Disposition", blocked)

    def test_an_account_without_a_password_cannot_download(self):
        self.member_user.set_unusable_password()
        self.member_user.save()
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(url("download")), "todavía no tiene contraseña")
        response = self.post_download()
        self.assertNotIn("Content-Disposition", response)

    def test_two_a_day_and_the_third_is_refused_until_a_day_passes(self):
        self.assertEqual(self.post_download().status_code, 200)
        self.assertEqual(self.client.post(url("download"), {"password": PASSWORD, "formato": "pagina"}).status_code, 200)
        third = self.post_download()
        self.assertEqual(third.status_code, 429)
        self.assertNotIn("Content-Disposition", third)
        self.assertContains(third, "No se descargó nada", status_code=429)
        self.assertEqual(ChurchAuditEvent.objects.filter(action=services.DOWNLOAD_ACTION).count(), 2)
        # Another account is not held back by this one's downloads.
        other = Client()
        other.force_login(self.asked_user)
        self.assertEqual(self.post_download(other).status_code, 200)
        # A day later the first one no longer counts.
        ChurchAuditEvent.objects.filter(action=services.DOWNLOAD_ACTION, actor=self.member_user).update(
            created_at=timezone.now() - timedelta(days=1, minutes=1)
        )
        self.assertEqual(self.post_download().status_code, 200)

    def test_the_limit_is_checked_inside_the_same_transaction_that_writes_the_row(self):
        """Two requests that both read "one so far" cannot both go through: the service counts after it locks."""
        for _ in range(services.DOWNLOADS_PER_DAY):
            services.build_download(self.church, self.member, self.member_user, set(), kind="json")
        with self.assertRaises(services.TooManyDownloads):
            services.build_download(self.church, self.member, self.member_user, set(), kind="json")

    def test_the_audit_row_carries_ids_and_fixed_words_only(self):
        self.downloaded(oracion="on")
        row = ChurchAuditEvent.objects.get(action=services.DOWNLOAD_ACTION)
        self.assertEqual(row.metadata, {"person_id": self.member.pk, "method": "json", "value": "oracion"})
        self.assertEqual(row.summary, "Descargó sus propios datos")
        self.assertEqual(row.actor, self.member_user)
        self.assertNotIn("MI-PETICION", json.dumps(row.metadata) + row.summary)

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.member_user)
        self.assertEqual(client.post(url("download"), {"password": PASSWORD}).status_code, 403)
        self.assertEqual(client.post(url("ask_removal"), {"ask": "todo"}).status_code, 403)

    def test_the_page_to_print_has_the_same_content_and_is_not_an_attachment(self):
        response = self.client.post(url("download"), {"password": PASSWORD, "formato": "pagina", "oracion": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Content-Disposition", response)
        self.assertIn("no-store", response["Cache-Control"])
        page = response.content.decode()
        for text in ("Jóvenes Roca", "Equipo de Alabanza", "MI-PETICION-PRIVADA", "Convivio de otoño", "Esta página no se guarda"):
            self.assertIn(text, page, text)
        for foreign in FOREIGN:
            self.assertNotIn(foreign, page, foreign)

    def test_the_registry_decides_what_is_written(self):
        """Take a category out and its rows leave the file: nothing is exported from outside the registry."""
        known = registry.CATEGORIES
        registry.CATEGORIES = tuple(category for category in known if category.key != "grupos")
        self.addCleanup(setattr, registry, "CATEGORIES", known)
        _data, text = self.downloaded()
        self.assertNotIn("Grupos en los que estás o estuviste", text)
        self.assertNotIn("Guía del grupo", text)
        self.assertNotIn("Integrante", text)


class MinorTests(RichFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        today = timezone.localdate()
        Person.objects.filter(pk=cls.member.pk).update(birth_date=today.replace(year=today.year - 15))

    def test_a_known_minor_reads_the_page_and_gets_no_download_and_no_request(self):
        page = self.client.get(url("home"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Descargar mis datos")
        self.assertNotContains(page, "Pedir que borren mis datos")
        self.assertContains(page, "tutor lo pide directamente a la administración")
        self.assertContains(self.client.get(url("download")), "tutor lo pide directamente")
        refused = self.post_download()
        self.assertEqual(refused.status_code, 403)
        self.assertNotIn("Content-Disposition", refused)
        self.assertNotContains(refused, "Jóvenes Roca", status_code=403)
        self.assertEqual(self.client.get(url("ask_removal")).status_code, 403)
        self.assertEqual(self.client.post(url("ask_removal"), {"ask": "todo"}).status_code, 403)
        self.assertFalse(ChurchAuditEvent.objects.filter(action__startswith="mis_datos").exists())

    def test_a_person_only_marked_as_minor_at_a_door_is_a_known_minor_too(self):
        Person.objects.filter(pk=self.member.pk).update(birth_date=None)
        EventRegistration.objects.filter(person=self.member).update(guest_is_minor=True)
        self.assertEqual(self.post_download().status_code, 403)
