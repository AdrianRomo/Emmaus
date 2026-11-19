"""Tags: the administration's own labels for filtering its people. Staff-only, never a permission."""

from django.test import Client
from django.urls import reverse

from core.authz import Action, has_scope
from groups import services as groups
from groups.tests.base import CommunityFixture
from people import flags, tags
from people.models import ChurchAuditEvent, Person, PersonTag, Tag
from people.services import PeopleError


class TagFixture(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.coro = tags.create_tag(cls.church, "Coro", cls.admin)
        cls.llaves = tags.create_tag(cls.church, "Llaves del templo", cls.admin)
        cls.tag_b = tags.create_tag(cls.church_b, "Secreta de B", cls.admin_b)

    def carried(self, person) -> set[str]:
        return set(PersonTag.objects.filter(person=person).values_list("tag__name", flat=True))


class TagScreenTests(TagFixture):
    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("people:tags")

    def test_create_with_the_warning_beside_the_field(self):
        page = self.client.get(self.url)
        self.assertContains(page, "No uses etiquetas para asuntos delicados: salud, disciplina o situaciones familiares.")
        self.assertNotContains(page, "Secreta de B")
        response = self.client.post(self.url, {"nombre": "  Ujieres   del domingo "}, follow=True)
        self.assertContains(response, "Creaste la etiqueta «Ujieres del domingo».")
        tag = Tag.objects.get(church=self.church, name="Ujieres del domingo")
        row = ChurchAuditEvent.objects.get(action="etiqueta_creada", object_id=str(tag.pk))
        self.assertEqual(row.metadata, {"tag_id": tag.pk})
        self.assertNotIn("Ujieres", row.summary)  # what a person typed is never in the log

    def test_names_are_short_single_line_unique_and_escaped(self):
        for name, words in (("", "Escribe el nombre"), ("   ", "Escribe el nombre"), ("x" * 41, "40 caracteres o menos"),
                            ("Dos\nlíneas", "una sola línea"), ("CORO", "Ya hay una etiqueta con ese nombre"),
                            ("córo", "Ya hay una etiqueta con ese nombre")):
            self.assertContains(self.client.post(self.url, {"nombre": name}), words)
        self.assertEqual(Tag.objects.filter(church=self.church).count(), 2)
        self.client.post(self.url, {"nombre": "<script>alert(1)</script>"})
        page = self.client.get(self.url)
        self.assertContains(page, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(page, "<script>alert(1)")
        tag = Tag.objects.get(name__startswith="<script>")
        tags.set_tags(self.church, self.member.pk, {tag.pk}, self.admin, seen=set())
        for url in (reverse("people:detail", args=[self.member.pk]), reverse("people:list"), reverse("people:tag_delete", args=[tag.pk])):
            self.assertNotContains(self.client.get(url), "<script>alert(1)")

    def test_at_most_thirty_per_church(self):
        for number in range(28):
            tags.create_tag(self.church, f"Etiqueta {number}", self.admin)
        self.assertEqual(Tag.objects.filter(church=self.church).count(), 30)
        self.assertContains(self.client.post(self.url, {"nombre": "Una más"}), "que es el máximo")
        with self.assertRaises(PeopleError):
            tags.create_tag(self.church, "Otra más", self.admin)
        self.assertEqual(Tag.objects.filter(church=self.church).count(), 30)
        self.assertNotContains(self.client.get(self.url), 'name="nombre"')
        tags.create_tag(self.church_b, "La B no está llena", self.admin_b)  # the limit is per church

    def test_delete_takes_it_off_everybody_after_one_question(self):
        tags.set_tags(self.church, self.member.pk, {self.coro.pk, self.llaves.pk}, self.admin, seen=set())
        url = reverse("people:tag_delete", args=[self.coro.pk])
        self.assertContains(self.client.get(url), "la tiene 1 persona")
        self.assertTrue(Tag.objects.filter(pk=self.coro.pk).exists())  # a GET deletes nothing
        # The number changed while the question was open: nothing is deleted, the question is asked again.
        tags.set_tags(self.church, self.leader.pk, {self.coro.pk}, self.admin, seen=set())
        response = self.client.post(url, {"personas": "1"})
        self.assertContains(response, "No borramos nada.")
        self.assertContains(response, "la tienen 2 personas")
        self.assertTrue(Tag.objects.filter(pk=self.coro.pk).exists())
        self.assertRedirects(self.client.post(url, {"personas": "2"}), self.url)
        self.assertFalse(Tag.objects.filter(pk=self.coro.pk).exists())
        self.assertEqual(self.carried(self.member), {"Llaves del templo"})
        self.assertTrue(Person.objects.filter(pk=self.member.pk).exists())
        row = ChurchAuditEvent.objects.get(action="etiqueta_borrada")
        self.assertEqual(row.metadata, {"tag_id": self.coro.pk, "count": 2})
        self.assertNotIn("Coro", row.summary)


class PersonTagTests(TagFixture):
    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("people:person_tags", args=[self.member.pk])

    def test_mark_and_unmark_from_the_persons_page(self):
        page = self.client.get(reverse("people:detail", args=[self.member.pk]))
        self.assertContains(page, "Llaves del templo")
        self.assertNotContains(page, "Secreta de B")
        self.client.post(self.url, {"etiqueta": [str(self.coro.pk), str(self.llaves.pk)], "antes": ""})
        self.assertEqual(self.carried(self.member), {"Coro", "Llaves del templo"})
        seen = f"{self.coro.pk},{self.llaves.pk}"
        self.client.post(self.url, {"etiqueta": [str(self.llaves.pk)], "antes": seen})
        self.assertEqual(self.carried(self.member), {"Llaves del templo"})
        rows = ChurchAuditEvent.objects.filter(action="persona_etiquetas").order_by("pk")
        self.assertEqual([row.metadata for row in rows], [{"person_id": self.member.pk, "count": 2}, {"person_id": self.member.pk, "count": 1}])
        self.client.post(self.url, {"etiqueta": [str(self.llaves.pk)], "antes": str(self.llaves.pk)})  # nothing changes: no row
        self.assertEqual(rows.count(), 2)

    def test_a_tag_of_another_church_is_never_put_on_a_person(self):
        response = self.client.post(self.url, {"etiqueta": [str(self.tag_b.pk)], "antes": ""}, follow=True)
        self.assertContains(response, "Alguna de esas etiquetas ya no existe")
        self.assertEqual(self.carried(self.member), set())
        with self.assertRaises(PeopleError):
            tags.set_tags(self.church, self.member_b.pk, {self.coro.pk}, self.admin, seen=set())  # nor a person of B
        self.assertEqual(PersonTag.objects.count(), 0)

    def test_a_page_opened_before_somebody_else_changed_the_tags_changes_nothing(self):
        tags.set_tags(self.church, self.member.pk, {self.coro.pk}, self.admin, seen=set())
        response = self.client.post(self.url, {"etiqueta": [str(self.llaves.pk)], "antes": ""}, follow=True)
        self.assertContains(response, "Alguien más cambió sus etiquetas")
        self.assertEqual(self.carried(self.member), {"Coro"})

    def test_the_list_filters_by_tag_and_by_stage(self):
        tags.set_tags(self.church, self.member.pk, {self.coro.pk}, self.admin, seen=set())
        Person.objects.filter(pk=self.leader.pk).update(stage="miembro")
        url = reverse("people:list")
        page = self.client.get(url, {"etiqueta": self.coro.pk})
        self.assertContains(page, "Diego")
        self.assertNotContains(page, "Gala")
        self.assertContains(page, "1 resultado")
        page = self.client.get(url, {"etapa": "miembro"})
        self.assertContains(page, "Gala")
        self.assertContains(page, "Miembro. Con acceso a Emmaus.")
        self.assertNotContains(page, "Diego")
        page = self.client.get(url, {"etapa": "sin-anotar", "q": "diego"})
        self.assertContains(page, "Diego")
        self.assertNotContains(page, "Gala")
        self.assertContains(self.client.get(url, {"etapa": "miembro", "etiqueta": self.coro.pk}), "Nadie coincide con lo que buscas.")
        # A tag of another church, a word that is no stage: no filter, and nothing of church B.
        page = self.client.get(url, {"etiqueta": self.tag_b.pk, "etapa": "<b>"})
        self.assertContains(page, "Diego")
        self.assertContains(page, "Gala")
        self.assertNotContains(page, "Secreta de B")
        self.assertNotContains(page, "Bruno")
        # A record that was merged away is not listed by its tag either.
        hidden = Person.objects.create(church=self.church, first_name="Dora", last_name="Oculta", merged_into=self.member)
        PersonTag.objects.create(person=hidden, tag=self.coro)
        self.assertNotContains(self.client.get(url, {"etiqueta": self.coro.pk}), "Oculta")
        self.assertContains(self.client.get(reverse("people:tags")), "La tiene 1 persona.")


class TagDenialTests(TagFixture):
    def test_only_the_administration_of_this_church(self):
        pages = [(reverse("people:tags"), None), (reverse("people:tag_delete", args=[self.coro.pk]), None),
                 (reverse("people:tags"), {"nombre": "Nueva"}), (reverse("people:tag_delete", args=[self.coro.pk]), {"personas": "0"}),
                 (reverse("people:person_tags", args=[self.member.pk]), {"etiqueta": [str(self.coro.pk)], "antes": ""})]

        def ask(url, data):
            return self.client.get(url) if data is None else self.client.post(url, data)

        for url, data in pages:
            self.assertEqual(ask(url, data).status_code, 302)  # signed out
        for user, expected in ((self.member_user, 403), (self.leader_user, 403), (self.coord1, 403)):
            self.client.force_login(user)
            for url, data in pages:
                self.assertEqual(ask(url, data).status_code, expected, (user.email, url))
        # The administration of church B: its own tag screen answers, church A's tag and person do not exist.
        self.client.force_login(self.admin_b)
        self.assertEqual(self.client.get(reverse("people:tag_delete", args=[self.coro.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("people:tag_delete", args=[self.coro.pk]), {"personas": "0"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("people:person_tags", args=[self.member.pk]), {"etiqueta": [], "antes": ""}).status_code, 404)
        self.assertNotContains(self.client.get(reverse("people:tags")), "la etiqueta Coro")
        self.assertEqual(self.client.get(reverse("people:person_tags", args=[self.member_b.pk])).status_code, 405)
        flags.set_enabled(self.church, "people", False)
        self.client.force_login(self.admin)
        for url, data in pages:
            self.assertEqual(ask(url, data).status_code, 404)
        self.assertEqual((Tag.objects.filter(church=self.church).count(), PersonTag.objects.count()), (2, 0))

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        for url, data in ((reverse("people:tags"), {"nombre": "Nueva"}), (reverse("people:tag_delete", args=[self.coro.pk]), {"personas": "0"}),
                          (reverse("people:person_tags", args=[self.member.pk]), {"etiqueta": [str(self.coro.pk)], "antes": ""})):
            self.assertEqual(client.post(url, data).status_code, 403)
        self.assertEqual((Tag.objects.filter(church=self.church).count(), PersonTag.objects.count()), (2, 0))

    def test_a_tag_is_seen_by_nobody_else_and_gives_no_permission(self):
        groups.set_membership(self.group, self.member, self.admin)

        def scopes():
            return [has_scope(self.member_user, self.church, scope, scope_id, action)
                    for scope, scope_id in (("church", self.church.pk), ("group", self.group.pk), ("group", self.other_group.pk))
                    for action in Action]

        before = scopes()
        tags.set_tags(self.church, self.member.pk, {self.llaves.pk, self.coro.pk}, self.admin, seen=set())
        self.assertEqual(scopes(), before)
        for user, urls in (
            (self.member_user, [reverse("community:home"), reverse("community:profile"), reverse("community:group_detail", args=[self.group.pk])]),
            (self.leader_user, [reverse("community:group_people", args=[self.group.pk]), reverse("community:group_detail", args=[self.group.pk])]),
        ):
            self.client.force_login(user)
            for url in urls:
                page = self.client.get(url)
                self.assertEqual(page.status_code, 200)
                self.assertNotContains(page, "Llaves del templo")
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(reverse("people:list")).status_code, 403)

    def test_the_authorization_layer_never_reads_a_tag(self):
        from pathlib import Path

        from django.conf import settings

        root = Path(settings.BASE_DIR)
        sources = [root / "core" / "authz.py", *sorted((root / "access").glob("*.py")), root / "events" / "access.py", root / "core" / "access.py"]
        self.assertGreater(len(sources), 3)
        for source in sources:
            text = source.read_text()
            for word in ("PersonTag", "people.tags", "tag_links", "person_tags"):
                self.assertNotIn(word, text, source.name)
