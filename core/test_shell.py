"""The redesigned shell ("Santuario", docs/v2/DESIGN_SYSTEM.md): who gets it, what it lists, and
that a church without the community module keeps Atrio's bar and stylesheet untouched."""

import re

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.templatetags.shell import initials, tel
from groups.tests.base import CommunityFixture
from people import flags

SHEET = settings.BASE_DIR / "static" / "css" / "santuario.css"


def rail(response) -> str:
    html = response.content.decode()
    return html[html.index('<header class="rail">'):html.index("</header>", html.index('<header class="rail">'))]


class ShellTests(CommunityFixture):
    def get(self, user, name="community:home", *args):
        self.client.force_login(user)
        return self.client.get(reverse(name, args=args))

    def test_a_member_gets_the_shell_with_only_what_is_theirs(self):
        response = self.get(self.member_user)
        self.assertContains(response, '<html lang="es-MX" class="no-js" data-ui="2">')
        self.assertContains(response, "css/santuario.css")
        self.assertNotContains(response, '<header class="bar')
        nav = rail(response)
        self.assertIn(">Mi iglesia<", nav)
        for label in ("Avisos", "Eventos", "Grupos"):
            self.assertIn(f">{label}<", nav)
        for hidden in ("Personas", "Administración", "A tu cargo", "Eventos donde sirves", "Configuración"):
            self.assertNotIn(hidden, nav)
        self.assertEqual(nav.count('aria-current="page"'), 1)

    def test_the_guide_sees_the_group_and_what_waits_there(self):
        nav = rail(self.get(self.leader_user))
        self.assertIn("A tu cargo", nav)
        self.assertIn(reverse("community:group_people", args=[self.group.pk]), nav)
        self.assertRegex(nav, r'<span class="badge">1<span class="visually-hidden"> solicitud por decidir</span>')
        self.assertNotIn("Administración", nav)
        # On the group's work page the place marked is the group under "A tu cargo", not "Grupos".
        work = rail(self.get(self.leader_user, "community:group_people", self.group.pk))
        self.assertEqual(work.count('aria-current="page"'), 1)
        self.assertRegex(work, r'group_people|personas/" aria-current="page"')

    def test_the_administration_gets_its_section_and_no_link_twice(self):
        nav = rail(self.get(self.admin))
        for label in ("Administración", "Personas", "Grupos de la iglesia", "Eventos de la iglesia", "Configuración", "Eventos donde sirves"):
            self.assertIn(label, nav)
        self.assertEqual(nav.count(f'href="{reverse("church_settings")}"'), 1)

    def test_the_phone_gets_tabs_and_the_whole_navigation_in_mas(self):
        html = self.get(self.leader_user).content.decode()
        tabs = html[html.index('<nav class="tabs"'):html.index("</nav>", html.index('<nav class="tabs"'))]
        self.assertEqual(re.findall(r'<span class="tabs__text">([^<]+)</span>', tabs), ["Mi iglesia", "Grupos", "A tu cargo", "Más"])
        self.assertIn('data-menu-open aria-controls="menu-panel"', tabs)
        panel = html[html.index('<div id="menu-panel"'):]
        for label in ("Mi iglesia", "Grupos", "Mi cuenta", "Ayuda", "Cerrar sesión"):
            self.assertIn(label, panel)
        member = self.get(self.member_user).content.decode()
        tabs = member[member.index('<nav class="tabs"'):member.index("</nav>", member.index('<nav class="tabs"'))]
        self.assertEqual(re.findall(r'<span class="tabs__text">([^<]+)</span>', tabs), ["Mi iglesia", "Eventos", "Grupos", "Más"])

    def test_every_page_wears_the_shell_and_an_event_is_a_place_in_it(self):
        for name in ("account", "ayuda"):
            response = self.get(self.admin, name)
            self.assertContains(response, '<header class="rail">')
            self.assertNotContains(response, '<header class="bar')
        self.client.force_login(self.admin)
        response = self.client.get(reverse("events:admin_home", args=[self.event.slug]))
        self.assertNotContains(response, '<header class="bar')
        nav = response.content.decode()
        nav = nav[nav.index('<header class="rail rail--event">'):nav.index("</header>")]
        self.assertIn('<p class="rail__label" id="rail-evento">Campamento A</p>', nav)
        self.assertIn("Tu rol: administrador.", nav)
        for label in ("Inicio", "Camperos", "Equipo", "Cuestionarios", "Reportes", "Configuración"):
            self.assertIn(f'<span class="rail__text">{label}</span>', nav)
        self.assertEqual(nav.count('aria-current="page"'), 1)  # the event's "Inicio", not "Eventos"
        self.assertIn(reverse("events:ayuda", args=[self.event.slug]), nav)
        tabs = response.content.decode()
        tabs = tabs[tabs.index('<nav class="tabs"'):tabs.index("</nav>", tabs.index('<nav class="tabs"'))]
        self.assertEqual(re.findall(r'<span class="tabs__text">([^<]+)</span>', tabs), ["Inicio", "Camperos", "Equipo", "Más"])
        # The list of events marks "Eventos".
        listing = rail(self.client.get(reverse("events:list") + "?todos=1"))
        self.assertRegex(listing, r'aria-current="page">.*?<span class="rail__text">Eventos donde sirves</span>')
        self.assertEqual(listing.count('aria-current="page"'), 1)

    def test_an_event_role_without_a_church_role_keeps_the_bar(self):
        self.client.force_login(self.tutor1)
        response = self.client.get(reverse("events:tutor_home", args=[self.event.slug]))
        self.assertContains(response, '<header class="bar')
        self.assertNotContains(response, "santuario.css")

    def test_without_the_module_nothing_of_the_redesign_is_served(self):
        with override_settings(EMMAUS_V2_MODULES=False):
            self.client.force_login(self.admin)
            for url in (reverse("account"), reverse("events:admin_home", args=[self.event.slug])):
                response = self.client.get(url)
                for new in ("santuario.css", "data-ui", 'class="rail"', 'class="tabs"'):
                    self.assertNotContains(response, new)
            self.client.logout()
            self.assertNotContains(self.client.get(reverse("login")), "santuario.css")
        self.client.logout()
        self.assertContains(self.client.get(reverse("login")), "santuario.css")  # the public pages of that church

    def test_the_church_colours_still_win_over_the_new_tokens(self):
        type(self.church).objects.filter(pk=self.church.pk).update(custom_theme_enabled=True)
        html = self.get(self.admin).content.decode()
        self.assertLess(html.index("santuario.css"), html.index('<style>:root[data-ui="2"]{--petroleo:'))


class MyDataTests(CommunityFixture):
    def test_the_home_asks_for_what_is_missing_and_the_person_fills_it_in(self):
        self.client.force_login(self.leader_user)
        home = self.client.get(reverse("community:home"))
        self.assertContains(home, "A tu iglesia le falta tu teléfono")
        response = self.client.post(
            reverse("community:profile"),
            {"phone": "55 3333 4444", "email": "gala@prueba.mx", "preferred_contact_method": "whatsapp"},
            follow=True,
        )
        self.assertContains(response, "Guardamos tus datos.")
        self.assertNotContains(response, "A tu iglesia le falta")
        self.leader.refresh_from_db()
        self.assertEqual((self.leader.phone, self.leader.email), ("55 3333 4444", "gala@prueba.mx"))

    def test_only_contact_fields_are_the_person_s_to_change(self):
        self.client.force_login(self.member_user)
        self.client.post(
            reverse("community:profile"),
            {"phone": "55 0000 0000", "email": "", "preferred_contact_method": "phone", "first_name": "Otro", "birth_date": "01/01/1990"},
        )
        self.member.refresh_from_db()
        self.assertEqual(self.member.first_name, "Diego")
        self.assertIsNone(self.member.birth_date)
        self.assertEqual(self.member.phone, "55 0000 0000")

    def test_the_page_does_not_exist_with_the_module_off(self):
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(reverse("community:profile")).status_code, 404)

    def test_an_email_of_somebody_else_in_the_church_is_refused(self):
        type(self.asked).objects.filter(pk=self.asked.pk).update(email="pia@prueba.mx")
        self.client.force_login(self.member_user)
        response = self.client.post(
            reverse("community:profile"), {"phone": "", "email": "pia@prueba.mx", "preferred_contact_method": "email"}
        )
        self.assertContains(response, "Otra persona de la iglesia ya tiene este correo.")


class GroupListTests(CommunityFixture):
    def test_mine_come_first_and_the_filter_keeps_one_kind(self):
        from groups.models import Group

        study = Group.objects.create(church=self.church, name="Aarón estudia", kind=Group.Kind.STUDY)
        self.client.force_login(self.leader_user)
        html = self.client.get(reverse("community:groups")).content.decode()
        self.assertLess(html.index(">Jóvenes Roca</a>"), html.index(">Aarón estudia</a>"))  # hers first, not alphabetical
        self.assertIn("Estudios bíblicos", html)
        only = self.client.get(reverse("community:groups") + "?tipo=estudio")
        self.assertContains(only, "Aarón estudia")
        self.assertNotContains(only, ">Jóvenes Roca</a>")
        study.delete()
        self.assertNotContains(self.client.get(reverse("community:groups")), "Estudios bíblicos")  # one kind: no filter

    def test_a_guide_can_call_or_write_to_who_asked(self):
        self.client.force_login(self.leader_user)
        type(self.asked).objects.filter(pk=self.asked.pk).update(phone="+52 55 1234 5678", email="pia@prueba.mx")
        response = self.client.get(reverse("community:group_people", args=[self.group.pk]))
        self.assertContains(response, 'href="tel:+525512345678"')
        self.assertContains(response, 'href="mailto:pia@prueba.mx"')


class PiecesTests(SimpleTestCase):
    def test_initials(self):
        self.assertEqual(initials("Jóvenes del jueves"), "JJ")
        self.assertEqual(initials("Ana"), "A")
        self.assertEqual(initials("ana.lopez@demo.invalid"), "AL")
        self.assertEqual(initials(""), "")

    def test_tel(self):
        self.assertEqual(tel("55 1111 2222"), "5511112222")
        self.assertEqual(tel("+52 (55) 1111-2222"), "+525511112222")

    def test_the_sheet_is_balanced_and_scopes_its_tokens(self):
        css = re.sub(r"/\*.*?\*/", "", SHEET.read_text(), flags=re.S)
        self.assertEqual(css.count("{"), css.count("}"))
        self.assertIn(':root[data-ui="2"] {', css)
        self.assertNotRegex(css, r"(?m)^:root\s*\{")  # never a bare :root: the church's own colours must win
        self.assertNotIn("linear-gradient", css)
        self.assertNotIn("box-shadow: 0 ", css.replace("box-shadow: 0 0 0 2px", ""))  # no depth shadows, as in Atrio

    def test_the_serif_range_is_what_the_file_was_cut_to(self):
        face = re.search(r"@font-face\s*\{(.*?)\}", SHEET.read_text(), re.S).group(1)
        self.assertIn('url("../fonts/newsreader-display-latin.woff2")', face)
        self.assertIn("U+0020-007E, U+00A0-00FF, U+2013-2014, U+2018-2019, U+201C-201D, U+2022, U+2026", face)
        self.assertTrue((settings.BASE_DIR / "static" / "fonts" / "OFL-Newsreader.txt").is_file())


class HomeModulesTests(CommunityFixture):
    """What the two newer modules put on Mi iglesia and in the shell."""

    def test_a_notice_shows_on_the_home_and_counts_in_the_rail_until_it_is_read(self):
        from notices import services as notices

        notice = notices.publish(self.church, self.admin, title="Cambia la hora del domingo", body="Será a las 11.")
        self.client.force_login(self.member_user)
        home = self.client.get(reverse("community:home"))
        self.assertContains(home, "Cambia la hora del domingo")
        self.assertRegex(rail(home), r'Avisos</span><span class="badge">1<span class="visually-hidden"> aviso nuevo</span>')
        self.client.get(reverse("notices:detail", args=[notice.pk]))
        self.assertNotIn('class="badge"', rail(self.client.get(reverse("community:home"))))

    def test_the_home_lists_only_events_the_church_published(self):
        from agenda import services as agenda
        from events.models import Event

        day = timezone.localdate() + timezone.timedelta(days=10)
        hidden = Event.objects.create(church=self.church, name="Retiro sin publicar", slug="retiro-oculto", starts_on=day, ends_on=day)
        shown = Event.objects.create(church=self.church, name="Noche de alabanza", slug="noche-alabanza", starts_on=day, ends_on=day)
        agenda.save_page(shown, self.admin, published=True, registration="abierta")
        self.client.force_login(self.member_user)
        home = self.client.get(reverse("community:home"))
        self.assertContains(home, "Noche de alabanza")
        self.assertContains(home, reverse("agenda:detail", args=[shown.slug]))
        self.assertContains(home, "Inscribirme")
        self.assertNotContains(home, hidden.name)
        agenda.register(shown, self.member, self.member_user)
        self.assertContains(self.client.get(reverse("community:home")), "Te inscribiste")

    def test_the_devotional_of_the_day_is_on_the_home_and_nobody_else_learns_who_read_it(self):
        from resources import services as resources

        resources.save_devotional(self.church, self.admin, date=timezone.localdate(), title="Permanecer", body="Quédate.", publish=True)
        self.client.force_login(self.member_user)
        home = self.client.get(reverse("community:home"))
        self.assertContains(home, "Devocional de hoy")
        self.assertContains(home, "Toma unos minutos.")
        self.assertIn(">Devocional<", rail(home))
        resources.mark_read(resources.today_devotional(self.church, self.member), self.member)
        self.assertContains(self.client.get(reverse("community:home")), "Ya lo leíste.")
        self.client.force_login(self.admin)
        admin_home = self.client.get(reverse("community:home"))
        self.assertContains(admin_home, "Toma unos minutos.")  # her own state, not the member's
        self.assertNotContains(admin_home, "Diego")
