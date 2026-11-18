"""Issue #87 on synthetic data: the public home of a church shows what its administration published
on purpose to anybody with the address, and nothing else. Two churches, unpublished material and an
anonymous visitor; everything that is not such a page is the same 404."""

from datetime import date, time, timedelta
from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from agenda import services as agenda
from agenda.models import EventPage, EventRegistration
from agenda.tests.test_public_page import PublicFixture
from core.models import Church
from notices import services as notices
from people import flags
from people.models import ChurchAuditEvent, Person
from resources import services as resources
from resources.models import MediaItem
from sitio import services
from sitio.models import PublicHome, PublicMedia, ServiceTime

PHONE_A, PHONE_B = "55 1234 5678", "999 876 5432"


def home_url(church) -> str:
    return reverse("iglesia:home", args=[church.slug])


class HomeFixture(PublicFixture):
    """Two churches with a published home each. Church A: two times on Sunday and one on Wednesday,
    the retreat with its public page, one public sermon, one sermon nobody marked. Church B: its own
    time, phone, e-mail, public event and public resource."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Church.objects.filter(pk=cls.church.pk).update(contact_email="hola@iglesia-a.invalid")
        Church.objects.filter(pk=cls.church_b.pk).update(contact_email="hola@iglesia-b.invalid")
        cls.church.refresh_from_db()
        cls.church_b.refresh_from_db()
        services.save_home(cls.church, cls.admin, welcome="Llega como eres: te esperamos en la entrada.",
                           address="Calle de la Paz 12\nColonia Centro", map_url="https://mapas.invalid/iglesia-a",
                           contact_phone=PHONE_A)
        sunday, wednesday = ServiceTime.Weekday.SUNDAY, ServiceTime.Weekday.WEDNESDAY
        services.save_time(cls.church, cls.admin, weekday=wednesday, time=time(19, 30), name="Oración entre semana")
        services.save_time(cls.church, cls.admin, weekday=sunday, time=time(9, 0), name="Primera reunión", place="Templo principal")
        services.save_time(cls.church, cls.admin, weekday=sunday, time=time(11, 0), name="Reunión general")
        cls.sermon = cls._media(cls.church, cls.admin, "La vid y las ramas", "https://videos.invalid/vid", speaker="Pastor Ejemplo Público",
                                series="Juan")
        cls.private_sermon = cls._media(cls.church, cls.admin, "Sermón solo para la iglesia", "https://videos.invalid/interno",
                                        speaker="Predicadora Reservada")
        services.show_media(cls.sermon, cls.admin)
        cls.publish(cls.church, cls.admin)

        services.save_home(cls.church_b, cls.admin_b, welcome="Bienvenida de la iglesia B.", address="Avenida B 99",
                           map_url="https://mapas.invalid/iglesia-b", contact_phone=PHONE_B)
        services.save_time(cls.church_b, cls.admin_b, weekday=ServiceTime.Weekday.SATURDAY, time=time(18, 15), name="Culto sabatino B")
        cls.sermon_b = cls._media(cls.church_b, cls.admin_b, "Mensaje de la iglesia B", "https://videos.invalid/b", speaker="Predicador Be")
        services.show_media(cls.sermon_b, cls.admin_b)
        agenda.save_page(cls.retiro_b, cls.admin_b, public=True)
        cls.publish(cls.church_b, cls.admin_b)
        # What only the people of church A see: a notice and a group.
        notices.publish(cls.church, cls.admin, title="Aviso interno reservado", body="Solo para la iglesia.")

    @staticmethod
    def _media(church, actor, title, url, **extra) -> MediaItem:
        return resources.save_media(church, actor, kind="predicacion", title=title, url=url, date=date(2026, 11, 15), **extra)

    @staticmethod
    def publish(church, actor) -> PublicHome:
        return services.publish(church, actor, services.page_content(church).digest())

    def get(self, church=None):
        return self.client.get(home_url(church or self.church))

    def private(self):
        words = [word.decode() for word in super().private()]
        words += ["Sermón solo para la iglesia", "Predicadora Reservada", "videos.invalid/interno", "Aviso interno reservado",
                  self.group.name, self.other_group.name, "Salón 2", "Grupos", "Avisos", "asistencia", "Cerrar sesión", "<form",
                  "Mi cuenta", self.admin.first_name, self.admin.last_name, self.admin.email]
        return [word.encode() for word in words if word]


class PublicHomeTests(HomeFixture):
    def test_a_visitor_finds_the_times_an_event_and_a_sermon(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        for text in (self.church.name, "Llega como eres: te esperamos en la entrada.", "Cuándo nos reunimos", "Domingos", "Miércoles",
                     "Primera reunión", ", en Templo principal", "Reunión general", "Oración entre semana", "Calle de la Paz 12<br>Colonia Centro",
                     "Retiro de jóvenes", "Rancho El Olivo", "La vid y las ramas", "Predicación, de Pastor Ejemplo Público, serie «Juan», 15 de noviembre de 2026.",
                     "¿Ya eres parte de esta iglesia?", "Entra a Emmaus"):
            self.assertContains(response, text)
        html = response.content.decode()
        self.assertContains(response, "<h1", count=1)
        self.assertContains(response, "<main", count=1)
        self.assertContains(response, "<footer", count=1)
        self.assertContains(response, '<meta name="robots" content="noindex, nofollow">')
        self.assertContains(response, "css/santuario.css")
        self.assertContains(response, 'data-ui="2"')
        # The times read as a definition list, Sunday first, each day once.
        self.assertContains(response, "<dt>Domingos</dt>", count=1)
        self.assertLess(html.index("<dt>Domingos</dt>"), html.index("<dt>Miércoles</dt>"))
        self.assertLess(html.index("Primera reunión"), html.index("Reunión general"))
        self.assertContains(response, '<time class="horarios__time" datetime="09:00">09:00</time>')
        # Contact as links with their text in sight; the map and the sermon leave Emmaus and say so.
        self.assertContains(response, f'<a href="tel:5512345678">{PHONE_A}</a>')
        self.assertContains(response, '<a href="mailto:hola@iglesia-a.invalid">hola@iglesia-a.invalid</a>')
        self.assertContains(response, '<a href="https://mapas.invalid/iglesia-a" rel="noopener noreferrer">')
        self.assertContains(response, '<a href="https://videos.invalid/vid" rel="noopener noreferrer" aria-describedby="recursos-nota">')
        self.assertContains(response, "fuera de Emmaus", count=2)
        # The event leads to its own public page; the way in leads to the sign-in.
        self.assertContains(response, f'href="{reverse("evento:detail", args=[self.retiro.slug])}"')
        self.assertContains(response, f'href="{reverse("login")}"')
        self.assertEqual(html.count("<a "), 7)  # skip link, map, event, sermon, phone, e-mail, sign-in

    def test_an_anonymous_phone_gets_no_cookie_and_nothing_is_cached(self):
        response = self.client.get(home_url(self.church), HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertEqual(dict(response.cookies), {})
        self.assertEqual(dict(self.client.cookies), {})
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
        self.assertNotIn(b"csrfmiddlewaretoken", response.content)
        self.assertNotIn(b"<form", response.content)

    def test_nothing_about_people_is_in_the_bytes(self):
        content = self.get().content
        for word in self.private():
            self.assertNotIn(word, content, word.decode())
        for person in Person.objects.all():
            for value in (person.full_name, person.email, person.phone):
                if value:
                    self.assertNotIn(value.encode(), content, value)
        for row in EventRegistration.objects.all():
            self.assertNotIn(f"/{row.pk}/".encode(), content)

    def test_the_page_never_asks_a_table_of_people(self):
        with CaptureQueriesContext(connection) as queries:
            self.get()
        sql = " ".join(query["sql"] for query in queries.captured_queries)
        for table in ("agenda_eventregistration", "agenda_guardianconsent", "people_person", "events_eventparticipant", "auth_user",
                      "care_followup", "groups_group", "notices_announcement", "django_session"):
            self.assertNotIn(table, sql)
        self.assertLessEqual(len(queries), 8)

    def test_who_is_signed_in_sees_the_same_bytes(self):
        anonymous = self.get().content
        for user in (self.member_user, self.leader_user, self.admin, self.member_b_user, self.admin_b):
            self.client.force_login(user)
            self.assertEqual(self.get().content, anonymous, user.email)

    def test_it_only_reads(self):
        for method in (self.client.post, self.client.put, self.client.delete):
            self.assertEqual(method(home_url(self.church)).status_code, 405)

    def test_empty_sections_are_not_drawn(self):
        ServiceTime.objects.filter(church=self.church).delete()
        PublicMedia.objects.filter(church=self.church).delete()
        EventPage.objects.filter(event__church=self.church).update(public=False)
        PublicHome.objects.filter(church=self.church).update(welcome="", address="", map_url="", contact_phone="")
        Church.objects.filter(pk=self.church.pk).update(contact_email="")
        response = self.get()
        self.assertContains(response, self.church.name)
        self.assertNotContains(response, "<h2")
        self.assertNotContains(response, "<dl")
        self.assertContains(response, "Entra a Emmaus")

    def test_the_church_wears_its_own_colours_and_its_initials(self):
        self.assertContains(self.get(), 'class="mono mono--lg mono--grupo" aria-hidden="true"')  # the church's arch, not a person's circle
        self.assertNotContains(self.get(), "--petroleo:")
        Church.objects.filter(pk=self.church.pk).update(custom_theme_enabled=True, theme_primary_color="#7A1F3D")
        self.client.force_login(self.member_b_user)  # a reader of another church still gets this church's colours
        self.assertContains(self.get(), "--petroleo:#7A1F3D")


class OnlyWhatWasPublishedTests(HomeFixture):
    def test_each_church_shows_only_its_own(self):
        page_a, page_b = self.get().content, self.get(self.church_b).content
        of_a = [self.church.name, "Llega como eres", "Calle de la Paz", "mapas.invalid/iglesia-a", PHONE_A, "5512345678",
                "hola@iglesia-a.invalid", "Primera reunión", "Oración entre semana", "09:00", "19:30", "Retiro de jóvenes",
                "retiro-jovenes", "La vid y las ramas", "Pastor Ejemplo Público", "videos.invalid/vid"]
        of_b = [self.church_b.name, "Bienvenida de la iglesia B.", "Avenida B 99", "mapas.invalid/iglesia-b", PHONE_B, "9998765432",
                "hola@iglesia-b.invalid", "Culto sabatino B", "18:15", "Vigilia de la iglesia B", "vigilia-b",
                "Mensaje de la iglesia B", "Predicador Be", "videos.invalid/b"]
        for word in of_a:
            self.assertIn(word.encode(), page_a, word)
            self.assertNotIn(word.encode(), page_b, word)
        for word in of_b:
            self.assertIn(word.encode(), page_b, word)
            self.assertNotIn(word.encode(), page_a, word)

    def test_a_resource_needs_the_library_the_mark_and_the_church(self):
        self.assertNotContains(self.get(), "Sermón solo para la iglesia")  # published in the library, never marked
        # Marked, then retired from the library: the mark alone shows nothing.
        services.show_media(self.private_sermon, self.admin)
        self.assertContains(self.get(), "Sermón solo para la iglesia")
        resources.retire_media(self.private_sermon, self.admin)
        self.assertTrue(PublicMedia.objects.filter(item=self.private_sermon).exists())
        response = self.get()
        self.assertNotContains(response, "Sermón solo para la iglesia")
        self.assertNotContains(response, "videos.invalid/interno")
        # A status that is not "publicado" at all, with the mark.
        MediaItem.objects.filter(pk=self.sermon.pk).update(status="borrador")
        self.assertNotContains(self.get(), "La vid y las ramas")
        MediaItem.objects.filter(pk=self.sermon.pk).update(status=MediaItem.Status.PUBLISHED)
        self.assertContains(self.get(), "La vid y las ramas")
        # A mark planted by another church (no service writes this) shows on neither page.
        PublicMedia.objects.filter(item=self.sermon).update(church=self.church_b)
        for church in (self.church, self.church_b):
            self.assertNotContains(self.get(church), "La vid y las ramas")
        # Taking the mark away is enough.
        PublicMedia.objects.filter(item=self.sermon).update(church=self.church)
        services.hide_media(self.sermon, self.admin)
        self.assertNotContains(self.get(), "La vid y las ramas")

    def test_only_the_newest_resources_are_listed(self):
        for number in range(services.MEDIA_LIMIT + 1):
            item = resources.save_media(self.church, self.admin, kind="estudio", title=f"Estudio número {number}",
                                        url=f"https://videos.invalid/e{number}", date=date(2026, 11, 16) + timedelta(days=number))
            services.show_media(item, self.admin)
        response = self.get()
        self.assertContains(response, 'class="row sitio-media"', count=services.MEDIA_LIMIT)
        self.assertNotContains(response, "Estudio número 0")
        self.assertNotContains(response, "La vid y las ramas")

    def test_an_event_needs_a_published_page_that_is_public_and_not_over(self):
        self.assertContains(self.get(), "Retiro de jóvenes")
        for event in (self.hidden, self.no_page):  # not published; no page at all
            self.assertNotContains(self.get(), event.name)
        EventPage.objects.filter(event=self.hidden).update(public=True)  # public but not published
        self.assertNotContains(self.get(), self.hidden.name)
        self.set_page(public=False)  # published for the church, not for everybody
        self.assertNotContains(self.get(), "Retiro de jóvenes")
        self.set_page(public=True)
        for status in ("borrador", "cerrado"):
            type(self.retiro).objects.filter(pk=self.retiro.pk).update(status=status)
            self.assertNotContains(self.get(), "Retiro de jóvenes")
        type(self.retiro).objects.filter(pk=self.retiro.pk).update(status="activo")
        self.move(self.retiro, -5, -3)
        self.assertNotContains(self.get(), "Retiro de jóvenes")
        self.move(self.retiro, 0, 2)
        response = self.get()
        self.assertContains(response, "Retiro de jóvenes")
        # Every event the home lists has a page that answers.
        self.assertEqual(self.client.get(reverse("evento:detail", args=[self.retiro.slug])).status_code, 200)


class NoOracleTests(HomeFixture):
    def gone(self, church, why):
        """The answer for ``church`` is, byte for byte, the answer for an address that never existed."""
        response = self.get(church)
        missing = self.client.get(reverse("iglesia:home", args=["no-existe"]))
        self.assertEqual((response.status_code, missing.status_code), (404, 404), why)
        self.assertEqual(response.content, missing.content, why)
        self.assertEqual(response.headers.get("Cache-Control"), missing.headers.get("Cache-Control"), why)
        self.assertNotIn(church.name.encode(), response.content, why)

    def test_everything_that_is_not_a_published_home_is_the_same_404(self):
        self.assertEqual(self.get().status_code, 200)
        services.unpublish(self.church, self.admin)
        self.gone(self.church, "not published")
        self.publish(self.church, self.admin)
        self.assertEqual(self.get().status_code, 200)
        flags.set_enabled(self.church, "community", False)
        self.gone(self.church, "module off")
        flags.set_enabled(self.church, "community", True)
        Church.objects.filter(pk=self.church.pk).update(active=False)
        self.gone(self.church, "church not active")
        Church.objects.filter(pk=self.church.pk).update(active=True)
        PublicHome.objects.filter(church=self.church).delete()
        self.gone(self.church, "no home at all")
        # Signed in, the church's own administration gets the same refusal as a stranger.
        self.client.force_login(self.admin)
        self.assertEqual(self.get().status_code, 404)
        self.assertEqual(self.client.get(reverse("iglesia:home", args=["no-existe"])).status_code, 404)
        # Church B is untouched by all of it.
        self.assertEqual(self.get(self.church_b).status_code, 200)

    def test_with_the_kill_switch_off_every_home_is_gone(self):
        with self.settings(EMMAUS_V2_MODULES=False):
            self.assertEqual(self.get().status_code, 404)

    def test_the_logo_address_still_answers_by_itself(self):
        self.assertEqual(self.client.get(reverse("church_logo", args=[self.church.slug, "claro"])).status_code, 404)  # no logo stored
        self.assertEqual(reverse("church_logo", args=["x", "claro"]), "/iglesia/x/logotipo/claro/")


class AdministrationTests(HomeFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def audits(self, action):
        return ChurchAuditEvent.objects.filter(church=self.church, action=action)

    def test_only_the_administration_of_the_church_opens_it(self):
        addresses = [reverse(f"sitio:{name}") for name in ("edit", "preview", "publish", "unpublish", "time_create")]
        row = ServiceTime.objects.filter(church=self.church).first()
        addresses += [reverse("sitio:time_edit", args=[row.pk]), reverse("sitio:time_delete", args=[row.pk])]
        for address in addresses:
            self.client.force_login(self.admin)
            self.assertIn(self.client.get(address).status_code, (200, 302), address)
            for user in (self.member_user, self.leader_user):
                self.client.force_login(user)
                self.assertEqual(self.client.get(address).status_code, 403, address)
                self.assertEqual(self.client.post(address, {}).status_code, 403, address)
            self.client.logout()
            self.assertEqual(self.client.get(address).status_code, 302, address)
        self.client.force_login(self.admin)
        flags.set_enabled(self.church, "community", False)
        for address in addresses:
            self.assertEqual(self.client.get(address).status_code, 404, address)

    def test_configuracion_leads_to_it_only_with_the_module(self):
        self.assertContains(self.client.get(reverse("church_settings")), reverse("sitio:edit"))
        flags.set_enabled(self.church, "community", False)
        self.assertNotContains(self.client.get(reverse("church_settings")), "Página pública de la iglesia")

    def test_the_screen_says_what_is_published_and_gives_the_address(self):
        response = self.client.get(reverse("sitio:edit"))
        self.assertContains(response, "<h1", count=1)
        self.assertContains(response, f'value="http://testserver{home_url(self.church)}"')
        self.assertContains(response, "Dejar de publicar la página")
        self.assertContains(response, "Hoy mostraría 1: Retiro de jóvenes.")
        self.assertContains(response, "Hoy mostraría 1: La vid y las ramas.")
        self.assertContains(response, "Domingos, 09:00")
        self.assertNotContains(response, "Culto sabatino B")
        # «Ventanas»: withdrawing is a quiet action whose page asks first; a time's row is the way into its form, and
        # it is taken out from there (one trailing action at most, none here).
        self.assertContains(response, f'<a class="btn btn--link btn--quieto" href="{reverse("sitio:unpublish")}">Dejar de publicar la página</a>')
        self.assertContains(response, "Nunca muestra personas, grupos, avisos ni asistencia.")
        row = ServiceTime.objects.filter(church=self.church).first()
        self.assertContains(response, f'<a href="{reverse("sitio:time_edit", args=[row.pk])}">')
        self.assertNotContains(response, reverse("sitio:time_delete", args=[row.pk]))
        form = self.client.get(reverse("sitio:time_edit", args=[row.pk]))
        self.assertContains(form, f'<a class="btn btn--link btn--quieto" href="{reverse("sitio:time_delete", args=[row.pk])}">Quitar el horario</a>')
        self.assertNotContains(self.client.get(reverse("sitio:time_create")), "Quitar el horario")
        services.unpublish(self.church, self.admin)
        response = self.client.get(reverse("sitio:edit"))
        self.assertContains(response, "Sin publicar")
        self.assertContains(response, reverse("sitio:publish"))
        self.assertContains(response, reverse("sitio:preview"))
        self.assertNotContains(response, home_url(self.church))

    def test_saving_the_data_never_publishes_and_leaves_one_audit_row(self):
        services.unpublish(self.church, self.admin)
        before = self.audits("sitio_modificado").count()
        data = {"welcome": "  Te esperamos   el domingo. ", "address": "Calle Nueva 5", "map_url": "https://mapas.invalid/nuevo",
                "contact_phone": "55 0000 1111"}
        response = self.client.post(reverse("sitio:edit"), data, follow=True)
        self.assertContains(response, "Nadie los ve todavía")
        home = PublicHome.objects.get(church=self.church)
        self.assertEqual((home.welcome, home.address, home.map_url, home.contact_phone, home.published),
                         ("Te esperamos el domingo.", "Calle Nueva 5", "https://mapas.invalid/nuevo", "55 0000 1111", False))
        self.assertEqual(self.audits("sitio_modificado").count(), before + 1)
        audit = self.audits("sitio_modificado").latest("id")
        self.assertEqual(audit.metadata, {})
        for word in ("Calle Nueva", "0000", "mapas", "Te esperamos"):
            self.assertNotIn(word, audit.summary)
        self.client.post(reverse("sitio:edit"), data)  # the same again: nothing changed, nothing recorded
        self.assertEqual(self.audits("sitio_modificado").count(), before + 1)
        self.assertEqual(self.get().status_code, 404)
        self.assertEqual(PublicHome.objects.get(church=self.church_b).address, "Avenida B 99")

    def test_what_the_form_refuses(self):
        cases = [
            ({"map_url": "http://mapas.invalid/x"}, "El enlace debe empezar con https://"),
            ({"map_url": "javascript:alert(1)"}, "El enlace debe empezar con https://"),
            ({"map_url": "https://"}, "Ese enlace no se entiende"),
            ({"contact_phone": "llámame"}, "Escribe el teléfono solo con números"),
            ({"contact_phone": "123"}, "Escribe el teléfono solo con números"),
            ({"welcome": "x" * 281}, "La bienvenida puede tener hasta 280 caracteres"),
        ]
        before = PublicHome.objects.get(church=self.church)
        for data, message in cases:
            response = self.client.post(reverse("sitio:edit"), data)
            self.assertContains(response, message)
            self.assertContains(response, "Error: ")
        after = PublicHome.objects.get(church=self.church)
        self.assertEqual((after.map_url, after.contact_phone, after.welcome), (before.map_url, before.contact_phone, before.welcome))
        for bad in ("http://x.invalid", "ftp://x.invalid", "//x.invalid"):
            with self.assertRaises(services.SiteError):
                services.save_home(self.church, self.admin, map_url=bad)

    def test_times_are_added_changed_and_taken_away_inside_the_church(self):
        before = self.audits("sitio_modificado").count()
        response = self.client.post(reverse("sitio:time_create"), {"weekday": "4", "time": "20:00", "name": "Jóvenes", "place": "", "position": ""},
                                    follow=True)
        self.assertContains(response, "Agregaste el horario.")
        row = ServiceTime.objects.get(church=self.church, name="Jóvenes")
        self.assertContains(self.get(), "Viernes")
        self.client.post(reverse("sitio:time_edit", args=[row.pk]), {"weekday": "5", "time": "17:00", "name": "Jóvenes", "place": "Patio", "position": "1"})
        row.refresh_from_db()
        self.assertEqual((row.weekday, row.time, row.place, row.position), (5, time(17, 0), "Patio", 1))
        self.assertContains(self.client.get(reverse("sitio:time_delete", args=[row.pk])), "¿Quitar el horario «Jóvenes»?")
        self.assertTrue(ServiceTime.objects.filter(pk=row.pk).exists())
        self.client.post(reverse("sitio:time_delete", args=[row.pk]))
        self.assertFalse(ServiceTime.objects.filter(pk=row.pk).exists())
        self.assertEqual(self.audits("sitio_modificado").count(), before + 3)
        self.assertContains(self.client.post(reverse("sitio:time_create"), {"weekday": "", "time": "", "name": ""}), "Elige el día de la reunión.")
        # A time of another church is not there to edit or to take away.
        other = ServiceTime.objects.get(church=self.church_b)
        for name in ("time_edit", "time_delete"):
            self.assertEqual(self.client.get(reverse(f"sitio:{name}", args=[other.pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse(f"sitio:{name}", args=[other.pk]), {"weekday": "1", "time": "10:00", "name": "X"}).status_code, 404)
        other.refresh_from_db()
        self.assertEqual(other.name, "Culto sabatino B")

    def test_the_review_lists_exactly_what_will_be_public_and_nothing_else(self):
        services.unpublish(self.church, self.admin)
        response = self.client.get(reverse("sitio:publish"))
        self.assertContains(response, "<h1", count=1)
        for text in ("Todavía no se publica nada.", self.church.name, "Llega como eres: te esperamos en la entrada.", "Calle de la Paz 12",
                     "https://mapas.invalid/iglesia-a", PHONE_A, "hola@iglesia-a.invalid", "Domingos, 09:00: Primera reunión, en Templo principal.",
                     "Miércoles, 19:30: Oración entre semana.", "Retiro de jóvenes", "La vid y las ramas", "https://videos.invalid/vid",
                     "El nombre de quien predicó o enseñó será público.", "Publicar la página", f"http://testserver{home_url(self.church)}"):
            self.assertContains(response, text)
        for text in ("Sermón solo para la iglesia", "Predicadora Reservada", self.hidden.name, self.no_page.name, "Culto sabatino B",
                     "Vigilia de la iglesia B", "Mensaje de la iglesia B", PHONE_B, "Aviso interno reservado"):
            self.assertNotContains(response, text)
        self.assertEqual(self.get().status_code, 404)  # looking at the review publishes nothing
        self.assertFalse(self.audits("sitio_publicado").filter(pk__gt=self.audits("sitio_despublicado").latest("id").pk).exists())

    def test_publishing_takes_the_review_and_is_audited_and_so_is_unpublishing(self):
        services.unpublish(self.church, self.admin)
        published, withdrawn = self.audits("sitio_publicado").count(), self.audits("sitio_despublicado").count()
        # Without the review, or with another one, nothing is published.
        for revision in ("", "0" * 64):
            response = self.client.post(reverse("sitio:publish"), {"revision": revision}, follow=True)
            self.assertContains(response, "No se publicó nada")
            self.assertFalse(PublicHome.objects.get(church=self.church).published)
        # Something that would be public changed after the review was shown: the list is shown again.
        review = self.client.get(reverse("sitio:publish"))
        services.show_media(self.private_sermon, self.admin)
        response = self.client.post(reverse("sitio:publish"), {"revision": review.context["revision"]}, follow=True)
        self.assertContains(response, "No se publicó nada")
        self.assertContains(response, "Sermón solo para la iglesia")
        self.assertEqual(self.get().status_code, 404)
        response = self.client.post(reverse("sitio:publish"), {"revision": response.context["revision"]}, follow=True)
        self.assertContains(response, "Publicaste la página.")
        home = PublicHome.objects.get(church=self.church)
        self.assertEqual((home.published, home.published_by), (True, self.admin))
        self.assertEqual(self.get().status_code, 200)
        self.assertEqual(self.audits("sitio_publicado").count(), published + 1)
        audit = self.audits("sitio_publicado").latest("id")
        self.assertEqual((audit.actor, audit.object_type, audit.object_id, audit.metadata), (self.admin, "PublicHome", str(home.pk), {}))
        # Already published: the review has nothing to do.
        self.assertRedirects(self.client.get(reverse("sitio:publish")), reverse("sitio:edit"))
        # Unpublishing asks first.
        self.assertContains(self.client.get(reverse("sitio:unpublish")), "¿Dejar de publicar la página de la iglesia?")
        self.assertEqual(self.get().status_code, 200)
        self.client.post(reverse("sitio:unpublish"))
        self.assertEqual(self.get().status_code, 404)
        self.assertEqual(self.audits("sitio_despublicado").count(), withdrawn + 1)
        self.client.post(reverse("sitio:unpublish"))  # again: nothing to record
        self.assertEqual(self.audits("sitio_despublicado").count(), withdrawn + 1)
        self.assertTrue(PublicHome.objects.get(church=self.church_b).published)

    def test_the_preview_is_the_page_for_the_administration_even_unpublished(self):
        services.unpublish(self.church, self.admin)
        response = self.client.get(reverse("sitio:preview"))
        self.assertContains(response, "Vista previa.")
        self.assertContains(response, "Todavía nadie más la ve")
        self.assertContains(response, "Reunión general")
        self.assertContains(response, "<h1", count=1)
        self.assertNotContains(response, 'class="rail"')
        self.assertNotContains(response, "Culto sabatino B")

    def test_a_resource_is_made_public_and_private_from_the_resources_list_with_an_audit_row(self):
        listing = self.client.get(reverse("resources:media_admin"))
        show, hide = reverse("sitio:media_show", args=[self.private_sermon.pk]), reverse("sitio:media_hide", args=[self.sermon.pk])
        self.assertContains(listing, f'action="{show}"')
        self.assertContains(listing, f'action="{hide}"')
        self.assertContains(listing, "Mostrar en la página pública", count=1)
        self.assertContains(listing, "Quitar de la página pública", count=1)
        self.assertContains(listing, "En la página pública:", count=1)
        self.assertEqual(self.client.get(show).status_code, 405)
        self.assertFalse(PublicMedia.objects.filter(item=self.private_sermon).exists())
        response = self.client.post(show, follow=True)
        self.assertContains(response, "ya se puede ver en la página pública de la iglesia, sin cuenta")
        mark = PublicMedia.objects.get(item=self.private_sermon)
        self.assertEqual((mark.church, mark.published_by), (self.church, self.admin))
        audit = self.audits("recurso_publico").latest("id")
        self.assertEqual((audit.summary, audit.object_type, audit.object_id, audit.metadata),
                         ("Mostró un recurso (predicación) en la página pública", "MediaItem", str(self.private_sermon.pk), {}))
        self.client.post(show)  # twice: one mark, one audit row
        self.assertEqual(self.audits("recurso_publico").filter(object_id=str(self.private_sermon.pk)).count(), 1)
        self.assertContains(self.get(), "Sermón solo para la iglesia")
        response = self.client.post(reverse("sitio:media_hide", args=[self.private_sermon.pk]), follow=True)
        self.assertContains(response, "ya no se ve en la página pública")
        self.assertFalse(PublicMedia.objects.filter(item=self.private_sermon).exists())
        self.assertEqual(self.audits("recurso_privado").latest("id").summary, "Quitó un recurso (predicación) de la página pública")
        self.assertNotContains(self.get(), "Sermón solo para la iglesia")
        self.assertTrue(MediaItem.objects.filter(pk=self.private_sermon.pk, status="publicado").exists())  # still in the library
        self.client.post(reverse("sitio:media_hide", args=[self.private_sermon.pk]))
        self.assertEqual(self.audits("recurso_privado").count(), 1)
        # With the home unpublished the message says when it will be seen.
        services.unpublish(self.church, self.admin)
        self.assertContains(self.client.post(show, follow=True), "se verá en la página pública de la iglesia cuando la publiques")

    def test_a_resource_of_another_church_or_a_retired_one_is_never_marked(self):
        for name in ("media_show", "media_hide"):
            self.assertEqual(self.client.post(reverse(f"sitio:{name}", args=[self.sermon_b.pk])).status_code, 404)
        self.assertTrue(PublicMedia.objects.filter(item=self.sermon_b, church=self.church_b).exists())
        resources.retire_media(self.private_sermon, self.admin)
        response = self.client.post(reverse("sitio:media_show", args=[self.private_sermon.pk]), follow=True)
        self.assertContains(response, "Este recurso está retirado")
        self.assertFalse(PublicMedia.objects.filter(item=self.private_sermon).exists())
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.post(reverse("sitio:media_show", args=[self.sermon.pk])).status_code, 403)

    def test_the_new_audit_words_are_in_the_list(self):
        from people.audit import ACTION_LABELS

        for action in ("sitio_modificado", "sitio_publicado", "sitio_despublicado", "recurso_publico", "recurso_privado"):
            self.assertIn(action, ACTION_LABELS)


@override_settings(DEBUG=True)
class SeedTests(TestCase):
    def test_the_plain_seed_adds_nothing_of_the_public_home(self):
        call_command("seed_community_demo", password="x", stdout=StringIO())
        self.assertFalse(PublicHome.objects.exists() or ServiceTime.objects.exists() or PublicMedia.objects.exists())
        self.assertFalse(EventPage.objects.filter(public=True).exists())
        self.assertEqual(Church.objects.get(slug="iglesia-demo").contact_email, "")
        self.assertEqual(self.client.get("/iglesia/iglesia-demo/").status_code, 404)

    def test_with_eventos_the_demo_church_has_a_published_home(self):
        for _ in range(2):  # twice: the same rows
            call_command("seed_community_demo", "--eventos", password="x", stdout=StringIO())
        church = Church.objects.get(slug="iglesia-demo")
        self.assertTrue(PublicHome.objects.get(church=church).published)
        self.assertEqual(ServiceTime.objects.filter(church=church).count(), 2)
        self.assertEqual(PublicMedia.objects.filter(church=church).count(), 1)
        self.assertTrue(EventPage.objects.get(event__slug="convivencia-de-jovenes-demo").public)
        self.assertEqual(ChurchAuditEvent.objects.filter(church=church, action="sitio_publicado").count(), 1)
        response = self.client.get("/iglesia/iglesia-demo/")
        for text in ("Iglesia de demostración", "Reunión general", "Oración y estudio", "Convivencia de jóvenes", "La vid y las ramas",
                     "contacto@demo.invalid"):
            self.assertContains(response, text)
        for text in ("Visita", "Tutora Demo", "Valeria", "Cambio de horario"):
            self.assertNotContains(response, text)
