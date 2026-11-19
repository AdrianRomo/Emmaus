"""``seed_community_demo --dos-iglesias``: a second synthetic church and one account of both (wave 10).

The plain seed is what it was: the flag adds, and running it again adds nothing.
"""

from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from core.authz import churches_of, has_scope
from core.forms import ChurchSettingsForm
from core.models import Church, Role, UserChurchRole
from groups.management.commands.seed_community_demo import CHURCH_SLUG, SECOND_ADMIN, SECOND_CHURCH_SLUG, TWO_CHURCHES
from groups.models import Group, GroupMembership
from people import flags
from people.models import Person

PASSWORD = "clave-sintetica-de-prueba-37"


def seed(*flags_):
    out = StringIO()
    with override_settings(DEBUG=True):
        call_command("seed_community_demo", *flags_, password=PASSWORD, stdout=out)
    return out.getvalue()


def counts():
    return (Church.objects.count(), User.objects.count(), Person.objects.count(), UserChurchRole.objects.count(),
            Group.objects.count(), GroupMembership.objects.count())


class TwoChurchSeedTests(TestCase):
    def test_the_plain_seed_has_one_church_and_no_such_account(self):
        out = seed()
        self.assertEqual(Church.objects.count(), 1)
        self.assertFalse(User.objects.filter(username__in=[TWO_CHURCHES[0], SECOND_ADMIN[0]]).exists())
        self.assertNotIn(TWO_CHURCHES[0], out)

    def test_the_flag_adds_the_church_and_the_account_and_a_second_run_adds_nothing(self):
        seed()
        before = counts()
        out = seed("--dos-iglesias")
        self.assertIn(TWO_CHURCHES[0], out)
        after = counts()
        self.assertEqual(after[0], before[0] + 1)
        seed("--dos-iglesias")
        seed()  # and the plain seed after it changes nothing either
        self.assertEqual(counts(), after)

        first, second = Church.objects.get(slug=CHURCH_SLUG), Church.objects.get(slug=SECOND_CHURCH_SLUG)
        self.assertTrue(flags.enabled(second, "community"))
        self.assertTrue(second.custom_theme_enabled)
        self.assertNotEqual(second.theme_primary_color, first.theme_primary_color)
        both = User.objects.get(username=TWO_CHURCHES[0])
        self.assertTrue(both.email.endswith("@demo.invalid"))
        self.assertEqual(churches_of(both), [first, second])  # it starts in the first one
        self.assertEqual(
            dict(UserChurchRole.objects.filter(user=both).values_list("church__slug", "role")),
            {CHURCH_SLUG: Role.CHURCH_ADMIN, SECOND_CHURCH_SLUG: Role.MENTOR},
        )
        self.assertTrue(has_scope(both, first, "church", first.pk, "manage"))
        self.assertFalse(has_scope(both, second, "church", second.pk, "manage"))
        guided = GroupMembership.objects.get(person__user=both, person__church=second)
        self.assertEqual(guided.role, GroupMembership.Role.LEADER)
        self.assertFalse(Person.objects.filter(user=both).exclude(church__in=[first, second]).exists())

    def test_the_second_churchs_colours_pass_the_settings_form(self):
        seed("--dos-iglesias")
        second = Church.objects.get(slug=SECOND_CHURCH_SLUG)
        self.client.force_login(User.objects.get(username=SECOND_ADMIN[0]))
        form = self.client.get(reverse("church_settings")).context["form"]
        self.assertEqual(form.instance, second)
        data = {name: value for name, value in form.initial.items() if value not in (None, False) and not hasattr(value, "name")}
        bound = ChurchSettingsForm(data, instance=second)
        self.assertTrue(bound.is_valid(), bound.errors)

    def test_the_account_switches_and_each_church_shows_its_own(self):
        seed("--dos-iglesias")
        second = Church.objects.get(slug=SECOND_CHURCH_SLUG)
        self.assertTrue(self.client.login(username=TWO_CHURCHES[0], password=PASSWORD))
        self.assertEqual(self.client.get(reverse("church_settings")).status_code, 200)
        home = self.client.get(reverse("community:home"))
        self.assertContains(home, f"Cambiar a {second.name}")
        self.assertEqual(self.client.post(reverse("switch_church"), {"church": second.pk}).status_code, 302)
        self.assertEqual(self.client.get(reverse("church_settings")).status_code, 403)  # a mentor here
        self.assertEqual(self.client.get(reverse("mentor_dashboard")).status_code, 200)
        home = self.client.get(reverse("community:home"))
        self.assertContains(home, "Jóvenes del Norte")
        self.assertContains(home, f"--petroleo:{second.theme_primary_color}")
        self.assertNotContains(home, "Jóvenes Roca")

    def test_the_seed_leaves_no_tenant_mismatch(self):
        seed("--dos-iglesias")
        out = StringIO()
        call_command("tenant_integrity", stdout=out)
        call_command("rebuild_role_assignments", "--check", stdout=out)
        self.assertIn("Sin diferencias", out.getvalue())
