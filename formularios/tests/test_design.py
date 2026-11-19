"""What the restyle of wave 11 relies on (docs/v2/wave11/formularios.md): the two actions that act in place keep
working as ordinary posts, «Pendientes» stays read-only, and the screens keep to the design system's marks."""

from pathlib import Path

from django.conf import settings

from formularios import services
from formularios.models import FormTask

from .base import PUBLIC, FormFixture, url

TEMPLATES = Path(settings.BASE_DIR) / "templates" / "formularios"


class InPlaceTests(FormFixture):
    def test_marking_a_task_done_is_one_region_and_the_same_post(self):
        """«Marcar como hecho» changes the region ``seguimiento`` (emmaus.js, "Actions in place"): the form names it,
        the POST is the usual one and comes back to the answer's page; the note, which has a field, does not."""
        form = self.public_form(creates_task=True, task_due_days=3)
        submission, _ = self.answer(form)
        address = url("submission", form.pk, submission.pk)
        self.client.force_login(self.admin)
        page = self.client.get(address).content.decode()
        self.assertIn('id="seguimiento"', page)
        done = page[page.index('data-inline="seguimiento"'):]
        self.assertIn('name="accion" value="hecho"', done[:done.index("</form>")])
        self.assertEqual(page.count("data-inline="), 1)  # the note, accepting and deleting are never in place
        response = self.client.post(url("task", form.pk, submission.pk), {"accion": "hecho"}, headers={"X-Emmaus-Inline": "1"})
        self.assertRedirects(response, address)
        self.assertEqual(FormTask.objects.get(submission=submission).status, FormTask.Status.DONE)
        self.assertContains(self.client.get(address), "Abrir otra vez")

    def test_moving_a_question_returns_to_the_list_and_names_the_one_that_moved(self):
        form = self.draft(audience=PUBLIC)
        first, _ = services.save_question(form, self.admin, kind="corto", label="Primera")
        second, _ = services.save_question(form, self.admin, kind="corto", label="Segunda")
        self.client.force_login(self.admin)
        response = self.client.post(url("question_move", form.pk, first.pk), {"hacia": "abajo"})
        self.assertRedirects(response, url("detail", form.pk) + f"?movida={first.pk}#preguntas", fetch_redirect_response=False)
        page = self.client.get(url("detail", form.pk) + f"?movida={first.pk}").content.decode()
        self.assertLess(page.index("Segunda</a>"), page.index("Primera</a>"))
        self.assertEqual(page.count("data-inline-focus"), 1)
        focused = page[:page.index("data-inline-focus")].rsplit("<a ", 1)[1]
        self.assertIn(url("question_edit", form.pk, first.pk), focused)
        # Removing a question is on its own page, never beside «Subir» and «Bajar»
        self.assertNotIn(url("question_delete", form.pk, first.pk), page)
        self.assertContains(self.client.get(url("question_edit", form.pk, first.pk)), url("question_delete", form.pk, first.pk))

    def test_pendientes_has_no_action_of_its_own(self):
        form = self.public_form(creates_task=True, task_due_days=3)
        submission, _ = self.answer(form)
        services.assign_task(FormTask.objects.get(submission=submission), self.admin, self.admin, None)
        self.client.force_login(self.admin)
        page = self.client.get(url("queue")).content.decode()
        main = page[page.index("<main"):page.index("</main>")]
        self.assertIn(url("submission", form.pk, submission.pk), main)
        self.assertIn('class="row row--lead', main)
        for mark in ("<form", "<button", "data-inline"):
            self.assertNotIn(mark, main)


class DesignMarksTests(FormFixture):
    def test_no_template_of_the_area_keeps_an_old_mark(self):
        old = ("step__actions", "notice--quiet", " lede=", 'class="lede"', "btn--small", 'class="more"', "forms__bulk", "choice__input")
        for template in sorted(TEMPLATES.glob("*.html")):
            text = template.read_text(encoding="utf-8")
            for mark in old:
                self.assertNotIn(mark, text, f"{template.name}: {mark}")

    def test_the_public_form_keeps_its_seal_and_its_trap(self):
        form = self.public_form()
        page = self.client.get(f"/formulario/{form.public_token}/").content.decode()
        self.assertIn('class="form-trap" aria-hidden="true"', page)
        self.assertIn('name="sello" value="', page)
        self.assertEqual(page.count("btn--primary"), 1)
        self.assertNotIn("data-inline", page)
