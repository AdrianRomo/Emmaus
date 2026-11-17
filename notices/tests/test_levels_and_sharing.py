"""A notice's level (normal, importante, urgente), "Enterado" on an urgent one, and the WhatsApp
message that says there is a notice without saying what it says."""

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from django.urls import reverse
from django.utils import timezone

from events.tests.base import make_user
from groups import services as group_services
from groups.models import GroupMembership
from groups.tests.base import CommunityFixture
from notices import services
from notices.models import Announcement, AnnouncementRead
from people import flags
from people.models import ChurchAuditEvent

LIST = reverse("notices:list")
CREATE = reverse("notices:create")
HOME = reverse("community:home")
TITLE = "Cambio de horario"
BODY = "La reunión de jóvenes comenzará a las 6:00 p. m. en lugar de las 5:00 p. m."
URGENT, IMPORTANT, NORMAL = "urgent", "important", "normal"


def detail(notice):
    return reverse("notices:detail", args=[notice.pk])


def enterado(notice):
    return reverse("notices:acknowledge", args=[notice.pk])


class LevelFixture(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        group_services.set_membership(cls.group, cls.member, cls.admin, role=GroupMembership.Role.MEMBER)
        group_services.set_membership(cls.other_group, cls.asked, cls.admin, role=GroupMembership.Role.MEMBER)
        cls.today = timezone.localdate()

    def data(self, para, **changes):
        return {"para": para, "title": TITLE, "priority": NORMAL, "body": BODY, "publish_on": self.today.isoformat(), **changes}

    def notice(self, priority=NORMAL, **changes):
        fields = {"title": TITLE, "body": BODY, "priority": priority, "by_admin": True, **changes}
        return services.publish(self.church, self.admin, **fields)


class LevelTests(LevelFixture):
    # ---- who may choose a level -----------------------------------------------------------------
    def test_the_administration_is_offered_the_three_levels_and_publishes_an_urgent_notice(self):
        self.client.force_login(self.admin)
        page = self.client.get(CREATE)
        for word in ("Nivel", "Normal", "Importante", "Urgente"):
            self.assertContains(page, word)
        self.client.post(CREATE, self.data("iglesia", priority=URGENT))
        self.client.post(CREATE, self.data(self.group.pk, priority=URGENT, title="Otro"))
        self.assertEqual([n.priority for n in Announcement.objects.order_by("id")], [URGENT, URGENT])
        self.client.post(CREATE, self.data("iglesia", title="El de siempre"))
        self.assertEqual(Announcement.objects.get(title="El de siempre").priority, NORMAL)
        self.assertContains(self.client.post(CREATE, self.data("iglesia", priority="")), "Elige el nivel del aviso.")
        self.assertEqual(Announcement.objects.count(), 3)

    def test_a_guide_is_offered_normal_and_importante_and_urgente_is_refused(self):
        self.client.force_login(self.leader_user)
        page = self.client.get(CREATE + f"?grupo={self.group.pk}")
        self.assertContains(page, 'value="important"')
        self.assertNotContains(page, 'value="urgent"')
        self.assertNotContains(page, "Urgente")
        response = self.client.post(CREATE, self.data(self.group.pk, priority=URGENT))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Elige el nivel del aviso entre los que aparecen.")
        self.assertFalse(Announcement.objects.exists())
        response = self.client.post(CREATE, self.data(self.group.pk, priority="lo-que-sea"))
        self.assertFalse(Announcement.objects.exists())
        self.assertRedirects(
            self.client.post(CREATE, self.data(self.group.pk, priority=IMPORTANT)), detail(Announcement.objects.get())
        )
        notice = Announcement.objects.get()
        self.assertEqual(notice.priority, IMPORTANT)
        # Nor by correcting it afterwards.
        edit = reverse("notices:edit", args=[notice.pk])
        self.assertNotContains(self.client.get(edit), "Urgente")
        self.assertEqual(self.client.post(edit, self.data("", priority=URGENT)).status_code, 200)
        notice.refresh_from_db()
        self.assertEqual(notice.priority, IMPORTANT)

    def test_the_service_refuses_an_urgent_notice_that_is_not_the_administrations(self):
        for by_admin in (False, None):
            kwargs = {} if by_admin is None else {"by_admin": by_admin}
            with self.assertRaises(services.NoticeError):
                services.publish(self.church, self.leader_user, group=self.group, title=TITLE, body=BODY, priority=URGENT, **kwargs)
        with self.assertRaises(services.NoticeError):
            services.publish(self.church, self.admin, title=TITLE, body=BODY, priority="enorme", by_admin=True)
        notice = services.publish(self.church, self.leader_user, group=self.group, title=TITLE, body=BODY, priority=IMPORTANT)
        with self.assertRaises(services.NoticeError):
            services.update(notice, self.leader_user, title=TITLE, body=BODY, publish_on=self.today, priority=URGENT)
        notice.refresh_from_db()
        self.assertEqual(notice.priority, IMPORTANT)
        self.assertEqual(Announcement.objects.count(), 1)

    def test_a_guide_correcting_a_notice_the_administration_made_urgent_keeps_its_level(self):
        notice = services.publish(self.church, self.leader_user, group=self.group, title=TITLE, body=BODY)
        self.client.force_login(self.admin)
        edit = reverse("notices:edit", args=[notice.pk])
        self.assertContains(self.client.get(edit), 'value="urgent"')
        self.client.post(edit, self.data("", priority=URGENT))
        notice.refresh_from_db()
        self.assertEqual(notice.priority, URGENT)
        self.client.force_login(self.leader_user)
        self.assertNotContains(self.client.get(edit), "Nivel")
        for sent in ("", NORMAL):  # not asked, and not heard if sent anyway
            self.client.post(edit, self.data("", body="Nos vemos a las seis.", priority=sent))
            notice.refresh_from_db()
            self.assertEqual((notice.priority, notice.body), (URGENT, "Nos vemos a las seis."))

    def test_the_audit_row_says_the_level_and_still_no_title_or_body(self):
        self.client.force_login(self.admin)
        self.client.post(CREATE, self.data("iglesia", priority=URGENT))
        self.client.post(CREATE, self.data(self.group.pk, priority=IMPORTANT))
        rows = ChurchAuditEvent.objects.filter(action="aviso_publicado").order_by("id")
        self.assertEqual(
            [(row.summary, row.metadata) for row in rows],
            [
                ("Publicó un aviso urgente para toda la iglesia", {}),
                ("Publicó un aviso importante para Jóvenes Roca", {"group_id": self.group.pk}),
            ],
        )
        for row in rows:
            self.assertNotIn("horario", (row.summary + str(row.metadata)).lower())

    # ---- how a level shows ------------------------------------------------------------------------
    def test_the_louder_ones_come_first_and_carry_their_word(self):
        normal = self.notice(title="Uno normal")
        important = self.notice(IMPORTANT, title="Uno importante", publish_on=self.today - timedelta(days=3))
        urgent = self.notice(URGENT, title="Uno urgente", publish_on=self.today - timedelta(days=5))
        self.assertEqual(list(services.visible_to(self.member, is_admin=False)), [urgent, important, normal])
        self.client.force_login(self.member_user)
        html = self.client.get(LIST).content.decode()
        self.assertLess(html.index("Uno urgente"), html.index("Uno importante"))
        self.assertLess(html.index("Uno importante"), html.index("Uno normal"))
        self.assertEqual(html.count("</span>Urgente</span>"), 1)
        self.assertEqual(html.count("</span>Importante</span>"), 1)
        self.assertNotIn("</span>Normal</span>", html)
        page = self.client.get(detail(important))
        self.assertContains(page, "</span>Importante</span>")
        self.assertNotContains(page, "Enterado")  # only an urgent notice asks for it
        self.assertNotContains(self.client.get(detail(normal)), "status--")
        # On the home an important notice is a row with its word, not a notice at the top.
        services.acknowledge(urgent, self.member)
        home = self.client.get(HOME)
        self.assertContains(home, "</span>Importante</span>")
        self.assertNotContains(home, "Aviso urgente:")

    def test_the_owners_example_reads_as_asked(self):
        notice = self.notice(IMPORTANT)
        self.client.force_login(self.member_user)
        html = self.client.get(detail(notice)).content.decode()
        places = [html.index(words) for words in (TITLE, "</span>Importante", "comenzará a las 6:00", "Publicado por la iglesia", "Compartir por WhatsApp")]
        self.assertEqual(places, sorted(places))

    def test_who_published_is_said_without_a_name(self):
        by_guide = services.publish(self.church, self.leader_user, group=self.group, title=TITLE, body=BODY)
        by_admin = self.notice(group=self.group)
        self.client.force_login(self.member_user)
        page = self.client.get(detail(by_guide))
        self.assertContains(page, "Publicado por quien guía Jóvenes Roca el ")
        for name in ("Gala", "Guía</"):
            self.assertNotContains(page, name)
        self.assertContains(self.client.get(detail(by_admin)), "Publicado por la iglesia el ")
        self.assertContains(self.client.get(detail(self.notice())), "Publicado por la iglesia el ")

    # ---- urgent: the home and "Enterado" ------------------------------------------------------------
    def test_an_urgent_notice_waits_at_the_top_of_the_home_until_enterado(self):
        notice = self.notice(URGENT, body=BODY + " " + "Trae tu Biblia. " * 30 + "FINAL")
        self.client.force_login(self.member_user)
        html = self.client.get(HOME).content.decode()
        main = html[html.index("<main"):html.index("</main>")]
        self.assertIn(f"Aviso urgente: {TITLE}", main)
        self.assertIn("comenzará a las 6:00", main)
        self.assertNotIn("FINAL", main)  # the first lines, not the whole notice
        self.assertIn(f'href="{detail(notice)}"', main)
        self.assertEqual(main.count(TITLE), 1)  # said once: not again among the other notices
        self.assertLess(main.index("Aviso urgente:"), main.index("<h2", main.index("Aviso urgente:")))
        self.assertEqual(html.count("<h1"), 1)
        # Reading it is not answering it.
        page = self.client.get(detail(notice))
        self.assertContains(page, ">Enterado</button>")
        self.assertContains(self.client.get(HOME), "Aviso urgente:")
        self.assertEqual(self.client.get(enterado(notice)).status_code, 405)
        self.assertContains(self.client.get(HOME), "Aviso urgente:")
        response = self.client.post(enterado(notice), follow=True)
        self.assertRedirects(response, detail(notice))
        self.assertContains(response, "Listo, anotamos tu «Enterado».")
        self.assertContains(response, "Dijiste «Enterado» el ")
        self.assertNotContains(response, ">Enterado</button>")
        self.client.post(enterado(notice))  # again: nothing changes
        read = AnnouncementRead.objects.get(announcement=notice, person=self.member)
        self.assertIsNotNone(read.acknowledged_at)
        home = self.client.get(HOME)
        self.assertNotContains(home, "Aviso urgente:")
        self.assertContains(home, TITLE)  # now a row among the notices, with its word
        self.assertContains(home, "</span>Urgente</span>")
        # One person's answer is theirs.
        self.client.force_login(self.asked_user)
        self.assertContains(self.client.get(HOME), "Aviso urgente:")

    def test_an_urgent_notice_leaves_the_home_when_it_expires_is_retired_or_is_not_yet_published(self):
        self.client.force_login(self.member_user)
        expired = self.notice(URGENT, publish_on=self.today - timedelta(days=3), expires_on=self.today - timedelta(days=1))
        future = self.notice(URGENT, publish_on=self.today + timedelta(days=1))
        self.assertNotContains(self.client.get(HOME), "Aviso urgente:")
        for notice in (expired, future):
            self.assertEqual(self.client.post(enterado(notice)).status_code, 404)
        current = self.notice(URGENT)
        self.assertContains(self.client.get(HOME), "Aviso urgente:")
        services.retire(current, self.admin)
        self.assertNotContains(self.client.get(HOME), "Aviso urgente:")
        self.assertEqual(self.client.post(enterado(current)).status_code, 404)
        self.assertFalse(AnnouncementRead.objects.filter(acknowledged_at__isnull=False).exists())

    def test_who_published_it_is_not_asked_and_reads_the_count(self):
        notice = self.notice(URGENT)
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(HOME), "Aviso urgente:")
        page = self.client.get(detail(notice))
        self.assertNotContains(page, ">Enterado</button>")
        # With an account in the church and not the author: member, leader, coord1, asked.
        self.assertContains(page, "0 de 4 personas dijeron «Enterado».")
        self.assertContains(page, "No prueba que todas lo hayan recibido")
        for person in (self.member, self.leader):
            services.acknowledge(notice, person)
        page = self.client.get(detail(notice))
        self.assertContains(page, "2 de 4 personas dijeron «Enterado».")
        for name in ("Diego", "Gala", "Pía", "Nora"):  # numbers, never who did or did not
            self.assertNotContains(page, name)
        # A reader gets the button and never the count.
        self.client.force_login(self.asked_user)
        page = self.client.get(detail(notice))
        self.assertContains(page, ">Enterado</button>")
        self.assertNotContains(page, "dijeron")
        self.assertNotContains(page, "No prueba")

    def test_the_count_is_of_the_people_the_notice_is_for_who_have_an_account(self):
        church_wide = self.notice(URGENT)
        self.assertEqual(services.acknowledgement_count(church_wide), (0, 4))
        make_user("sin-uso@prueba.mx", "Sin", "Uso")  # an account that is nobody of this church
        inactive = make_user("inactiva@prueba.mx", "Ina", "Activa")
        self._person(self.church, inactive)
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        self.assertEqual(services.acknowledgement_count(church_wide), (0, 4))  # nor Nora, who has no account
        grouped = self.notice(URGENT, group=self.group)
        self.assertEqual(services.acknowledgement_count(grouped), (0, 2))  # member and leader
        services.acknowledge(grouped, self.member)
        services.acknowledge(grouped, self.member)
        self.assertEqual(services.acknowledgement_count(grouped), (1, 2))
        self.assertEqual(services.acknowledgement_count(church_wide), (0, 4))
        # The administration reads every group's notice but is not who it is for: it does not move the count.
        other_admin_notice = services.publish(
            self.church, self.leader_user, group=self.group, title=TITLE, body=BODY, priority=IMPORTANT
        )
        services.update(other_admin_notice, self.admin, title=TITLE, body=BODY, publish_on=self.today, priority=URGENT, by_admin=True)
        other_admin_notice.refresh_from_db()
        services.acknowledge(other_admin_notice, self.admin_person)
        self.assertEqual(services.acknowledgement_count(other_admin_notice), (0, 1))  # the member; the guide wrote it
        self.client.force_login(self.admin)
        # With one or two readers the number would say who: the page does not give it.
        page = self.client.get(detail(other_admin_notice))
        self.assertNotContains(page, "0 de 1 persona")
        self.assertContains(page, "es para menos de 3 personas con cuenta")
        self.assertContains(self.client.get(detail(church_wide)), "0 de 4 personas dijeron «Enterado».")

    def test_enterado_is_only_for_an_urgent_notice_the_person_may_read(self):
        grouped = self.notice(URGENT, group=self.group)
        important = self.notice(IMPORTANT)
        theirs = services.publish(self.church_b, self.admin_b, title="Aviso de B", body="Cosas de B", priority=URGENT, by_admin=True)
        self.client.force_login(self.asked_user)  # in another group
        for notice in (grouped, important, theirs):
            self.assertEqual(self.client.post(enterado(notice)).status_code, 404, notice.title)
            self.assertEqual(self.client.get(enterado(notice)).status_code, 404, notice.title)
        self.assertNotContains(self.client.get(HOME), "Aviso urgente:")
        self.client.force_login(self.member_b_user)  # another church
        self.assertEqual(self.client.post(enterado(grouped)).status_code, 404)
        self.assertContains(self.client.get(HOME), "Aviso urgente: Aviso de B")
        self.assertNotContains(self.client.get(HOME), TITLE)
        with self.assertRaises(services.NoticeError):
            services.acknowledge(important, self.member)
        self.assertFalse(AnnouncementRead.objects.filter(acknowledged_at__isnull=False).exists())
        self.client.logout()
        self.assertEqual(self.client.post(enterado(grouped)).status_code, 302)
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.post(enterado(grouped)).status_code, 404)
        self.assertFalse(AnnouncementRead.objects.filter(acknowledged_at__isnull=False).exists())


class ShareTests(LevelFixture):
    def share_link(self, notice):
        html = self.client.get(detail(notice)).content.decode()
        start = html.index('href="https://wa.me/?text=')
        tag = html[start:html.index(">", start)]
        return tag, tag.split('"')[1].replace("&amp;", "&")

    def test_the_message_names_the_church_and_the_page_and_nothing_of_the_notice(self):
        secret_title, secret_body = "Cambio de salón secreto", "La llave está bajo la maceta azul"
        for notice in (
            self.notice(title=secret_title, body=secret_body),
            self.notice(URGENT, group=self.group, title=secret_title, body=secret_body),
        ):
            self.client.force_login(self.member_user)
            tag, href = self.share_link(notice)
            self.assertIn('target="_blank"', tag)
            self.assertIn('rel="noopener"', tag)
            self.assertContains(self.client.get(detail(notice)), "Compartir por WhatsApp")
            parts = urlsplit(href)
            self.assertEqual((parts.scheme, parts.netloc, parts.path), ("https", "wa.me", "/"))
            self.assertEqual(list(parse_qs(parts.query)), ["text"])
            (text,) = parse_qs(parts.query)["text"]
            address = "http://testserver" + detail(notice)
            self.assertEqual(text, f"{self.church.name}: hay un aviso nuevo en Emmaus. Entra con tu cuenta para leerlo: {address}")
            for words in (secret_title, "salón", secret_body, "maceta", "Jóvenes Roca", "urgente", "Urgente"):
                self.assertNotIn(words, text)
                self.assertNotIn(words, href)
            # The address is the page and nothing else: no token, no query, nothing that opens it by itself.
            link = urlsplit(text.rsplit(" ", 1)[1])
            self.assertEqual((link.path, link.query, link.fragment), (detail(notice), "", ""))

    def test_who_follows_the_link_signs_in_first_and_then_reads_only_what_is_theirs(self):
        notice = self.notice(group=self.group, title="Cambio de salón secreto", body="La llave está bajo la maceta azul")
        login = reverse("login")
        response = self.client.get(detail(notice))
        self.assertRedirects(response, f"{login}?next={detail(notice)}", fetch_redirect_response=False)
        self.assertContains(self.client.get(response["Location"]), f'name="next" value="{detail(notice)}"')
        # In the group: after signing in they land on the notice.
        self.member_user.set_password("Clave-de-prueba-2026")
        self.member_user.save()
        response = self.client.post(
            login, {"username": self.member_user.email, "password": "Clave-de-prueba-2026", "next": detail(notice)}
        )
        self.assertRedirects(response, detail(notice), fetch_redirect_response=False)
        self.assertContains(self.client.get(detail(notice)), "maceta azul")
        # Not in the group, another church, the module off: the page does not exist.
        for user in (self.asked_user, self.coord1, self.admin_b, self.member_b_user):
            self.client.force_login(user)
            response = self.client.get(detail(notice))
            self.assertEqual(response.status_code, 404, user.email)
            self.assertNotContains(response, "maceta", status_code=404)
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(detail(notice)).status_code, 404)

    def test_a_notice_nobody_can_read_yet_or_any_more_is_not_shared(self):
        self.client.force_login(self.admin)
        future = self.notice(publish_on=self.today + timedelta(days=1))
        expired = self.notice(publish_on=self.today - timedelta(days=3), expires_on=self.today - timedelta(days=1))
        retired = services.retire(self.notice(), self.admin)
        for notice in (future, expired, retired):
            self.assertNotContains(self.client.get(detail(notice)), "wa.me")

    def test_the_pages_keep_the_design_rules_with_an_urgent_notice(self):
        notice = self.notice(URGENT, group=self.group)
        for user in (self.admin, self.member_user):
            self.client.force_login(user)
            for url in (LIST, detail(notice), HOME):
                html = self.client.get(url).content.decode()
                main = html[html.index("<main"):html.index("</main>")]
                self.assertEqual(html.count("<h1"), 1, url)
                self.assertLessEqual(main.count("btn--primary"), 1, url)
                self.assertNotIn("·", main, url)
                self.assertNotIn("<svg", main, url)
