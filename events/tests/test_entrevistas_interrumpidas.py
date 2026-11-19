"""Interviews left half done (issue #47).

The coordinador reads "Tienes N entrevistas sin terminar" on their home with "Retomar" and "Descartar" (the actions
that already existed); a tutor reads their team's per coordinador and can open none; the administración reads them on
its home, and ending the event lists the ones still in progress first. Nothing discards an interview by itself.
"""

from __future__ import annotations

from datetime import timedelta

from django.test import Client
from django.utils import timezone

from events.models import Event, Interview
from events.services.metrics import STALE_MINUTES

from .base import CampFixture

IN_PROGRESS = Interview.Status.IN_PROGRESS
FETCH = {"X-Requested-With": "fetch"}


class InterruptedBase(CampFixture):
    def interview(self, participant, *, minutes_idle, conducted_by=None):
        """An interview in progress, last touched ``minutes_idle`` ago, with no claim held (the scheduler released it)."""
        moment = timezone.now() - timedelta(minutes=minutes_idle)
        return Interview.objects.create(
            event=self.event, participant=participant, version=self.version, status=IN_PROGRESS,
            conducted_by=conducted_by, started_at=moment, last_activity_at=moment,
        )

    def get(self, user, name, *args, **kwargs):
        response = self.login(user, Client()).get(self.url(name, *args), **kwargs)
        self.assertEqual(response.status_code, 200, name)
        return response

    def resume_url(self, interview):
        return self.url("interview_resume", interview.public_id)

    def discard_url(self, interview):
        return self.url("interview_discard", interview.public_id)

    def basics(self, **changes):
        event = Event.objects.get(pk=self.event.pk)
        data = {
            "form": "basics", "basics-name": event.name, "basics-kind": event.kind, "basics-slug": event.slug,
            "basics-location": event.location, "basics-starts_on": event.starts_on.isoformat(),
            "basics-ends_on": event.ends_on.isoformat(), "basics-status": event.status,
        }
        data.update(changes)
        return data


class CoordinatorHomeTests(InterruptedBase):
    def setUp(self):
        self.old = self.interview(self.p1, minutes_idle=STALE_MINUTES + 600, conducted_by=self.coord1)
        self.older = self.interview(self.p2, minutes_idle=STALE_MINUTES + 1200, conducted_by=self.coord1)
        self.moving = self.interview(self.p_minor_no_auth, minutes_idle=STALE_MINUTES - 5, conducted_by=self.coord1)
        self.elsewhere = self.interview(self.p3, minutes_idle=STALE_MINUTES + 600, conducted_by=self.coord2)

    def test_the_home_says_how_many_and_offers_retomar_and_descartar_for_each(self):
        response = self.get(self.coord1, "coord_home")
        self.assertEqual([iv.pk for iv in response.context["stalled"]], [self.older.pk, self.old.pk])  # the oldest first
        self.assertContains(response, '<h2 id="entrevistas-interrumpidas" class="heading">Tienes 2 entrevistas sin terminar</h2>', html=True)
        self.assertContains(response, f"Llevan {STALE_MINUTES} minutos o más sin cambios.")
        html = response.content.decode()
        prompt = html[html.index('id="entrevistas-interrumpidas"'):html.index('class="coord__lists')]
        for interview in (self.old, self.older):
            self.assertIn(f'href="{self.resume_url(interview)}"', prompt)
            self.assertIn(f'href="{self.discard_url(interview)}"', prompt)
            self.assertIn(f"Retomar<span class=\"visually-hidden\"> la entrevista de {interview.participant.full_name}</span>", prompt)
            self.assertIn(f"Descartar<span class=\"visually-hidden\"> la entrevista de {interview.participant.full_name}</span>", prompt)
        self.assertEqual(prompt.count("Interrumpida"), 2)
        # One still moving is not in the prompt: it stays in "También sin terminar" or in the sign.
        self.assertNotIn(str(self.moving.public_id), prompt)
        self.assertNotIn(str(self.elsewhere.public_id), html)  # another coordinador's

    def test_one_interview_reads_in_the_singular(self):
        Interview.objects.filter(pk=self.older.pk).delete()
        response = self.get(self.coord1, "coord_home")
        self.assertContains(response, "Tienes 1 entrevista sin terminar")
        self.assertContains(response, f"Lleva {STALE_MINUTES} minutos o más sin cambios. Al descartar se borran sus respuestas.")

    def test_the_live_fragment_carries_the_prompt(self):
        response = self.get(self.coord1, "coord_home", headers=FETCH)
        self.assertContains(response, "Tienes 2 entrevistas sin terminar")
        self.assertContains(response, self.resume_url(self.old))

    def test_under_the_limit_there_is_no_prompt(self):
        Interview.objects.filter(pk__in=[self.old.pk, self.older.pk]).delete()
        response = self.get(self.coord1, "coord_home")
        self.assertEqual(response.context["stalled"], [])
        self.assertNotContains(response, "entrevistas-interrumpidas")
        self.assertNotContains(response, "sin terminar</h2>")
        self.assertContains(response, self.resume_url(self.moving))  # the sign still offers to finish it

    def test_tambien_sin_terminar_does_not_repeat_the_interrupted_ones(self):
        response = self.get(self.coord1, "coord_home")
        # The newest in progress is the sign's; the other two are in the prompt, so the old list has nothing left.
        self.assertEqual(response.context["also_in_progress"], [])
        self.assertNotContains(response, "También sin terminar")

    def test_resuming_from_the_prompt_opens_the_interview_where_it_was_left(self):
        client = self.login(self.coord1, Client())
        self.choose_station(client)
        response = client.get(self.resume_url(self.old))
        self.assertRedirects(response, self.url("interview_step", self.old.public_id, self.steps[0].number), fetch_redirect_response=False)
        self.old.refresh_from_db()
        self.assertEqual(self.old.status, IN_PROGRESS)
        self.assertEqual(self.old.lock_holder, self.coord1)
        self.assertEqual(client.get(response.url).status_code, 200)

    def test_descartar_from_the_prompt_asks_first_and_discards_only_on_confirmation(self):
        client = self.login(self.coord1, Client())
        response = client.get(self.discard_url(self.old))
        self.assertContains(response, "¿Descartar la entrevista de Mariana?")
        self.old.refresh_from_db()
        self.assertEqual(self.old.status, IN_PROGRESS)  # opening the question discards nothing
        response = client.post(self.discard_url(self.old), {"reason": "no_desea"})
        self.assertRedirects(response, self.url("coord_home"), fetch_redirect_response=False)
        self.old.refresh_from_db()
        self.assertEqual(self.old.status, Interview.Status.DISCARDED)
        self.assertContains(client.get(self.url("coord_home")), "Tienes 1 entrevista sin terminar")

    def test_nothing_is_discarded_by_itself(self):
        for user, name in ((self.coord1, "coord_home"), (self.tutor1, "tutor_home"), (self.tutor1, "pending"), (self.admin, "admin_home"),
                           (self.admin, "pending"), (self.pastor, "pastor_home")):
            self.get(user, name)
            self.get(user, name, headers=FETCH)
        self.assertEqual(Interview.objects.filter(event=self.event, status=IN_PROGRESS).count(), 4)

    def test_another_coordinador_cannot_open_them_by_guessing_the_address(self):
        client = self.login(self.coord3, Client())
        for url in (self.resume_url(self.old), self.discard_url(self.old)):
            self.assertEqual(client.get(url).status_code, 404, url)
        self.assertEqual(client.post(self.discard_url(self.old), {"reason": "otro"}).status_code, 404)
        self.old.refresh_from_db()
        self.assertEqual((self.old.status, self.old.lock_holder), (IN_PROGRESS, None))


class TutorTests(InterruptedBase):
    def setUp(self):
        self.mine_a = self.interview(self.p1, minutes_idle=STALE_MINUTES + 30, conducted_by=self.coord1)
        self.mine_b = self.interview(self.p3, minutes_idle=STALE_MINUTES + 900, conducted_by=self.coord2)
        self.other_team = self.interview(self.p4, minutes_idle=STALE_MINUTES + 900, conducted_by=self.coord3)

    def test_a_tutor_reads_their_teams_interrupted_interviews_per_coordinador(self):
        response = self.get(self.tutor1, "pending")
        groups = response.context["stale_groups"]
        # The coordinador whose interview waits longest comes first; nothing of the other team.
        self.assertEqual([(g["coordinator"], [iv.pk for iv in g["rows"]]) for g in groups], [(self.m_coord2, [self.mine_b.pk]), (self.m_coord1, [self.mine_a.pk])])
        html = response.content.decode()
        section = html[html.index('id="interrumpidas"'):html.index('id="seguimientos"')]
        self.assertIn(f'<a href="{self.url("coordinator_detail", self.m_coord2.id)}">César Dos</a> <span class="count">1</span>', section)
        self.assertIn(f'<a href="{self.url("coordinator_detail", self.m_coord1.id)}">Carla Uno</a> <span class="count">1</span>', section)
        self.assertLess(section.index("César Dos"), section.index("Carla Uno"))
        self.assertIn(self.p1.full_name, section)
        self.assertIn(self.p3.full_name, section)
        self.assertNotIn(self.p4.full_name, html)
        self.assertNotIn("Cora Tres", html)

    def test_a_tutor_gets_no_action_on_the_rows(self):
        html = self.get(self.tutor1, "pending").content.decode()
        self.assertNotIn("/continuar/", html)
        self.assertNotIn("/descartar/", html)
        self.assertIn("Pide a cada coordinador que las retome o las descarte", html)

    def test_the_other_tutor_reads_only_theirs(self):
        response = self.get(self.tutor2, "pending")
        self.assertEqual([g["coordinator"] for g in response.context["stale_groups"]], [self.m_coord3])
        self.assertNotContains(response, self.p1.full_name)
        self.assertNotContains(response, self.p3.full_name)

    def test_a_guessed_interview_address_stays_403_for_a_tutor(self):
        client = self.login(self.tutor1, Client())
        for interview in (self.mine_a, self.other_team):  # theirs to read, never theirs to conduct
            self.assertEqual(client.get(self.resume_url(interview)).status_code, 403)
            self.assertEqual(client.get(self.discard_url(interview)).status_code, 403)
            self.assertEqual(client.post(self.discard_url(interview), {"reason": "otro"}).status_code, 403)
        self.assertEqual(Interview.objects.filter(event=self.event, status=IN_PROGRESS).count(), 3)

    def test_a_guessed_coordinador_of_another_team_is_not_shown(self):
        client = self.login(self.tutor1, Client())
        self.assertEqual(client.get(self.url("coordinator_detail", self.m_coord3.id)).status_code, 404)
        self.assertEqual(self.login(self.outsider, Client()).get(self.url("pending")).status_code, 403)
        self.assertEqual(self.login(self.coord1, Client()).get(self.url("pending")).status_code, 403)

    def test_the_administracion_gets_the_actions_on_pendientes(self):
        html = self.get(self.admin, "pending").content.decode()
        for interview in (self.mine_a, self.mine_b, self.other_team):
            self.assertIn(f'href="{self.resume_url(interview)}"', html)
            self.assertIn(f'href="{self.discard_url(interview)}"', html)
        pastor = self.get(self.pastor, "pending").content.decode()
        self.assertIn(self.p4.full_name, pastor)
        self.assertNotIn("/continuar/", pastor)


class AdminHomeAndClosingTests(InterruptedBase):
    def setUp(self):
        self.old = self.interview(self.p1, minutes_idle=STALE_MINUTES + 600, conducted_by=self.coord1)
        self.moving = self.interview(self.p4, minutes_idle=2, conducted_by=self.coord3)
        self.client = self.login(self.admin, Client())

    def close(self, status=Event.Status.CLOSED, **extra):
        return self.client.post(self.url("settings"), self.basics(**{"basics-status": status}, **extra))

    def status(self):
        return Event.objects.get(pk=self.event.pk).status

    def test_the_admin_home_lists_the_interrupted_interviews_with_both_actions(self):
        for kwargs in ({}, {"headers": FETCH}):
            response = self.client.get(self.url("admin_home"), **kwargs)
            self.assertEqual(response.context["stalled_total"], 1)
            self.assertContains(response, '<h2 id="entrevistas-interrumpidas" class="heading">1 entrevista sin terminar</h2>', html=True)
            self.assertContains(response, "Antes de terminar el evento, retómala o descártala: nada se descarta solo.")
            self.assertContains(response, f'href="{self.resume_url(self.old)}"')
            self.assertContains(response, f'href="{self.discard_url(self.old)}"')
            self.assertContains(response, "Carla Uno, computadora sin registrar, última actividad")
            self.assertNotContains(response, self.resume_url(self.moving))

    def test_the_admin_home_says_nothing_when_none_is_interrupted(self):
        Interview.objects.filter(pk=self.old.pk).delete()
        response = self.client.get(self.url("admin_home"))
        self.assertEqual((response.context["stalled_total"], response.context["stalled_interviews"]), (0, []))
        self.assertNotContains(response, "entrevistas-interrumpidas")

    def test_the_admin_home_shows_the_oldest_ten_and_links_the_rest(self):
        for n in range(11):
            camper = self._camper(f"Campero Extra {n}", 20, self.m_coord2)
            self.interview(camper, minutes_idle=STALE_MINUTES + 100 + n)
        response = self.client.get(self.url("admin_home"))
        self.assertEqual((response.context["stalled_total"], len(response.context["stalled_interviews"])), (12, 10))
        self.assertContains(response, f'<a href="{self.url("pending")}#interrumpidas">Ver las 12 entrevistas interrumpidas</a>', html=True)

    def test_ending_the_event_lists_the_interviews_in_progress_first_and_saves_nothing(self):
        for status in (Event.Status.CLOSED, Event.Status.ARCHIVED):
            response = self.close(status)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.status(), Event.Status.ACTIVE, status)
            self.assertEqual([iv.pk for iv in response.context["closing_open"]], [self.old.pk, self.moving.pk])
            self.assertContains(response, "2 entrevistas siguen en curso. Revísalas abajo antes de terminar el evento, o marca la casilla para terminarlo así.")
            self.assertContains(response, '<p class="notice__title">2 entrevistas siguen en curso</p>', html=True)
            html = response.content.decode()
            notice = html[html.index('id="entrevistas-en-curso"'):html.index("Guardar datos del evento")]
            for interview in (self.old, self.moving):
                self.assertIn(interview.participant.full_name, notice)
                self.assertIn(f'href="{self.resume_url(interview)}"', notice)
                self.assertIn(f'href="{self.discard_url(interview)}"', notice)
            self.assertEqual((notice.count("Interrumpida"), notice.count("En curso")), (1, 1))
            self.assertIn('<input type="checkbox" id="cerrar-con-entrevistas" name="cerrar_con_entrevistas" value="si">', notice)
            self.assertIn('<option value="{}" selected>'.format(status), html)  # what was chosen stays chosen
        self.assertEqual(Interview.objects.filter(event=self.event, status=IN_PROGRESS).count(), 2)

    def test_ending_it_anyway_saves_and_discards_nothing(self):
        response = self.close(cerrar_con_entrevistas="si")
        self.assertRedirects(response, self.url("settings") + "#datos", fetch_redirect_response=False)
        self.assertEqual(self.status(), Event.Status.CLOSED)
        self.assertEqual(Interview.objects.filter(event=self.event, status=IN_PROGRESS).count(), 2)
        self.assertEqual(self.client.get(self.resume_url(self.old)).status_code, 302)  # still resumable: no lifecycle rule changed

    def test_without_interviews_in_progress_it_ends_at_once(self):
        Interview.objects.filter(event=self.event).update(status=Interview.Status.COMPLETED, completed_at=timezone.now())
        response = self.close()
        self.assertRedirects(response, self.url("settings") + "#datos", fetch_redirect_response=False)
        self.assertEqual(self.status(), Event.Status.CLOSED)

    def test_another_change_to_the_event_is_never_held_back(self):
        response = self.client.post(self.url("settings"), self.basics(**{"basics-location": "Rancho El Refugio"}))
        self.assertRedirects(response, self.url("settings") + "#datos", fetch_redirect_response=False)
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.CLOSED)
        # Already ended: saving its data again, or archiving it, asks nothing.
        response = self.client.post(self.url("settings"), self.basics(**{"basics-status": Event.Status.ARCHIVED}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.status(), Event.Status.ARCHIVED)

    def test_the_settings_page_shows_no_notice_until_somebody_tries_to_end_the_event(self):
        response = self.client.get(self.url("settings"))
        self.assertEqual(response.context["closing_open"], [])
        self.assertNotContains(response, "entrevistas-en-curso")

    def test_an_interview_of_another_event_is_not_listed(self):
        Interview.objects.filter(pk=self.moving.pk).delete()
        Interview.objects.create(event=self.other_event, participant=self.p_other_event, version=self.version, status=IN_PROGRESS)
        response = self.close()
        self.assertEqual([iv.pk for iv in response.context["closing_open"]], [self.old.pk])
        self.assertNotContains(response, "Persona Otro Evento")
