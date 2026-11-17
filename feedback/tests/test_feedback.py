"""Opinions after an event: who may give one, once, without a name; what the administration reads."""


from django.urls import reverse

from agenda.models import EventRegistration
from agenda.tests.test_agenda import AgendaFixture
from feedback import services
from feedback.models import EventFeedback, FeedbackGiven
from people import flags

ANSWER = {"rating": "bien", "recommend": "si", "comment": "Más sillas."}


class FeedbackTests(AgendaFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.reg = EventRegistration.objects.create(event=cls.retiro, person=cls.member)
        EventRegistration.objects.create(event=cls.retiro_b, person=cls.member_b)

    def end(self, days_ago=1, event=None):
        self.move(event or self.retiro, -days_ago - 2, -days_ago)

    def url(self, name="give", event=None):
        return reverse(f"feedback:{name}", args=[(event or self.retiro).slug])

    def test_nobody_answers_before_the_event_ends(self):
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(services.pending_for(self.member, self.church), [])

    def test_who_went_answers_once_and_no_name_is_stored(self):
        self.end()
        self.assertEqual(services.pending_for(self.member, self.church), [self.retiro])
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(self.url()), "sin nada que la una a ti")
        self.assertRedirects(self.client.post(self.url(), ANSWER), self.url())
        row = EventFeedback.objects.get()
        self.assertEqual((row.event, row.rating, row.recommend, row.comment), (self.retiro, "bien", "si", "Más sillas."))
        self.assertFalse([f.name for f in EventFeedback._meta.get_fields() if f.name in {"person", "user", "created_by"}])
        self.assertContains(self.client.get(self.url()), "Gracias por tu opinión")
        self.client.post(self.url(), ANSWER)
        self.assertEqual(EventFeedback.objects.count(), 1)
        self.assertEqual(services.pending_for(self.member, self.church), [])
        with self.assertRaises(services.FeedbackError):
            services.give(self.member, self.retiro, rating="mal", recommend="no")

    def test_who_was_not_there_cannot_answer(self):
        self.end()
        for user in (self.asked_user, self.leader_user):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url()).status_code, 404)
            self.assertEqual(self.client.post(self.url(), ANSWER).status_code, 404)
        EventRegistration.objects.filter(pk=self.reg.pk).update(attended=False)
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.post(self.url(), ANSWER).status_code, 404)
        EventRegistration.objects.filter(pk=self.reg.pk).update(attended=None, status=EventRegistration.Status.CANCELLED)
        self.assertEqual(self.client.post(self.url(), ANSWER).status_code, 404)
        self.assertEqual(EventFeedback.objects.count(), 0)

    def test_it_closes_fourteen_days_after_the_last_day(self):
        self.end(days_ago=15)
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(services.pending_for(self.member, self.church), [])
        self.end(days_ago=14)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_an_answer_must_choose_both_options(self):
        self.end()
        self.client.force_login(self.member_user)
        response = self.client.post(self.url(), {"comment": "x"})
        self.assertContains(response, "Elige una opción.")
        self.assertEqual(FeedbackGiven.objects.count(), 0)

    def test_another_church_and_module_off(self):
        self.end(event=self.retiro_b)
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(self.url(event=self.retiro_b)).status_code, 404)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url("results", self.retiro_b)).status_code, 404)
        self.end()
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_results_are_for_the_administration_after_it_closes_and_hide_small_numbers(self):
        self.end()
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(self.url("results")).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url("results")), "Todavía nadie opina")
        answers = (("mal", "no", "Comentario secreto"), ("bien", "si", ""), ("bien", "tal_vez", "Empezar a tiempo"),
                   ("muy_bien", "si", ""), ("bien", "si", ""))
        for rating, recommend, comment in answers:
            EventFeedback.objects.create(event=self.retiro, rating=rating, recommend=recommend, comment=comment)
        # While people can still answer, only how many arrived: results that grow would show each new answer.
        response = self.client.get(self.url("results"))
        self.assertContains(response, "Ya llegaron 5 opiniones")
        self.assertNotContains(response, "Comentario secreto")
        self.assertNotContains(response, "Regular")
        self.end(days_ago=15)
        response = self.client.get(self.url("results"))
        self.assertContains(response, "5 personas dieron su opinión")
        self.assertContains(response, "Comentario secreto")
        self.assertContains(response, "Empezar a tiempo")
        self.assertEqual(dict(services.results(self.retiro)["ratings"]), {"Muy bien": 1, "Bien": 3, "Regular": 0, "Mal": 1})
        for name in (self.member.first_name, self.member.last_name):
            self.assertNotContains(response, name)
        # Four is under the church's minimum reporting size: an event that never reaches it shows no answer.
        EventFeedback.objects.filter(comment="Empezar a tiempo").delete()
        response = self.client.get(self.url("results"))
        self.assertContains(response, "Hubo 4 opiniones")
        self.assertContains(response, "al menos 5")
        self.assertNotContains(response, "Comentario secreto")
        self.assertEqual(services.results(self.retiro)["comments"], [])

    def test_the_order_of_the_opinions_does_not_follow_the_order_of_who_answered(self):
        self.assertEqual(EventFeedback._meta.pk.get_internal_type(), "UUIDField")
        self.assertFalse([f.name for f in EventFeedback._meta.get_fields() if "DateTime" in f.get_internal_type()])
        self.assertFalse([f.name for f in FeedbackGiven._meta.get_fields() if "Date" in f.get_internal_type()])

    def test_who_answered_is_kept_as_a_keyed_hash_not_as_a_person(self):
        self.assertEqual(sorted(f.name for f in FeedbackGiven._meta.get_fields()), ["event", "id", "token"])
        self.end()
        services.give(self.member, self.retiro, rating="bien", recommend="si")
        token = FeedbackGiven.objects.get().token
        self.assertEqual(len(token), 64)
        self.assertNotIn(str(self.member.pk), [token])
        self.assertEqual(token, services.token_for(self.retiro, self.member))
        self.assertNotEqual(token, services.token_for(self.retiro, self.asked))
        self.assertNotEqual(token, services.token_for(self.retiro_b, self.member))
        with self.settings(SECRET_KEY="otra-clave-de-prueba"):
            self.assertNotEqual(token, services.token_for(self.retiro, self.member))

    def test_asking_to_be_contacted_is_apart_from_the_opinion(self):
        from care.models import FollowUp

        self.end()
        self.client.force_login(self.member_user)
        self.client.post(self.url(), ANSWER)
        thanks = self.client.get(self.url())
        self.assertContains(thanks, "Quiero que alguien me busque")
        self.assertRedirects(self.client.post(reverse("feedback:contact")), reverse("community:home"))
        self.client.post(reverse("feedback:contact"))
        follow_up = FollowUp.objects.get(person=self.member)
        self.assertEqual((follow_up.reason, follow_up.owner, follow_up.church), ("contacto", None, self.church))
        self.assertFalse([f.name for f in FollowUp._meta.get_fields() if f.name in {"event", "feedback"}])
        self.assertNotContains(self.client.get(self.url()), "Quiero que alguien me busque")
        self.assertEqual(self.client.get(reverse("feedback:contact")).status_code, 405)

    def test_somebody_else_marked_absent_does_not_hide_the_invitation(self):
        EventRegistration.objects.create(event=self.retiro, person=self.asked, attended=False)
        EventRegistration.objects.filter(pk=self.reg.pk).update(attended=True)
        self.end()
        self.assertEqual(services.pending_for(self.member, self.church), [self.retiro])
        self.assertEqual(services.pending_for(self.asked, self.church), [])

    def test_mi_iglesia_invites_who_went_until_they_answer(self):
        self.client.force_login(self.member_user)
        self.assertNotContains(self.client.get(reverse("community:home")), "Dar mi opinión")
        self.end()
        self.assertContains(self.client.get(reverse("community:home")), "¿Cómo estuvo Retiro de jóvenes?")
        self.client.post(self.url(), ANSWER)
        self.assertNotContains(self.client.get(reverse("community:home")), "Dar mi opinión")
