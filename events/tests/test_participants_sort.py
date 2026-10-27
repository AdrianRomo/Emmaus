"""Camperos (administración): each column header sorts the table (?orden=, ?dir=), keeping the search and the filters.

Each test names the defect it would catch.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

from events.models import EventParticipant

from .base import CampFixture

TABLE_NAMES = re.compile(r'<tr><td class="num">[^<]*</td><td><a href="[^"]+">([^<]+)</a>')
PHONE_NAMES = re.compile(r'<p class="row__title"><a class="row__link" href="[^"]+">([^<]+)</a></p>')
HEADER = re.compile(r"<th ([^>]*)><a href=\"([^\"]+)\">(.*?)</a></th>", re.S)


class ParticipantSortTests(CampFixture):
    def page(self, query=""):
        self.login(self.admin)
        response = self.client.get(self.url("participants") + query)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def names(self, html):
        table = TABLE_NAMES.findall(html)
        self.assertEqual(PHONE_NAMES.findall(html), table)  # the phone rows share the order
        return table

    def headers(self, html):
        """{orden: (attributes of the <th>, query of its link, its text)}."""
        found = {}
        for attrs, href, text in HEADER.findall(html):
            query = parse_qs(urlsplit(unescape(href)).query)
            found[query["orden"][0]] = (attrs, query, text)
        return found

    def test_a_column_sorts_both_ways(self):
        """Defect (owner's ask): the table could only be read by name."""
        by_age = sorted([self.p1, self.p2, self.p3, self.p4, self.p_minor_no_auth], key=lambda p: p.age)
        self.assertEqual(self.names(self.page("?orden=edad&dir=desc")), [p.full_name for p in reversed(by_age)])
        self.assertEqual(self.names(self.page("?orden=edad&dir=asc")), [p.full_name for p in by_age])
        self.assertEqual(self.names(self.page("?orden=edad")), [p.full_name for p in by_age])  # no dir: ascending

    def test_an_unknown_column_falls_back_to_the_name(self):
        """Defect: ?orden=<anything> reaching order_by (a 500, or a sort by a field the page does not show)."""
        by_name = sorted(p.full_name for p in (self.p1, self.p2, self.p3, self.p4, self.p_minor_no_auth))
        for query in ("", "?orden=password", "?orden=coordinator__user__email", "?orden=edad%27", "?orden=nombre&dir=sideways"):
            with self.subTest(query=query):
                self.assertEqual(self.names(self.page(query)), by_name)

    def test_a_campero_without_a_coordinator_goes_last_both_ways(self):
        """Defect: "sin coordinador" first on one database and last on another."""
        EventParticipant.objects.filter(pk=self.p2.pk).update(coordinator=None)
        for direction in ("asc", "desc"):
            with self.subTest(direction=direction):
                self.assertEqual(self.names(self.page(f"?orden=coordinador&dir={direction}"))[-1], self.p2.full_name)

    def test_the_active_header_says_how_it_is_sorted(self):
        """Defect: a screen reader not told which column sorts the table, or which way."""
        headers = self.headers(self.page("?orden=edad&dir=desc"))
        attrs, query, text = headers["edad"]
        self.assertIn('scope="col"', attrs)
        self.assertIn('aria-sort="descending"', attrs)
        self.assertEqual(query["dir"], ["asc"])  # the active header flips the direction
        self.assertIn('<span class="table__sort table__sort--desc" aria-hidden="true"></span>', text)
        self.assertIn('<span class="visually-hidden">, ordenado de mayor a menor</span>', text)
        for key, (attrs, query, text) in headers.items():
            self.assertIn('scope="col"', attrs, key)
            if key != "edad":
                self.assertNotIn("aria-sort", attrs, key)
                self.assertEqual(query["dir"], ["asc"], key)
                self.assertIn('<span class="visually-hidden">, ordenar</span>', text, key)
                self.assertNotIn("table__sort", text, key)
        self.assertEqual(set(headers), {"numero", "nombre", "edad", "cuarto", "grupo", "coordinador", "entrevista"})  # no sedes: no column
        attrs, _, text = self.headers(self.page("?orden=nombre"))["nombre"]
        self.assertIn('aria-sort="ascending"', attrs)
        self.assertIn('<span class="visually-hidden">, ordenado de menor a mayor</span>', text)

    def test_headers_keep_the_filters_and_the_pages_keep_the_order(self):
        """Defect: sorting forgetting the search and filters, or page 2 forgetting the sort."""
        EventParticipant.objects.bulk_create([
            EventParticipant(event=self.event, number=100 + n, full_name=f"Campero {n:02d} Relleno", age=10 + n % 9, room="9", church_group="Iglesia Centro")
            for n in range(60)
        ])
        html = self.page("?estado=pendiente&participa=si&orden=edad&dir=desc&pagina=2")
        for key, (_, query, _) in self.headers(html).items():
            self.assertEqual((query["estado"], query["participa"]), (["pendiente"], ["si"]), key)
            self.assertNotIn("pagina", query, key)  # a new order starts at page 1
        previous = re.search(r'<a class="btn btn--secondary btn--small" href="\?([^"]+)">Anterior</a>', html).group(1)
        self.assertEqual(parse_qs(unescape(previous))["orden"], ["edad"])
        self.assertEqual(parse_qs(unescape(previous))["dir"], ["desc"])
        first = self.page("?orden=edad&dir=desc")
        following = re.search(r'<a class="btn btn--secondary btn--small" href="\?([^"]+)">Siguiente</a>', first).group(1)
        self.assertEqual((parse_qs(unescape(following))["orden"], parse_qs(unescape(following))["dir"]), (["edad"], ["desc"]))
        ages = [int(age) for age in re.findall(r'</a>(?:<span class="table__note">[^<]*</span>)?</td><td class="num">(\d+)</td>', first)]
        self.assertEqual(ages, sorted(ages, reverse=True))
