"""«Mi inicio» (redesign, ui_v2): the unfinished interview's card says where it is open before its button. On another
computer it reads «Abierta en Computadora 5, no en esta» and the button says what it does, «Continuar aquí» (the
same resume, which asks to take over); on the same computer it stays «Retomar entrevista». Module off: unchanged."""

from __future__ import annotations

from datetime import timedelta

from django.test import Client
from django.utils import timezone

from events.models import Interview, ParticipantSession
from people import flags

from .base import CampFixture


class ResumeCardSaysWhereItIsOpenTests(CampFixture):
    def setUp(self):
        flags.set_enabled(self.church, "community", True)
        self.a = self.login(self.coord1, Client())
        self.station_a = self.choose_station(self.a, number=5)
        self.start(self.p1, client=self.a)
        self.interview = Interview.objects.get(participant=self.p1)
        self.answer(self.interview, "salvo", "si", client=self.a)
        self.resume = self.url("interview_resume", self.interview.public_id)

    def home(self, client):
        return client.get(self.url("coord_home")).content.decode().split("<main", 1)[1]

    def test_another_computer_is_told_where_it_is_open(self):
        b = self.login(self.coord1, Client())
        self.choose_station(b, number=6)
        page = self.home(b)
        self.assertIn(f"Abierta en {self.station_a.name}, no en esta", page)
        self.assertIn(f'href="{self.resume}">Continuar aquí</a>', page)
        self.assertNotIn(">Retomar entrevista</a>", page)

    def test_the_same_computer_resumes(self):
        page = self.home(self.a)
        self.assertIn(f'href="{self.resume}">Retomar entrevista</a>', page)
        self.assertNotIn("no en esta", page)
        # Where «Retomar» lands, said on the card: one question answered, so the second (no reassuring sentence)
        self.assertRegex(page, r"Pregunta 2 de \d+, en " + self.station_a.name)
        self.assertNotIn("Lo que respondió está guardado", page)

    def test_a_campero_answering_alone_is_said_and_never_offered_to_resume(self):
        ParticipantSession.objects.create(
            interview=self.interview, station=self.station_a, token_hash="t" * 64, lock_token_hash="l" * 64,
            started_by=self.coord1, expires_at=timezone.now() + timedelta(minutes=30),
        )
        page = self.home(self.a)
        self.assertIn(f"{self.p1.first_name} responde a solas en {self.station_a.name}", page)
        self.assertNotIn(f'href="{self.resume}"', page)  # «Retomar» would close their screen

    def test_with_the_module_off_the_card_is_unchanged(self):
        flags.set_enabled(self.church, "community", False)
        b = self.login(self.coord1, Client())
        self.choose_station(b, number=6)
        page = self.home(b)
        self.assertNotIn("no en esta", page)
        self.assertNotIn("Continuar aquí", page)
