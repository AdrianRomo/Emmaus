"""An urgent notice has versions: a change of what it says asks for "Enterado" again, a corrected
detail does not (decision 0015). And who may publish an urgent notice is the church's policy."""

from datetime import timedelta
from unittest import mock

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Role
from core.services import assign_role
from events.tests.base import make_user
from groups import services as group_services
from groups.models import GroupMembership
from groups.templatetags.community import long_date
from groups.tests.base import CommunityFixture
from notices import services
from notices.models import Announcement, AnnouncementRead
from people import flags
from people.audit import ACTION_LABELS
from people.models import ChurchAuditEvent

HOME = reverse("community:home")
CREATE = reverse("notices:create")
TITLE = "Salida al retiro"
AT_FIVE = "La salida será a las 5:00 p. m."
AT_SEVEN = "La salida será a las 7:00 p. m."
URGENT, IMPORTANT, NORMAL = "urgent", "important", "normal"
QUESTION = "¿Cambió la información?"
ASK_AGAIN = "Sí, cambió algo importante: pedir «Enterado» otra vez"
A_DETAIL = "No, solo corregí un detalle: se conservan los «Enterado»"
CHANGED = "Léelo otra vez y toca «Enterado»."
WITH_PASTORS = frozenset({Role.CHURCH_ADMIN.value, Role.PASTOR.value})


def detail(notice):
    return reverse("notices:detail", args=[notice.pk])


def edit(notice):
    return reverse("notices:edit", args=[notice.pk])


def enterado(notice):
    return reverse("notices:acknowledge", args=[notice.pk])


class Fixture(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        group_services.set_membership(cls.group, cls.member, cls.admin, role=GroupMembership.Role.MEMBER)
        cls.today = timezone.localdate()
        for number in range(2):  # six readers with an account: the count is given
            cls._person(cls.church, make_user(f"persona{number}@prueba.mx", "Persona", f"Número {number}"))

    def urgent(self, **changes):
        fields = {"title": TITLE, "body": AT_FIVE, "priority": URGENT, **changes}
        return services.publish(self.church, self.admin, **fields)

    def change(self, notice, actor=None, **changes):
        fields = {"title": notice.title, "body": notice.body, "publish_on": notice.publish_on, "expires_on": notice.expires_on}
        return services.update(notice, actor or self.admin, **{**fields, **changes})

    def data(self, **changes):
        return {"title": TITLE, "priority": URGENT, "body": AT_FIVE, "publish_on": self.today.isoformat(), **changes}

    def waiting(self, person):
        return services.urgent_pending(person, is_admin=False)


class VersionTests(Fixture):
    # ---- the owner's example ----------------------------------------------------------------------
    def test_the_departure_moves_from_five_to_seven_and_everybody_is_asked_again(self):
        notice = self.urgent()
        self.assertEqual((notice.version, notice.changed_at), (1, None))
        self.client.force_login(self.member_user)
        self.client.post(enterado(notice), {"version": "1"})
        first = AnnouncementRead.objects.get(announcement=notice, person=self.member)
        self.assertEqual(first.acknowledged_version, 1)
        days_ago = timezone.now() - timedelta(days=40)  # that answer was given another month
        AnnouncementRead.objects.filter(pk=first.pk).update(acknowledged_at=days_ago)
        self.assertEqual(self.waiting(self.member), [])
        self.assertEqual(services.acknowledgement_count(notice), (1, 6))

        self.client.force_login(self.admin)
        page = self.client.get(detail(notice))
        self.assertContains(page, "Menos de 5 de 6 personas han dicho «Enterado».")
        self.assertNotContains(page, "la cuenta empezó de nuevo")
        response = self.client.post(edit(notice), self.data(body=AT_SEVEN, material="si"), follow=True)
        self.assertRedirects(response, detail(notice))
        self.assertContains(response, "Guardamos los cambios y pedimos «Enterado» otra vez")
        notice.refresh_from_db()
        self.assertEqual((notice.version, notice.body), (2, AT_SEVEN))
        self.assertIsNotNone(notice.changed_at)
        # The earlier answer does not count, and the count line starts over.
        self.assertEqual(services.acknowledgement_count(notice), (0, 6))
        self.assertIsNone(services.acknowledged_at(notice, self.member))
        self.assertContains(response, "0 de 6 personas dijeron «Enterado».")
        self.assertContains(response, "y se pidió «Enterado» otra vez: la cuenta empezó de nuevo ese día.")
        self.assertNotContains(response, "1 de 6")
        # The notice is at the top of Mi iglesia again for who had answered, and for who had not.
        self.assertEqual(self.waiting(self.member), [notice])
        self.assertEqual(self.waiting(self.asked), [notice])
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(HOME), f"Aviso urgente: {TITLE}")
        page = self.client.get(detail(notice))
        day = notice.changed_at.astimezone(timezone.get_current_timezone())
        self.assertContains(page, f"Este aviso cambió el {day.day} de ")
        self.assertContains(page, f"de {day.year}. {CHANGED}")
        self.assertContains(page, "7:00 p. m.")
        self.assertContains(page, ">Enterado</button>")
        self.assertNotContains(page, "Dijiste «Enterado»")
        self.assertNotContains(page, "la cuenta empezó")  # that line is for who may change the notice
        # Answering again keeps one date: the latest.
        response = self.client.post(enterado(notice), {"version": "2"}, follow=True)
        self.assertContains(response, "Listo, anotamos tu «Enterado».")
        again = AnnouncementRead.objects.get(announcement=notice, person=self.member)
        self.assertEqual((again.pk, again.acknowledged_version), (first.pk, 2))
        self.assertGreater(again.acknowledged_at, notice.changed_at)
        self.assertEqual(AnnouncementRead.objects.filter(announcement=notice, person=self.member).count(), 1)
        page = self.client.get(detail(notice)).content.decode()
        self.assertEqual(page.count("Dijiste «Enterado» el "), 1)
        self.assertIn(f"Dijiste «Enterado» el {long_date(self.today)}.", page)
        self.assertNotIn(long_date(timezone.localdate(days_ago)), page)
        self.assertNotIn(CHANGED, page)
        self.assertNotIn(">Enterado</button>", page)
        self.assertEqual(self.waiting(self.member), [])
        self.assertEqual(services.acknowledgement_count(notice), (1, 6))
        self.assertEqual(self.waiting(self.asked), [notice])

    def test_who_opens_it_for_the_first_time_after_the_change_is_not_told_to_read_it_again(self):
        notice = self.change(self.urgent(), body=AT_SEVEN, material=True)
        self.client.force_login(self.asked_user)
        page = self.client.get(detail(notice))
        self.assertContains(page, "Este aviso es urgente. Cuando lo hayas leído, dilo aquí.")
        self.assertNotContains(page, CHANGED)
        # Who had opened it without answering is told.
        other = self.urgent(title="Otro aviso")
        services.mark_read(other, self.asked)
        other = self.change(other, body=AT_SEVEN, material=True)
        self.assertContains(self.client.get(detail(other)), CHANGED)

    # ---- a detail ---------------------------------------------------------------------------------
    def test_a_corrected_detail_keeps_the_answers(self):
        notice = self.urgent()
        services.acknowledge(notice, self.member)
        self.client.force_login(self.admin)
        response = self.client.post(edit(notice), self.data(body="La salida será a las 5:00 p. m. en punto.", material="no"), follow=True)
        self.assertContains(response, "Guardamos los cambios.")
        self.assertNotContains(response, "pedimos «Enterado» otra vez")
        self.assertContains(response, "Menos de 5 de 6 personas han dicho «Enterado».")
        notice.refresh_from_db()
        self.assertEqual((notice.version, notice.changed_at, notice.body), (1, None, "La salida será a las 5:00 p. m. en punto."))
        self.assertEqual(self.waiting(self.member), [])
        self.client.force_login(self.member_user)
        page = self.client.get(detail(notice))
        self.assertContains(page, "Dijiste «Enterado» el ")
        self.assertNotContains(page, CHANGED)
        self.assertFalse(ChurchAuditEvent.objects.filter(action="aviso_enterado_pedido").exists())

    def test_with_a_small_audience_the_change_is_said_and_no_count_is_promised(self):
        notice = self.change(self.urgent(group=self.group), body=AT_SEVEN, material=True)  # two readers
        self.client.force_login(self.admin)
        page = self.client.get(detail(notice))
        self.assertContains(page, "y se pidió «Enterado» otra vez.</p>")
        self.assertContains(page, "Este aviso es para menos de 5 personas con cuenta")
        self.assertNotContains(page, "la cuenta empezó")

    # ---- the question on the form -----------------------------------------------------------------
    def test_the_form_asks_the_question_and_does_not_save_a_change_of_words_without_the_answer(self):
        notice = self.urgent()
        self.client.force_login(self.admin)
        page = self.client.get(edit(notice))
        html = page.content.decode()
        for words in (QUESTION, ASK_AGAIN, A_DETAIL):
            self.assertContains(page, words)
        self.assertEqual(html.count('type="radio" name="material"'), 2)
        self.assertNotIn("checked", html[html.index('id="campo-material"'):html.index('id="campo-publish_on"')])
        self.assertLess(html.index('id="campo-body"'), html.index('id="campo-material"'))
        for change in ({"body": AT_SEVEN}, {"title": "Salida al retiro de jóvenes"}, {"body": AT_SEVEN, "material": "quizá"}):
            response = self.client.post(edit(notice), self.data(**change))
            self.assertEqual(response.status_code, 200, change)
            self.assertContains(response, "Error: Editar el aviso")
            notice.refresh_from_db()
            self.assertEqual((notice.title, notice.body, notice.version), (TITLE, AT_FIVE, 1), change)
        self.assertNotContains(response, "Cambiaste lo que dice el aviso.")  # an answer that is not one of the two
        response = self.client.post(edit(notice), self.data(body=AT_SEVEN))
        self.assertContains(response, "Cambiaste lo que dice el aviso. Elige si cambió la información o si solo corregiste un detalle.")
        self.assertContains(response, 'href="#id_material_0"')  # the summary's link lands on the first answer
        self.assertContains(response, 'name="material" value="si" id="id_material_0"')

    def test_the_question_is_not_needed_when_the_words_do_not_change(self):
        notice = self.urgent()
        services.acknowledge(notice, self.member)
        self.client.force_login(self.admin)
        later = (self.today + timedelta(days=9)).isoformat()
        # Another last day, the same words sent with a browser's line breaks and spaces: nothing to answer.
        response = self.client.post(edit(notice), self.data(title=f"  {TITLE} ", body=AT_FIVE + "\r\n", expires_on=later))
        self.assertRedirects(response, detail(notice))
        # An answer nobody needed changes nothing.
        self.client.post(edit(notice), self.data(expires_on=later, material="si"))
        notice.refresh_from_db()
        self.assertEqual((notice.version, notice.changed_at, notice.expires_on.isoformat()), (1, None, later))
        self.assertEqual(services.acknowledgement_count(notice), (1, 6))

    def test_a_notice_that_is_not_urgent_or_that_nobody_could_read_yet_does_not_ask(self):
        self.client.force_login(self.admin)
        important = services.publish(self.church, self.admin, title=TITLE, body=AT_FIVE, priority=IMPORTANT)
        scheduled = self.urgent(publish_on=self.today + timedelta(days=2))
        for notice in (important, scheduled):
            self.assertNotContains(self.client.get(edit(notice)), QUESTION)
            data = self.data(body=AT_SEVEN, priority=notice.priority, publish_on=notice.publish_on.isoformat())
            self.assertRedirects(self.client.post(edit(notice), data), detail(notice))
            notice.refresh_from_db()
            self.assertEqual((notice.body, notice.version, notice.changed_at), (AT_SEVEN, 1, None))
        # Scheduled again after somebody answered: there is an answer to keep or to ask again.
        answered = self.urgent(title="Con respuesta")
        services.acknowledge(answered, self.member)
        answered = self.change(answered, publish_on=self.today + timedelta(days=2))
        self.assertContains(self.client.get(edit(answered)), QUESTION)
        with self.assertRaises(services.NoticeError):
            self.change(answered, body=AT_SEVEN)

    def test_a_guide_correcting_an_urgent_notice_of_their_group_is_asked_too(self):
        notice = services.publish(self.church, self.leader_user, group=self.group, title=TITLE, body=AT_FIVE)
        notice = self.change(notice, priority=URGENT)
        services.acknowledge(notice, self.member)
        self.client.force_login(self.leader_user)
        page = self.client.get(edit(notice))
        self.assertContains(page, QUESTION)
        self.assertNotContains(page, "Nivel")
        self.assertEqual(self.client.post(edit(notice), self.data(body=AT_SEVEN, priority="")).status_code, 200)
        self.assertRedirects(self.client.post(edit(notice), self.data(body=AT_SEVEN, priority="", material="si")), detail(notice))
        notice.refresh_from_db()
        self.assertEqual((notice.version, notice.priority), (2, URGENT))
        self.assertEqual(self.waiting(self.member), [notice])

    # ---- the service decides, not the form ----------------------------------------------------------
    def test_the_service_refuses_a_change_of_words_on_an_urgent_notice_without_the_answer(self):
        notice = self.urgent()
        for change in ({"body": AT_SEVEN}, {"title": "Otro título"}):
            with self.assertRaises(services.NoticeError) as refused:
                self.change(notice, **change)
            self.assertEqual(refused.exception.code, "material")
        notice.refresh_from_db()
        self.assertEqual((notice.title, notice.body, notice.version), (TITLE, AT_FIVE, 1))
        self.assertFalse(ChurchAuditEvent.objects.filter(action="aviso_modificado").exists())
        # The dates alone, or leaving the level, do not need it.
        self.change(notice, expires_on=self.today + timedelta(days=3))
        self.change(notice, body=AT_SEVEN, priority=NORMAL)
        notice.refresh_from_db()
        self.assertEqual((notice.body, notice.priority, notice.version), (AT_SEVEN, NORMAL, 1))

    def test_a_notice_that_becomes_urgent_starts_at_its_version_with_no_answers(self):
        notice = self.change(self.urgent(), body=AT_SEVEN, material=True)
        services.acknowledge(notice, self.member)
        self.assertEqual(services.acknowledgement_count(notice), (1, 6))
        notice = self.change(notice, priority=IMPORTANT)
        with self.assertRaises(services.NoticeError):
            services.acknowledge(notice, self.asked)
        notice = self.change(notice, body="La salida será a las 8:00 p. m.", priority=URGENT)  # no answer needed
        self.assertEqual((notice.version, notice.changed_at), (2, None))
        self.assertEqual(services.acknowledgement_count(notice), (0, 6))
        self.assertEqual(self.waiting(self.member), [notice])
        self.client.force_login(self.member_user)
        page = self.client.get(detail(notice))
        self.assertContains(page, ">Enterado</button>")
        self.assertNotContains(page, CHANGED)
        self.assertTrue(services.acknowledge(notice, self.member))
        self.assertEqual(services.acknowledgement_count(notice), (1, 6))

    def test_an_answer_from_a_page_that_showed_the_earlier_words_is_not_taken(self):
        notice = self.urgent()
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(detail(notice)), '<input type="hidden" name="version" value="1">')
        self.change(notice, body=AT_SEVEN, material=True)
        for sent in ({"version": "1"}, {}, {"version": "lo-que-sea"}):
            response = self.client.post(enterado(notice), sent, follow=True)
            self.assertContains(response, "Este aviso cambió mientras lo leías. Léelo otra vez y toca «Enterado».")
            self.assertContains(response, '<input type="hidden" name="version" value="2">')
        self.assertFalse(AnnouncementRead.objects.filter(acknowledged_at__isnull=False).exists())
        self.assertEqual(self.waiting(self.member), [notice])
        with self.assertRaises(services.NoticeError):
            services.acknowledge(notice, self.member, version=1)
        self.client.post(enterado(notice), {"version": "2"})
        self.assertEqual(self.waiting(self.member), [])
        self.assertFalse(services.acknowledge(notice, self.member))  # idempotent in the same version

    # ---- audit ------------------------------------------------------------------------------------
    def test_asking_again_leaves_an_audit_row_without_the_words_or_anybody(self):
        notice = self.urgent(group=self.group)
        services.acknowledge(notice, self.member)
        self.change(notice, body=AT_SEVEN, material=True)
        row = ChurchAuditEvent.objects.get(action="aviso_enterado_pedido")
        self.assertEqual(row.summary, "Cambió la información de un aviso urgente para Jóvenes Roca y pidió «Enterado» otra vez")
        self.assertEqual((row.metadata, row.object_type, row.object_id, row.actor), ({"group_id": self.group.pk}, "Announcement", str(notice.pk), self.admin))
        for words in ("salida", "7:00", "Diego", TITLE):
            self.assertNotIn(words, row.summary + str(row.metadata))
        self.assertEqual(ChurchAuditEvent.objects.filter(action="aviso_modificado").count(), 1)
        self.assertEqual(ACTION_LABELS["aviso_enterado_pedido"], "Pidió «Enterado» otra vez en un aviso urgente")

    # ---- another church, the module off -------------------------------------------------------------
    def test_another_church_and_the_module_off(self):
        notice = self.urgent()
        theirs = services.publish(self.church_b, self.admin_b, title="Aviso de B", body="Cosas de B", priority=URGENT)
        services.acknowledge(theirs, self.member_b)
        self.change(notice, body=AT_SEVEN, material=True)
        theirs.refresh_from_db()
        self.assertEqual((theirs.version, theirs.changed_at), (1, None))  # asking again here asks nobody there
        self.assertEqual(services.urgent_pending(self.member_b, is_admin=False), [])
        self.assertEqual(self.waiting(self.member), [notice])
        self.client.force_login(self.admin_b)
        for url in (edit(notice), detail(notice)):
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(edit(notice), self.data(body="De otra iglesia", material="si")).status_code, 404)
        self.assertEqual(self.client.post(enterado(notice), {"version": "2"}).status_code, 404)
        self.client.force_login(self.member_user)  # a reader does not edit
        self.assertEqual(self.client.post(edit(notice), self.data(body="De un lector", material="si")).status_code, 403)
        notice.refresh_from_db()
        self.assertEqual((notice.body, notice.version), (AT_SEVEN, 2))
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(edit(notice)).status_code, 404)
        self.assertEqual(self.client.post(edit(notice), self.data(body="Apagado", material="si")).status_code, 404)
        notice.refresh_from_db()
        self.assertEqual((notice.body, notice.version), (AT_SEVEN, 2))

    def test_the_edit_form_keeps_the_design_rules(self):
        notice = self.urgent()
        self.client.force_login(self.admin)
        for response in (self.client.get(edit(notice)), self.client.post(edit(notice), self.data(body=AT_SEVEN))):
            html = response.content.decode()
            main = html[html.index("<main"):html.index("</main>")]
            self.assertEqual(html.count("<h1"), 1)
            self.assertEqual(main.count("btn--primary"), 1)
            self.assertIn(f'<legend class="legend">{QUESTION}</legend>', main)
            self.assertNotIn("<svg", main)


class UrgentPolicyTests(Fixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.pastor = make_user("pastor-a@prueba.mx", "Pablo", "Pastor")
        assign_role(cls.pastor, cls.church, Role.PASTOR)
        cls.pastor_person = cls._person(cls.church, cls.pastor)
        cls.pastor_b = make_user("pastor-b@prueba.mx", "Benito", "Pastor")
        assign_role(cls.pastor_b, cls.church_b, Role.PASTOR)
        cls._person(cls.church_b, cls.pastor_b)

    def publish(self, actor, church=None, **changes):
        return services.publish(church or self.church, actor, **{"title": TITLE, "body": AT_FIVE, "priority": URGENT, **changes})

    def test_today_only_the_administration_of_that_church(self):
        self.assertTrue(services.may_publish_urgent(self.admin, self.church))
        for user, church in (
            (self.pastor, self.church), (self.leader_user, self.church), (self.member_user, self.church),
            (self.admin_b, self.church), (self.admin, self.church_b), (None, self.church),
        ):
            self.assertFalse(services.may_publish_urgent(user, church))
            with self.assertRaises(services.NoticeError):
                self.publish(user, church)
        self.admin.is_active = False
        self.assertFalse(services.may_publish_urgent(self.admin, self.church))
        self.assertEqual(self.publish(self.pastor, priority=IMPORTANT).priority, IMPORTANT)
        self.assertFalse(Announcement.objects.filter(priority=URGENT).exists())

    def test_a_church_whose_policy_names_pastors_lets_its_pastor_and_not_another_churchs(self):
        with mock.patch("people.policy.urgent_notice_roles", return_value=WITH_PASTORS) as asked:
            notice = self.publish(self.pastor)  # for the whole church
            self.assertEqual((notice.priority, notice.group, notice.church), (URGENT, None, self.church))
            self.assertEqual(asked.call_args.args, (self.church,))  # the policy of the church the notice is of
            self.assertEqual(self.publish(self.admin, title="El de la administración").priority, URGENT)
            # A pastor of another church holds nothing in this one; nor does who guides a group here.
            for actor in (self.pastor_b, self.leader_user, self.member_user):
                with self.assertRaises(services.NoticeError) as refused:
                    self.publish(actor)
                self.assertEqual(refused.exception.message, "En esta iglesia no tienes permiso para publicar avisos urgentes.")
            # Nor by making urgent, afterwards, a notice that was not.
            plain = self.publish(self.admin, title="Uno normal", priority=NORMAL)
            with self.assertRaises(services.NoticeError):
                services.update(plain, self.pastor_b, title=plain.title, body=plain.body, publish_on=self.today, priority=URGENT)
            self.assertEqual(self.publish(self.pastor_b, self.church_b).church, self.church_b)  # in their own, yes
        self.assertEqual(Announcement.objects.filter(church=self.church, priority=URGENT).count(), 2)
        # Without that policy the same pastor is refused again.
        with self.assertRaises(services.NoticeError) as refused:
            self.publish(self.pastor)
        self.assertEqual(refused.exception.message, "Solo la administración de la iglesia publica avisos urgentes.")

    def test_the_form_offers_urgente_by_the_policy_and_the_view_still_decides_who_it_is_for(self):
        group_services.set_membership(self.group, self.pastor_person, self.admin, role=GroupMembership.Role.LEADER)
        data = {"para": self.group.pk, "title": TITLE, "priority": URGENT, "body": AT_FIVE, "publish_on": self.today.isoformat()}
        self.client.force_login(self.pastor)
        page = self.client.get(CREATE + f"?grupo={self.group.pk}")
        self.assertNotContains(page, 'value="urgent"')
        self.assertContains(self.client.post(CREATE, data), "Elige el nivel del aviso entre los que aparecen.")
        self.assertFalse(Announcement.objects.exists())
        with mock.patch("people.policy.urgent_notice_roles", return_value=WITH_PASTORS):
            self.assertContains(self.client.get(CREATE + f"?grupo={self.group.pk}"), 'value="urgent"')
            self.client.post(CREATE, data)
            notice = Announcement.objects.get()
            self.assertEqual((notice.priority, notice.group), (URGENT, self.group))
            # The policy is about the level. Writing to the whole church is still the administration's.
            self.assertEqual(self.client.post(CREATE, {**data, "para": "iglesia"}).status_code, 403)
            # A guide who is not a pastor is offered what they were.
            self.client.force_login(self.leader_user)
            self.assertNotContains(self.client.get(CREATE + f"?grupo={self.group.pk}"), 'value="urgent"')
        self.assertEqual(Announcement.objects.count(), 1)


class MigrationTests(TransactionTestCase):
    def test_the_answers_given_before_versions_existed_still_count(self):
        executor = MigrationExecutor(connection)
        before = [("notices", "0002_priority_and_acknowledgement")]
        after = [("notices", "0003_versions_of_an_urgent_notice")]
        try:
            executor.migrate(before)
            apps = executor.loader.project_state(before).apps
            church = apps.get_model("core", "Church").objects.create(name="Iglesia de prueba", slug="iglesia-de-prueba")
            people = apps.get_model("people", "Person").objects
            notice = apps.get_model("notices", "Announcement").objects.create(
                church=church, title=TITLE, body=AT_FIVE, priority=URGENT, publish_on=timezone.localdate()
            )
            reads = apps.get_model("notices", "AnnouncementRead").objects
            answered = reads.create(
                announcement=notice, person=people.create(church=church, first_name="Ana"), acknowledged_at=timezone.now()
            )
            only_read = reads.create(announcement=notice, person=people.create(church=church, first_name="Beto"))
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            self.assertEqual(Announcement.objects.get(pk=notice.pk).version, 1)
            self.assertEqual(AnnouncementRead.objects.get(pk=answered.pk).acknowledged_version, 1)
            self.assertIsNone(AnnouncementRead.objects.get(pk=only_read.pk).acknowledged_version)
            self.assertEqual(list(services._answers().values_list("pk", flat=True)), [answered.pk])
        finally:
            MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())


class CountMaskTests(SimpleTestCase):
    """A count that leaves fewer than the minimum on either side says who (review of wave 5)."""

    def line(self, done, total):
        from unittest import mock

        from notices import views

        notice = mock.Mock()
        with mock.patch.object(views.services, "acknowledgement_count", return_value=(done, total)):
            return views._acknowledged_line(notice)[0]

    def test_neither_the_few_who_answered_nor_the_few_who_did_not_are_counted(self):
        self.assertEqual(self.line(0, 12), "0 de 12 personas dijeron «Enterado».")
        self.assertEqual(self.line(3, 12), "Menos de 5 de 12 personas han dicho «Enterado».")
        self.assertEqual(self.line(6, 12), "6 de 12 personas dijeron «Enterado».")
        self.assertEqual(self.line(11, 12), "Casi todas las 12 personas dijeron «Enterado».")
        self.assertEqual(self.line(12, 12), "12 de 12 personas dijeron «Enterado».")
        self.assertIn("menos de 5 personas con cuenta", self.line(2, 4))
