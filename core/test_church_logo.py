"""The church's own logo (PNG only): what an upload may be, how it is served, the tag that puts it in the
wordmark's slot, where it shows on base.html pages, and the church settings form that uploads and removes it.

Every image here is made by the tests themselves (Pillow, or a PNG assembled chunk by chunk); no real logo is
used. The hostile cases rebuild, with harmless markers, the files two refutations of this feature produced.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import tempfile
import time
import zlib
from io import BytesIO

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError, connection
from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .logos import MAX_BYTES, UPLOAD_HELP, inspect_logo_upload, logo_meta, validate_church_logo
from .models import Church
from .tests import EmmausFixture
from .views import server_error


# ---- images made here -------------------------------------------------------------------------------
def png_bytes(width: int = 300, height: int = 100, *, noise: bool = False, mode: str = "RGBA") -> bytes:
    from PIL import Image

    if noise:
        image = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    else:
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        for x in range(width // 4, width // 2):  # a black bar on transparent, like a monochrome mark
            for y in range(height // 4, height * 3 // 4):
                image.putpixel((x, y), (0, 0, 0, 255))
        if mode != "RGBA":
            image = image.convert(mode)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def jpg_bytes() -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (40, 20), (0, 0, 0)).save(buffer, "JPEG")
    return buffer.getvalue()


def svg_bytes(body: str = "", *, root: str = 'viewBox="0 0 240 80"', prolog: str = "") -> bytes:
    body = body or '<circle cx="40" cy="40" r="32" fill="none" stroke="#000" stroke-width="6"/><path d="M88 20h140v40H88z"/>'
    return f'{prolog}<svg xmlns="http://www.w3.org/2000/svg" {root}>{body}</svg>'.encode()


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png_file(width: int, height: int, idat: bytes | None = None, *, tail: bytes = b"", before_idat: bytes = b"",
             after_idat: bytes = b"", iend: bytes = b"", bit_depth: int = 8, colour: int = 6) -> bytes:
    """A PNG assembled chunk by chunk (valid CRCs), so any part of it can be wrong on purpose."""
    if idat is None:
        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
        row = (width * channels * bit_depth + 7) // 8
        idat = zlib.compress((b"\x00" + b"\x00" * row) * height, 9)
    header = struct.pack(">IIBBBBB", width, height, bit_depth, colour, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + before_idat + chunk(b"IDAT", idat) + after_idat
        + chunk(b"IEND", iend) + tail
    )


def apng(frames: int = 2) -> bytes:
    raw = zlib.compress((b"\x00" + b"\x00" * 400) * 100)
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 100, 100, 8, 6, 0, 0, 0))
    out += chunk(b"acTL", struct.pack(">II", frames, 0))
    out += chunk(b"fcTL", struct.pack(">IIIIIHHBB", 0, 100, 100, 0, 0, 1, 10, 0, 0)) + chunk(b"IDAT", raw)
    for n in range(1, frames):
        out += chunk(b"fcTL", struct.pack(">IIIIIHHBB", 2 * n - 1, 100, 100, 0, 0, 1, 10, 0, 0))
        out += chunk(b"fdAT", struct.pack(">I", 2 * n) + raw)
    return out + chunk(b"IEND", b"")


def upload(name: str, data: bytes, content_type: str = "application/octet-stream") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type=content_type)


class MediaRootMixin:
    """MEDIA_ROOT in a temporary directory for each test, so no test writes into the checkout's media/."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="emmaus-media-")
        self.addCleanup(shutil.rmtree, self.media_root)
        media = override_settings(MEDIA_ROOT=self.media_root)
        media.enable()
        self.addCleanup(media.disable)
        super().setUp()

    def give_logo(self, church, data: bytes, *, field: str = "logo", name: str = "logotipo.png"):
        """Stored straight into storage, without the form's checks (what a file already on disk can be)."""
        getattr(church, field).save(name, ContentFile(data), save=True)
        return getattr(church, field)


# ---- what an upload may be ----------------------------------------------------------------------------
class LogoValidatorTests(SimpleTestCase):
    def assert_refused(self, file, code: str, words: str):
        with self.assertRaises(ValidationError) as caught:
            inspect_logo_upload(file)
        self.assertEqual(caught.exception.code, code)
        self.assertIn(words, caught.exception.messages[0])
        return caught.exception.messages[0]

    def test_only_png_is_accepted_with_one_answer_for_the_rest(self):
        from PIL import Image, features

        others = {"svg": svg_bytes(), "jpeg": jpg_bytes(), "texto": b"esto no es una imagen", "html": b"<html>hola</html>"}
        if features.check("webp"):
            buffer = BytesIO()
            Image.new("RGBA", (40, 20)).save(buffer, "WEBP")
            others["webp"] = buffer.getvalue()
        for name, data in others.items():
            with self.subTest(name=name):
                message = self.assert_refused(upload("logo.png", data, "image/png"), "logo_type", UPLOAD_HELP)
                self.assertEqual(message, "Sube el logotipo en PNG con fondo transparente (hasta 512 KB, lado mayor de 32 a 2000 px).")

    def test_more_than_512_kb_is_refused(self):
        data = png_bytes(600, 600, noise=True)
        self.assertGreater(len(data), MAX_BYTES)
        message = self.assert_refused(upload("logo.png", data), "logo_size", "el máximo es 512 KB")
        self.assertIn(UPLOAD_HELP, message)

    def test_a_file_that_lies_about_its_size_is_measured(self):
        file = upload("logo.png", png_bytes(600, 600, noise=True))
        file.size = 1000
        self.assert_refused(file, "logo_size", "más de 512 KB")

    def test_a_png_over_2000_px_is_refused(self):
        for width, height in ((2001, 201), (201, 2001), (4000, 4000)):
            with self.subTest(size=(width, height)):
                self.assert_refused(upload("logo.png", png_file(width, height, bit_depth=1, colour=0)), "logo_pixels", "hasta 2000 píxeles por lado")

    def test_a_broken_png_is_refused(self):
        truncated = png_bytes()[:40] + b"\x00" * 60
        self.assert_refused(upload("logo.png", truncated), "logo_unreadable", "No pudimos abrir este PNG")
        damaged = bytearray(png_bytes())  # one byte of image data changed: its chunk's CRC no longer matches
        damaged[damaged.index(b"IDAT") + 6] ^= 0xFF
        self.assert_refused(upload("logo.png", bytes(damaged)), "logo_unreadable", "Expórtalo otra vez")

    def test_a_clean_png_is_accepted(self):
        info = inspect_logo_upload(upload("logo.png", png_bytes(300, 100)))
        self.assertEqual((info.width, info.height), (300, 100))

    def test_the_name_is_not_trusted(self):
        self.assertEqual(inspect_logo_upload(upload("logo.svg", png_bytes())).width, 300)
        self.assert_refused(upload("logo.png", svg_bytes()), "logo_type", UPLOAD_HELP)


def hostile_pngs() -> dict[str, bytes]:
    one = png_file(1, 1, zlib.compress(b"\x00" * 5))
    idat = zlib.compress((b"\x00" + b"\x00" * 400) * 50)
    bomb = zlib.compress(b"\x00" * (8 * 1024 * 1024), 9)
    return {
        "garbage_idat_valid_crc": png_file(100, 50, b"esto no es zlib" * 3),
        "short_idat_valid_crc": png_file(100, 50, zlib.compress(b"\x00" * 10)),
        "1x1": one,
        "1x4000": png_file(1, 4000),
        "4000x1": png_file(4000, 1),
        "31x10": png_file(31, 10),
        "too_wide": png_file(400, 39),
        "2000x199": png_file(2000, 199, bit_depth=1, colour=0),
        "4000x4000": png_file(4000, 4000, bit_depth=1, colour=0),
        "html_after_iend": one + b"<html><b>MARCA</b></html>",
        "html_after_iend_big_enough": png_file(64, 64, tail=b"<html><head><title>x</title></head><body>MARCA</body></html>"),
        "iend_with_data": png_file(100, 50, idat, iend=b"<html><b>MARCA</b></html>"),
        "second_iend": png_file(100, 50, idat) + chunk(b"IEND", b""),
        "no_iend": png_file(100, 50, idat)[: -len(chunk(b"IEND", b""))],
        "duplicate_ihdr": png_file(100, 50, idat, before_idat=chunk(b"IHDR", struct.pack(">IIBBBBB", 5000, 5000, 8, 6, 0, 0, 0))),
        # Pillow itself accepts this one: only the chunk walk refuses a second IHDR.
        "duplicate_identical_ihdr": png_file(100, 50, idat, before_idat=chunk(b"IHDR", struct.pack(">IIBBBBB", 100, 50, 8, 6, 0, 0, 0))),
        "bad_crc_ancillary": png_file(100, 50, idat, before_idat=b"\x00\x00\x00\x04tEXtab\x00c\xde\xad\xbe\xef"),
        "chunk_length_2gb": png_file(100, 50, idat, before_idat=struct.pack(">I", 0x7FFFFFFF) + b"tEXt" + b"x" * 100),
        "chunk_length_4gb": png_file(100, 50, idat, before_idat=struct.pack(">I", 0xFFFFFFFF) + b"pHYs" + b"x" * 100),
        "ihdr_width_0": png_file(0, 100, idat),
        "ihdr_width_2_31": png_file(2**31, 100, idat),
        "apng_2_frames": apng(2),
        "apng_40_frames": apng(40),
        "ztxt_bomb": png_file(100, 50, idat, before_idat=chunk(b"zTXt", b"Comment\x00\x00" + bomb)),
        "itxt_bomb": png_file(100, 50, idat, before_idat=chunk(b"iTXt", b"Comment\x00\x01\x00\x00\x00" + bomb)),
        "iccp_bomb": png_file(100, 50, idat, before_idat=chunk(b"iCCP", b"icc\x00\x00" + bomb)),
        "16bit_palette": png_file(100, 50, bit_depth=16, colour=3, before_idat=chunk(b"PLTE", b"\x00\x00\x00" * 2)),
        "png_magic_then_svg": b"\x89PNG\r\n\x1a\n" + svg_bytes("<script>marca()</script>"),
    }


class HostilePngTests(SimpleTestCase):
    """Every hostile file is refused, quickly; the well-formed ones are accepted."""

    def hostile(self) -> dict[str, bytes]:
        return hostile_pngs()

    def test_every_hostile_png_is_refused_fast(self):
        for name, data in self.hostile().items():
            with self.subTest(name=name):
                started = time.perf_counter()
                with self.assertRaises(ValidationError) as caught:
                    inspect_logo_upload(upload(f"{name}.png", data))
                self.assertLess(time.perf_counter() - started, 0.5, name)
                self.assertTrue(caught.exception.messages[0].strip())

    def test_specific_refusals(self):
        cases = {
            "iend_with_data": ("logo_unreadable", "No pudimos abrir este PNG"),
            "html_after_iend_big_enough": ("logo_unreadable", "datos de más después de la imagen"),
            "apng_2_frames": ("logo_animated", "Este PNG es animado"),
            "duplicate_ihdr": ("logo_unreadable", "No pudimos abrir este PNG"),
            "duplicate_identical_ihdr": ("logo_unreadable", "No pudimos abrir este PNG"),
            "too_wide": ("logo_ratio", "hasta 10 veces más ancho que alto"),
            "31x10": ("logo_pixels", "al menos 32 píxeles"),
        }
        hostile = self.hostile()
        for name, (code, words) in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ValidationError) as caught:
                    inspect_logo_upload(upload("logo.png", hostile[name]))
                self.assertEqual(caught.exception.code, code)
                self.assertIn(words, caught.exception.messages[0])

    def test_a_2_gb_length_claim_is_refused_at_once(self):
        data = self.hostile()["chunk_length_2gb"]
        started = time.perf_counter()
        with self.assertRaises(ValidationError):
            inspect_logo_upload(upload("logo.png", data))
        self.assertLess(time.perf_counter() - started, 0.1)

    def test_well_formed_pngs_are_accepted(self):
        accepted = {
            "1-bit": png_bytes(400, 100, mode="1"),
            "grey": png_bytes(400, 100, mode="L"),
            "grey and alpha": png_bytes(400, 100, mode="LA"),
            "palette": png_bytes(400, 100, mode="P"),
            "16-bit grey": png_file(100, 50, bit_depth=16, colour=0),
            "rgb": png_bytes(400, 100, mode="RGB"),
            "10:1 exactly": png_file(320, 32),
            "1:10 exactly": png_file(40, 400, bit_depth=1, colour=0),
            "2000 x 2000 RGBA, the largest": png_file(2000, 2000),
        }
        for name, data in accepted.items():
            with self.subTest(name=name):
                inspect_logo_upload(upload("logo.png", data))

    def test_png_size_limits(self):
        for width, height, code in ((31, 10, "logo_pixels"), (400, 39, "logo_ratio"), (39, 400, "logo_ratio")):
            with self.subTest(size=(width, height)):
                with self.assertRaises(ValidationError) as caught:
                    inspect_logo_upload(upload("logo.png", png_file(width, height)))
                self.assertEqual(caught.exception.code, code)


class HostileUploadFormTests(MediaRootMixin, EmmausFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def post(self, **files):
        data = {
            "name": self.church.name,
            "contact_email": self.church.contact_email,
            "basic_disclaimer_text": self.church.basic_disclaimer_text,
            "leaders_can_invite_participants": "on",
            "theme_primary_color": "#111111",
            "theme_accent_color": "#B85C38",
            "theme_secondary_color": "#596B54",
            "theme_background_color": "#F0F0EE",
            "theme_surface_color": "#FFFFFF",
            "theme_text_color": "#111111",
            "theme_muted_text_color": "#6D6A64",
            "theme_border_color": "#D9D9D6",
            **files,
        }
        return self.client.post(reverse("church_settings"), data)

    def test_no_hostile_file_and_no_svg_reaches_storage_through_the_form(self):
        files = {**hostile_pngs(), "svg": svg_bytes(), "jpeg": jpg_bytes()}
        for name, data in files.items():
            with self.subTest(name=name):
                Church.objects.filter(pk=self.church.pk).update(logo="")
                response = self.post(logo=upload(f"{name}.png", data, "image/png"))
                self.assertEqual(response.status_code, 200)
                self.church.refresh_from_db()
                self.assertFalse(self.church.logo, f"{name} was stored")
        self.assertEqual(os.listdir(self.media_root), [])

    def test_a_png_is_stored_as_it_came(self):
        data = png_bytes(240, 80)
        self.assertRedirects(self.post(logo=upload("x.png", data)), reverse("church_settings"))
        self.church.refresh_from_db()
        with open(self.church.logo.path, "rb") as handle:
            self.assertEqual(handle.read(), data)
class StoredLogoValidationTests(MediaRootMixin, TestCase):
    def test_a_stored_logo_is_not_read_again_when_the_settings_are_saved(self):
        """A lost media volume must not lock the settings form: the stored file was checked when it was uploaded."""
        church = Church.objects.create(name="Iglesia Uno", slug="uno")
        fieldfile = self.give_logo(church, png_bytes())
        os.remove(fieldfile.path)
        validate_church_logo(church.logo)  # no FileNotFoundError, no ValidationError
        church.full_clean()


# ---- serving ------------------------------------------------------------------------------------------
class ChurchLogoViewTests(MediaRootMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.church = Church.objects.create(name="Iglesia del Camino", slug="camino")

    def url(self, variant="claro", slug="camino"):
        return reverse("church_logo", args=[slug, variant])

    def test_a_png_is_served_with_its_headers(self):
        data = png_bytes()
        self.give_logo(self.church, data)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["Cache-Control"], "public, max-age=86400")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("sandbox", response.get("Content-Security-Policy", ""))
        self.assertEqual(b"".join(response.streaming_content), data)

    def test_a_file_that_is_not_a_png_is_never_served(self):
        """Something put in storage by hand (an SVG, a page): the walk at render time refuses it, so a bare 404."""
        for data in (svg_bytes(), b"<html><body>hola</body></html>", png_bytes()[:-20]):
            with self.subTest(data=data[:20]):
                self.give_logo(self.church, data, field="logo_dark", name="logotipo-oscuro.png")
                with self.assertLogs("core.logos", "WARNING"):
                    response = self.client.get(self.url("oscuro"))
                self.assertEqual((response.status_code, response.content), (404, b""))

    def test_the_type_comes_from_the_bytes(self):
        self.give_logo(self.church, png_bytes(), name="engano.svg")
        self.assertEqual(self.client.get(self.url())["Content-Type"], "image/png")

    def test_no_file_unknown_church_or_variant_is_404(self):
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.give_logo(self.church, png_bytes())
        self.assertEqual(self.client.get(self.url("oscuro")).status_code, 404)  # no white version: the tag uses the plate
        self.assertEqual(self.client.get(self.url(slug="no-existe")).status_code, 404)
        self.assertEqual(self.client.get(reverse("church_logo", args=["camino", "azul"])).status_code, 404)

    def test_a_file_lost_from_media_is_404_and_logged(self):
        fieldfile = self.give_logo(self.church, png_bytes())
        os.remove(fieldfile.path)
        with self.assertLogs("core.logos", "WARNING") as logs:
            self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertIn(fieldfile.name, logs.output[0])

    def test_only_reading_is_allowed(self):
        self.give_logo(self.church, png_bytes())
        self.assertEqual(self.client.post(self.url()).status_code, 405)

    def test_a_signed_in_request_stays_cacheable_and_carries_no_cookie(self):
        """The image is not activity on a shared computer: no session write, so no cookie on a public response."""
        self.give_logo(self.church, png_bytes())
        user = User.objects.create_user("persona@example.org", "persona@example.org", "clave-larga-123")
        self.client.force_login(user)
        session = self.client.session
        session["last_seen"] = int(time.time()) - 120  # a page two minutes ago: the next real request refreshes it
        session.save()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(dict(response.cookies), {})
        self.assertEqual(response["Cache-Control"], "public, max-age=86400")
        self.assertNotIn("Cookie", response.get("Vary", ""))

    def test_pages_stay_private(self):
        self.assertEqual(self.client.get(reverse("login"))["Cache-Control"], "no-store, max-age=0")


# ---- the tag ------------------------------------------------------------------------------------------
def render(source: str, **context) -> str:
    return Template("{% load brand %}" + source).render(Context(context))


class ChurchBrandTagTests(MediaRootMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.church = Church.objects.create(name="Iglesia del Camino", slug="camino")

    def test_without_a_logo_the_wordmark_is_exactly_what_it_was(self):
        old_link = Template('<a class="wordmark" href="{{ url }}">Emmaus</a>').render(Context({"url": "/eventos/a/"}))
        old_span = '<span class="wordmark">Emmaus</span>'
        for church in (self.church, None, ""):
            with self.subTest(church=church):
                self.assertEqual(render('{% church_brand church size="bar" href=url %}', church=church, url="/eventos/a/"), old_link)
                self.assertEqual(render('{% church_brand church size="bar" %}', church=church), old_span)
                self.assertEqual(render('{% church_brand church variant="oscuro" size="deck" %}', church=church), old_span)

    def test_with_a_logo_an_image_named_for_the_church(self):
        fieldfile = self.give_logo(self.church, png_bytes(300, 100))
        html = render('{% church_brand church size="bar" href="/inicio/" %}', church=self.church)
        stat = os.stat(fieldfile.path)
        self.assertEqual(
            html,
            '<a class="brand brand--bar" href="/inicio/"><img class="brand__logo brand__logo--bar" '
            f'src="/iglesia/camino/logotipo/claro/?v={stat.st_size}-{stat.st_mtime_ns}" alt="Iglesia del Camino" width="96" height="32"></a>',
        )

    def test_a_new_upload_changes_the_address(self):
        self.give_logo(self.church, png_bytes(300, 100))
        before = re.search(r'src="([^"]+)"', render("{% church_brand church %}", church=self.church)).group(1)
        self.assertIn("?v=", before)
        self.church.logo.delete(save=False)
        self.give_logo(self.church, png_bytes(200, 100))
        after = re.search(r'src="([^"]+)"', render("{% church_brand church %}", church=self.church)).group(1)
        self.assertNotEqual(before, after)

    def test_each_slot_keeps_the_proportions(self):
        self.give_logo(self.church, png_bytes(300, 100))
        sizes = {"bar": (96, 32), "auth": (216, 72), "mark": (120, 40), "deck": (320, 107)}
        for size, (width, height) in sizes.items():
            with self.subTest(size=size):
                html = render("{% church_brand church size=size %}", church=self.church, size=size)
                self.assertIn(f'class="brand__logo brand__logo--{size}"', html)
                self.assertIn(f'width="{width}" height="{height}"', html)
        self.assertIn('style="width:16.67cqw"', render('{% church_brand church size="deck" %}', church=self.church))

    def test_a_wide_logo_is_held_to_the_max_width_never_stretched(self):
        self.give_logo(self.church, png_bytes(1000, 100))
        self.assertIn('width="180" height="18"', render('{% church_brand church size="bar" %}', church=self.church))
        self.assertIn('width="220" height="22"', render('{% church_brand church size="auth" %}', church=self.church))

    def test_on_dark_the_white_version_or_the_plate(self):
        self.give_logo(self.church, png_bytes())
        plated = render('{% church_brand church variant="oscuro" size="deck" %}', church=self.church)
        self.assertTrue(plated.startswith('<span class="brand__plate"><img'), plated)
        self.assertIn("/iglesia/camino/logotipo/claro/", plated)
        self.give_logo(self.church, png_bytes(), field="logo_dark")
        dark = render('{% church_brand church variant="oscuro" size="deck" %}', church=self.church)
        self.assertNotIn("brand__plate", dark)
        self.assertIn("/iglesia/camino/logotipo/oscuro/", dark)
        self.assertIn("/iglesia/camino/logotipo/claro/", render('{% church_brand church variant="claro" %}', church=self.church))

    def test_only_a_white_version_is_not_used_on_white(self):
        self.give_logo(self.church, png_bytes(), field="logo_dark")
        self.assertEqual(render("{% church_brand church %}", church=self.church), '<span class="wordmark">Emmaus</span>')

    def test_a_file_lost_from_media_falls_back_to_the_wordmark(self):
        os.remove(self.give_logo(self.church, png_bytes()).path)
        with self.assertLogs("core.logos", "WARNING"):
            self.assertEqual(render('{% church_brand church href="/" %}', church=self.church), '<a class="wordmark" href="/">Emmaus</a>')

    def test_the_church_name_is_escaped(self):
        self.church.name = 'Iglesia "Luz" & <Vida>'
        self.give_logo(self.church, png_bytes())
        self.assertIn('alt="Iglesia &quot;Luz&quot; &amp; &lt;Vida&gt;"', render("{% church_brand church %}", church=self.church))


# ---- where it shows on base.html pages ------------------------------------------------------------------
def h1_count(response) -> int:
    return len(re.findall(r"<h1\b", response.content.decode()))


class SignInPlacementTests(MediaRootMixin, TestCase):
    def test_the_logo_leads_the_sign_in_page(self):
        church = Church.objects.create(name="Iglesia del Camino", slug="camino")
        self.give_logo(church, png_bytes(300, 100))
        response = self.client.get(reverse("login"))
        html = response.content.decode()
        self.assertContains(response, '<div class="auth-card__brand"><img class="brand__logo brand__logo--auth" src="/iglesia/camino/logotipo/claro/?v=')
        self.assertContains(response, 'alt="Iglesia del Camino" width="216" height="72">')
        self.assertLess(html.index("auth-card__brand"), html.index("<h1"))
        # The bar keeps the painted "Emmaus" here, so the logo is not shown twice; the product name stays in the title.
        self.assertContains(response, '<a class="wordmark" href="/">Emmaus</a>')
        self.assertContains(response, "<title>Entrar · Emmaus</title>")
        self.assertEqual(h1_count(response), 1)

    def test_sign_in_without_a_logo_is_unchanged(self):
        Church.objects.create(name="Iglesia del Camino", slug="camino")
        response = self.client.get(reverse("login"))
        self.assertNotContains(response, "auth-card__brand")
        self.assertContains(response, '<a class="wordmark" href="/">Emmaus</a>')

    def test_sign_in_works_with_no_church_at_all(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "brand__logo")
        self.assertContains(response, '<a class="wordmark" href="/">Emmaus</a>')

    def test_a_logo_lost_from_media_adds_nothing_above_the_title(self):
        church = Church.objects.create(name="Iglesia del Camino", slug="camino")
        os.remove(self.give_logo(church, png_bytes()).path)
        with self.assertLogs("core.logos", "WARNING"):
            response = self.client.get(reverse("login"))
        self.assertNotContains(response, "auth-card__brand")
        self.assertContains(response, 'class="wordmark"', count=1)  # only the bar's: no second "Emmaus" above the title

    def test_the_public_bar_shows_the_first_active_church(self):
        Church.objects.create(name="Iglesia Cerrada", slug="cerrada", active=False)
        church = Church.objects.create(name="Iglesia del Camino", slug="camino")
        self.give_logo(church, png_bytes(300, 100))
        response = self.client.get(reverse("password_reset"))
        self.assertContains(response, '<a class="brand brand--bar" href="/"><img class="brand__logo brand__logo--bar" src="/iglesia/camino/logotipo/claro/')

    def test_the_page_policy_allows_the_logo(self):
        """The CSP's img-src 'self' covers the logo: the tag writes a same-origin path."""
        church = Church.objects.create(name="Iglesia del Camino", slug="camino")
        self.give_logo(church, png_bytes())
        response = self.client.get(reverse("login"))
        img_src = re.search(r"img-src ([^;]+);", response["Content-Security-Policy"]).group(1).split()
        self.assertIn("'self'", img_src)
        src = re.search(r'class="brand__logo brand__logo--auth" src="([^"]+)"', response.content.decode()).group(1)
        self.assertTrue(src.startswith("/iglesia/"), src)


class SignedInPlacementTests(MediaRootMixin, EmmausFixture):
    def test_the_bar_and_the_menu_show_the_churchs_logo(self):
        self.give_logo(self.church, png_bytes(300, 100))
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("mentor_dashboard"))
        self.assertContains(response, f'<a class="brand brand--bar" href="{reverse("dashboard")}"><img class="brand__logo brand__logo--bar"')
        head = response.content.decode().split('class="menu-panel__head">', 1)[1].split("<button", 1)[0]
        self.assertIn('<img class="brand__logo brand__logo--bar"', head)
        self.assertIn('alt="Emmaus Church"', head)
        self.assertEqual(h1_count(response), 1)

    def test_without_a_logo_the_bar_is_unchanged(self):
        self.client.force_login(self.mentor)
        response = self.client.get(reverse("mentor_dashboard"))
        self.assertContains(response, f'<div class="bar__inner">\n    <a class="wordmark" href="{reverse("dashboard")}">Emmaus</a>\n')
        self.assertContains(response, '<div class="menu-panel__head">\n    <span class="wordmark">Emmaus</span>\n')

    def test_someone_without_a_church_role_sees_the_first_active_church(self):
        self.give_logo(self.church, png_bytes())
        user = User.objects.create_user("sinrol@example.org", "sinrol@example.org", "clave-larga-123")
        self.client.force_login(user)
        response = self.client.get(reverse("events:list"))
        self.assertContains(response, 'alt="Emmaus Church"')


class ServerErrorTests(MediaRootMixin, TestCase):
    def test_the_500_page_draws_without_the_database(self):
        church = Church.objects.create(name="Iglesia del Camino", slug="camino")
        self.give_logo(church, png_bytes())

        def refuse(execute, sql, params, many, context):
            raise OperationalError("sin base de datos")

        with connection.execute_wrapper(refuse):
            response = server_error(RequestFactory().get("/"))
        self.assertEqual(response.status_code, 500)
        html = response.content.decode()
        self.assertIn("Emmaus no pudo abrir esta página", html)
        self.assertIn('<a class="wordmark" href="/">Emmaus</a>', html)


# ---- the settings form --------------------------------------------------------------------------------
class ChurchSettingsLogoTests(MediaRootMixin, EmmausFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.url = reverse("church_settings")

    def post(self, **extra):
        data = {
            "name": self.church.name,
            "public_welcome_text": self.church.public_welcome_text,
            "contact_email": self.church.contact_email,
            "basic_disclaimer_text": self.church.basic_disclaimer_text,
            "leaders_can_invite_participants": "on",
            "theme_primary_color": "#111111",
            "theme_accent_color": "#B85C38",
            "theme_secondary_color": "#596B54",
            "theme_background_color": "#F0F0EE",
            "theme_surface_color": "#FFFFFF",
            "theme_text_color": "#111111",
            "theme_muted_text_color": "#6D6A64",
            "theme_border_color": "#D9D9D6",
            **extra,
        }
        return self.client.post(self.url, data)

    def test_the_form_sends_files(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'type="file" name="logo" accept="image/png"')
        self.assertContains(response, 'type="file" name="logo_dark" accept="image/png"')
        self.assertContains(response, "Sube el logotipo en PNG con fondo transparente (hasta 512 KB, lado mayor de 32 a 2000 px).")
        self.assertNotContains(response, "Quitar el logotipo")  # nothing to remove yet

    def test_upload_preview_and_remove(self):
        response = self.post(logo=upload("Mi Logo Final (2).png", png_bytes(300, 100)))
        self.assertRedirects(response, self.url)
        self.church.refresh_from_db()
        self.assertEqual(self.church.logo.name, "church-logos/logotipo-emmaus.png")
        path = self.church.logo.path
        self.assertTrue(path.startswith(self.media_root))
        self.assertTrue(os.path.exists(path))

        page = self.client.get(self.url)
        self.assertContains(page, '<div class="logo-preview__plate"><img class="brand__logo brand__logo--auth"')
        self.assertContains(page, "Tu logotipo de ahora, sobre blanco.")
        self.assertContains(page, "Quitar el logotipo")
        self.assertNotContains(page, "logo-preview--dark")

        response = self.post(logo_clear="on")
        self.assertRedirects(response, self.url)
        self.church.refresh_from_db()
        self.assertFalse(self.church.logo)
        self.assertFalse(os.path.exists(path))

    def test_the_white_version_is_previewed_on_a_dark_background(self):
        self.post(logo=upload("a.png", png_bytes()), logo_dark=upload("b.png", png_bytes(240, 80)))
        self.church.refresh_from_db()
        self.assertEqual(self.church.logo_dark.name, "church-logos/logotipo-oscuro-emmaus.png")
        page = self.client.get(self.url)
        dark = page.content.decode().split('class="logo-preview logo-preview--dark"', 1)[1].split("</figure>", 1)[0]
        self.assertIn("/iglesia/emmaus/logotipo/oscuro/", dark)
        self.assertIn("Tu versión en blanco, sobre fondo oscuro.", dark)
        self.assertContains(page, "Quitar el logotipo para fondo oscuro")

    def test_replacing_the_logo_removes_the_old_file(self):
        self.post(logo=upload("a.png", png_bytes(300, 100)))
        self.church.refresh_from_db()
        old_path = self.church.logo.path
        self.post(logo=upload("b.png", png_bytes(200, 100)))
        self.church.refresh_from_db()
        self.assertNotEqual(self.church.logo.path, old_path)
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(os.path.exists(self.church.logo.path))

    def test_a_rejected_upload_says_what_to_do_and_keeps_the_saved_logo(self):
        self.post(logo=upload("a.png", png_bytes(300, 100)))
        self.church.refresh_from_db()
        saved = self.church.logo.name
        response = self.post(logo=upload("foto.png", jpg_bytes()))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Logotipo: Sube el logotipo en PNG con fondo transparente (hasta 512 KB, lado mayor de 32 a 2000 px).")
        self.assertContains(response, 'aria-invalid="true"')
        self.church.refresh_from_db()
        self.assertEqual(self.church.logo.name, saved)
        self.assertContains(response, "/iglesia/emmaus/logotipo/claro/?v=")  # the preview shows what is saved

    def test_an_error_elsewhere_keeps_the_preview_of_the_saved_logo(self):
        """A valid new file waits on the form's instance while another field fails: the preview shows what is saved."""
        self.post(logo=upload("a.png", png_bytes(300, 100)))
        self.church.refresh_from_db()
        response = self.post(logo=upload("b.png", png_bytes(200, 100)), theme_primary_color="rojo")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Color principal:")
        self.assertContains(response, '<div class="logo-preview__plate"><img class="brand__logo brand__logo--auth" src="/iglesia/emmaus/logotipo/claro/?v=')
        self.assertContains(response, 'width="216" height="72"')  # the saved 3:1 logo, not the new 2:1 one

    def test_uploading_and_removing_at_once_is_refused(self):
        self.post(logo=upload("a.png", png_bytes()))
        response = self.post(logo=upload("b.png", png_bytes(200, 100)), logo_clear="on")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Elige una sola cosa: sube un logotipo nuevo o marca “Quitar el logotipo”.")

    def test_saving_other_settings_keeps_the_logo(self):
        self.post(logo=upload("a.png", png_bytes()))
        self.church.refresh_from_db()
        saved = self.church.logo.name
        self.assertRedirects(self.post(name="Iglesia Renombrada"), self.url)
        self.church.refresh_from_db()
        self.assertEqual((self.church.name, self.church.logo.name), ("Iglesia Renombrada", saved))
        self.assertTrue(os.path.exists(self.church.logo.path))

    def test_only_the_church_admin_can_change_it(self):
        self.client.force_login(self.pastor)
        with self.assertLogs("django.request", "WARNING"):
            self.assertEqual(self.post(logo=upload("a.png", png_bytes())).status_code, 403)
        self.church.refresh_from_db()
        self.assertFalse(self.church.logo)



class LogoSizingTests(MediaRootMixin, TestCase):
    def test_fit_never_divides_by_zero_or_infinity(self):
        from core.templatetags.brand import fit

        for width, height in ((0, 10), (10, 0), (float("inf"), 10), (10, float("nan")), (-5, 10)):
            with self.subTest(size=(width, height)):
                self.assertIsNone(fit(width, height, "bar"))

    def test_fit_caps_both_sides(self):
        from core.templatetags.brand import SIZES, fit

        for size, (max_width, max_height) in SIZES.items():
            for width, height in ((1, 4000), (4000, 1), (300, 100), (100, 300)):
                with self.subTest(size=size, image=(width, height)):
                    fitted_width, fitted_height = fit(width, height, size)
                    self.assertLessEqual(fitted_height, max_height)
                    self.assertLessEqual(fitted_width, max_width)
                    self.assertGreaterEqual(min(fitted_width, fitted_height), 1)

    def test_fit_survives_anything_it_is_given(self):
        from core.templatetags.brand import fit

        for width, height in (("300", 100), (None, 100), (300, object()), (10**400, 1)):
            with self.subTest(size=(width, height)):
                self.assertIsNone(fit(width, height, "bar"))
        self.assertIsNone(fit(300, 100, "no-existe"))

    def test_a_degenerate_stored_logo_shows_the_wordmark(self):
        """A file stored before these checks (or by hand): too thin to be a logo, so the wordmark it is."""
        church = Church.objects.create(name="Iglesia del Camino", slug="camino")
        self.give_logo(church, png_file(1, 4000))
        with self.assertLogs("core.logos", "WARNING"):
            self.assertEqual(render("{% church_brand church %}", church=church), '<span class="wordmark">Emmaus</span>')

    def test_the_stylesheet_caps_every_slot(self):
        from django.conf import settings

        css = (settings.BASE_DIR / "static" / "css" / "rotulo.css").read_text()
        self.assertRegex(css, r"\.brand__logo \{[^}]*object-fit: contain;")
        for size, cap in (("bar", "32px"), ("auth", "72px"), ("mark", "40px"), ("deck", "6.25cqw")):
            self.assertRegex(css, rf"\.brand__logo--{size} \{{[^}}]*max-height: {re.escape(cap)};")


class LogoServingHardeningTests(MediaRootMixin, EmmausFixture):
    def test_a_missing_logo_is_a_bare_404_even_signed_in(self):
        self.client.force_login(self.admin)
        for url in (
            reverse("church_logo", args=[self.church.slug, "claro"]),
            reverse("church_logo", args=["no-existe", "claro"]),
            reverse("church_logo", args=[self.church.slug, "azul"]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.content, b"")
                self.assertNotIn(self.admin.email.encode(), response.content)

    def test_a_logo_lost_from_media_is_a_bare_404(self):
        os.remove(self.give_logo(self.church, png_bytes()).path)
        with self.assertLogs("core.logos", "WARNING"):
            response = self.client.get(reverse("church_logo", args=[self.church.slug, "claro"]))
        self.assertEqual((response.status_code, response.content), (404, b""))


class LogoAdminAndPinTests(SimpleTestCase):
    def test_the_django_admin_cannot_upload_around_the_checks(self):
        from django.contrib import admin

        form = admin.site._registry[Church].get_form(RequestFactory().get("/"))
        self.assertNotIn("logo", form.base_fields)
        self.assertNotIn("logo_dark", form.base_fields)

    def test_pillow_is_pinned_to_the_version_in_use(self):
        import PIL
        from django.conf import settings

        pins = (settings.BASE_DIR / "requirements.txt").read_text().lower().splitlines()
        self.assertIn(f"pillow=={PIL.__version__}", [line.split("#", 1)[0].strip() for line in pins])


class SignInByteIdenticalTests(TestCase):
    def test_sign_in_without_a_logo_is_byte_identical_to_before(self):
        """The markup before this feature: nothing at all between the card and the error summary's output."""
        Church.objects.create(name="Iglesia del Camino", slug="camino")
        html = self.client.get(reverse("login")).content.decode()
        self.assertIn('<div class="auth-card">\n  \n\n  \n\n<header class="page-head">', html)


class StoredFileNeverBreaksAPageTests(MediaRootMixin, EmmausFixture):
    """Whatever ends up on disk after an upload, every page still draws, with the wordmark in the logo's place."""

    def corrupt(self, data: bytes):
        fieldfile = self.give_logo(self.church, png_bytes(300, 100))
        with open(fieldfile.path, "wb") as handle:
            handle.write(data)
        return fieldfile

    def corruptions(self):
        good = png_bytes(300, 100)
        flipped = bytearray(good)
        flipped[flipped.index(b"IDAT") + 10] ^= 0xFF
        return {
            "random bytes": os.urandom(4000),
            "truncated": good[: len(good) // 2],
            "svg text": svg_bytes(),
            "image data changed": bytes(flipped),
            "empty": b"",
            "too thin": png_file(1, 4000),
        }

    def test_sign_in_and_the_bar_draw_the_wordmark(self):
        for name, data in self.corruptions().items():
            with self.subTest(name=name):
                self.corrupt(data)
                with self.assertLogs("core.logos", "WARNING"):
                    login = self.client.get(reverse("login"))
                self.assertEqual(login.status_code, 200)
                self.assertNotContains(login, "brand__logo")
                # Logged once per stored file version: the next pages reuse the verdict, silently.
                self.client.force_login(self.mentor)
                page = self.client.get(reverse("mentor_dashboard"))
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, f'<a class="wordmark" href="{reverse("dashboard")}">Emmaus</a>')
                image = self.client.get(reverse("church_logo", args=[self.church.slug, "claro"]))
                self.assertEqual((image.status_code, image.content), (404, b""))
                self.client.logout()

    def test_a_directory_where_the_file_was(self):
        fieldfile = self.give_logo(self.church, png_bytes())
        os.remove(fieldfile.path)
        os.mkdir(fieldfile.path)
        with self.assertLogs("core.logos", "WARNING"):
            self.assertIsNone(logo_meta(self.church.logo))

    def test_a_field_file_that_cannot_say_where_it_is(self):
        class Unreachable:
            name = "church-logos/x.png"

            def __bool__(self):
                return True

            @property
            def path(self):
                raise ValueError("no path")

        with self.assertLogs("core.logos", "WARNING"):
            self.assertIsNone(logo_meta(Unreachable()))
