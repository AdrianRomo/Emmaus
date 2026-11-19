"""After the integration of wave 9: a booking's requester in a merge, a member's own save racing a
merge, and the seed's pair for "Posibles duplicados"."""

from datetime import date
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from agenda import services as agenda
from core.models import Church
from espacios import access as espacios_access
from events.tests.base import make_user
from people import duplicates, merge
from people.models import Person

from .merge_base import MergeFixture


class BookingTests(MergeFixture):
    def test_the_survivor_keeps_the_requests_and_what_asking_gives_and_the_undo_returns_them(self):
        booking = self.row_for("espacios.Booking.requested_by", self.dup)
        words = (booking.title, booking.status, booking.decided_by_id, booking.starts_at)
        preview = merge.build_preview(self.church, self.keep, self.dup)
        self.assertEqual(preview.move_lines, ["1 reserva de un espacio que pidió"])  # how many, never for what
        record = self.do_merge()
        booking.refresh_from_db()
        self.assertEqual(booking.requested_by_id, self.keep.pk)
        self.assertEqual((booking.title, booking.status, booking.decided_by_id, booking.starts_at), words)
        self.assertTrue(espacios_access.can_read_details(booking, self.keep, False))
        self.assertFalse(espacios_access.can_read_details(booking, Person.objects.get(pk=self.dup.pk), False))
        self.assertNotIn("Ensayo", str(record.manifest))
        merge.undo(self.church, record.pk, self.admin)
        booking.refresh_from_db()
        self.assertEqual(booking.requested_by_id, self.dup.pk)


class OwnSaveRacesAMergeTests(MergeFixture):
    """The member's screen read their record, the administration merged it away, the member saves."""

    def setUp(self):
        self.account = make_user("lia@prueba.mx", "Lía", "Luna")
        Person.objects.filter(pk=self.dup.pk).update(user=self.account)
        self.stale = Person.objects.get(pk=self.dup.pk)
        self.do_merge()

    def test_mis_datos_says_so_and_writes_nothing(self):
        from groups import views

        form_class = views.MyDataForm
        self.client.force_login(self.account)
        # The form is built on the copy read before the merge, as a request already in flight would have it.
        with mock.patch.object(views, "MyDataForm", lambda data=None, instance=None: form_class(data, instance=self.stale)):
            response = self.client.post(reverse("community:profile"),
                                        {"phone": "55 7777 8888", "email": "lia@prueba.mx", "preferred_contact_method": "email"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No pudimos guardar tus datos porque tu registro cambió")
        self.dup.refresh_from_db()
        self.keep.refresh_from_db()
        self.assertEqual((self.dup.merged_into_id, self.dup.user_id, self.dup.phone), (self.keep.pk, None, ""))
        self.assertEqual((self.keep.user_id, self.keep.phone), (self.account.pk, "55 3333 4444"))

    def test_a_date_of_birth_typed_at_sign_up_is_refused_as_an_agenda_error(self):
        with self.assertRaises(agenda.AgendaError) as caught:
            agenda.set_birth_date(self.stale, date(1990, 1, 1), self.account)
        self.assertEqual(caught.exception.code, "merged")
        self.dup.refresh_from_db()
        self.assertEqual((self.dup.birth_date, self.dup.merged_into_id), (None, self.keep.pk))
        # The survivor, which the next request reads, saves as always.
        agenda.set_birth_date(Person.objects.get(pk=self.keep.pk), date(1990, 1, 1), self.account)
        self.assertEqual(Person.objects.get(pk=self.keep.pk).birth_date, date(1990, 1, 1))


class SeedTests(TestCase):
    def test_the_plain_seed_suggests_nobody_and_the_flag_adds_exactly_one_pair(self):
        with override_settings(DEBUG=True):
            call_command("seed_camp_demo", "--participants", "20", "--hours", "30", password="x", stdout=StringIO())
            for options in ((), ("--panorama", "--eventos")):
                call_command("seed_community_demo", *options, password="x", stdout=StringIO())
                church = Church.objects.filter(people__user__email="admin.comunidad@demo.invalid").distinct().get()
                self.assertEqual(duplicates.suggestions(church), [], options)
            for _ in range(2):  # twice: the same pair, not two
                call_command("seed_community_demo", "--duplicados", password="x", stdout=StringIO())
        pairs = duplicates.suggestions(church)
        self.assertEqual([(pair.a.full_name, pair.b.full_name, pair.signals) for pair in pairs],
                         [("Renata Ibarra Solís", "Renata Ibarra", ["email"])])
        self.assertTrue(merge.build_preview(church, pairs[0].a, pairs[0].b).allowed)
