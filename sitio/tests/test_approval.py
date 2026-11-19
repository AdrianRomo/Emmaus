"""Issue #99 for the public home: the administration asks with the review, a pastor of the church reads
the same list and approves, and only that makes the page public."""

import importlib

from django.apps import apps as django_apps
from django.urls import reverse

from resources import approvals
from resources.models import ContentApproval
from resources.tests.approving import church_pastor
from sitio import services
from sitio.models import PublicHome
from sitio.tests.test_public_home import PHONE_A, PHONE_B, HomeFixture, home_url

QUEUE = reverse("resources:approval_queue")


class HomeApprovalTests(HomeFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.home = PublicHome.objects.get(church=cls.church)
        cls.home_b = PublicHome.objects.get(church=cls.church_b)
        cls.pastor_a, cls.pastor_b = church_pastor(cls.church), church_pastor(cls.church_b)

    def address(self, name, home=None):
        return reverse(f"resources:approval_{name}", args=["sitio", (home or self.home).pk])

    def ask(self):
        """A new welcome takes the page out; the administration asks again."""
        services.save_home(self.church, self.admin, welcome="Bienvenida <b>nueva</b>.", address=self.home.address,
                           map_url=self.home.map_url, contact_phone=PHONE_A)
        services.request_publication(self.church, self.admin, services.page_content(self.church).digest())
        self.home.refresh_from_db()

    def test_the_pastor_reads_the_whole_list_and_approving_makes_the_page_public(self):
        self.ask()
        self.assertEqual(self.client.get(home_url(self.church)).status_code, 404)
        self.client.force_login(self.pastor_a)
        self.assertContains(self.client.get(QUEUE), "Página pública de la iglesia")
        page = self.client.get(self.address("review"))
        self.assertContains(page, "<h1", count=1)
        for text in ("Bienvenida &lt;b&gt;nueva&lt;/b&gt;.", "Calle de la Paz 12", PHONE_A, "Domingos, 09:00: Primera reunión, en Templo principal.",
                     "Retiro de jóvenes", "La vid y las ramas", "Apruebas la página como está en esta lista."):
            self.assertContains(page, text)
        for text in ("<b>nueva</b>", "Sermón solo para la iglesia", "Culto sabatino B", PHONE_B, "Aviso interno reservado", "<h2 id=\"rev-"):
            self.assertNotContains(page, text)
        seen = page.context["revision"]
        # What would be public changed after the preview (a resource was marked): nothing is approved.
        services.show_media(self.private_sermon, self.admin)
        response = self.client.post(self.address("approve"), {"revision": seen, "permitted_use": "propia"}, follow=True)
        self.assertContains(response, "El contenido cambió mientras lo revisabas. No se aprobó nada")
        self.assertEqual(self.client.get(home_url(self.church)).status_code, 404)
        seen = response.context["revision"]
        response = self.client.post(self.address("approve"), {"revision": seen, "permitted_use": "propia"}, follow=True)
        self.assertRedirects(response, QUEUE)
        self.assertContains(response, "Aprobaste «Página pública de la iglesia». Cualquier persona con el enlace puede leerla.")
        self.home.refresh_from_db()
        row = ContentApproval.objects.get(kind="sitio", object_id=self.home.pk)
        self.assertEqual((self.home.published, self.home.published_by, row.state, row.approved_by, row.church),
                         (True, self.pastor_a, "aprobado", self.pastor_a, self.church))
        self.assertEqual(row.approved_digest, services.authored_digest(self.church, self.home))
        self.assertContains(self.client.get(home_url(self.church)), "Bienvenida &lt;b&gt;nueva&lt;/b&gt;.")

    def test_the_administration_and_another_churchs_pastor_cannot_make_it_public(self):
        self.ask()
        data = {"revision": services.page_content(self.church).digest(), "permitted_use": "propia", "reason": "x"}
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.address("review")), "Solo un pastor de tu iglesia puede aprobarlo.")
        self.assertContains(self.client.get(reverse("sitio:edit")), "la administración no puede publicarlo por su cuenta")
        for name in ("approve", "return"):
            self.assertEqual(self.client.post(self.address(name), data).status_code, 403)
        self.client.force_login(self.pastor_b)
        self.assertContains(self.client.get(QUEUE), "No hay nada esperando tu aprobación.")  # nothing of church A waits for them
        self.assertEqual(self.client.get(self.address("review")).status_code, 404)
        for name in ("approve", "return"):
            self.assertEqual(self.client.post(self.address(name), data).status_code, 404)
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(self.address("review")).status_code, 403)
        self.home.refresh_from_db()
        self.assertFalse(self.home.published)
        self.assertEqual(self.client.get(home_url(self.church)).status_code, 404)
        self.assertEqual(self.client.get(home_url(self.church_b)).status_code, 200)
        self.assertEqual(ContentApproval.objects.get(kind="sitio", object_id=self.home.pk).state, "pendiente")

    def test_a_pastor_returns_it_and_the_administration_reads_why(self):
        self.ask()
        self.client.force_login(self.pastor_a)
        response = self.client.post(self.address("return"), {"reason": "La dirección <i>no</i> es esa."}, follow=True)
        self.assertRedirects(response, QUEUE)
        self.client.force_login(self.admin)
        for address in (reverse("sitio:edit"), reverse("sitio:publish")):
            page = self.client.get(address)
            self.assertContains(page, "La dirección &lt;i&gt;no&lt;/i&gt; es esa.")
            self.assertNotContains(page, "<i>no</i>")
        self.assertEqual(self.client.get(home_url(self.church)).status_code, 404)

    def test_the_migration_marks_a_page_that_was_public_and_guesses_nobody(self):
        migration = importlib.import_module("sitio.migrations.0002_mark_published_before_approvals")
        ContentApproval.objects.filter(kind="sitio").delete()  # as before approvals existed: public, with no row
        PublicHome.objects.filter(pk=self.home_b.pk).update(published=False)
        for _ in range(2):
            migration.mark_published_before(django_apps, None)
        mark = ContentApproval.objects.get(kind="sitio")
        self.assertEqual((mark.church, mark.object_id, mark.state, mark.approved_by, mark.approved_at), (self.church, self.home.pk, "anterior", None, None))
        self.assertEqual(self.client.get(home_url(self.church)).status_code, 200)  # still public
        self.client.force_login(self.pastor_a)
        page = self.client.get(QUEUE)
        self.assertContains(page, "Se publicó antes de que existieran las aprobaciones.")
        self.assertEqual([entry.obj for entry in approvals.unrecorded(self.church)], [self.home])
        self.assertEqual(approvals.unrecorded(self.church_b), [])
        migration.unmark(django_apps, None)
        self.assertFalse(ContentApproval.objects.filter(kind="sitio").exists())
