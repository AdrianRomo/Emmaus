"""Lists that do not fit one screen (core/paging.py, partials/ui/pager.html).

Every list of the community side is paged by fifty: it shows the first fifty, a link to the next page that works,
which rows are on the screen and how many there are in all, and each link keeps the filters of the address. A short
list under a search box says how many names matched. The administration's follow-ups are in care/tests/test_care.py.
"""

import re
from datetime import timedelta

from django.template import Context, Template
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.paging import PAGE_SIZE, first_rows, paginate
from groups.models import GroupMeeting
from groups.tests.base import CommunityFixture
from notices.models import Announcement
from people.models import Person
from resources.models import Devotional, MediaItem

PAGER = re.compile(r'<nav class="pager" aria-label="([^"]+)">(.*?)</nav>', re.DOTALL)


def pagers(response) -> list[tuple[str, str]]:
    return PAGER.findall(response.content.decode())


class PagerPartialTests(CommunityFixture):
    def render(self, address: str, total: int, **extra) -> str:
        request = RequestFactory().get(address)
        page = paginate(request, list(range(total)), param=extra.get("param", "pagina"))
        template = Template('{% include "partials/ui/pager.html" %}')
        return template.render(Context({"request": request, "page": page, **extra}))

    def test_one_page_draws_nothing(self):
        self.assertEqual(self.render("/x/", PAGE_SIZE).strip(), "")

    def test_the_first_page_names_itself_and_leads_only_forward(self):
        html = self.render("/x/?q=ana+mar%C3%ADa&estado=a%26b", 120)
        self.assertIn('<nav class="pager" aria-label="Páginas de la lista">', html)
        self.assertIn('<p class="pager__where" aria-current="page">Página 1 de 3. Del 1 al 50 de 120.</p>', html)
        self.assertIn('rel="next" href="?q=ana+mar%C3%ADa&amp;estado=a%26b&amp;pagina=2">Siguiente</a>', html)
        self.assertNotIn('rel="prev"', html)

    def test_a_middle_page_leads_both_ways_and_the_way_back_to_the_first_carries_no_number(self):
        html = self.render("/x/?pagina=2&q=ana", 120, label="Páginas de personas")
        self.assertIn('aria-label="Páginas de personas"', html)
        self.assertIn("Página 2 de 3. Del 51 al 100 de 120.", html)
        self.assertIn('rel="prev" href="?q=ana">Anterior</a>', html)
        self.assertIn('rel="next" href="?q=ana&amp;pagina=3">Siguiente</a>', html)

    def test_a_page_that_does_not_exist_gives_the_nearest_one(self):
        self.assertIn("Página 3 de 3. Del 101 al 120 de 120.", self.render("/x/?pagina=99", 120))
        self.assertIn("Página 1 de 3.", self.render("/x/?pagina=abc", 120))

    def test_a_second_list_keeps_the_page_of_the_first(self):
        html = self.render("/x/?pagina=2", 120, param="publicados")
        self.assertIn('rel="next" href="?pagina=2&amp;publicados=2"', html)

    def test_first_rows_counts_only_when_it_cuts(self):
        people = Person.objects.filter(church=self.church).order_by("id")
        total = people.count()
        everybody = list(people)
        with self.assertNumQueries(1):
            self.assertEqual(first_rows(people.all(), total), (everybody, total))
        with self.assertNumQueries(2):
            found, count = first_rows(people.all(), 2)
        self.assertEqual((len(found), count), (2, total))


class PagedListsTests(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.today = timezone.localdate()
        # 60 people called Zoe and one Zulema: a search has to reach past the first page
        Person.objects.bulk_create(
            [Person(church=cls.church, first_name="Zoe", last_name=f"Prueba {n:02d}", normalized_name=f"zoe prueba {n:02d}") for n in range(60)]
        )
        Person.objects.create(church=cls.church, first_name="Zulema", last_name="Última")

    def assert_paged(self, url: str, key: str, total: int, *, keeps: str = "", label: str = ""):
        """The first fifty of ``total``, said so, with a next link that keeps ``keeps`` and answers with the rest."""
        self.client.force_login(self.admin)
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.context[key]), PAGE_SIZE)
        (found_label, nav), = pagers(first)
        if label:
            self.assertEqual(found_label, label)
        self.assertIn(f"Página 1 de 2. Del 1 al 50 de {total}.", nav)
        href = re.search(r'rel="next" href="([^"]+)"', nav).group(1).replace("&amp;", "&")
        self.assertEqual(href, f"?{keeps}&pagina=2" if keeps else "?pagina=2")
        second = self.client.get(url.split("?")[0] + href)
        self.assertEqual(len(second.context[key]), total - PAGE_SIZE)
        (_, nav), = pagers(second)
        self.assertIn(f"Página 2 de 2. Del 51 al {total} de {total}.", nav)
        self.assertIn(f'rel="prev" href="?{keeps}"', nav)
        self.assertNotIn('rel="next"', nav)
        first_ids = {row.pk for row in first.context[key]}
        self.assertFalse(first_ids & {row.pk for row in second.context[key]})
        return first, second

    def test_people_and_the_search_across_pages(self):
        total = Person.objects.filter(church=self.church, merged_into__isnull=True).count()
        self.client.force_login(self.admin)
        first = self.client.get(reverse("people:list"))
        self.assertEqual(len(first.context["page"]), PAGE_SIZE)
        self.assertContains(first, f"{total} personas")
        self.assertNotContains(first, "Zulema Última")  # she is on the second page
        # The search looks in the whole church, not in the page on the screen
        self.assertContains(self.client.get(reverse("people:list") + "?q=zulema"), "Zulema Última")
        _, second = self.assert_paged(reverse("people:list") + "?q=zoe", "page", 60, keeps="q=zoe", label="Páginas de personas")
        self.assertContains(second, "60 resultados")
        self.assertContains(second, "Zoe Prueba 59")
        self.assertNotContains(second, "Zulema")

    def test_notices_for_the_person_and_the_ones_they_published_page_apart(self):
        Announcement.objects.bulk_create(
            [Announcement(church=self.church, title=f"Aviso {n:02d}", body="Texto", created_by=self.admin) for n in range(51)]
        )
        self.client.force_login(self.admin)
        page = self.client.get(reverse("notices:list"))
        self.assertEqual(len(page.context["notices"]), PAGE_SIZE)
        self.assertEqual(len(page.context["published"]), PAGE_SIZE)
        (mine, nav), (published, nav_published) = pagers(page)
        self.assertEqual((mine, published), ("Páginas de avisos para ti", "Páginas de avisos que publicaste"))
        self.assertIn("Página 1 de 2. Del 1 al 50 de 51.", nav)
        self.assertIn('rel="next" href="?pagina=2"', nav)
        self.assertIn('rel="next" href="?publicados=2"', nav_published)
        page = self.client.get(reverse("notices:list") + "?publicados=2")
        self.assertEqual((len(page.context["notices"]), len(page.context["published"])), (PAGE_SIZE, 1))
        (_, nav), (_, nav_published) = pagers(page)
        self.assertIn('rel="next" href="?publicados=2&amp;pagina=2"', nav)  # each list keeps the other's page
        self.assertIn('rel="prev" href="?"', nav_published)
        # Somebody who cannot write has one list and one pager
        self.client.force_login(self.member_user)
        page = self.client.get(reverse("notices:list") + "?pagina=2")
        self.assertEqual(len(page.context["notices"]), 1)
        self.assertEqual(len(pagers(page)), 1)

    def test_devotionals_to_read_and_to_administer(self):
        Devotional.objects.bulk_create([
            Devotional(church=self.church, date=self.today - timedelta(days=n), title=f"Día {n:02d}", body="Texto",
                       status=Devotional.Status.PUBLISHED)
            for n in range(55)
        ])
        self.assert_paged(reverse("resources:devotional_list"), "devotionals", 55, label="Páginas de devocionales")
        Devotional.objects.create(church=self.church, date=self.today + timedelta(days=3), title="Borrador", body="Texto")
        self.assert_paged(reverse("resources:devotional_admin"), "devotionals", 56)

    def test_resources_keep_the_kind_and_the_series(self):
        MediaItem.objects.bulk_create([
            MediaItem(church=self.church, kind=MediaItem.Kind.SERMON, title=f"Predicación {n:02d}", series="Romanos",
                      url=f"https://ejemplo.invalid/p/{n}")
            for n in range(52)
        ])
        MediaItem.objects.bulk_create([
            MediaItem(church=self.church, kind=MediaItem.Kind.MUSIC, title=f"Canto {n:02d}", url=f"https://ejemplo.invalid/c/{n}")
            for n in range(3)
        ])
        listing = reverse("resources:media_list")
        first, _ = self.assert_paged(listing, "items", 55, label="Páginas de recursos")
        self.assertContains(first, 'Todos <span class="count">55</span>')
        self.assert_paged(listing + "?tipo=predicacion", "items", 52, keeps="tipo=predicacion")
        self.assert_paged(listing + "?serie=Romanos", "items", 52, keeps="serie=Romanos")
        self.assertEqual(pagers(self.client.get(listing + "?tipo=musica")), [])
        self.assert_paged(reverse("resources:media_admin"), "items", 55)

    def test_the_meetings_of_a_group(self):
        GroupMeeting.objects.bulk_create(
            [GroupMeeting(group=self.group, date=self.today - timedelta(days=7 * n), expected=4, present_kept=3) for n in range(51)]
        )
        self.assert_paged(reverse("community:group_meetings", args=[self.group.pk]), "meetings", 51, label="Páginas de reuniones")

    def test_names_under_a_search_box_say_how_many_matched(self):
        self.client.force_login(self.admin)
        said = "Estas son las primeras {} de 60 personas con ese nombre."
        for url, shown in (
            (reverse("community:group_people", args=[self.group.pk]) + "?q=zoe", 10),
            (reverse("care:create") + "?q=zoe", 10),
        ):
            page = self.client.get(url)
            self.assertEqual(len(page.context["found"]), shown, url)
            self.assertContains(page, said.format(shown))
            self.assertNotContains(self.client.get(url.replace("zoe", "zulema")), "Estas son las primeras")

    def test_a_persons_own_attendance_says_it_is_the_last_eight(self):
        from groups import services

        services.set_membership(self.group, self.member, self.admin)
        GroupMeeting.objects.bulk_create(
            [GroupMeeting(group=self.group, date=self.today + timedelta(days=n), expected=2) for n in range(10)]
        )
        self.client.force_login(self.member_user)
        page = self.client.get(reverse("community:group_detail", args=[self.group.pk]))
        self.assertContains(page, "Las últimas 8 de 10 reuniones con lista desde que eres parte del grupo.")
        GroupMeeting.objects.filter(group=self.group, date__gt=self.today + timedelta(days=7)).delete()
        page = self.client.get(reverse("community:group_detail", args=[self.group.pk]))
        self.assertNotContains(page, "Las últimas 8 de")
