"""Camperos (redesign, ui_v2): when two filters are on at once — the administration home's «Menores esperan la
autorización» opens minors without it AND not interviewed yet — no capsule is current, so the results' heading names
what the list shows; with one capsule's filter it adds nothing."""

from __future__ import annotations

from people import flags
from people.models import Person

from .base import CampFixture


def main_of(response) -> str:
    return response.content.decode().split("<main", 1)[1]


class CombinedFiltersAreNamedTests(CampFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        flags.set_enabled(cls.church, "community", True)
        Person.objects.create(church=cls.church, user=cls.admin, first_name=cls.admin.first_name, last_name=cls.admin.last_name)

    def test_two_filters_are_said_in_the_heading(self):
        self.client.force_login(self.admin)
        page = main_of(self.client.get(self.url("participants") + "?autorizacion=falta&estado=pendiente"))
        self.assertIn('<span class="camperos__filtro">: menores sin autorización, sin entrevista</span>', page)

    def test_one_capsule_s_filter_adds_nothing(self):
        self.client.force_login(self.admin)
        page = main_of(self.client.get(self.url("participants") + "?autorizacion=falta"))
        self.assertNotIn("camperos__filtro", page)
