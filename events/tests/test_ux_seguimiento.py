"""Seguimiento: who attends each follow-up (UX-15, UI-08, UI-09) and the way back after saving (UX-21).

The list orders open work by urgency, filters by who is responsible, says how many there are when
it shows only the first 200, and lets whoever may manage a follow-up assign it from its row. The
row's control is only a shortcut: the endpoint runs the full ``access.can_manage_follow_up``.
"""

from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.contrib.messages import get_messages
from django.db import connection
from django.shortcuts import resolve_url
from django.test import Client, SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from events.access import resolve_access
from events.models import AuditEvent, EventMembership, FollowUp, MembershipRole

from .base import CampFixture, make_user
from .test_camp_visual import declarations, stylesheet
from .test_ui import ENGLISH, analyse

CARE = FollowUp.Kind.CARE
SAFETY = FollowUp.Kind.SAFETY
PENDING = FollowUp.Status.PENDING
IN_PROGRESS = FollowUp.Status.IN_PROGRESS
DONE = FollowUp.Status.DONE
CANCELLED = FollowUp.Status.CANCELLED

UNSAFE = ("https://evil.example/", "//evil.example/", "/\\evil.example", "http://testserver.evil.example/")
REFUSED = "Elige a una persona del equipo."
SAVED = "Seguimiento actualizado."


class FollowUpBase(CampFixture):
    def follow_up(self, participant, *, kind=CARE, status=PENDING, assigned_to=None, minutes_ago=0, **extra):
        item = FollowUp.objects.create(
            event=self.event, participant=participant, kind=kind, status=status, assigned_to=assigned_to, **extra
        )
        # created_at is auto_now_add: move it afterwards so the order of creation is not the order tested.
        FollowUp.objects.filter(pk=item.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))
        item.refresh_from_db()
        return item

    def listed(self, client, query=""):
        response = client.get(self.url("follow_ups") + query)
        self.assertEqual(response.status_code, 200)
        return response

    @staticmethod
    def ids(response):
        return [f.id for f in response.context["follow_ups"]]

    def assign_url(self, item):
        return self.url("follow_up_assign", item.id)

    def summary(self, item):
        return f"Actualizó un seguimiento del campero #{item.participant.code}"

    def audit_rows(self, item):
        return AuditEvent.objects.filter(event=self.event, action="seguimiento", summary=self.summary(item))

    @staticmethod
    def flashed(response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    @staticmethod
    def responsable_tabs(html):
        match = re.search(r'<ul class="filters" aria-label="Responsable">.*?</ul>', html, re.DOTALL)
        assert match, "no responsable tabs"
        return match.group(0)


# ---- 1. Order: safety first, then what nobody has started, nobody attends, newest ----------------


class OrderTests(FollowUpBase):
    def setUp(self):
        self.safety = self.follow_up(self.p4, kind=SAFETY, minutes_ago=50)
        self.pending_unassigned = self.follow_up(self.p1, minutes_ago=40)
        self.pending_assigned = self.follow_up(self.p2, assigned_to=self.tutor1, minutes_ago=5)
        self.in_progress = self.follow_up(self.p3, status=IN_PROGRESS, minutes_ago=1)
        self.done = self.follow_up(self.p_minor_no_auth, status=DONE, minutes_ago=0)
        self.cancelled = self.follow_up(self.p2, status=CANCELLED, minutes_ago=0)
        self.client = self.login(self.admin, Client())

    def test_open_rows_put_risk_first_then_pending_unassigned_then_pending_assigned_then_in_progress(self):
        response = self.listed(self.client)
        self.assertEqual(
            self.ids(response),
            [self.safety.id, self.pending_unassigned.id, self.pending_assigned.id, self.in_progress.id],
        )

    def test_closed_rows_come_after_the_open_ones(self):
        response = self.listed(self.client, "?estado=todos")
        self.assertEqual(
            self.ids(response),
            [self.safety.id, self.pending_unassigned.id, self.pending_assigned.id, self.in_progress.id, self.done.id, self.cancelled.id],
        )

    def test_unassigned_first_within_a_status_even_when_older(self):
        newer_assigned = self.follow_up(self.p3, assigned_to=self.pastor, minutes_ago=0)
        response = self.listed(self.client, "?estado=pendiente")
        self.assertEqual(self.ids(response), [self.safety.id, self.pending_unassigned.id, newer_assigned.id, self.pending_assigned.id])


# ---- 2. Filter by who is responsable ----------------------------------------------------------------


class ResponsableFilterTests(FollowUpBase):
    def setUp(self):
        self.unassigned = self.follow_up(self.p1, minutes_ago=30)
        self.theirs = self.follow_up(self.p2, assigned_to=self.tutor1, minutes_ago=20)
        self.mine = self.follow_up(self.p3, assigned_to=self.admin, minutes_ago=10)
        self.unassigned_in_progress = self.follow_up(self.p4, status=IN_PROGRESS, minutes_ago=5)
        self.unassigned_done = self.follow_up(self.p_minor_no_auth, status=DONE, minutes_ago=1)
        self.client = self.login(self.admin, Client())

    def test_default_is_everyone(self):
        response = self.listed(self.client)
        self.assertEqual(response.context["responsable"], "todos")
        self.assertEqual(set(self.ids(response)), {self.unassigned.id, self.theirs.id, self.mine.id, self.unassigned_in_progress.id})

    def test_ninguno_shows_only_rows_without_a_responsable(self):
        response = self.listed(self.client, "?responsable=ninguno")
        self.assertEqual(response.context["responsable"], "ninguno")
        self.assertEqual(self.ids(response), [self.unassigned.id, self.unassigned_in_progress.id])

    def test_mio_shows_only_rows_assigned_to_me(self):
        response = self.listed(self.client, "?responsable=mio")
        self.assertEqual(self.ids(response), [self.mine.id])

    def test_the_filter_combines_with_the_status_tab(self):
        response = self.listed(self.client, "?estado=todos&responsable=ninguno")
        self.assertEqual(set(self.ids(response)), {self.unassigned.id, self.unassigned_in_progress.id, self.unassigned_done.id})
        response = self.listed(self.client, "?estado=atendido&responsable=mio")
        self.assertEqual(self.ids(response), [])

    def test_anything_else_means_everyone(self):
        for value in ("otro", "", "MIO", "1"):
            response = self.listed(self.client, f"?responsable={value}")
            self.assertEqual(response.context["responsable"], "todos", value)
            self.assertEqual(len(self.ids(response)), 4, value)

    def test_the_tabs_count_over_the_current_status_and_mark_the_current_one(self):
        tabs = self.responsable_tabs(self.listed(self.client, "?responsable=ninguno").content.decode())
        self.assertIn('<a href="?estado=abiertos&amp;responsable=ninguno" aria-current="page">Sin asignar <span class="count">2</span></a>', tabs)
        self.assertIn('<a href="?estado=abiertos&amp;responsable=mio">Míos <span class="count">1</span></a>', tabs)
        self.assertIn('<a href="?estado=abiertos">Todos</a>', tabs)
        self.assertEqual(tabs.count('aria-current="page"'), 1)
        # Over every status, the closed unassigned one counts too.
        tabs = self.responsable_tabs(self.listed(self.client, "?estado=todos").content.decode())
        self.assertIn('<a href="?estado=todos" aria-current="page">Todos</a>', tabs)
        self.assertIn('Sin asignar <span class="count">3</span>', tabs)
        self.assertEqual(tabs.count('aria-current="page"'), 1)

    def test_each_tab_keeps_the_other_parameter(self):
        html = self.listed(self.client, "?estado=en_proceso&responsable=mio").content.decode()
        self.assertIn('href="?estado=pendiente&amp;responsable=mio"', html)
        self.assertIn('href="?estado=abiertos&amp;responsable=mio"', html)
        self.assertIn('href="?estado=todos&amp;responsable=mio"', html)
        self.assertIn('href="?estado=en_proceso&amp;responsable=ninguno"', html)
        self.assertIn('href="?estado=en_proceso"', html)
        # With every responsable the status tabs carry no responsable at all.
        html = self.listed(self.client, "?estado=en_proceso").content.decode()
        self.assertIn('href="?estado=pendiente"', html)
        self.assertNotIn("responsable=todos", html)

    def test_a_strange_status_is_echoed_encoded(self):
        html = self.listed(self.client, '?estado="><b>x&responsable=mio').content.decode()
        self.assertNotIn("<b>x", html)
        self.assertIn('href="?estado=%22%3E%3Cb%3Ex&amp;responsable=ninguno"', html)

    def test_empty_state_per_filter(self):
        FollowUp.objects.filter(pk__in=[self.unassigned.pk, self.unassigned_in_progress.pk]).update(assigned_to=self.tutor2)
        self.assertContains(self.listed(self.client, "?responsable=ninguno"), "Todos los seguimientos abiertos tienen responsable.")
        FollowUp.objects.filter(pk=self.mine.pk).update(assigned_to=self.tutor2)
        self.assertContains(self.listed(self.client, "?responsable=mio"), "No tienes seguimientos asignados en esta lista.")
        response = self.listed(self.client, "?estado=todos&responsable=mio")
        self.assertContains(response, "No tienes seguimientos asignados.")
        self.assertNotContains(response, "en esta lista")
        FollowUp.objects.all().delete()
        response = self.listed(self.client)
        self.assertContains(
            response,
            "No hay seguimientos en esta lista. Cuando un coordinador marque que un campero necesita seguimiento aparecerá aquí.",
        )
        self.assertNotContains(response, "Todos los seguimientos abiertos tienen responsable.")
        self.assertNotContains(response, "No tienes seguimientos asignados")

    def test_an_empty_filtered_tab_speaks_of_that_tab_only(self):
        # Closed tab, nobody unassigned there, while an OPEN one has nobody: not "todos los abiertos tienen responsable".
        FollowUp.objects.filter(pk=self.unassigned_done.pk).update(assigned_to=self.tutor1)
        self.assertTrue(FollowUp.objects.filter(status=PENDING, assigned_to__isnull=True).exists())
        response = self.listed(self.client, "?estado=atendido&responsable=ninguno")
        self.assertEqual(self.ids(response), [])
        self.assertContains(response, '<p class="empty-line">Ningún seguimiento de esta lista está sin asignar.</p>', html=True)
        self.assertNotContains(response, "Todos los seguimientos abiertos tienen responsable.")
        # "En proceso" has none of mine while a pendiente one is mine: not "No tienes seguimientos asignados."
        self.assertEqual(self.mine.status, PENDING)
        response = self.listed(self.client, "?estado=en_proceso&responsable=mio")
        self.assertEqual(self.ids(response), [])
        self.assertContains(response, '<p class="empty-line">No tienes seguimientos asignados en esta lista.</p>', html=True)
        self.assertNotContains(response, "No tienes seguimientos asignados.")


# ---- 3. The total, and the 200 shown -------------------------------------------------------------


class TotalTests(FollowUpBase):
    def bulk(self, n):
        campers = [self.p1, self.p2, self.p3, self.p4]
        FollowUp.objects.bulk_create(FollowUp(event=self.event, participant=campers[i % 4]) for i in range(n))

    def test_more_than_200_says_how_many_are_shown(self):
        self.bulk(201)
        response = self.listed(self.login(self.pastor, Client()))
        self.assertEqual(response.context["total"], 201)
        self.assertEqual(len(response.context["follow_ups"]), 200)
        self.assertContains(response, "201 seguimientos")
        self.assertContains(response, '<p class="aside-note">Mostrando 200 de 201. Usa los filtros para ver los demás.</p>', html=True)

    def test_200_or_fewer_say_nothing_about_a_cap(self):
        self.bulk(200)
        response = self.listed(self.login(self.pastor, Client()))
        self.assertEqual(response.context["total"], 200)
        self.assertContains(response, "200 seguimientos")
        self.assertNotContains(response, "Mostrando")

    def test_one_is_singular(self):
        self.bulk(1)
        response = self.listed(self.login(self.pastor, Client()))
        self.assertContains(response, "1 seguimiento<")
        self.assertNotContains(response, "1 seguimientos")

    def test_the_total_follows_the_filter(self):
        self.bulk(5)
        FollowUp.objects.filter(pk=FollowUp.objects.order_by("pk").first().pk).update(assigned_to=self.pastor)
        response = self.listed(self.login(self.pastor, Client()), "?responsable=mio")
        self.assertEqual(response.context["total"], 1)
        self.assertContains(response, "1 seguimiento<")

    def test_rows_do_not_cost_a_query_each(self):
        self.bulk(3)
        client = self.login(self.pastor, Client())
        self.listed(client)  # the first visit also stores the current event in the session
        with CaptureQueriesContext(connection) as few:
            self.listed(client)
        self.bulk(30)
        with CaptureQueriesContext(connection) as many:
            self.listed(client)
        self.assertEqual(len(few), len(many), [q["sql"] for q in many][-10:])


# ---- 4. Every row says who attends it, and offers the assign control when it may ------------------


class RowTests(FollowUpBase):
    def setUp(self):
        self.unassigned = self.follow_up(self.p1, minutes_ago=30)
        self.assigned = self.follow_up(self.p2, assigned_to=self.tutor1, minutes_ago=20)
        nameless = make_user("sin.nombre@prueba.mx", "", "")
        EventMembership.objects.create(event=self.event, user=nameless, role=MembershipRole.PASTOR)
        self.nameless = self.follow_up(self.p3, assigned_to=nameless, minutes_ago=10)
        self.closed = self.follow_up(self.p4, status=DONE, assigned_to=self.tutor2, minutes_ago=5)
        self.client = self.login(self.admin, Client())

    def test_each_row_says_responsable_or_sin_asignar_on_its_own_line(self):
        html = self.listed(self.client, "?estado=todos").content.decode()
        self.assertIn('<p class="row__meta">Responsable: Teresa Uno</p>', html)
        self.assertIn('<p class="row__meta">Responsable: sin.nombre@prueba.mx</p>', html)
        self.assertIn('<p class="row__meta">Responsable: Tomás Dos</p>', html)
        self.assertEqual(html.count('<p class="row__meta">Sin asignar</p>'), 1)
        self.assertNotIn(". Responsable:", html)
        # The first line keeps three data: reason, coordinator, since.
        self.assertIn(f'<p class="row__meta">{self.unassigned.get_reason_display()}, Carla Uno, ', html)

    def test_the_row_link_carries_the_way_back(self):
        html = self.listed(self.client, "?estado=todos&responsable=ninguno").content.decode()
        back = quote(self.url("follow_ups") + "?estado=todos&responsable=ninguno")
        self.assertIn(f'href="{self.url("follow_up_detail", self.unassigned.id)}?siguiente={back}"', html)

    def test_an_open_row_has_a_labelled_assign_form(self):
        response = self.listed(self.client, "?estado=todos")
        html = response.content.decode()
        f = self.unassigned
        self.assertIn(f'<form method="post" action="{self.assign_url(f)}" class="row-assign">', html)
        self.assertIn(f'<label class="visually-hidden" for="asignar-{f.id}">Responsable de {f.participant.full_name}</label>', html)
        self.assertIn(f'<select class="input input--small" id="asignar-{f.id}" name="assigned_to">', html)
        self.assertIn(f'<input type="hidden" name="siguiente" value="{self.url("follow_ups")}?estado=todos">', html)
        self.assertIn(f'<option value="{self.tutor1.id}" selected>Teresa Uno</option>', html)  # the assigned row
        self.assertIn('<button class="btn btn--secondary btn--small" type="submit">Asignar</button>', html)
        # The campero's own coordinador is offered on that row only, after the leaders.
        self.assertIn(f'<option value="{self.coord1.id}">Carla Uno</option>', html)
        self.assertNotIn(f'<option value="{self.coord3.id}">', html)
        self.assertNotIn(f'action="{self.assign_url(self.closed)}"', html)
        self.assertEqual(html.count('class="row-assign"'), 3)
        self.assertEqual(html.count("btn--primary"), 0)

    def test_the_page_stays_spanish_and_every_control_is_labelled(self):
        for user in (self.admin, self.pastor, self.tutor1, self.coord1):
            html = self.listed(self.login(user, Client()), "?estado=todos").content.decode()
            parsed = analyse(html)
            match = ENGLISH.search(" ".join(parsed.text))
            self.assertIsNone(match, (user.email, match and match.group(0)))
            ids = [attrs.get("id") for _, attrs in parsed.controls]
            self.assertEqual(len(ids), len(set(ids)), user.email)
            for tag, attrs in parsed.controls:  # the rule of test_ui.test_pages_are_spanish_and_labelled
                labelled = attrs.get("id") in parsed.labels_for or attrs.get("aria-label") or "visually-hidden" in attrs.get("class", "")
                self.assertTrue(labelled, (user.email, tag, attrs.get("name")))
            self.assertTrue(any(attrs.get("name") == "assigned_to" for _, attrs in parsed.controls), user.email)
            self.assertEqual(html.count("<h1"), 1)

    def test_someone_who_left_the_team_stays_selected_and_cannot_be_sent_again(self):
        EventMembership.objects.filter(pk=self.m_tutor1.pk).update(is_active=False)
        html = self.listed(self.client).content.decode()
        select = re.search(rf'<select [^>]*id="asignar-{self.assigned.id}"[^>]*>(.*?)</select>', html, re.DOTALL).group(1)
        self.assertIn(f'<option value="{self.tutor1.id}" selected>Teresa Uno (ya no está en el equipo)</option>', select)
        self.assertEqual(select.count("selected"), 1)
        self.assertIn('<option value="">Sin asignar</option>', select)
        self.assertIn('<p class="row__meta">Responsable: Teresa Uno</p>', html)
        # Only on that row: the others do not offer her at all.
        other = re.search(rf'<select [^>]*id="asignar-{self.unassigned.id}"[^>]*>(.*?)</select>', html, re.DOTALL).group(1)
        self.assertNotIn(f'value="{self.tutor1.id}"', other)
        # Sending the row unchanged is refused and leaves her assigned; nothing is written.
        siguiente = self.url("follow_ups")
        response = self.client.post(self.assign_url(self.assigned), {"assigned_to": self.tutor1.id, "siguiente": siguiente})
        self.assertRedirects(response, siguiente, fetch_redirect_response=False)
        self.assertEqual(self.flashed(response), [REFUSED])
        self.assigned.refresh_from_db()
        self.assertEqual(self.assigned.assigned_to, self.tutor1)
        self.assertFalse(self.audit_rows(self.assigned).exists())

    def test_an_eligible_assignee_is_never_marked_as_gone(self):
        self.assertNotContains(self.listed(self.client, "?estado=todos"), "ya no está en el equipo")
        # The campero's own coordinador is eligible even though a coordinador is not a leader.
        FollowUp.objects.filter(pk=self.unassigned.pk).update(assigned_to=self.coord1)
        html = self.listed(self.client).content.decode()
        self.assertNotIn("ya no está en el equipo", html)
        self.assertIn(f'<option value="{self.coord1.id}" selected>Carla Uno</option>', html)

    def test_the_row_rule_is_the_rule_of_can_manage_follow_up(self):
        """For every role, the control appears exactly where the endpoint would accept the POST."""
        self.follow_up(self.p4, kind=SAFETY, created_by=self.coord3, minutes_ago=3)
        self.follow_up(self.p2, kind=SAFETY, created_by=self.pastor, minutes_ago=2)
        self.follow_up(self.p1, confidential=True, created_by=self.coord1, minutes_ago=1)
        users = (self.admin, self.pastor, self.care, self.tutor1, self.tutor2, self.coord1, self.coord2, self.coord3)
        seen = 0
        for user in users:
            access = resolve_access(user, self.event)
            response = self.listed(self.login(user, Client()), "?estado=todos")
            html = response.content.decode()
            for f in response.context["follow_ups"]:
                seen += 1
                self.assertEqual(f.can_assign, access.can_manage_follow_up(f), (user.email, f.kind, f.participant.full_name))
                shown = f'action="{self.assign_url(f)}"' in html
                self.assertEqual(shown, f.can_assign and f.is_open, (user.email, f.id))
        self.assertGreater(seen, 20)


# ---- 5. The assign endpoint ----------------------------------------------------------------------


class AssignTests(FollowUpBase):
    def setUp(self):
        self.item = self.follow_up(self.p1, minutes_ago=10)
        self.list_url = self.url("follow_ups")

    def post(self, user, item=None, **data):
        client = self.login(user, Client())
        return client.post(self.assign_url(item or self.item), data)

    def assert_unchanged(self, assigned_to=None):
        self.item.refresh_from_db()
        self.assertEqual(self.item.assigned_to, assigned_to)
        self.assertEqual(self.item.status, PENDING)
        self.assertFalse(self.audit_rows(self.item).exists())

    def test_pastor_assigns_and_returns_to_siguiente(self):
        siguiente = self.list_url + "?estado=todos&responsable=ninguno"
        response = self.post(self.pastor, assigned_to=self.tutor1.id, siguiente=siguiente)
        self.assertRedirects(response, siguiente, fetch_redirect_response=False)
        self.item.refresh_from_db()
        self.assertEqual(self.item.assigned_to, self.tutor1)
        rows = list(self.audit_rows(self.item))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].actor, self.pastor)
        self.assertEqual(rows[0].metadata, {"status": "pendiente"})
        self.assertEqual(rows[0].object_type, "FollowUp")
        self.assertEqual(self.flashed(response), [SAVED])

    def test_only_the_responsable_is_written(self):
        self.post(self.admin, assigned_to=self.pastor.id, status=DONE, resolution_note="no", kind=SAFETY, siguiente=self.list_url)
        self.item.refresh_from_db()
        self.assertEqual(self.item.assigned_to, self.pastor)
        self.assertEqual((self.item.status, self.item.resolution_note, self.item.kind, self.item.resolved_at), (PENDING, "", CARE, None))

    def test_an_empty_value_unassigns(self):
        FollowUp.objects.filter(pk=self.item.pk).update(assigned_to=self.tutor1)
        response = self.post(self.tutor1, assigned_to="", siguiente=self.list_url)
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.assigned_to)
        self.assertEqual(self.audit_rows(self.item).count(), 1)

    def test_the_campero_s_own_coordinador_may_take_it(self):
        response = self.post(self.coord1, assigned_to=self.coord1.id, siguiente=self.list_url)
        self.assertEqual(self.flashed(response), [SAVED])
        self.item.refresh_from_db()
        self.assertEqual(self.item.assigned_to, self.coord1)

    def test_tutor_of_the_other_team_gets_404(self):
        response = self.post(self.tutor2, assigned_to=self.tutor2.id, siguiente=self.list_url)
        self.assertEqual(response.status_code, 404)
        self.assert_unchanged()

    def test_coordinator_of_another_campero_cannot_assign(self):
        # access.follow_ups() of coord2 never contains coord1's camperos, so the row does not exist for them.
        response = self.post(self.coord2, assigned_to=self.coord2.id, siguiente=self.list_url)
        self.assertEqual(response.status_code, 404)
        self.assert_unchanged()

    def test_pastor_without_sensitive_access_cannot_assign_a_safety_report(self):
        report = self.follow_up(self.p2, kind=SAFETY, created_by=self.pastor, minutes_ago=1)
        pastor = self.login(self.pastor, Client())
        html = self.listed(pastor).content.decode()
        self.assertIn(report.participant.full_name, html)  # the reporter still sees that it exists
        self.assertNotIn(f'action="{self.assign_url(report)}"', html)
        self.assertIn(f'action="{self.assign_url(self.item)}"', html)  # an ordinary one is still assignable
        response = pastor.post(self.assign_url(report), {"assigned_to": self.pastor.id, "siguiente": self.list_url})
        self.assertEqual(response.status_code, 403)
        report.refresh_from_db()
        self.assertIsNone(report.assigned_to)
        self.assertFalse(self.audit_rows(report).exists())
        # The authorized care team can.
        response = self.post(self.care, report, assigned_to=self.care.id, siguiente=self.list_url)
        self.assertEqual(response.status_code, 302)
        report.refresh_from_db()
        self.assertEqual(report.assigned_to, self.care)

    def test_a_reporting_coordinator_sees_the_safety_report_but_cannot_assign_it(self):
        report = self.follow_up(self.p2, kind=SAFETY, created_by=self.coord1, minutes_ago=1)
        coord = self.login(self.coord1, Client())
        html = self.listed(coord).content.decode()
        self.assertIn(report.participant.full_name, html)
        self.assertNotIn(f'action="{self.assign_url(report)}"', html)
        self.assertEqual(coord.post(self.assign_url(report), {"assigned_to": self.coord1.id}).status_code, 403)
        report.refresh_from_db()
        self.assertIsNone(report.assigned_to)

    def test_a_confidential_referral_is_404_outside_the_care_team(self):
        secret = self.follow_up(self.p3, confidential=True, created_by=self.coord2, minutes_ago=1)
        for user in (self.admin, self.pastor, self.tutor1):
            self.assertEqual(self.post(user, secret, assigned_to=user.id).status_code, 404, user.email)
        secret.refresh_from_db()
        self.assertIsNone(secret.assigned_to)

    def test_foreign_siguiente_falls_back_to_the_list(self):
        for unsafe in UNSAFE:
            response = self.post(self.pastor, assigned_to=self.tutor1.id, siguiente=unsafe)
            self.assertRedirects(response, self.list_url, fetch_redirect_response=False, msg_prefix=unsafe)
        response = self.post(self.pastor, assigned_to=self.tutor1.id)
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)

    def test_a_person_outside_the_team_is_refused(self):
        FollowUp.objects.filter(pk=self.item.pk).update(assigned_to=self.tutor1)
        inactive = make_user("baja@prueba.mx", "Bea", "Baja")
        EventMembership.objects.create(event=self.event, user=inactive, role=MembershipRole.PASTOR, is_active=False)
        for value in (self.outsider.id, self.other_coord.id, self.coord3.id, inactive.id, 999999, "abc", "²"):
            siguiente = self.list_url + "?responsable=mio"
            response = self.post(self.pastor, assigned_to=value, siguiente=siguiente)
            self.assertRedirects(response, siguiente, fetch_redirect_response=False, msg_prefix=str(value))
            self.assertEqual(self.flashed(response), [REFUSED], value)
            self.assert_unchanged(assigned_to=self.tutor1)

    def test_get_is_refused(self):
        client = self.login(self.pastor, Client())
        self.assertEqual(client.get(self.assign_url(self.item)).status_code, 405)

    def test_anonymous_goes_to_login(self):
        response = Client().post(self.assign_url(self.item), {"assigned_to": self.tutor1.id})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(resolve_url(settings.LOGIN_URL)), response["Location"])
        self.assert_unchanged()

    def test_another_event_s_follow_up_is_404(self):
        other = FollowUp.objects.create(event=self.other_event, participant=self.p_other_event)
        client = self.login(self.admin, Client())
        self.assertEqual(client.post(self.assign_url(other), {"assigned_to": self.admin.id}).status_code, 404)
        other.refresh_from_db()
        self.assertIsNone(other.assigned_to)


# ---- 6. The detail returns where it was opened -----------------------------------------------------


class ReturnToOriginTests(FollowUpBase):
    def setUp(self):
        self.item = self.follow_up(self.p1, minutes_ago=10)
        self.pending_url = self.url("pending")
        self.detail_url = self.url("follow_up_detail", self.item.id)
        self.list_url = self.url("follow_ups")
        self.client = self.login(self.admin, Client())

    def test_saving_returns_to_siguiente(self):
        response = self.client.post(self.detail_url, {"status": "en_proceso", "assigned_to": "", "siguiente": self.pending_url})
        self.assertRedirects(response, self.pending_url, fetch_redirect_response=False)
        self.assertEqual(self.flashed(response), [SAVED])
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, IN_PROGRESS)

    def test_the_query_string_alone_also_brings_you_back(self):
        response = self.client.post(f"{self.detail_url}?siguiente={quote(self.pending_url)}", {"status": "en_proceso", "assigned_to": ""})
        self.assertRedirects(response, self.pending_url, fetch_redirect_response=False)

    def test_a_foreign_siguiente_falls_back_to_the_list(self):
        for unsafe in UNSAFE:
            response = self.client.post(self.detail_url, {"status": "en_proceso", "assigned_to": "", "siguiente": unsafe})
            self.assertRedirects(response, self.list_url, fetch_redirect_response=False, msg_prefix=unsafe)
            back = self.client.get(f"{self.detail_url}?siguiente={quote(unsafe)}").context["back"]
            self.assertEqual(back, self.list_url, unsafe)
        response = self.client.post(self.detail_url, {"status": "en_proceso", "assigned_to": ""})
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)

    def test_the_back_link_and_the_form_echo_it(self):
        html = self.client.get(f"{self.detail_url}?siguiente={quote(self.pending_url)}").content.decode()
        self.assertIn(f'<a class="back-link" href="{self.pending_url}">Volver</a>', html)
        self.assertIn(f'<input type="hidden" name="siguiente" value="{self.pending_url}">', html)
        html = self.client.get(self.detail_url).content.decode()
        self.assertIn(f'<a class="back-link" href="{self.list_url}">Volver</a>', html)

    def test_an_invalid_form_keeps_the_way_back(self):
        response = self.client.post(self.detail_url, {"status": "otro", "assigned_to": "", "siguiente": self.pending_url})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'<input type="hidden" name="siguiente" value="{self.pending_url}">', html=False)
        self.assertContains(response, f'<a class="back-link" href="{self.pending_url}">Volver</a>', html=False)

    def test_the_pending_page_links_with_its_own_address(self):
        html = self.client.get(self.pending_url + "?x=1").content.decode()
        self.assertIn(f'href="{self.detail_url}?siguiente={quote(self.pending_url + "?x=1")}"', html)

    def test_the_tutor_home_links_back_to_the_tutor_home_never_to_the_fetch_url(self):
        tutor = self.login(self.tutor1, Client())
        home = self.url("tutor_home")
        expected = f'href="{self.detail_url}?siguiente={home}"'
        self.assertIn(expected, tutor.get(home).content.decode())
        fragment = tutor.get(home + "?_=1730000000000", HTTP_X_REQUESTED_WITH="fetch").content.decode()
        self.assertIn(expected, fragment)
        self.assertNotIn("_=1730000000000", fragment)
        # And the detail opened from there returns to it.
        response = tutor.post(f"{self.detail_url}?siguiente={home}", {"status": "en_proceso", "assigned_to": ""})
        self.assertRedirects(response, home, fetch_redirect_response=False)


# ---- Phones: a long name or e-mail never pushes the row past the edge -----------------------------


class PhoneLayoutTests(SimpleTestCase):
    def test_the_form_and_the_select_may_shrink_below_their_widest_option(self):
        css = stylesheet()
        for width in (360, 390, 600):
            self.assertEqual(declarations(css, ".row-assign", width).get("min-width"), "0", width)
            self.assertEqual(declarations(css, ".row-assign .input--small", width).get("min-width"), "0", width)
            self.assertEqual(declarations(css, ".row-assign", width).get("flex"), "1 1 100%", width)

    def test_a_long_e_mail_in_the_meta_line_breaks(self):
        css = stylesheet()
        for width in (390, 1440):
            self.assertEqual(declarations(css, ".results .row__meta", width).get("overflow-wrap"), "anywhere", width)
