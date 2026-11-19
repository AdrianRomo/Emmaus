"""``seed_community_demo --formularios``: the synthetic forms the browser looks at.

The seed goes through the same services as the screens, so a rule added to ``services.submit`` can refuse what the
seed sends and abort the whole command (it did: a known minor answering a form of the church). This runs it."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from formularios import services
from formularios.models import Form, FormTask, FormVersion, Question, Submission

PASSWORD = "clave-sintetica-de-prueba-37"


def seed(*flags):
    with override_settings(DEBUG=True):
        call_command("seed_community_demo", *flags, password=PASSWORD, stdout=StringIO())


def counts():
    return tuple(model.objects.count() for model in (Form, FormVersion, Question, Submission, FormTask))


class FormSeedTests(TestCase):
    def test_the_plain_seed_makes_no_form(self):
        seed()
        self.assertEqual(counts(), (0, 0, 0, 0, 0))

    def test_the_flag_seeds_two_published_forms_and_a_second_run_adds_nothing(self):
        seed("--formularios")
        self.assertEqual(counts(), (2, 2, 7, 5, 5))
        self.assertEqual(set(Form.objects.values_list("audience", "status")), {("iglesia", "publicado"), ("publico", "publicado")})
        for form in Form.objects.all():
            self.assertTrue(services.is_open(form), form.title)
        answers = Submission.objects.select_related("person")
        self.assertFalse(answers.filter(held=True).exists())
        self.assertTrue(all(answer.delete_after for answer in answers))
        for answer in answers.filter(person__isnull=False):  # the seed does nothing the screens refuse
            self.assertFalse(services.is_known_minor(answer.person), answer.person.first_name)
        contacts = answers.filter(person__isnull=True).values_list("contact_email", flat=True)
        self.assertTrue(all(email == "" or email.endswith("@demo.invalid") for email in contacts))
        before = counts()
        seed("--formularios")
        seed("--formularios", "--eventos", "--panorama")  # with the other flags of the browser gate
        self.assertEqual(counts(), before)
