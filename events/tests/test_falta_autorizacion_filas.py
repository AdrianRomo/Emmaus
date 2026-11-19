"""The coordinator's rows (redesign, ui_v2): a minor whose guardian authorization is not registered says so on the
row, in vela, apart from the interview's state, on «Mi inicio» («Por entrevistar») and on «Entrevistas pendientes»
(«Sin iniciar»); such a row never carries the screen's one filled button. Module off: unchanged."""

from __future__ import annotations

import re

from people import flags

from .base import CampFixture

CHIP = '<span class="chip chip--wait">Falta la autorización</span>'


def main_of(response) -> str:
    return response.content.decode().split("<main", 1)[1]


def row_of(page: str, name: str) -> str:
    match = re.search(r'<li class="row[^"]*">(?:(?!</li>).)*?' + re.escape(name) + r"(?:(?!</li>).)*</li>", page, re.S)
    assert match, f"no row for {name}"
    return match.group(0)


class CoordinatorRowsSayTheMissingAuthorizationTests(CampFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        flags.set_enabled(cls.church, "community", True)

    def test_the_home_and_pendientes_mark_the_minor_without_authorization(self):
        self.client.force_login(self.coord1)
        for name in ("coord_home", "coord_pending"):
            with self.subTest(screen=name):
                page = main_of(self.client.get(self.url(name)))
                blocked = row_of(page, self.p_minor_no_auth.full_name)
                self.assertIn(CHIP, blocked)
                self.assertNotIn("btn--primary", blocked)
                authorized = row_of(page, self.p1.full_name)
                self.assertNotIn(CHIP, authorized)

    def test_with_the_module_off_the_rows_are_unchanged(self):
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.coord1)
        for name in ("coord_home", "coord_pending"):
            with self.subTest(screen=name):
                self.assertNotIn("Falta la autorización", main_of(self.client.get(self.url(name))))
