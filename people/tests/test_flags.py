from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import Http404, HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from core.models import Church, Role
from core.services import assign_role
from events.tests.base import make_user
from people import flags
from people.decorators import module_required
from people.models import ChurchAuditEvent, ChurchSettings, Person


class FlagTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.church = Church.objects.create(name="Iglesia", slug="iglesia")
        cls.admin = make_user("admin@prueba.mx", "Ada", "Admin")
        assign_role(cls.admin, cls.church, Role.CHURCH_ADMIN)

    def test_off_without_a_row_and_reading_creates_none(self):
        self.assertFalse(flags.enabled(self.church, "community"))
        self.assertFalse(flags.enabled(None, "community"))
        self.assertFalse(ChurchSettings.objects.exists())
        self.assertFalse(ChurchSettings(church=self.church).community_enabled)  # not a truthy DatabaseDefault

    def test_on_per_module_and_per_church(self):
        other = Church.objects.create(name="Otra", slug="otra")
        self.assertTrue(flags.set_enabled(self.church, "community", True))
        self.assertFalse(flags.set_enabled(self.church, "community", True))
        self.assertTrue(flags.enabled(self.church, "community"))
        self.assertFalse(flags.enabled(self.church, "people"))
        self.assertFalse(flags.enabled(other, "community"))

    def test_kill_switch_reads_everything_off_and_keeps_the_rows(self):
        flags.set_enabled(self.church, "community", True)
        with override_settings(EMMAUS_V2_MODULES=False):
            self.assertFalse(flags.enabled(self.church, "community"))
        self.assertTrue(ChurchSettings.objects.get(church=self.church).community_enabled)

    def test_unknown_module_raises(self):
        for call in (lambda: flags.enabled(self.church, "comunidad"), lambda: flags.set_enabled(self.church, "x", True),
                     lambda: module_required("groups")):
            with self.assertRaises(ValueError):
                call()

    def test_decorator_is_404_when_off_and_runs_the_view_when_on(self):
        view = module_required("community")(lambda request: HttpResponse(request.church.slug))
        request = RequestFactory().get("/")
        request.user, request.session = self.admin, {}
        with self.assertRaises(Http404):
            view(request)
        flags.set_enabled(self.church, "community", True)
        self.assertEqual(view(request).content, b"iglesia")
        with override_settings(EMMAUS_V2_MODULES=False), self.assertRaises(Http404):
            view(request)

    def test_command_switches_audits_and_backfills_role_holders_only(self):
        make_user("sin-rol@prueba.mx")
        out = StringIO()
        call_command("set_church_module", "iglesia", "community", "on", stdout=out)
        self.assertTrue(flags.enabled(self.church, "community"))
        self.assertEqual(list(Person.objects.values_list("user__email", flat=True)), ["admin@prueba.mx"])
        self.assertEqual(ChurchAuditEvent.objects.get().action, "modulo_cambiado")
        call_command("set_church_module", "iglesia", "community", "on", stdout=out)  # again: nothing new
        self.assertEqual((Person.objects.count(), ChurchAuditEvent.objects.count()), (1, 1))
        call_command("set_church_module", "iglesia", "community", "off", stdout=out)
        self.assertFalse(flags.enabled(self.church, "community"))
        self.assertEqual(Person.objects.count(), 1)  # turning a module off deletes nothing
        with self.assertRaises(CommandError):
            call_command("set_church_module", "no-existe", "community", "on", stdout=out)
