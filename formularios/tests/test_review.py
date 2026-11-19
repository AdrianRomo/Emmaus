"""What the independent review of wave 10 found in this slice, one test per finding that had none elsewhere.

The others live beside what they changed: the day each answer is kept until (``test_staff.RetentionTests``), the
seal, the answers set aside and the repeated contact (``test_answers.PublicAnswerTests``), and the 404 of a form's
number for who reads no form (``test_builder``)."""

from datetime import date, timedelta

from formularios import services
from formularios.models import Submission
from people.models import ChurchAuditEvent, Person

from .base import FormFixture, url


class MinorsTests(FormFixture):
    """A person the church has on record as a minor neither sees nor answers the forms of the church."""

    def test_a_known_minor_gets_the_404_of_a_form_that_does_not_exist(self):
        form = self.member_form()
        services.save_question(form, self.admin, kind="largo", label="Cuéntanos")
        form = self.publish(form)
        today = date.today()
        Person.objects.filter(pk=self.member.pk).update(birth_date=today.replace(year=today.year - 15))
        self.member.refresh_from_db()
        self.assertTrue(services.is_known_minor(self.member))
        self.client.force_login(self.member_user)
        for method, address in (("get", url("list")), ("get", url("fill", form.pk)), ("get", url("thanks", form.pk))):
            self.assertEqual(getattr(self.client, method)(address).status_code, 404, address)
        self.assertEqual(self.client.post(url("fill", form.pk), self.member_data(form)).status_code, 404)
        with self.assertRaises(services.FormChanged):  # and under the views, the service holds the same
            services.submit(form, form.current_version_id, answers={}, person=self.member)
        self.assertEqual(Submission.objects.count(), 0)
        # an adult, and somebody whose age the church does not know and nothing marks as a minor, answer as before
        Person.objects.filter(pk=self.member.pk).update(birth_date=today.replace(year=today.year - 30))
        self.assertEqual(self.client.get(url("fill", form.pk)).status_code, 200)
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.get(url("fill", form.pk)).status_code, 200)
        # the builder says it
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url("detail", form.pk)), "registrado como menor de edad no ve ni responde")


class AddPersonSignalsTests(FormFixture):
    """"Agregar a personas" uses the signals of ``people.duplicates``: the e-mail AND the phone."""

    def setUp(self):
        self.form = self.public_form()
        self.submission, _ = self.answer(self.form, self.public_data(self.form, email="otro.correo@demo.invalid", phone="55 9999 0000"))
        self.address = url("add_person", self.form.pk, self.submission.pk)
        self.same_phone = Person.objects.create(church=self.church, first_name="Vera", last_name="Visita", email="vera@demo.invalid",
                                                phone="+52 (55) 9999-0000")

    def test_the_same_phone_with_another_e_mail_does_not_make_a_second_record(self):
        people = Person.objects.count()
        self.client.force_login(self.admin)
        page = self.client.get(self.address)
        self.assertContains(page, "Tiene el mismo teléfono.")
        self.assertContains(page, "Es esta persona")
        self.assertNotContains(page, ">Agregar a personas de la iglesia</button>")  # the plain "new" button is gone
        self.assertContains(page, "Es otra persona: agregarla")
        response = self.client.post(self.address, follow=True)  # a POST without the explicit override creates nobody
        self.assertContains(response, "Alguien de la iglesia ya tiene este teléfono.")
        with self.assertRaises(services.FormError):
            services.add_to_people(self.submission, self.admin)
        self.assertEqual(Person.objects.count(), people)
        # the explicit "es otra persona" creates the record and says so in the log, with a fixed word
        self.client.post(self.address, {"otra": "1"})
        self.submission.refresh_from_db()
        self.assertEqual(Person.objects.count(), people + 1)
        self.assertNotEqual(self.submission.person, self.same_phone)
        row = ChurchAuditEvent.objects.get(action="formulario_persona_agregada")
        self.assertEqual(row.metadata, {"form_id": self.form.pk, "person_id": self.submission.person_id, "override": "mismo_telefono"})
        self.assertIn("aunque alguien más tiene ese teléfono", row.summary)
        self.assertNotIn("9999", row.summary + str(row.metadata))

    def test_a_shared_e_mail_cannot_be_overridden(self):
        Person.objects.filter(pk=self.same_phone.pk).update(email="OTRO.correo@demo.invalid")
        people = Person.objects.count()
        self.client.force_login(self.admin)
        page = self.client.get(self.address)
        self.assertContains(page, "No se crea otro registro")
        self.assertNotContains(page, "Es otra persona: agregarla")
        self.client.post(self.address, {"otra": "1"})
        self.assertEqual(Person.objects.count(), people)
        self.client.post(self.address, {"persona": self.same_phone.pk})
        self.submission.refresh_from_db()
        self.assertEqual((Person.objects.count(), self.submission.person), (people, self.same_phone))
        self.assertNotIn("override", ChurchAuditEvent.objects.get(action="formulario_persona_agregada").metadata)

    def test_the_scan_has_no_limit(self):
        Person.objects.bulk_create(
            Person(church=self.church, first_name=f"A{number:04d}", phone=f"55 1000 {number:04d}") for number in range(600)
        )
        late = Person.objects.create(church=self.church, first_name="Zoe", phone="5599990000")  # after 600 others by name
        self.assertEqual(services.similar_people(self.submission), [self.same_phone, late])
        with self.assertNumQueries(2):
            services.similar_people(self.submission)

    def test_no_signal_no_list(self):
        Person.objects.filter(pk=self.same_phone.pk).update(phone="55 0000 1111")
        self.assertEqual(services.similar_people(self.submission), [])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.address), ">Agregar a personas de la iglesia</button>")
        other = self.member_form(title="Otro") and Person.objects.count()
        self.client.post(self.address)
        self.assertEqual(Person.objects.count(), other + 1)
        self.assertEqual(self.submission.delete_after, self.form.closes_on + timedelta(days=90))
