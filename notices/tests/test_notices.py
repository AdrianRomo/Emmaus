"""Avisos: the administration tells the church, a guide tells their group, each person reads theirs."""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from groups import services as group_services
from groups.models import GroupMembership
from groups.tests.base import CommunityFixture
from notices import services
from notices.models import Announcement, AnnouncementRead
from people import flags
from people.models import ChurchAuditEvent

LIST = reverse("notices:list")
CREATE = reverse("notices:create")
SECRET = "La llave está bajo la maceta azul"
# A notice this person has not opened: a dot before its title, and the words for a screen reader.
UNREAD_ROW = "row--unread"
UNREAD_WORDS = ", sin leer</li>"


def detail(notice):
    return reverse("notices:detail", args=[notice.pk])


class NoticeTests(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        group_services.set_membership(cls.group, cls.member, cls.admin, role=GroupMembership.Role.MEMBER)
        group_services.set_membership(cls.other_group, cls.asked, cls.admin, role=GroupMembership.Role.MEMBER)
        cls.today = timezone.localdate()

    def data(self, para, **changes):
        return {
            "para": para, "title": "Retiro de noviembre", "priority": "normal", "body": SECRET,
            "publish_on": self.today.isoformat(), **changes,
        }

    def church_notice(self, **changes):
        return services.publish(self.church, self.admin, **{"title": "Retiro de noviembre", "body": SECRET, **changes})

    def group_notice(self, **changes):
        return self.church_notice(**{"group": self.group, "title": "Cambio de salón", **changes})

    # ---- writing --------------------------------------------------------------------------------
    def test_the_administration_publishes_for_the_church_and_a_member_reads_it(self):
        self.client.force_login(self.admin)
        page = self.client.get(CREATE)
        self.assertContains(page, "Toda la iglesia")
        self.assertContains(page, "Jóvenes Roca")
        self.assertNotContains(page, "Emmaus no lo envía por correo")
        response = self.client.post(CREATE, self.data("iglesia"), follow=True)
        notice = Announcement.objects.get()
        self.assertRedirects(response, detail(notice))
        self.assertContains(response, "Publicaste el aviso para toda la iglesia.")
        self.assertEqual((notice.church, notice.group, notice.created_by, notice.status), (self.church, None, self.admin, "publicado"))

        self.client.force_login(self.member_user)
        page = self.client.get(LIST)
        self.assertContains(page, "Retiro de noviembre")
        self.assertContains(page, "Para toda la iglesia")
        self.assertContains(page, UNREAD_ROW, count=1)
        self.assertContains(page, UNREAD_WORDS, count=1)
        self.assertNotContains(page, "Publicar un aviso")
        self.assertNotContains(page, "Los que publicaste")
        self.assertNotContains(page, 'id="publicaste"')
        page = self.client.get(detail(notice))
        self.assertContains(page, SECRET)
        self.assertNotContains(page, "Retirar el aviso")
        self.assertEqual(page.content.decode().count("<h1"), 1)

    def test_a_guide_publishes_for_their_group(self):
        self.client.force_login(self.leader_user)
        page = self.client.get(CREATE + f"?grupo={self.group.pk}")
        self.assertContains(page, "Jóvenes Roca")
        self.assertNotContains(page, "Toda la iglesia")
        self.assertNotContains(page, "Estudio de Romanos")
        response = self.client.post(CREATE, self.data(self.group.pk), follow=True)
        self.assertContains(response, "Publicaste el aviso para Jóvenes Roca.")
        notice = Announcement.objects.get()
        self.assertEqual((notice.group, notice.created_by), (self.group, self.leader_user))
        page = self.client.get(LIST)
        self.assertContains(page, '<h2 id="publicaste" class="heading">Los que publicaste</h2>', html=True)
        self.assertNotContains(page, UNREAD_ROW)  # who publishes has read it
        self.assertNotContains(page, UNREAD_WORDS)
        # The member of the group reads it; the administration too.
        for user in (self.member_user, self.admin):
            self.client.force_login(user)
            self.assertContains(self.client.get(LIST), "Para Jóvenes Roca")
            self.assertEqual(self.client.get(detail(notice)).status_code, 200)

    def test_a_guide_cannot_publish_for_the_church_or_for_a_group_they_do_not_guide(self):
        self.client.force_login(self.leader_user)
        for para in ("iglesia", self.other_group.pk):
            self.assertEqual(self.client.post(CREATE, self.data(para)).status_code, 403, para)
        self.assertEqual(self.client.get(CREATE + f"?grupo={self.other_group.pk}").status_code, 403)
        self.assertEqual(self.client.post(CREATE, self.data(self.group_b.pk)).status_code, 404)
        group_services.archive_group(self.other_group, self.admin)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(CREATE, self.data(self.other_group.pk)).status_code, 404)
        self.assertFalse(Announcement.objects.exists())

    def test_who_has_nothing_to_write_to_cannot_open_the_form(self):
        for user in (self.member_user, self.coord1):
            self.client.force_login(user)
            self.assertEqual(self.client.get(CREATE).status_code, 403)
            self.assertEqual(self.client.post(CREATE, self.data("iglesia")).status_code, 403)
        self.assertFalse(Announcement.objects.exists())

    def test_the_author_or_the_administration_edits_and_retires_and_nobody_else(self):
        notice = services.publish(self.church, self.leader_user, group=self.group, title="Cambio de salón", body=SECRET)
        edit, retire = reverse("notices:edit", args=[notice.pk]), reverse("notices:retire", args=[notice.pk])
        new = {
            "title": "Cambio de salón y de hora", "priority": "normal", "body": "Nos vemos a las siete.",
            "publish_on": self.today.isoformat(),
        }
        for user, expected in ((self.member_user, 403), (self.asked_user, 404), (self.admin_b, 404)):
            self.client.force_login(user)
            for url in (edit, retire):
                self.assertEqual(self.client.get(url).status_code, expected, (user.email, url))
                self.assertEqual(self.client.post(url, new).status_code, expected, (user.email, url))
        notice.refresh_from_db()
        self.assertEqual((notice.title, notice.status), ("Cambio de salón", "publicado"))

        self.client.force_login(self.leader_user)
        self.assertContains(self.client.get(detail(notice)), "Retirar el aviso")
        # Who it is for is not asked again: the field is not on the form, and the page says why.
        edit_page = self.client.get(edit)
        self.assertNotContains(edit_page, 'name="para"')
        self.assertNotContains(edit_page, "Para quién</label>")
        self.assertContains(edit_page, "Para quién es no se puede cambiar: si era para alguien más, retira este aviso y publica otro.")
        self.assertContains(self.client.post(edit, new, follow=True), "Nos vemos a las siete.")
        self.client.force_login(self.admin)
        confirm = self.client.get(retire)
        self.assertContains(confirm, "Sí, retirar el aviso")
        self.assertEqual(Announcement.objects.get().status, "publicado")  # the question retires nothing
        self.assertRedirects(self.client.post(retire), LIST)
        self.assertEqual(Announcement.objects.get().status, "retirado")
        self.assertEqual(self.client.get(edit).status_code, 404)  # a retired notice is not edited
        # A whole-church notice of the administration is not a guide's to change.
        church = self.church_notice()
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.post(reverse("notices:retire", args=[church.pk])).status_code, 403)

    def test_a_guide_who_no_longer_guides_cannot_change_what_they_published(self):
        notice = services.publish(self.church, self.leader_user, group=self.group, title="Cambio de salón", body=SECRET)
        membership = GroupMembership.objects.get(group=self.group, person=self.leader)
        group_services.end_membership(membership, self.admin)
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.post(reverse("notices:retire", args=[notice.pk])).status_code, 404)
        self.assertEqual(self.client.get(detail(notice)).status_code, 404)

    # ---- reading --------------------------------------------------------------------------------
    def test_a_group_notice_is_for_its_people_only(self):
        notice = self.group_notice()
        for user in (self.asked_user, self.coord1):  # in another group; in no group
            self.client.force_login(user)
            self.assertNotContains(self.client.get(LIST), "Cambio de salón")
            self.assertEqual(self.client.get(detail(notice)).status_code, 404)
        self.assertFalse(AnnouncementRead.objects.filter(person__in=[self.asked, self.coord_person]).exists())
        self.assertEqual(services.unread_count(self.asked, is_admin=False), 0)
        self.assertEqual(list(services.visible_to(self.member, is_admin=False)), [notice])
        self.assertEqual(list(services.visible_to(self.leader, is_admin=False)), [notice])
        self.assertEqual(list(services.visible_to(self.asked, is_admin=False)), [])
        self.assertEqual(list(services.visible_to(self.admin_person, is_admin=True)), [notice])
        # Who leaves the group stops reading it.
        group_services.end_membership(GroupMembership.objects.get(group=self.group, person=self.member), self.admin)
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(detail(notice)).status_code, 404)

    def test_scheduled_and_expired_notices_are_hidden_from_readers_and_listed_for_the_author(self):
        future = self.church_notice(title="Aviso de mañana", publish_on=self.today + timedelta(days=1))
        past = self.church_notice(
            title="Aviso de antes", publish_on=self.today - timedelta(days=9), expires_on=self.today - timedelta(days=1)
        )
        last_day = self.church_notice(title="Aviso de hoy", publish_on=self.today - timedelta(days=2), expires_on=self.today)
        self.assertEqual(list(services.visible_to(self.member, is_admin=False)), [last_day])
        self.assertEqual(list(services.visible_to(self.admin_person, is_admin=True)), [last_day])
        self.client.force_login(self.member_user)
        page = self.client.get(LIST)
        self.assertContains(page, "Aviso de hoy")
        self.assertNotContains(page, "Aviso de mañana")
        self.assertNotContains(page, "Aviso de antes")
        for notice in (future, past):
            self.assertEqual(self.client.get(detail(notice)).status_code, 404)
        self.client.force_login(self.admin)
        page = self.client.get(LIST)
        for words in ("Aviso de mañana", "Programado", "Aviso de antes", "Vencido", "Publicado"):
            self.assertContains(page, words)
        self.assertContains(self.client.get(detail(future)), "Hasta entonces solo lo ves tú")
        self.assertFalse(AnnouncementRead.objects.filter(announcement__in=[future, past]).exclude(person=self.admin_person).exists())

    def test_the_newest_comes_first(self):
        old = self.church_notice(title="Uno", publish_on=self.today - timedelta(days=3))
        second = self.church_notice(title="Dos")
        third = self.church_notice(title="Tres")
        self.assertEqual(list(services.visible_to(self.member, is_admin=False)), [third, second, old])
        home = services.for_home(self.member, is_admin=False, limit=2)
        self.assertEqual(home, [third, second])
        self.assertTrue(all(notice.is_unread for notice in home))

    def test_retiring_hides_it(self):
        notice = self.church_notice()
        services.retire(notice, self.admin)
        services.retire(notice, self.admin)  # again: nothing happens
        self.assertEqual(ChurchAuditEvent.objects.filter(action="aviso_retirado").count(), 1)
        self.assertEqual(services.unread_count(self.member, is_admin=False), 0)
        self.client.force_login(self.member_user)
        self.assertNotContains(self.client.get(LIST), "Retiro de noviembre")
        self.assertEqual(self.client.get(detail(notice)).status_code, 404)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(LIST), "Retirado")
        retired = self.client.get(detail(notice))
        self.assertContains(retired, "Retirado</span></li>")
        self.assertContains(retired, '<p class="aside-note">Ya nadie lo ve en Mi iglesia.</p>', html=True)

    def test_opening_marks_it_read_and_the_list_writes_nothing(self):
        notice, other = self.church_notice(), self.group_notice()
        self.assertEqual(services.unread_count(self.member, is_admin=False), 2)
        self.client.force_login(self.member_user)
        listing = self.client.get(LIST)
        self.assertContains(listing, UNREAD_ROW, count=2)
        self.assertContains(listing, UNREAD_WORDS, count=2)
        self.assertFalse(AnnouncementRead.objects.filter(person=self.member).exists())
        self.client.get(detail(notice))
        self.client.get(detail(notice))  # idempotent
        self.assertEqual(AnnouncementRead.objects.filter(person=self.member).count(), 1)
        self.assertEqual(services.unread_count(self.member, is_admin=False), 1)
        self.assertEqual([n.is_unread for n in services.for_home(self.member, is_admin=False)], [True, False])
        listing = self.client.get(LIST)
        self.assertContains(listing, UNREAD_ROW, count=1)
        self.assertContains(listing, UNREAD_WORDS, count=1)
        self.assertEqual(services.unread_count(self.leader, is_admin=False), 2)  # one person's reading is theirs
        self.assertFalse(services.mark_read(other, self.member) and services.mark_read(other, self.member))

    # ---- isolation ------------------------------------------------------------------------------
    def test_another_church_does_not_exist(self):
        notice, grouped = self.church_notice(), self.group_notice()
        theirs = services.publish(self.church_b, self.admin_b, title="Aviso de la iglesia B", body="Cosas de B")
        self.client.force_login(self.admin_b)
        self.assertNotContains(self.client.get(LIST), "Retiro de noviembre")
        for item in (notice, grouped):
            for name in ("notices:detail", "notices:edit", "notices:retire"):
                url = reverse(name, args=[item.pk])
                self.assertEqual(self.client.get(url).status_code, 404, url)
                self.assertEqual(self.client.post(url, {"title": "x", "body": "x", "publish_on": self.today}).status_code, 404, url)
        self.assertEqual(self.client.get(CREATE + f"?grupo={self.group.pk}").status_code, 404)
        self.assertEqual(self.client.post(CREATE, self.data(self.group.pk)).status_code, 404)
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(LIST), "Aviso de la iglesia B")
        self.assertEqual(self.client.get(detail(theirs)).status_code, 404)
        self.assertEqual(list(services.visible_to(self.member_b, is_admin=False)), [theirs])
        with self.assertRaises(services.NoticeError):
            services.publish(self.church, self.admin, group=self.group_b, title="x", body="x")
        self.assertEqual(Announcement.objects.filter(status="publicado").count(), 3)

    def test_the_module_off_hides_every_page(self):
        notice = self.church_notice()
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.admin)
        urls = [LIST, CREATE] + [reverse(name, args=[notice.pk]) for name in ("notices:detail", "notices:edit", "notices:retire")]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404, url)
            self.assertEqual(self.client.post(url, self.data("iglesia")).status_code, 404, url)
        self.assertEqual(Announcement.objects.filter(status="publicado").count(), 1)

    def test_signed_out_reads_nothing(self):
        notice = self.church_notice()
        for url in (LIST, CREATE, detail(notice)):
            response = self.client.get(url)
            self.assertIn(response.status_code, (302, 404), url)
            self.assertNotContains(response, SECRET, status_code=response.status_code)

    # ---- what is stored and shown ---------------------------------------------------------------
    def test_the_audit_rows_carry_no_title_and_no_body(self):
        self.client.force_login(self.admin)
        self.client.post(CREATE, self.data("iglesia"))
        self.client.post(CREATE, self.data(self.group.pk))
        church, grouped = Announcement.objects.order_by("id")
        self.client.post(reverse("notices:edit", args=[grouped.pk]), self.data("", body=SECRET + " hoy"))
        self.client.post(reverse("notices:retire", args=[grouped.pk]))
        rows = list(ChurchAuditEvent.objects.filter(action__startswith="aviso_").order_by("id"))
        self.assertEqual(
            [(row.action, row.summary, row.metadata) for row in rows],
            [
                ("aviso_publicado", "Publicó un aviso para toda la iglesia", {}),
                ("aviso_publicado", "Publicó un aviso para Jóvenes Roca", {"group_id": self.group.pk}),
                ("aviso_modificado", "Modificó un aviso para Jóvenes Roca", {"group_id": self.group.pk}),
                ("aviso_retirado", "Retiró un aviso para Jóvenes Roca", {"group_id": self.group.pk}),
            ],
        )
        for row in rows:
            self.assertNotIn("maceta", row.summary + str(row.metadata))
            self.assertNotIn("Retiro de noviembre", row.summary)
            self.assertEqual(row.object_type, "Announcement")

    def test_a_body_that_is_too_long_is_refused_in_spanish(self):
        self.client.force_login(self.admin)
        response = self.client.post(CREATE, self.data("iglesia", body="a" * 1201))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El aviso puede tener hasta 1200 caracteres y este tiene 1201. Acórtalo.")
        self.assertContains(response, "Error: Publicar un aviso")
        self.assertFalse(Announcement.objects.exists())
        with self.assertRaises(services.NoticeError):
            self.church_notice(body="a" * 1201)
        # Line breaks count once, as the person counts them.
        self.assertEqual(self.client.post(CREATE, self.data("iglesia", body="a\r\n" * 600)).status_code, 302)

    def test_what_is_missing_or_backwards_is_said_in_spanish(self):
        self.client.force_login(self.admin)
        response = self.client.post(CREATE, {"para": "iglesia", "title": "", "body": " ", "publish_on": ""})
        for words in ("Escribe un título para el aviso.", "Elige el nivel del aviso.", "Escribe el aviso.", "Elige el día en que se publica."):
            self.assertContains(response, words)
        yesterday = (self.today - timedelta(days=1)).isoformat()
        response = self.client.post(CREATE, self.data("iglesia", expires_on=yesterday))
        self.assertContains(response, "El último día no puede ser antes del día en que se publica.")
        self.assertFalse(Announcement.objects.exists())

    def test_html_in_a_notice_is_shown_as_text(self):
        notice = self.church_notice(title="<b>Título</b>", body="<script>alert(1)</script>\n\nSegundo párrafo & más")
        self.client.force_login(self.member_user)
        page = self.client.get(detail(notice))
        self.assertContains(page, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(page, "<script>alert(1)")
        self.assertContains(page, "<p>Segundo párrafo &amp; más</p>", html=True)
        self.assertNotContains(page, "<b>Título</b>")
        self.assertNotContains(self.client.get(LIST), "<b>Título</b>")

    def test_the_pages_keep_the_design_rules(self):
        notice = self.group_notice()
        self.client.force_login(self.admin)
        for url in (LIST, CREATE, detail(notice), reverse("notices:edit", args=[notice.pk]), reverse("notices:retire", args=[notice.pk])):
            html = self.client.get(url).content.decode()
            main = html[html.index("<main"):html.index("</main>")]
            self.assertEqual(html.count("<h1"), 1, url)
            self.assertLessEqual(main.count("btn--primary"), 1, url)
            self.assertNotIn("·", main, url)
            # An icon is a mark beside its word, never a control by itself: every one is hidden from a screen reader
            self.assertEqual(main.count("<svg"), main.count('aria-hidden="true" focusable="false">'), url)
