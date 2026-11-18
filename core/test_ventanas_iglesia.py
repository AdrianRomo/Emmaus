"""The church's administration screens shared with Atrio (templates/core/): with the community module they wear
«Ventanas» (every row led by its mark, a person's circle); without it they are what they were, byte for byte
(people/tests/test_screens_unchanged.py proves the bytes of «Configuración»)."""

import re

from django.urls import reverse

from core.models import Invitation, Role
from groups.tests.base import CommunityFixture
from people import flags


class ChurchAdministrationOnVentanasTests(CommunityFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_configuracion_leads_each_place_with_its_mark(self):
        html = self.client.get(reverse("church_settings")).content.decode()
        places = html[html.index('class="rows ajustes"'):html.index("</ul>", html.index('class="rows ajustes"'))]
        self.assertEqual(places.count('<li class="row row--lead">'), 3)  # who has access, the policies, the public page
        self.assertEqual(places.count('<span class="hoy__icono" aria-hidden="true">'), 3)
        self.assertNotIn("Va en lugar de “Emmaus”", html)  # the logo's hint says its format; no sentence of its own
        flags.set_enabled(self.church, "community", False)
        html = self.client.get(reverse("church_settings")).content.decode()
        self.assertNotIn("hoy__icono", html)
        self.assertIn("Va en lugar de “Emmaus”", html)
        self.assertIn('<li class="row"><div class="row__main"><p class="row__title">', html)

    def test_an_invitation_and_a_person_with_access_are_circles(self):
        Invitation.objects.create(church=self.church, email="nueva@prueba.mx", role=Role.PARTICIPANT, invited_by=self.admin)
        html = self.client.get(reverse("invitations_list")).content.decode()
        self.assertIn('<li class="row row--lead row--stack"><span class="mono" aria-hidden="true">NP</span>', html)
        html = self.client.get(reverse("user_list")).content.decode()
        listing = html[html.index("<main"):]
        self.assertTrue(re.search(r'<span class="mono" aria-hidden="true">', listing))
        self.assertNotIn("mono--grupo", listing)
