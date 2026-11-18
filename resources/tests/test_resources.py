"""Devocional y Recursos: the church writes, each person reads, and what a person read is only theirs."""

import re
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from core.templatetags.shell import icon
from groups.templatetags.community import weekday_date
from groups.tests.base import CommunityFixture
from people import flags
from people.models import ChurchAuditEvent
from resources import services
from resources.models import Devotional, DevotionalRead, MediaItem

TODAY_URL = reverse("resources:devotional_today")
PAST = reverse("resources:devotional_list")
DEV_ADMIN = reverse("resources:devotional_admin")
DEV_CREATE = reverse("resources:devotional_create")
MEDIA = reverse("resources:media_list")
MEDIA_ADMIN = reverse("resources:media_admin")
MEDIA_CREATE = reverse("resources:media_create")
BODY = "Permanezcan en mí, y yo permaneceré en ustedes.\n\nLa rama no da fruto por sí misma."
YOUTUBE = "https://www.youtube.com/watch?v=abc123"
# The person's own mark, once they said they read it (a pill: the word with its mark).
READ_MARK = '<span class="status status--done"><span class="status__mark" aria-hidden="true"></span>Leído</span>'


def dev_url(name, devotional):
    return reverse(f"resources:devotional_{name}", args=[devotional.pk])


def main(response) -> str:
    """The page without the shell around it."""
    html = response.content.decode()
    return html[html.index("<main"):html.index("</main>")]


def media_url(name, item):
    return reverse(f"resources:media_{name}", args=[item.pk])


class ResourcesFixture(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.today = timezone.localdate()

    def devotional(self, day=None, *, church=None, actor=None, **changes):
        values = {"title": "La vid verdadera", "passage": "Juan 15:1-8", "body": BODY, "author_name": "Ana López", "publish": True}
        return services.save_devotional(
            church or self.church, actor or self.admin, date=day or self.today, **{**values, **changes}
        )

    def media(self, *, church=None, actor=None, **changes):
        values = {"kind": "predicacion", "title": "Justificados por la fe", "url": YOUTUBE, "date": self.today}
        return services.save_media(church or self.church, actor or self.admin, **{**values, **changes})

    def dev_data(self, **changes):
        return {"date": self.today.isoformat(), "title": "La vid verdadera", "passage": "Juan 15:1-8", "body": BODY,
                "author_name": "Ana López", "publish": "on", **changes}

    def media_data(self, **changes):
        return {"kind": "predicacion", "title": "Justificados por la fe", "url": YOUTUBE, "date": self.today.isoformat(),
                "speaker": "Ana López", "series": "Romanos", "description": "", **changes}


class DevotionalTests(ResourcesFixture):
    # ---- reading --------------------------------------------------------------------------------
    def test_a_member_reads_todays_devotional_and_marks_it_read_once(self):
        devotional = self.devotional()
        self.client.force_login(self.member_user)
        page = self.client.get(TODAY_URL)
        html = page.content.decode()
        self.assertEqual(html.count("<h1"), 1)
        self.assertContains(page, '<h1 class="voice page-head__title">La vid verdadera</h1>', html=True)
        # Its verse (the passage, in the serif of a verse), then its day (today) and who signs it, each a fact with its mark.
        self.assertContains(page, f'{icon("calendario", "sm")}Hoy, {weekday_date(self.today)}</li>')
        self.assertContains(page, f'{icon("personas", "sm")}<span class="visually-hidden">Escrito por </span>Ana López</li>')
        self.assertContains(page, '<p class="verso devocional__verso"><span class="visually-hidden">Lectura: </span>Juan 15:1-8</p>', html=True)
        self.assertContains(page, '<div class="block prose">')
        self.assertContains(page, "La rama no da fruto por sí misma.")
        self.assertContains(page, "Marcar como leído</button>")
        self.assertEqual(html.count("btn--primary"), 1)
        self.assertContains(page, "Devocionales anteriores")
        self.assertContains(page, "Esta marca es solo para ti: nadie más ve qué has leído.")
        self.assertNotContains(page, READ_MARK)
        self.assertNotContains(page, "Administrar los devocionales")

        for _ in range(2):  # twice: the second one changes nothing
            response = self.client.post(dev_url("read", devotional), follow=True)
            self.assertRedirects(response, TODAY_URL)
            self.assertContains(response, READ_MARK, count=1)
            self.assertNotContains(response, "Marcar como leído</button>")
        self.assertEqual(DevotionalRead.objects.filter(devotional=devotional, person=self.member).count(), 1)
        self.assertEqual(self.client.get(dev_url("read", devotional)).status_code, 405)
        self.assertContains(self.client.get(PAST), "Leído")
        self.assertContains(self.client.get(dev_url("detail", devotional)), READ_MARK)

    def test_mark_read_is_idempotent_and_refuses_what_is_not_readable(self):
        devotional = self.devotional()
        self.assertTrue(services.mark_read(devotional, self.member))
        self.assertFalse(services.mark_read(devotional, self.member))
        with self.assertRaises(services.ResourceError):
            services.mark_read(devotional, self.member_b)
        draft = self.devotional(self.today - timedelta(days=1), publish=False)
        with self.assertRaises(services.ResourceError):
            services.mark_read(draft, self.member)
        self.assertEqual(DevotionalRead.objects.count(), 1)

    def test_a_draft_and_a_future_one_are_invisible_to_a_member_and_listed_for_the_administration(self):
        draft = self.devotional(self.today - timedelta(days=1), title="Borrador de ayer", publish=False)
        future = self.devotional(self.today + timedelta(days=1), title="El de mañana")
        self.client.force_login(self.member_user)
        page = self.client.get(TODAY_URL)
        self.assertContains(page, "Hoy no hay devocional.")
        self.assertContains(page, "Devocionales anteriores")
        self.assertEqual(page.content.decode().count("<h1"), 1)
        self.assertNotContains(page, "Borrador de ayer")
        page = self.client.get(PAST)
        self.assertNotContains(page, "Borrador de ayer")
        self.assertNotContains(page, "El de mañana")
        self.assertContains(page, "Tu iglesia todavía no publica devocionales.")
        for item in (draft, future):
            self.assertEqual(self.client.get(dev_url("detail", item)).status_code, 404)
            self.assertEqual(self.client.post(dev_url("read", item)).status_code, 404)
        self.assertEqual(DevotionalRead.objects.count(), 0)

        self.client.force_login(self.admin)
        page = self.client.get(DEV_ADMIN)
        for words in ("Borrador de ayer", "El de mañana", "Borrador", "Programado", "Escribir un devocional"):
            self.assertContains(page, words)
        self.assertNotContains(self.client.get(PAST), "El de mañana")
        page = self.client.get(dev_url("detail", draft))
        self.assertContains(page, "Borrador</span></li>")
        self.assertContains(page, '<p class="aside-note">Solo lo ve la administración de la iglesia.</p>', html=True)
        self.assertNotContains(page, "Marcar como leído</button>")
        page = self.client.get(dev_url("detail", future))
        self.assertContains(page, "Programado</span></li>")
        self.assertContains(page, '<p class="aside-note">Hasta su día solo lo ve la administración de la iglesia.</p>', html=True)
        self.assertNotContains(page, "Marcar como leído</button>")
        self.assertEqual(self.client.post(dev_url("read", future)).status_code, 404)

    def test_without_one_today_the_newest_of_the_last_seven_days_is_shown(self):
        self.devotional(self.today - timedelta(days=8), title="Demasiado viejo")
        self.assertIsNone(services.today_devotional(self.church, self.member))
        self.devotional(self.today - timedelta(days=7), title="El de hace una semana")
        recent = self.devotional(self.today - timedelta(days=2), title="El de antier")
        self.devotional(self.today - timedelta(days=1), title="Borrador de ayer", publish=False)
        self.devotional(self.today + timedelta(days=1), title="El de mañana")
        found = services.today_devotional(self.church, self.member)
        self.assertEqual((found, found.is_today, found.is_read), (recent, False, False))
        services.mark_read(recent, self.member)
        self.assertTrue(services.today_devotional(self.church, self.member).is_read)
        self.assertFalse(services.today_devotional(self.church, self.leader).is_read)

        self.client.force_login(self.member_user)
        page = self.client.get(TODAY_URL)
        self.assertContains(page, "El de antier")
        self.assertContains(page, "Hoy no hay devocional nuevo.")
        # Its own day, not today's.
        self.assertContains(page, f'{icon("calendario", "sm")}{weekday_date(recent.date)}')
        self.assertNotContains(page, "Hoy, ")
        self.assertContains(page, READ_MARK)
        page = self.client.get(PAST)
        self.assertContains(page, "Demasiado viejo")
        self.assertEqual(page.content.decode().count("status--done"), 1)

        todays = self.devotional(title="El de hoy")
        found = services.today_devotional(self.church, self.member)
        self.assertEqual((found, found.is_today, found.is_read), (todays, True, False))
        yesterday = self.today - timedelta(days=1)
        self.assertEqual(services.today_devotional(self.church, self.member, today=yesterday), recent)
        self.assertIsNone(services.today_devotional(self.church_b, self.member_b))

    def test_the_archive_is_paged_by_fifty_newest_first(self):
        for back in range(52):
            self.devotional(self.today - timedelta(days=back), title=f"Devocional número {back:02d}.")
        self.client.force_login(self.member_user)
        html = self.client.get(PAST).content.decode()
        self.assertEqual(html.count('class="row row--lead"'), 50)
        self.assertIn('<ul class="dates rows">', html)
        self.assertIn("date-block", html)
        self.assertLess(html.index("Devocional número 00."), html.index("Devocional número 49."))
        self.assertNotIn("Devocional número 50.", html)
        self.assertIn("Página 1 de 2. Del 1 al 50 de 52.", html)
        self.assertContains(self.client.get(PAST + "?pagina=2"), "Devocional número 51.")

    # ---- writing --------------------------------------------------------------------------------
    def test_the_administration_writes_a_draft_and_then_publishes_it(self):
        self.devotional(self.today, title="Ya hay uno hoy")
        self.devotional(self.today + timedelta(days=1), title="Y uno mañana", publish=False)
        self.client.force_login(self.admin)
        page = self.client.get(DEV_CREATE)
        free = self.today + timedelta(days=2)
        self.assertContains(page, f'value="{free.isoformat()}"')  # the first day without a devotional
        self.assertContains(page, "Publicarlo")
        self.assertContains(page, "Guardar el devocional")
        self.assertEqual(page.content.decode().count("btn--primary"), 1)

        data = self.dev_data(date=free.isoformat(), title="Nuevo")
        del data["publish"]
        response = self.client.post(DEV_CREATE, data, follow=True)
        self.assertRedirects(response, DEV_ADMIN)
        self.assertContains(response, "Guardamos el borrador")
        written = Devotional.objects.get(title="Nuevo")
        self.assertEqual((written.status, written.church, written.created_by, written.body), ("borrador", self.church, self.admin, BODY))

        page = self.client.get(dev_url("edit", written))
        self.assertContains(page, "Permanezcan en mí")
        self.assertNotContains(page, 'name="publish" checked')
        response = self.client.post(dev_url("edit", written), self.dev_data(date=free.isoformat(), title="Nuevo"), follow=True)
        self.assertContains(response, "Se publicará el")
        written.refresh_from_db()
        self.assertEqual(written.state, "programado")
        actions = list(ChurchAuditEvent.objects.filter(object_type="Devotional", object_id=str(written.pk)).order_by("id").values_list("action", flat=True))
        self.assertEqual(actions, ["devocional_modificado", "devocional_publicado"])

    def test_a_day_has_one_devotional(self):
        first = self.devotional()
        self.client.force_login(self.admin)
        response = self.client.post(DEV_CREATE, self.dev_data(title="Otro para hoy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ya hay un devocional para ese día.")
        other = self.devotional(self.today + timedelta(days=3), title="Otro día")
        response = self.client.post(dev_url("edit", other), self.dev_data(title="Otro día"))
        self.assertContains(response, "Ya hay un devocional para ese día.")
        with self.assertRaises(services.ResourceError):
            self.devotional(title="Por el servicio")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Devotional.objects.create(church=self.church, date=self.today, title="Directo", body="x")
        self.assertEqual(Devotional.objects.filter(church=self.church, date=self.today).get(), first)
        # Editing the same one on its own day is not a second one; another church has its own days.
        self.assertRedirects(self.client.post(dev_url("edit", first), self.dev_data(title="Corregido")), DEV_ADMIN)
        self.devotional(church=self.church_b, actor=self.admin_b, title="El de la iglesia B")

    def test_the_body_is_escaped_and_an_over_long_one_is_refused(self):
        devotional = self.devotional(body="Primera línea\n\n<script>alert('x')</script> <b>negritas</b>")
        self.client.force_login(self.member_user)
        page = self.client.get(TODAY_URL)
        self.assertNotContains(page, "<script>alert")
        self.assertNotContains(page, "<b>negritas</b>")
        self.assertContains(page, "&lt;script&gt;")
        self.assertContains(page, "<p>Primera línea</p>")

        self.client.force_login(self.admin)
        response = self.client.post(dev_url("edit", devotional), self.dev_data(body="a" * 3001))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "hasta 3000 caracteres y este tiene 3001")
        with self.assertRaises(services.ResourceError):
            self.devotional(self.today + timedelta(days=1), body="a" * 3001)
        # A line break counts once, however the browser sends it.
        self.assertRedirects(self.client.post(dev_url("edit", devotional), self.dev_data(body="a" * 2998 + "\r\n" + "b")), DEV_ADMIN)
        devotional.refresh_from_db()
        self.assertEqual(len(devotional.body), 3000)

    # ---- privacy of reading ---------------------------------------------------------------------
    def test_what_a_person_read_is_theirs_and_no_page_of_the_administration_counts_readers(self):
        devotional = self.devotional(title="Título sin cifras")
        older = self.devotional(self.today - timedelta(days=1), title="Otro título sin cifras")
        pages = [TODAY_URL, PAST, DEV_ADMIN, DEV_CREATE, MEDIA, MEDIA_ADMIN] + [
            dev_url(name, item) for item in (devotional, older) for name in ("detail", "edit")
        ]

        def seen_by_the_administration():
            self.client.force_login(self.admin)
            return [re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", main(self.client.get(url))) for url in pages]

        before = seen_by_the_administration()
        for person in (self.member, self.leader, self.asked):
            services.mark_read(devotional, person)
        services.mark_read(older, self.member)
        # Three people read: every page of the administration is, to the letter, what it was.
        self.assertEqual(seen_by_the_administration(), before)

        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.get(PAST).content.decode().count("Leído"), 1)
        self.client.force_login(self.coord1)  # read nothing
        self.assertNotContains(self.client.get(PAST), "Leído")
        self.assertContains(self.client.get(TODAY_URL), "Marcar como leído</button>")

        self.client.force_login(self.admin)
        audit_before = ChurchAuditEvent.objects.count()
        readers = [self.member, self.leader, self.asked]
        for url in pages:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            html = main(response)
            for person in readers:
                self.assertNotIn(person.first_name, html, url)
                self.assertNotIn(person.last_name, html, url)
            for words in ("Leído", "leyeron", "lectores", "leído por", "lo han leído", "Ya lo leíste", "racha", "%"):
                self.assertNotIn(words, html, url)
        # The reading itself leaves no audit row, and no audit row speaks of reading.
        self.assertEqual(ChurchAuditEvent.objects.count(), audit_before)
        self.assertFalse(ChurchAuditEvent.objects.filter(summary__icontains="ley").exists())
        self.assertFalse(ChurchAuditEvent.objects.filter(object_type="DevotionalRead").exists())

    def test_the_audit_rows_name_the_day_and_never_the_words(self):
        self.client.force_login(self.admin)
        self.client.post(DEV_CREATE, self.dev_data(title="Título reservado"))
        devotional = Devotional.objects.get()
        self.client.post(dev_url("edit", devotional), self.dev_data(title="Título reservado", body="Otro texto reservado"))
        rows = list(ChurchAuditEvent.objects.filter(action__startswith="devocional_").order_by("id"))
        self.assertEqual([row.action for row in rows], ["devocional_publicado", "devocional_modificado"])
        self.assertEqual(rows[0].summary, f"Publicó el devocional del {self.today:%d/%m/%Y}")
        for row in rows:
            stored = f"{row.summary} {row.metadata}"
            for secret in ("Título reservado", "Permanezcan", "Otro texto", "Juan 15", "Ana López"):
                self.assertNotIn(secret, stored)
            self.assertEqual((row.church, row.actor, row.metadata), (self.church, self.admin, {}))


class MediaTests(ResourcesFixture):
    def test_only_an_https_address_is_taken(self):
        self.client.force_login(self.admin)
        for bad in ("http://www.youtube.com/watch?v=abc", "ftp://example.org/audio.mp3", "javascript:alert(1)", "https://"):
            response = self.client.post(MEDIA_CREATE, self.media_data(url=bad))
            self.assertEqual(response.status_code, 200, bad)
            self.assertEqual(MediaItem.objects.count(), 0, bad)
            with self.assertRaises(services.ResourceError):
                self.media(url=bad)
        response = self.client.post(MEDIA_CREATE, self.media_data(url="http://www.youtube.com/watch?v=abc"))
        self.assertContains(response, "El enlace debe empezar con https://.")
        self.assertEqual(MediaItem.objects.count(), 0)

        response = self.client.post(MEDIA_CREATE, self.media_data(), follow=True)
        self.assertRedirects(response, MEDIA_ADMIN)
        self.assertContains(response, "Publicaste el recurso.")
        item = MediaItem.objects.get()
        self.assertEqual((item.church, item.url, item.status, item.created_by), (self.church, YOUTUBE, "publicado", self.admin))

    def test_the_link_says_where_it_goes_and_nothing_is_embedded(self):
        self.media(title="En YouTube", speaker="Ana López", series="Romanos", date=date(2026, 11, 15))
        self.media(title="Corto de YouTube", url="https://youtu.be/abc123")
        self.media(title="En Spotify", kind="musica", url="https://open.spotify.com/track/abc")
        self.media(title="En otro sitio", kind="estudio", url="https://iglesia.example.org/estudio.pdf")
        self.media(title="Casi YouTube", kind="otro", url="https://notyoutube.com/x")
        labels = {item.title: item.link_label for item in MediaItem.objects.all()}
        self.assertEqual(labels, {
            "En YouTube": "Ver en YouTube", "Corto de YouTube": "Ver en YouTube", "En Spotify": "Escuchar en Spotify",
            "En otro sitio": "Abrir el recurso", "Casi YouTube": "Abrir el recurso",
        })
        self.client.force_login(self.member_user)
        page = self.client.get(MEDIA)
        html = page.content.decode()
        self.assertEqual(html.count("<h1"), 1)
        self.assertContains(
            page,
            f'<a class="btn btn--secondary btn--small" href="{YOUTUBE}" rel="noopener noreferrer">'
            'Ver en YouTube<span class="visually-hidden">: En YouTube</span></a>',
            html=True,
        )
        # What it is, whose, its series and its day, each a fact with its mark.
        self.assertContains(
            page,
            f'<ul class="meta"><li>Predicación</li><li>{icon("personas", "sm")}Ana López</li>'
            f'<li>{icon("lista", "sm")}<a href="?serie=Romanos">Serie «Romanos»</a></li>'
            f'<li>{icon("calendario", "sm")}15 de noviembre de 2026</li></ul>',
            html=True,
        )
        self.assertContains(page, "Escuchar en Spotify")
        self.assertContains(page, "Abrir el recurso")
        self.assertContains(page, 'class="row row--lead row--stack"', count=5)
        # The only drawing on the page is the design's own marks (aria-hidden icons); nothing of the resource's site.
        # Decorative marks (a fact's icon, a row's leading mark) are drawn here; nothing from another site is
        without_marks = re.sub(r'<svg class="icon( icon--sm)?" [^>]*aria-hidden="true" focusable="false">.*?</svg>', "", main(page), flags=re.S)
        for forbidden in ("<iframe", "<embed", "<video", "<audio", "<object", "<script", "<svg", "·", 'target="_blank"'):
            self.assertNotIn(forbidden, without_marks)
        self.assertNotContains(page, "Administrar los recursos")
        self.assertNotContains(page, "Agregar un recurso")

    def test_search_only_finds_published_media_of_the_members_church(self):
        self.client.force_login(self.member_user)
        match = self.media(title="Camino nuevo", speaker="Ana Torres", series="Cartas")
        library = self.client.get(MEDIA)
        self.assertContains(library, f'<form class="search" method="get" action="{MEDIA}" role="search">')
        # The field keeps its label (read by a screen reader; the placeholder says the same on screen).
        self.assertContains(library, '<label class="visually-hidden" for="resource-search">Buscar por título, persona o serie</label>', html=True)
        self.assertContains(library, '<input class="input search__input" id="resource-search" type="search" name="q" maxlength="160" value="" autocomplete="off" spellcheck="false" placeholder="Buscar por título, persona o serie">', html=True)
        other = self.media(title="La vid")
        services.retire_media(self.media(title="Camino anterior", kind="musica"), self.admin)
        saved_filter = self.client.get(MEDIA, {"tipo": "musica"})
        self.assertCountEqual(saved_filter.context["items"], [match, other])
        self.assertContains(self.client.get(MEDIA, {"q": "desconocido"}), "No encontramos recursos")
        self.media(church=self.church_b, actor=self.admin_b, title="Camino ajeno")
        for query in ("camino", "torres", "cartas", "cam\x00ino"):
            response = self.client.get(MEDIA, {"q": query})
            self.assertEqual(list(response.context["items"]), [match])
        self.media(title="Alabanza", kind="musica")
        empty = self.client.get(MEDIA, {"q": "camino", "tipo": "musica"})
        self.assertContains(empty, "No encontramos recursos")
        self.assertContains(empty, 'href="?q=camino"')
        self.assertContains(empty, '<input type="hidden" name="tipo" value="musica">', html=True)
        series_page = self.client.get(MEDIA, {"serie": "Cartas"})
        self.assertContains(series_page, '<input type="hidden" name="serie" value="Cartas">', html=True)
        self.assertEqual(empty.context["total"], 1)
        self.assertEqual({kind["value"]: kind["count"] for kind in empty.context["kinds"]}, {"musica": 0, "predicacion": 1})

    def test_the_filters_by_kind_and_by_series(self):
        self.client.force_login(self.member_user)
        page = self.client.get(MEDIA)
        self.assertContains(page, "Tu iglesia todavía no comparte recursos aquí.")
        self.media(title="Romanos uno", series="Romanos")
        self.media(title="Romanos dos", series="Romanos", date=self.today - timedelta(days=7))
        page = self.client.get(MEDIA)
        self.assertNotContains(page, 'class="filters"')  # one kind only: no filter
        self.media(title="Canto nuevo", kind="musica", url="https://open.spotify.com/track/abc")
        self.media(title="Canto retirado", kind="otro", url="https://example.org/x")
        services.retire_media(MediaItem.objects.get(title="Canto retirado"), self.admin)

        page = self.client.get(MEDIA)
        self.assertContains(page, '<ul class="filters" aria-label="Tipo de recurso">')
        self.assertContains(page, "Predicaciones")
        self.assertContains(page, "?tipo=musica")
        self.assertNotContains(page, "Otros recursos")  # its only item is retired
        self.assertNotContains(page, "Estudios")
        html = page.content.decode()
        self.assertLess(html.index("Romanos uno"), html.index("Romanos dos"))  # newest first

        page = self.client.get(MEDIA + "?tipo=musica")
        self.assertContains(page, "Canto nuevo")
        self.assertNotContains(page, "Romanos uno")
        self.assertContains(self.client.get(MEDIA + "?tipo=inventado"), "Romanos uno")

        self.assertContains(self.client.get(MEDIA), "?serie=Romanos")
        page = self.client.get(MEDIA + "?serie=Romanos")
        self.assertContains(
            page,
            f'<ul class="filters" aria-label="Serie"><li><a href="{MEDIA}">Todos</a></li>'
            '<li><a href="?serie=Romanos" aria-current="page">Serie «Romanos»</a></li></ul>',
            html=True,
        )
        self.assertContains(page, "Romanos uno")
        self.assertContains(page, "Romanos dos")
        self.assertNotContains(page, "Canto nuevo")
        self.assertNotContains(page, "Ver la serie")
        page = self.client.get(MEDIA + "?serie=<script>")
        self.assertContains(page, "No hay recursos de esa serie.")
        self.assertNotContains(page, "<script>")

    def test_the_list_is_paged_by_fifty(self):
        for number in range(52):
            self.media(title=f"Recurso {number}", date=self.today - timedelta(days=number))
        self.client.force_login(self.member_user)
        page = self.client.get(MEDIA)
        self.assertContains(page, 'class="row row--lead row--stack"', count=50)
        self.assertContains(page, "Página 1 de 2. Del 1 al 50 de 52.")
        self.assertContains(self.client.get(MEDIA + "?pagina=2"), 'class="row row--lead row--stack"', count=2)

    def test_retiring_hides_it_and_nothing_is_deleted(self):
        item = self.media()
        self.client.force_login(self.admin)
        page = self.client.get(MEDIA_ADMIN)
        self.assertContains(page, f'<a href="{media_url("edit", item)}">Justificados por la fe</a>', html=True)
        self.assertContains(page, "Agregar un recurso")
        # Retiring is on the resource's own page, and asks first.
        self.assertContains(self.client.get(media_url("edit", item)), f'<a class="btn btn--link btn--quieto plan__dejar" href="{media_url("retire", item)}">Retirar el recurso</a>', html=True)
        page = self.client.get(media_url("retire", item))
        self.assertContains(page, "¿Retirar el recurso")
        self.assertEqual(MediaItem.objects.get().status, "publicado")
        response = self.client.post(media_url("retire", item), follow=True)
        self.assertRedirects(response, MEDIA_ADMIN)
        self.assertContains(response, "Retiraste el recurso.")
        self.assertContains(response, "Retirado")
        self.client.post(media_url("retire", item))  # again: nothing new
        self.assertEqual(ChurchAuditEvent.objects.filter(action="recurso_retirado").count(), 1)
        self.assertEqual(MediaItem.objects.get().status, "retirado")
        self.assertEqual(self.client.get(media_url("edit", item)).status_code, 404)
        with self.assertRaises(services.ResourceError):
            self.media(item=item, title="De vuelta")
        self.assertEqual(services.latest_media(self.church), [])
        self.client.force_login(self.member_user)
        self.assertNotContains(self.client.get(MEDIA), "Justificados por la fe")

    def test_editing_a_resource_and_its_over_long_description(self):
        item = self.media()
        self.client.force_login(self.admin)
        response = self.client.post(media_url("edit", item), self.media_data(description="a" * 601))
        self.assertContains(response, "hasta 600 caracteres y esta tiene 601")
        response = self.client.post(media_url("edit", item), self.media_data(title="Corregido", url="http://example.org/"))
        self.assertContains(response, "El enlace debe empezar con https://.")
        self.assertRedirects(self.client.post(media_url("edit", item), self.media_data(title="Corregido", description="Dos frases.")), MEDIA_ADMIN)
        item.refresh_from_db()
        self.assertEqual((item.title, item.series, item.description), ("Corregido", "Romanos", "Dos frases."))

    def test_the_audit_rows_carry_no_title_no_body_and_no_address(self):
        self.client.force_login(self.admin)
        self.client.post(MEDIA_CREATE, self.media_data(description="Descripción reservada"))
        item = MediaItem.objects.get()
        self.client.post(media_url("edit", item), self.media_data(title="Otro título reservado"))
        self.client.post(media_url("retire", item))
        rows = list(ChurchAuditEvent.objects.filter(action__startswith="recurso_").order_by("id"))
        self.assertEqual([row.action for row in rows], ["recurso_publicado", "recurso_modificado", "recurso_retirado"])
        self.assertEqual(rows[0].summary, "Publicó un recurso (predicación)")
        for row in rows:
            stored = f"{row.summary} {row.metadata}"
            for secret in ("Justificados", "reservad", "youtube", "https", "abc123", "Ana López", "Romanos"):
                self.assertNotIn(secret, stored)
            self.assertEqual((row.church, row.actor, row.metadata, row.object_id), (self.church, self.admin, {}, str(item.pk)))

    def test_latest_media_returns_the_newest_published_of_the_church(self):
        self.assertEqual(services.latest_media(self.church), [])
        items = [self.media(title=f"Recurso {n}", date=self.today - timedelta(days=n)) for n in range(5)]
        same_day = self.media(title="El último del mismo día", date=self.today)
        services.retire_media(items[1], self.admin)
        theirs = self.media(church=self.church_b, actor=self.admin_b, title="De la iglesia B")
        self.assertEqual(services.latest_media(self.church), [same_day, items[0], items[2]])
        self.assertEqual(services.latest_media(self.church, limit=1), [same_day])
        self.assertEqual(len(services.latest_media(self.church, limit=10)), 5)
        self.assertEqual(services.latest_media(self.church_b), [theirs])


class WhoMayTests(ResourcesFixture):
    def urls(self, devotional, item):
        admin_only = [DEV_ADMIN, DEV_CREATE, dev_url("edit", devotional), MEDIA_ADMIN, MEDIA_CREATE,
                      media_url("edit", item), media_url("retire", item)]
        with_id = [dev_url("detail", devotional), dev_url("read", devotional), dev_url("edit", devotional),
                   media_url("edit", item), media_url("retire", item)]
        everything = [TODAY_URL, PAST, MEDIA, dev_url("detail", devotional), dev_url("read", devotional)] + admin_only
        return admin_only, with_id, everything

    def test_a_member_cannot_open_any_page_of_the_administration(self):
        devotional, item = self.devotional(), self.media()
        admin_only, _, _ = self.urls(devotional, item)
        for user in (self.member_user, self.leader_user):
            self.client.force_login(user)
            for url in admin_only:
                self.assertEqual(self.client.get(url).status_code, 403, url)
                self.assertEqual(self.client.post(url, {**self.dev_data(title="Cambiado"), **self.media_data(title="Cambiado")}).status_code, 403, url)
        devotional.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual((devotional.title, item.title, item.status), ("La vid verdadera", "Justificados por la fe", "publicado"))
        self.assertEqual((Devotional.objects.count(), MediaItem.objects.count()), (1, 1))

    def test_another_church_does_not_exist(self):
        devotional, item = self.devotional(), self.media()
        _, with_id, _ = self.urls(devotional, item)
        for user in (self.admin_b, self.member_b_user):
            self.client.force_login(user)
            for url in (dev_url("detail", devotional), dev_url("read", devotional)):
                self.assertEqual(self.client.get(url).status_code, 405 if url.endswith("leido/") else 404, url)
                self.assertEqual(self.client.post(url).status_code, 404, url)
            for url in (TODAY_URL, PAST, MEDIA):
                page = self.client.get(url)
                self.assertNotContains(page, "La vid verdadera")
                self.assertNotContains(page, "Justificados por la fe")
        self.client.force_login(self.admin_b)
        for url in with_id:
            if not url.endswith("leido/"):
                self.assertEqual(self.client.get(url).status_code, 404, url)
            self.assertEqual(self.client.post(url, {**self.dev_data(title="Cambiado"), **self.media_data(title="Cambiado")}).status_code, 404, url)
        for url in (DEV_ADMIN, MEDIA_ADMIN):
            page = self.client.get(url)
            self.assertNotContains(page, "La vid verdadera")
            self.assertNotContains(page, "Justificados por la fe")
        # What church B writes stays in church B.
        self.client.post(DEV_CREATE, self.dev_data(title="El de la iglesia B"))
        self.client.post(MEDIA_CREATE, self.media_data(title="Recurso de la iglesia B"))
        self.assertEqual(Devotional.objects.get(title="El de la iglesia B").church, self.church_b)
        self.assertEqual(MediaItem.objects.get(title="Recurso de la iglesia B").church, self.church_b)
        self.client.force_login(self.admin)
        for url in (TODAY_URL, PAST, DEV_ADMIN, MEDIA, MEDIA_ADMIN):
            self.assertNotContains(self.client.get(url), "de la iglesia B")
        devotional.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual((devotional.title, item.title, item.status), ("La vid verdadera", "Justificados por la fe", "publicado"))
        self.assertEqual(DevotionalRead.objects.count(), 0)
        with self.assertRaises(services.ResourceError):
            services.save_devotional(self.church_b, self.admin_b, date=self.today + timedelta(days=5), title="x", body="x", devotional=devotional)
        with self.assertRaises(services.ResourceError):
            self.media(church=self.church_b, actor=self.admin_b, item=item)

    def test_the_module_off_hides_every_page(self):
        devotional, item = self.devotional(), self.media()
        _, _, everything = self.urls(devotional, item)
        flags.set_enabled(self.church, "community", False)
        for user in (self.admin, self.member_user):
            self.client.force_login(user)
            for url in everything:
                self.assertEqual(self.client.get(url).status_code, 404, url)
                self.assertEqual(self.client.post(url, {**self.dev_data(), **self.media_data()}).status_code, 404, url)
        self.assertEqual((DevotionalRead.objects.count(), MediaItem.objects.get().status), (0, "publicado"))

    def test_signed_out_reads_nothing(self):
        devotional, item = self.devotional(), self.media()
        _, _, everything = self.urls(devotional, item)
        for url in everything:
            response = self.client.get(url)
            self.assertIn(response.status_code, (302, 404), url)
            self.assertNotContains(response, "La vid verdadera", status_code=response.status_code)


class SeriesFilterNulTests(ResourcesFixture):
    def test_a_nul_in_the_series_filter_is_dropped(self):
        """PostgreSQL refuses NUL in a text parameter: the filter must never carry one (review of wave 8)."""
        self.client.force_login(self.member_user)
        response = self.client.get(MEDIA, {"serie": "Car\x00tas"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("\x00", response.context["series"])
