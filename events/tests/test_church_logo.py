"""The church's logo on the camp screens: the event bars, the campero's screens from "Hola, …", the title
slide of the presentation (Cal and Noche) and the header of the cutoff PDF. Images are made by the tests."""

from __future__ import annotations

import io
import os
import re
import zlib

from django.conf import settings
from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from core.test_church_logo import MediaRootMixin, png_bytes, png_file, svg_bytes
from events.middleware import PrivateResponseMiddleware, is_church_logo
from events.models import Interview

from .base import CampFixture

WORDMARK = '<span class="wordmark">Emmaus</span>'


class EventBarLogoTests(MediaRootMixin, CampFixture):
    def test_the_bars_of_both_shells_show_the_events_church(self):
        self.give_logo(self.church, png_bytes(300, 100))
        home = self.url("home")
        for user, page in ((self.coord1, "coord_home"), (self.pastor, "pastor_home"), (self.admin, "admin_home")):
            with self.subTest(user=user.email):
                client = self.login(user, Client())
                if user == self.coord1:
                    self.choose_station(client)
                response = client.get(self.url(page))
                html = response.content.decode()
                self.assertIn(
                    f'<a class="brand brand--bar" href="{home}"><img class="brand__logo brand__logo--bar" '
                    f'src="/iglesia/{self.church.slug}/logotipo/claro/?v=',
                    html,
                )
                self.assertIn('alt="Iglesia de prueba" width="96" height="32">', html)
                head = html.split('class="menu-panel__head">', 1)[1].split("<button", 1)[0]
                self.assertIn('<img class="brand__logo brand__logo--bar"', head)
                self.assertEqual(len(re.findall(r"<h1\b", html)), 1)
                self.assertIn("· Emmaus</title>", html)

    def test_without_a_logo_the_bars_are_unchanged(self):
        home = self.url("home")
        for user, page in ((self.coord1, "coord_home"), (self.pastor, "pastor_home")):
            with self.subTest(user=user.email):
                client = self.login(user, Client())
                if user == self.coord1:
                    self.choose_station(client)
                response = client.get(self.url(page))
                self.assertContains(response, f'<div class="bar__inner">\n    <a class="wordmark" href="{home}">Emmaus</a>\n')
                self.assertContains(response, f'<div class="menu-panel__head">\n    {WORDMARK}\n')
                self.assertNotContains(response, "brand__logo")

    def test_each_event_shows_its_own_church(self):
        from core.models import Church

        other = Church.objects.create(name="Otra iglesia", slug="otra")
        self.give_logo(other, png_bytes())
        self.give_logo(self.church, png_bytes(240, 80))
        client = self.login(self.pastor, Client())
        response = client.get(self.url("pastor_home"))
        self.assertContains(response, f"/iglesia/{self.church.slug}/logotipo/claro/")
        self.assertNotContains(response, "/iglesia/otra/")


class KioskLogoTests(MediaRootMixin, CampFixture):
    def hand_over(self):
        self.login(self.coord1)
        self.choose_station(number=5)
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", interview.public_id))

    def test_the_welcome_leads_with_the_churchs_mark(self):
        self.give_logo(self.church, png_bytes(300, 100))
        self.hand_over()
        response = self.client.get(reverse("kiosk:home"))
        self.assertContains(response, "Hola, Mariana.")
        self.assertContains(
            response,
            f'<p class="focusbar__who"><img class="brand__logo brand__logo--mark" src="/iglesia/{self.church.slug}/logotipo/claro/?v=',
        )
        self.assertContains(response, 'alt="Iglesia de prueba" width="120" height="40"> <span>Computadora 5</span>')
        # The campero's browser is confined to /estacion/, but may load the logo the page asks for.
        src = re.search(r'brand__logo--mark" src="([^"]+)"', response.content.decode()).group(1)
        image = self.client.get(src)
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image["Content-Type"], "image/png")
        self.assertRedirects(self.client.get(self.url("coord_home")), reverse("kiosk:home"), fetch_redirect_response=False)

    def test_without_a_logo_the_welcome_is_unchanged(self):
        self.hand_over()
        response = self.client.get(reverse("kiosk:home"))
        self.assertContains(response, f'<p class="focusbar__who">{WORDMARK} <span>Computadora 5</span></p>')


class DeckLogoTests(MediaRootMixin, CampFixture):
    def deck(self):
        client = self.login(self.pastor, Client())
        response = client.get(self.url("presentation_live"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    @staticmethod
    def cover(html: str) -> str:
        return html.split('<section class="slide slide--cover', 1)[1].split("</section>", 1)[0]

    def test_cal_and_noche_on_the_title_slide(self):
        """Cal: the usual logo. Noche: the white version. rotulo.css shows one of the two."""
        self.give_logo(self.church, png_bytes(300, 100))
        self.give_logo(self.church, png_bytes(300, 100), field="logo_dark")
        cover = self.cover(self.deck())
        slug = self.church.slug
        self.assertRegex(
            cover,
            r'<div class="slide__top"><span class="deck-brand deck-brand--cal"><img class="brand__logo brand__logo--deck" '
            rf'src="/iglesia/{slug}/logotipo/claro/\?v=[0-9-]+" alt="Iglesia de prueba" width="320" height="107" style="width:16.67cqw"></span>'
            r'<span class="deck-brand deck-brand--noche"><img class="brand__logo brand__logo--deck" '
            rf'src="/iglesia/{slug}/logotipo/oscuro/\?v=[0-9-]+" alt="Iglesia de prueba"',
        )
        self.assertNotIn(WORDMARK, cover)
        self.assertEqual(len(re.findall(r"<h1\b", self.deck())), 1)

    def test_noche_without_a_white_version_uses_the_plate(self):
        self.give_logo(self.church, png_bytes(240, 80))
        cover = self.cover(self.deck())
        noche = cover.split('class="deck-brand deck-brand--noche">', 1)[1]
        self.assertTrue(noche.startswith(f'<span class="brand__plate"><img class="brand__logo brand__logo--deck" src="/iglesia/{self.church.slug}/logotipo/claro/'), noche[:200])

    def test_the_other_slides_keep_the_wordmark(self):
        self.give_logo(self.church, png_bytes())
        html = self.deck()
        self.assertEqual(html.count(WORDMARK), 4)  # slides 2 to 5
        self.assertEqual(html.count("brand__logo--deck"), 2)  # the title slide's two versions

    def test_without_a_logo_the_title_slide_is_unchanged(self):
        html = self.deck()
        self.assertIn(f'<div class="slide__top">{WORDMARK}</div>', self.cover(html))
        self.assertEqual(html.count(WORDMARK), 5)
        self.assertNotIn("deck-brand", html)

    def test_the_stylesheet_shows_one_version_per_mode(self):
        css = (settings.BASE_DIR / "static" / "css" / "rotulo.css").read_text()
        self.assertIn(".deck-brand--noche, .deck--noche .deck-brand--cal { display: none; }", css)
        self.assertIn(".deck--noche .deck-brand--noche { display: inline-flex; }", css)
        self.assertRegex(css, r"\.brand__logo \{[^}]*height: auto;")  # never stretched: height follows the width


class PdfLogoTests(MediaRootMixin, CampFixture):
    def pdf(self) -> bytes:
        from events.services.metrics import event_scope
        from events.services.reports import generate_snapshot

        snap = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=timezone.now())
        client = self.login(self.pastor, Client())
        response = client.get(self.url("report_pdf", snap.public_id))
        self.assertEqual(response.status_code, 200)
        return response.content

    def test_the_png_logo_heads_the_pdf(self):
        without = self.pdf()
        self.assertNotIn(b"/Subtype /Image", without)
        self.give_logo(self.church, png_bytes(600, 200))
        with_logo = self.pdf()
        self.assertTrue(with_logo.startswith(b"%PDF"))
        self.assertIn(b"/Subtype /Image", with_logo)
        self.assertGreater(len(with_logo), len(without))

    def test_the_logo_is_at_most_2_2_inches_wide(self):
        from events.services.pdf import LOGO_MAX_HEIGHT, LOGO_MAX_WIDTH, _brand, _styles

        for width, height in ((1000, 100), (100, 1000), (600, 200)):
            with self.subTest(size=(width, height)):
                self.give_logo(self.church, png_bytes(width, height))
                image = _brand(self.church, _styles())
                self.assertLessEqual(image.drawWidth, LOGO_MAX_WIDTH + 0.01)
                self.assertLessEqual(image.drawHeight, LOGO_MAX_HEIGHT + 0.01)
                self.assertAlmostEqual(image.drawWidth / image.drawHeight, width / height, places=3)
        self.assertAlmostEqual(LOGO_MAX_WIDTH, 2.2 * 72)

    def test_a_file_that_is_not_a_png_or_a_lost_file_leaves_the_wordmark(self):
        self.give_logo(self.church, svg_bytes())  # put there by hand: the form accepts only PNG
        with self.assertLogs("core.logos", "WARNING"):
            self.assertNotIn(b"/Subtype /Image", self.pdf())
        self.give_logo(self.church, png_bytes())
        os.remove(self.church.logo.path)
        with self.assertLogs("core.logos", "WARNING"):
            pdf = self.pdf()
        self.assertNotIn(b"/Subtype /Image", pdf)


class PublicResponseMiddlewareTests(SimpleTestCase):
    """events.middleware.PrivateResponseMiddleware lets the logo keep its cache headers, never with a cookie on it."""

    def respond(self, *, cookie: bool):
        def view(request):
            response = HttpResponse(b"png", content_type="image/png")
            response["Cache-Control"] = "public, max-age=86400"
            response.public_asset = True
            if cookie:
                response.set_cookie("sessionid", "abc")
            return response

        return PrivateResponseMiddleware(view)(RequestFactory().get("/iglesia/x/logotipo/claro/"))

    def test_a_public_asset_keeps_its_cache_headers(self):
        self.assertEqual(self.respond(cookie=False)["Cache-Control"], "public, max-age=86400")

    def test_a_public_asset_with_a_cookie_is_private_again(self):
        response = self.respond(cookie=True)
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(response["Pragma"], "no-cache")

    def test_the_path_pattern_matches_the_route(self):
        for variant in ("claro", "oscuro"):
            self.assertTrue(is_church_logo(reverse("church_logo", args=["iglesia-x_1", variant])))
        self.assertFalse(is_church_logo("/iglesia/x/logotipo/otro/"))
        self.assertFalse(is_church_logo("/iglesia/x/logotipo/claro/extra/"))


class EventListLogoTests(MediaRootMixin, CampFixture):
    def test_the_church_admin_finds_the_church_settings_in_the_bar(self):
        """docs/user-guide-es.md: "Campamentos y eventos", then "Configuración" in the bar opens the church settings."""
        client = self.login(self.admin, Client())
        response = client.get(reverse("events:list"))
        self.assertContains(response, f'<a href="{reverse("church_settings")}">Configuración</a>')


class PdfLogoHardeningTests(MediaRootMixin, CampFixture):
    """A stored file the checks would refuse today (stored before them, or by hand) must not break or bloat the PDF."""

    def build(self) -> bytes:
        from events.services.metrics import event_scope
        from events.services.pdf import build_snapshot_pdf
        from events.services.reports import generate_snapshot

        snap = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=timezone.now())
        return build_snapshot_pdf(snap, snap.data)

    def test_an_undecodable_png_leaves_the_wordmark(self):
        from core.test_church_logo import png_file

        self.give_logo(self.church, png_file(100, 50, b"esto no es zlib" * 3))
        with self.assertLogs("events.services.pdf", "WARNING"):
            pdf = self.build()
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertNotIn(b"/Subtype /Image", pdf)

    @staticmethod
    def image_sizes(pdf: bytes) -> list[tuple[int, int]]:
        objects = [body for body in re.findall(rb"<<(.*?)>>", pdf, re.S) if b"/Subtype /Image" in body]
        return [(int(re.search(rb"/Width (\d+)", o).group(1)), int(re.search(rb"/Height (\d+)", o).group(1))) for o in objects]

    def test_a_large_logo_is_embedded_at_most_660_by_220(self):
        for width, height in ((2000, 600), (1000, 1000), (300, 2000)):
            with self.subTest(size=(width, height)):
                self.give_logo(self.church, png_bytes(width, height))
                sizes = self.image_sizes(self.build())
                self.assertTrue(sizes, "no image in the PDF")
                for embedded_width, embedded_height in sizes:
                    self.assertLessEqual(embedded_width, 660)
                    self.assertLessEqual(embedded_height, 220)

    def test_one_channel_logos_are_embedded(self):
        """1-bit, palette (with a transparent entry) and 16-bit grey: drawn, and 16-bit grey not clipped to white."""
        from events.services.pdf import _embeddable_logo
        from PIL import Image

        dark_grey_16 = png_file(64, 32, zlib.compress((b"\x00" + b"\x10\x00" * 64) * 32), bit_depth=16, colour=0)  # 4096 of 65535
        for name, data in (("1-bit", png_bytes(400, 100, mode="1")), ("palette", png_bytes(400, 100, mode="P")), ("16-bit grey", dark_grey_16)):
            with self.subTest(name=name):
                fieldfile = self.give_logo(self.church, data)
                self.assertIn(b"/Subtype /Image", self.build())
                stat = os.stat(fieldfile.path)
                embedded = Image.open(io.BytesIO(_embeddable_logo(fieldfile.path, stat.st_size, stat.st_mtime_ns)))
                if name == "16-bit grey":
                    self.assertEqual(embedded.getpixel((0, 0))[:3], (16, 16, 16))  # 4096 / 256, not clipped to 255

    def test_large_one_channel_logos_never_become_rgba_at_full_size(self):
        """Memory: a big 1-bit or palette logo is converted to grey before resizing; only the small copy is RGBA."""
        from unittest import mock

        from PIL import Image

        from events.services.pdf import _embeddable_logo

        original = Image.Image.convert
        conversions = []

        def recording(image, mode=None, *args, **kwargs):
            conversions.append((mode, image.size))
            return original(image, mode, *args, **kwargs)

        for name, data in (("1-bit", png_file(2000, 1000, bit_depth=1, colour=0)), ("palette", png_bytes(2000, 1000, mode="P"))):
            with self.subTest(name=name):
                fieldfile = self.give_logo(self.church, data)
                stat = os.stat(fieldfile.path)
                conversions.clear()
                with mock.patch.object(Image.Image, "convert", recording):
                    _embeddable_logo(fieldfile.path, stat.st_size, stat.st_mtime_ns)
                full_size = [mode for mode, size in conversions if size == (2000, 1000)]
                self.assertTrue(full_size)
                self.assertNotIn("RGBA", full_size)
                self.assertNotIn("RGB", full_size)


class StoredFileCampScreensTests(MediaRootMixin, CampFixture):
    """A stored file damaged after upload: the kiosk, the deck, the event bar and the PDF still draw."""

    def damage(self):
        fieldfile = self.give_logo(self.church, png_bytes(300, 100))
        with open(fieldfile.path, "wb") as handle:
            handle.write(os.urandom(3000))

    def test_every_camp_slot_draws_the_wordmark_and_the_pdf_builds(self):
        from events.services.metrics import event_scope
        from events.services.pdf import build_snapshot_pdf
        from events.services.reports import generate_snapshot

        self.damage()
        pastor = self.login(self.pastor, Client())
        with self.assertLogs("core.logos", "WARNING"):
            deck = pastor.get(self.url("presentation_live"))
            home = pastor.get(self.url("pastor_home"))
        self.assertEqual((deck.status_code, home.status_code), (200, 200))
        self.assertNotContains(deck, "brand__logo")
        self.assertContains(home, f'<a class="wordmark" href="{self.url("home")}">Emmaus</a>')
        snap = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=timezone.now())
        pdf = build_snapshot_pdf(snap, snap.data)  # the verdict on the file was logged once, above
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertNotIn(b"/Subtype /Image", pdf)
        self.login(self.coord1)
        self.choose_station(number=5)
        self.start(self.p1, mode="supervisada")
        self.client.post(self.url("interview_handoff", Interview.objects.get(participant=self.p1).public_id))
        welcome = self.client.get(reverse("kiosk:home"))
        self.assertContains(welcome, f'<p class="focusbar__who">{WORDMARK} <span>Computadora 5</span></p>')


class ChurchLogoPathTests(SimpleTestCase):
    def test_a_trailing_newline_is_not_the_logo_path(self):
        self.assertTrue(is_church_logo("/iglesia/x/logotipo/claro/"))
        self.assertFalse(is_church_logo("/iglesia/x/logotipo/claro/\n"))
