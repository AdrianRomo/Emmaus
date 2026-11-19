"""The screens of the duplicate review: who opens them, what they show, and what a guessed id gets."""

from django.test import Client
from django.urls import reverse

from core.models import Role
from core.services import assign_role
from events.tests.base import make_user
from groups import services as groups
from people import duplicates, flags, merge
from people.models import ChurchAuditEvent, DuplicateDismissal, Person, PersonMerge

from .merge_base import MergeFixture


class Screens(MergeFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.church_pastor = make_user("pastora-iglesia@prueba.mx", "Paula", "Pastora")
        assign_role(cls.church_pastor, cls.church, Role.PASTOR)
        cls.record = PersonMerge.objects.create(church=cls.church, survivor=cls.leader, absorbed=Person.objects.create(
            church=cls.church, first_name="Gala", last_name="Antigua", merged_into=cls.leader))
        low, high = sorted([cls.asked.pk, cls.no_account.pk])
        cls.dismissal = DuplicateDismissal.objects.create(church=cls.church, person_a_id=low, person_b_id=high)

    def preview_url(self, survivor=None, absorbed=None):
        return reverse("people:merge_preview", args=[(survivor or self.keep).pk, (absorbed or self.dup).pk])

    def confirm(self, client=None, survivor=None, absorbed=None, **data):
        survivor, absorbed = survivor or self.keep, absorbed or self.dup
        token = merge._token(Person.objects.get(pk=survivor.pk), Person.objects.get(pk=absorbed.pk))
        return (client or self.client).post(self.preview_url(survivor, absorbed), {"revision": token, **data})

    def gets(self):
        return [reverse("people:duplicates"), self.preview_url(), reverse("people:merge_detail", args=[self.record.pk])]

    def posts(self):
        return [
            (self.preview_url(), {"revision": "x"}),
            (reverse("people:duplicate_dismiss"), {"a": self.keep.pk, "b": self.dup.pk}),
            (reverse("people:duplicate_reopen", args=[self.dismissal.pk]), {}),
            (reverse("people:merge_undo", args=[self.record.pk]), {}),
        ]

    def state(self):
        return (
            self.pointers(), PersonMerge.objects.filter(undone_at__isnull=True).count(), DuplicateDismissal.objects.count(),
            ChurchAuditEvent.objects.count(),
        )


class PermissionTests(Screens):
    def test_only_the_church_administration_reviews_merges_and_undoes(self):
        before = self.state()
        for url in self.gets():
            self.assertEqual(self.client.get(url).status_code, 302, url)  # signed out: sign in
        for url, data in self.posts():
            self.assertEqual(self.client.post(url, data).status_code, 302, url)
        # A member, a group's guide, a former coordinator, an event's tutor (not of the church: 404) and the church's
        # pastor, who reads the church and administers nothing.
        for user, expected in ((self.member_user, 403), (self.leader_user, 403), (self.coord1, 403), (self.tutor1, 404),
                               (self.church_pastor, 403)):
            self.client.force_login(user)
            for url in self.gets():
                with self.subTest(user=user.email, url=url):
                    self.assertEqual(self.client.get(url).status_code, expected)
            for url, data in self.posts():
                with self.subTest(user=user.email, url=url):
                    self.assertEqual(self.client.post(url, data).status_code, expected)
        self.assertEqual(self.state(), before)
        self.client.force_login(self.admin)
        for url in self.gets():
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_the_administration_of_another_church_finds_nothing_by_guessed_ids(self):
        before = self.state()
        self.client.force_login(self.admin_b)
        response = self.client.get(reverse("people:duplicates"))
        self.assertEqual(response.status_code, 200)
        for word in ("Lía", "Luna", "Gala", "Pía", "Nora"):
            self.assertNotContains(response, word)
        for url in self.gets()[1:]:
            self.assertEqual(self.client.get(url).status_code, 404, url)
        for url, data in self.posts():
            self.assertEqual(self.client.post(url, data).status_code, 404, url)
        self.assertEqual(self.state(), before)

    def test_a_pair_across_churches_is_a_404_even_posted_by_hand(self):
        twin = Person.objects.create(church=self.church_b, first_name="Lía", last_name="Luna", email="lia@prueba.mx")
        before = self.state()
        for user in (self.admin, self.admin_b):
            self.client.force_login(user)
            for survivor, absorbed in ((self.keep, twin), (twin, self.keep)):
                self.assertEqual(self.client.get(self.preview_url(survivor, absorbed)).status_code, 404)
                self.assertEqual(self.confirm(survivor=survivor, absorbed=absorbed).status_code, 404)
                response = self.client.post(reverse("people:duplicate_dismiss"), {"a": survivor.pk, "b": absorbed.pk})
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.state(), before)
        twin.refresh_from_db()
        self.assertIsNone(twin.merged_into_id)
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse("people:duplicates")), f"/{twin.pk}/")

    def test_module_off_is_404_and_changes_nothing(self):
        before = self.state()
        flags.set_enabled(self.church, "people", False)
        self.client.force_login(self.admin)
        for url in self.gets():
            self.assertEqual(self.client.get(url).status_code, 404, url)
        for url, data in self.posts():
            self.assertEqual(self.client.post(url, data).status_code, 404, url)
        self.assertEqual(self.state()[:3], before[:3])

    def test_every_post_needs_the_csrf_token_and_a_get_changes_nothing(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        before = self.state()
        for url, data in self.posts():
            self.assertEqual(strict.post(url, data).status_code, 403, url)
        self.client.force_login(self.admin)
        for url, _ in self.posts()[1:]:
            self.assertEqual(self.client.get(url).status_code, 405, url)
        self.client.get(self.preview_url())
        self.assertEqual(self.state(), before)


class MergedAwayIdTests(Screens):
    """One answer everywhere: the id of a record that was merged away does not exist. Nobody is
    redirected to the survivor, so nobody learns from an old id who a person was merged into."""

    def test_staff_get_a_404_and_members_get_what_they_get_for_any_id(self):
        self.client.force_login(self.admin)
        self.confirm()
        gone, missing = self.dup.pk, 987654
        urls = lambda pk: [  # noqa: E731
            ("get", reverse("people:detail", args=[pk]), {}), ("get", reverse("people:edit", args=[pk]), {}),
            ("post", reverse("people:edit", args=[pk]), {"first_name": "Otra"}), ("post", reverse("people:grant_access", args=[pk]), {}),
            ("post", reverse("people:access_link", args=[pk]), {}),
            ("get", self.preview_url(self.keep, Person(pk=pk)), {}), ("get", self.preview_url(Person(pk=pk), self.keep), {}),
            ("post", reverse("people:duplicate_dismiss"), {"a": self.keep.pk, "b": pk}),
            ("post", reverse("community:member_add", args=[self.group.pk]), {"persona": pk}),
            ("post", reverse("serving:member_add", args=[self.team.pk]), {"persona": pk}),
            ("get", reverse("care:create") + f"?persona={pk}", {}),
        ]
        for (method, url, data), (_, same, _) in zip(urls(gone), urls(missing)):
            with self.subTest(url=url):
                response = getattr(self.client, method)(url, data)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(f"/{self.keep.pk}/", response.headers.get("Location", ""))
                self.assertEqual(response.status_code, getattr(self.client, method)(same, data).status_code)
        for user in (self.member_user, self.leader_user):
            self.client.force_login(user)
            for (method, url, data), (_, same, _) in zip(urls(gone), urls(missing)):
                with self.subTest(user=user.email, url=url):
                    response = getattr(self.client, method)(url, data)
                    self.assertEqual(response.status_code, getattr(self.client, method)(same, data).status_code)
                    self.assertIn(response.status_code, (403, 404))
                    self.assertNotContains(response, "Luna", status_code=response.status_code)
        self.dup.refresh_from_db()
        self.assertEqual((self.dup.merged_into_id, self.dup.first_name), (self.keep.pk, "Lia"))

    def test_the_absorbed_record_leaves_the_list_the_search_and_the_review(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("people:list") + "?q=luna"), "Luna Ríos")
        self.confirm()
        self.assertNotContains(self.client.get(reverse("people:list") + "?q=luna"), "Luna Ríos")
        self.assertContains(self.client.get(reverse("people:list") + "?q=luna"), "Lía Luna")
        page = self.client.get(reverse("people:duplicates"))
        self.assertNotContains(page, self.preview_url())
        self.assertContains(page, "Lia Luna Ríos se unió a Lía Luna")


class ReviewFlowTests(Screens):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_the_list_shows_pairs_and_reasons_and_not_the_shared_value(self):
        response = self.client.get(reverse("people:duplicates"))
        self.assertContains(response, "1 par por revisar")
        self.assertContains(response, self.preview_url(*sorted([self.keep, self.dup], key=lambda person: person.pk)))
        self.assertContains(response, "Tienen el mismo correo.")
        self.assertNotContains(response, "lia@prueba.mx")
        self.assertContains(response, "Pares que alguien descartó")
        self.assertContains(self.client.get(reverse("people:list")), reverse("people:duplicates"))

    def test_dismissing_keeps_the_pair_out_and_reopening_brings_it_back(self):
        audited = ChurchAuditEvent.objects.count()
        response = self.client.post(reverse("people:duplicate_dismiss"), {"a": self.dup.pk, "b": self.keep.pk})
        self.assertRedirects(response, reverse("people:duplicates"))
        page = self.client.get(reverse("people:duplicates"))
        self.assertContains(page, "0 pares por revisar")
        self.assertNotContains(page, self.preview_url())
        row = DuplicateDismissal.objects.get(person_a=self.keep, person_b=self.dup)
        self.assertEqual(row.dismissed_by, self.admin)
        # It stays dismissed: another look, another day, the same answer; and the merge is refused by hand too.
        self.assertEqual(duplicates.suggestions(self.church), [])
        response = self.confirm()
        self.assertContains(response, "Esta unión no se puede hacer")
        self.assertEqual(PersonMerge.objects.filter(absorbed=self.dup).count(), 0)
        self.client.post(reverse("people:duplicate_reopen", args=[row.pk]))
        self.assertContains(self.client.get(reverse("people:duplicates")), "1 par por revisar")
        self.assertEqual(ChurchAuditEvent.objects.count(), audited + 2)

    def test_preview_then_merge_then_undo(self):
        Person.objects.filter(pk=self.dup.pk).update(phone="55 9999 0000")
        groups.set_membership(self.other_group, self.dup, self.admin)
        self.row_for("resources.DevotionalRead.person", self.dup)
        response = self.client.get(self.preview_url())
        self.assertContains(response, "1 grupo en el que está o estuvo")
        self.assertContains(response, "Teléfono: ¿cuál se queda?")
        self.assertContains(response, "Nadie las ve ni las cuenta")
        self.assertNotContains(response, "devocional leído")
        self.assertNotContains(response, self.other_group.name)  # how many, never which
        self.assertEqual(PersonMerge.objects.filter(absorbed=self.dup).count(), 0)  # looking merges nothing

        # Without the choice, or with a value that is not one of the two records: nothing.
        for data in ({}, {"elige_phone": "55 0000 0000"}, {"elige_phone": self.leader.pk}):
            self.assertContains(self.confirm(**data), "¿cuál se queda?")
            self.assertEqual(PersonMerge.objects.filter(absorbed=self.dup).count(), 0)
        response = self.confirm(elige_phone=self.dup.pk)
        record = PersonMerge.objects.get(absorbed=self.dup)
        self.assertRedirects(response, reverse("people:merge_detail", args=[record.pk]))
        self.keep.refresh_from_db()
        self.assertEqual(self.keep.phone, "55 9999 0000")
        self.assertEqual(self.membership(self.other_group, self.keep).status, "activa")
        detail = self.client.get(reverse("people:merge_detail", args=[record.pk]))
        self.assertContains(detail, "1 grupo en el que está o estuvo")
        self.assertContains(detail, "Deshacer la unión")
        self.assertNotContains(detail, "55 3333 4444")  # the previous value is kept for the undo, not shown

        response = self.client.post(reverse("people:merge_undo", args=[record.pk]), follow=True)
        self.assertContains(response, "Regresó todo lo que la unión había pasado")
        self.keep.refresh_from_db()
        self.dup.refresh_from_db()
        self.assertEqual((self.keep.phone, self.dup.merged_into_id), ("55 3333 4444", None))
        self.assertEqual(self.membership(self.other_group, self.dup).status, "activa")
        self.assertContains(self.client.get(reverse("people:detail", args=[self.dup.pk])), "Luna Ríos")
        # Asked again: one undo, and the screen says so.
        response = self.client.post(reverse("people:merge_undo", args=[record.pk]), follow=True)
        self.assertContains(response, "ya se había deshecho")

    def test_a_refusal_is_explained_and_offers_no_button_to_merge(self):
        groups.set_membership(self.other_group, self.keep, self.admin)
        groups.set_membership(self.other_group, self.dup, self.admin)
        response = self.client.get(self.preview_url())
        self.assertContains(response, "Esta unión no se puede hacer")
        self.assertContains(response, "Las dos están en un mismo grupo")
        self.assertNotContains(response, "unirlas</button>")
        self.assertContains(response, "No son la misma persona")
        self.assertContains(self.confirm(), "Las dos están en un mismo grupo")  # posted by hand all the same
        self.assertEqual(PersonMerge.objects.filter(absorbed=self.dup).count(), 0)

    def test_the_second_administrator_is_told_and_merges_nothing(self):
        other = Client()
        other.force_login(self.admin)
        token = merge._token(self.keep, self.dup)
        self.assertEqual(self.client.post(self.preview_url(), {"revision": token}).status_code, 302)
        self.assertEqual(other.post(self.preview_url(), {"revision": token}).status_code, 404)  # the pair is not there any more
        self.assertEqual(other.post(self.preview_url(self.dup, self.keep), {"revision": token}).status_code, 404)
        self.assertEqual(PersonMerge.objects.filter(absorbed=self.dup).count(), 1)

    def test_a_stale_confirmation_shows_the_preview_again(self):
        token = merge._token(self.keep, self.dup)
        Person.objects.filter(pk=self.dup.pk).update(first_name="Lía Fernanda")
        response = self.client.post(self.preview_url(), {"revision": token})
        self.assertContains(response, "No unimos nada")
        self.assertContains(response, "Lía Fernanda")
        self.assertEqual(PersonMerge.objects.filter(absorbed=self.dup).count(), 0)

    def test_what_a_person_typed_is_escaped_on_every_screen(self):
        Person.objects.filter(pk=self.dup.pk).update(first_name="<script>alert(1)</script>", phone="<b>55 9999 0000</b>")
        Person.objects.get(pk=self.dup.pk).save()
        for url in (reverse("people:duplicates"), self.preview_url(), self.preview_url(self.dup, self.keep)):
            response = self.client.get(url)
            self.assertNotContains(response, "<script>alert(1)")
            self.assertContains(response, "&lt;script&gt;alert(1)")
            self.assertNotContains(response, "<b>55")
        record = PersonMerge.objects.get(pk=self.confirm(elige_phone=self.dup.pk, elige_name=self.dup.pk).url.rstrip("/").split("/")[-1])
        for url in (reverse("people:duplicates"), reverse("people:merge_detail", args=[record.pk])):
            response = self.client.get(url)
            self.assertNotContains(response, "<script>alert(1)")
            self.assertContains(response, "&lt;script&gt;alert(1)")

    def test_an_undo_after_the_window_is_refused_on_the_screen(self):
        from datetime import timedelta

        from django.utils import timezone

        self.confirm()
        record = PersonMerge.objects.get(absorbed=self.dup)
        PersonMerge.objects.filter(pk=record.pk).update(merged_at=timezone.now() - timedelta(days=merge.UNDO_DAYS + 1))
        detail = self.client.get(reverse("people:merge_detail", args=[record.pk]))
        self.assertContains(detail, "ya no se puede deshacer")
        self.assertNotContains(detail, reverse("people:merge_undo", args=[record.pk]))
        response = self.client.post(reverse("people:merge_undo", args=[record.pk]), follow=True)
        self.assertContains(response, "ya no se puede deshacer")
        self.dup.refresh_from_db()
        self.assertEqual(self.dup.merged_into_id, self.keep.pk)


class DetectionTests(MergeFixture):
    def pairs(self):
        return {(pair.a.pk, pair.b.pk): pair.signals for pair in duplicates.suggestions(self.church)}

    def make(self, **fields):
        fields.setdefault("first_name", "Rut")
        return Person.objects.create(church=self.church, **fields)

    def test_the_three_signals_and_nothing_fuzzier(self):
        self.assertEqual(self.pairs(), {(self.keep.pk, self.dup.pk): ["email"]})
        a = self.make(last_name="Peña", phone="+52 (55) 8888-7777", birth_date="2001-02-03")
        b = self.make(last_name="Pena", phone="55 8888 7777", birth_date="2001-02-03")  # same phone; same name without accents, same date
        c = self.make(last_name="Peña", birth_date="2001-02-04")  # same name, another date
        d = self.make(last_name="Peña")  # same name, no date
        e = self.make(last_name="Peñas", birth_date="2001-02-03")  # a similar name is not the same name
        f = self.make(first_name="Sara", phone="8888 7777")  # another phone (fewer digits)
        found = self.pairs()
        self.assertEqual(found[(a.pk, b.pk)], ["phone", "name_birth"])
        for person in (c, d, e, f):
            self.assertFalse([pair for pair in found if person.pk in pair], person)
        self.assertEqual(len(found), 2)

    def test_empty_values_short_phones_and_values_shared_by_many_say_nothing(self):
        for _ in range(3):
            self.make(email="", phone="", last_name="")
            self.make(phone="123")
        self.assertEqual(len(self.pairs()), 1)
        office = [self.make(first_name=f"Persona {n}", phone="55 5000 6000") for n in range(duplicates.MAX_SHARED)]
        self.assertEqual(len(self.pairs()), 1 + duplicates.MAX_SHARED * (duplicates.MAX_SHARED - 1) // 2)
        self.make(first_name="Una más", phone="55 5000 6000")
        self.assertEqual(len(self.pairs()), 1)  # the church's own phone on everybody: not a signal
        self.assertTrue(office)

    def test_merged_dismissed_and_other_churches_are_never_suggested(self):
        Person.objects.create(church=self.church_b, first_name="Lía", last_name="Luna", email="lia@prueba.mx")
        Person.objects.create(church=self.church, first_name="Vieja", email="lia@prueba.mx", merged_into=self.keep)
        self.assertEqual(list(self.pairs()), [(self.keep.pk, self.dup.pk)])
        merge.dismiss(self.church, self.keep.pk, self.dup.pk, self.admin)
        self.assertEqual(self.pairs(), {})
        self.assertEqual(duplicates.suggestions(self.church_b), [])

    def test_the_review_is_three_queries_whatever_the_size(self):
        for n in range(30):
            self.make(first_name=f"Persona {n}", email=f"persona{n // 2}@prueba.mx")
        with self.assertNumQueries(3):
            self.assertEqual(len(duplicates.suggestions(self.church)), 16)
