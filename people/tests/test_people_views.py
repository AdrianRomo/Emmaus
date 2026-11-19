import re
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import UserChurchRole
from events.access import resolve_access
from events.models import Event, EventMembership, EventParticipant
from groups.tests.base import CommunityFixture
from people import flags, services
from people.audit import record_church
from people.models import CampusAffiliation, ChurchAuditEvent, EventParticipantLink, Person

ADMIN_PAGES = [
    ("people:list", lambda f: []), ("people:create", lambda f: []), ("people:detail", lambda f: [f.member.pk]),
    ("people:edit", lambda f: [f.member.pk]), ("people:from_event_list", lambda f: []),
    ("people:from_event", lambda f: [f.event.slug]),
]


class PeoplePermissionTests(CommunityFixture):
    def test_only_the_church_administration_opens_the_directory(self):
        for name, args in ADMIN_PAGES:
            url = reverse(name, args=args(self))
            self.assertEqual(self.client.get(url).status_code, 302)  # anonymous: sign in
            named = 404 if args(self) else 403  # an address that names a row: one answer for whoever may not manage
            for user, expected in ((self.admin, 200), (self.member_user, named), (self.leader_user, named),
                                   (self.coord1, named), (self.tutor1, 404), (self.admin_b, 200 if not args(self) else 404)):
                self.client.force_login(user)
                with self.subTest(page=name, user=user.email):
                    self.assertEqual(self.client.get(url).status_code, expected)
            self.client.logout()

    def test_module_off_is_404_for_the_administration_too(self):
        flags.set_enabled(self.church, "people", False)
        self.client.force_login(self.admin)
        for name, args in ADMIN_PAGES:
            self.assertEqual(self.client.get(reverse(name, args=args(self))).status_code, 404)
        self.assertNotContains(self.client.get(reverse("community:home")), "/mi-iglesia/personas/")

    def test_people_and_events_of_another_church_do_not_exist(self):
        """Scenario 5 for the directory: reads, writes, search and the campero list, by guessed id."""
        self.client.force_login(self.admin)
        b = self.member_b
        for name in ("people:detail", "people:edit"):
            self.assertEqual(self.client.get(reverse(name, args=[b.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("people:edit", args=[b.pk]), {"first_name": "Robado"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("people:grant_access", args=[b.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("people:from_event", args=[self.event_b.slug])).status_code, 404)
        url = reverse("people:from_event_link", args=[self.event_b.slug, self.campero_b.public_id])
        self.assertEqual(self.client.post(url, {"persona": "nueva"}).status_code, 404)
        # A campero of this church cannot be tied to a person of the other one, nor the other way round.
        url = reverse("people:from_event_link", args=[self.event.slug, self.p1.public_id])
        self.assertEqual(self.client.post(url, {"persona": str(b.pk)}).status_code, 404)
        url = reverse("people:from_event_link", args=[self.event.slug, self.campero_b.public_id])
        self.assertEqual(self.client.post(url, {"persona": "nueva"}).status_code, 404)
        with self.assertRaises(services.PeopleError):
            services.link_participant(b, self.p1, self.admin)
        for url in (reverse("people:list"), reverse("people:list") + "?q=bruno", reverse("people:from_event_list")):
            response = self.client.get(url)
            self.assertNotContains(response, "Bruno")
            self.assertNotContains(response, "Retiro B")
        b.refresh_from_db()
        self.assertEqual(b.first_name, "Bruno")
        self.assertFalse(EventParticipantLink.objects.filter(participant__in=[self.p1, self.campero_b]).exists())


class DirectoryTests(CommunityFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_register_edit_and_search(self):
        response = self.client.post(reverse("people:create"), {
            "first_name": "  Rosa  ", "last_name": "Peña  Gil", "email": "ROSA@prueba.mx", "phone": "", "preferred_contact_method": "email",
            "birth_date": "14/03/2009",
        })
        rosa = Person.objects.get(email="rosa@prueba.mx")
        self.assertRedirects(response, reverse("people:detail", args=[rosa.pk]))
        self.assertEqual((rosa.full_name, rosa.church, rosa.user, rosa.created_from), ("Rosa Peña Gil", self.church, None, "manual"))
        self.assertFalse(User.objects.filter(email__iexact="rosa@prueba.mx").exists())  # registering gives no access
        self.assertContains(self.client.get(reverse("people:list") + "?q=pena"), "Rosa Peña Gil")
        self.assertNotContains(self.client.get(reverse("people:list") + "?q=pena"), "Diego")
        response = self.client.post(reverse("people:create"), {"first_name": "Otra", "email": "rosa@prueba.mx", "preferred_contact_method": "email"})
        self.assertContains(response, "Otra persona de la iglesia ya tiene este correo.")
        response = self.client.post(reverse("people:create"), {"first_name": "", "preferred_contact_method": "email"})
        self.assertContains(response, "Hay un problema")
        self.assertContains(response, "Escribe el nombre.")

    def test_bring_a_campero_as_a_new_person(self):
        page = self.client.get(reverse("people:from_event", args=[self.event.slug]))
        self.assertContains(page, "Mariana López Ruiz")
        self.assertNotContains(page, "Diego Hernández Cruz")  # already linked
        self.assertNotContains(page, "Persona Otro Evento")
        url = reverse("people:from_event_link", args=[self.event.slug, self.p1.public_id])
        response = self.client.post(url, {"persona": "nueva"}, follow=True)
        self.assertContains(response, "Mariana López Ruiz quedó en las personas de la iglesia")
        person = Person.objects.get(first_name="Mariana")
        self.assertEqual((person.last_name, person.church, person.user, person.created_from, person.birth_date, person.phone),
                         ("López Ruiz", self.church, None, "event", None, ""))
        self.assertEqual(EventParticipantLink.objects.get(participant=self.p1).linked_by, self.admin)
        self.assertContains(self.client.post(url, {"persona": "nueva"}, follow=True), "Este campero ya está vinculado con una persona.")
        self.assertEqual(Person.objects.filter(first_name="Mariana").count(), 1)

    def test_link_a_campero_to_a_person_that_already_exists_only_by_decision(self):
        sofia = Person.objects.create(church=self.church, first_name="Sofía", last_name="García")
        page = self.client.get(reverse("people:from_event", args=[self.event.slug]))
        self.assertContains(page, "Es Sofía García, que ya está")  # a hint; nothing is linked by showing it
        self.assertFalse(EventParticipantLink.objects.filter(person=sofia).exists())
        url = reverse("people:from_event_link", args=[self.event.slug, self.p3.public_id])
        self.client.post(url, {"persona": str(sofia.pk)})
        self.assertEqual(EventParticipantLink.objects.get(person=sofia).participant, self.p3)
        # One campero per person per event, one person per campero.
        with self.assertRaises(services.PeopleError):
            services.link_participant(sofia, self.p4, self.admin)
        with self.assertRaises(services.PeopleError):
            services.link_participant(self.no_account, self.p3, self.admin)
        response = self.client.post(reverse("people:unlink", args=[sofia.pk, sofia.event_links.get().pk]), follow=True)
        self.assertContains(response, "Los datos del evento no cambiaron.")
        self.assertTrue(EventParticipant.objects.filter(pk=self.p3.pk).exists())

    def test_camperos_are_linked_only_after_the_event(self):
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.ACTIVE)
        self.assertEqual(self.client.get(reverse("people:from_event", args=[self.event.slug])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("people:from_event_list")), "Campamento A")
        url = reverse("people:from_event_link", args=[self.event.slug, self.p1.public_id])
        self.assertEqual(self.client.post(url, {"persona": "nueva"}).status_code, 404)
        self.event.refresh_from_db()
        with self.assertRaises(services.PeopleError):
            services.create_person_from_participant(self.p1, self.admin)

    def test_the_purge_takes_the_link_and_never_the_person(self):
        self.p2.delete()
        self.member.refresh_from_db()
        self.assertFalse(EventParticipantLink.objects.filter(person=self.member).exists())
        self.client.force_login(self.member_user)
        self.assertNotContains(self.client.get(reverse("community:home")), "Donde has estado")

    def test_the_sede_crosses_over_and_nothing_else(self):
        from events.models import Sede

        sede = Sede.objects.create(church=self.church, name="Mérida", slug="merida")
        EventParticipant.objects.filter(pk=self.p4.pk).update(sede=sede)
        self.p4.refresh_from_db()
        person = services.create_person_from_participant(self.p4, self.admin)
        self.assertEqual(CampusAffiliation.objects.get(person=person).sede, sede)
        page = self.client.get(reverse("people:detail", args=[person.pk]))
        self.assertContains(page, "Campamento A")
        for private in ("Cuarto", str(self.p4.room) + ",", "16 años"):
            self.assertNotContains(page, private)


class GrantAccessTests(CommunityFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_a_new_account_gets_a_link_on_screen_and_no_role_anywhere(self):
        self.no_account.email = "nora@prueba.mx"
        self.no_account.save()
        response = self.client.post(reverse("people:grant_access", args=[self.no_account.pk]))
        self.assertContains(response, "Emmaus no se lo envió: mándaselo tú.")
        self.assertContains(response, "/cuenta/contrasena/")
        user = User.objects.get(email="nora@prueba.mx")
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertEqual(Person.objects.get(pk=self.no_account.pk).user, user)
        self.assertFalse(UserChurchRole.objects.filter(user=user).exists())
        self.assertFalse(EventMembership.objects.filter(user=user).exists())
        self.assertEqual(ChurchAuditEvent.objects.filter(action="cuenta_creada").count(), 1)
        # A second link while there is no password; none once the password exists.
        self.assertContains(self.client.post(reverse("people:access_link", args=[self.no_account.pk])), "/cuenta/contrasena/")
        user.set_password("una-clave-larga-123")
        user.save()
        response = self.client.post(reverse("people:access_link", args=[self.no_account.pk]), follow=True)
        self.assertContains(response, "Esta persona ya creó su contraseña.")
        self.assertContains(self.client.post(reverse("people:grant_access", args=[self.no_account.pk]), follow=True), "ya tiene acceso")

    def test_no_link_for_an_account_that_holds_something_else(self):
        for person in (self.admin_person, self.coord_person):
            response = self.client.post(reverse("people:access_link", args=[person.pk]), follow=True)
            self.assertNotContains(response, "/cuenta/contrasena/")

    def test_without_an_email_there_is_nothing_to_grant(self):
        response = self.client.post(reverse("people:grant_access", args=[self.no_account.pk]), follow=True)
        self.assertContains(response, "Primero escribe el correo de esta persona")
        self.assertIsNone(Person.objects.get(pk=self.no_account.pk).user)

    def test_an_existing_account_is_linked_only_after_an_explicit_yes(self):
        """coord2 served in the camp team. Their account becomes a person's only when the administrator says so,
        and what it could do in the event does not change."""
        person = Person.objects.create(church=self.church, first_name="César", last_name="Dos", email="COORD2@prueba.mx")
        response = self.client.post(reverse("people:grant_access", args=[person.pk]))
        self.assertContains(response, "Ya existe una cuenta con ese correo")
        self.assertIsNone(Person.objects.get(pk=person.pk).user)
        self.client.post(reverse("people:grant_access", args=[person.pk]), {"vincular_cuenta": "si"})
        self.assertEqual(Person.objects.get(pk=person.pk).user, self.coord2)
        self.assertEqual(resolve_access(self.coord2, self.event).role, "coordinador")
        self.assertFalse(UserChurchRole.objects.filter(user=self.coord2).exists())
        self.assertEqual(ChurchAuditEvent.objects.filter(action="cuenta_vinculada").count(), 1)

    def test_an_account_is_one_person_per_church(self):
        twin = Person.objects.create(church=self.church, first_name="Otro", last_name="Diego", email="otro@prueba.mx")
        Person.objects.filter(pk=twin.pk).update(email=self.member_user.email)
        response = self.client.post(reverse("people:grant_access", args=[twin.pk]), {"vincular_cuenta": "si"}, follow=True)
        self.assertContains(response, "ya es de otra persona de esta iglesia")

    def test_a_campero_who_was_a_minor_needs_the_guardian_confirmation(self):
        person = services.create_person_from_participant(self.p1, self.admin)  # Mariana, 15
        person.email = "mariana@prueba.mx"
        person.save()
        self.assertContains(self.client.get(reverse("people:detail", args=[person.pk])), "Su mamá, papá o tutor está de acuerdo")
        response = self.client.post(reverse("people:grant_access", args=[person.pk]), follow=True)
        self.assertContains(response, "Confirma que su mamá, papá o tutor está de acuerdo")
        self.assertFalse(User.objects.filter(email="mariana@prueba.mx").exists())
        self.client.post(reverse("people:grant_access", args=[person.pk]), {"tutor_de_acuerdo": "si"})
        self.assertTrue(User.objects.filter(email="mariana@prueba.mx").exists())
        self.assertTrue(ChurchAuditEvent.objects.get(action="cuenta_creada").metadata["minor_confirmed"])
        adult = services.create_person_from_participant(self.p4, self.admin)  # 16 at the camp, but the birth date says adult
        adult.birth_date, adult.email = "1990-01-01", "mateo@prueba.mx"
        adult.save()
        adult.refresh_from_db()
        self.assertFalse(services.was_minor_at_an_event(adult))


class ChurchAuditTests(CommunityFixture):
    def test_closed_vocabulary_and_no_contact_data(self):
        with self.assertRaises(ValueError):
            record_church(self.church, self.admin, "accion_inventada", "x")
        with self.assertRaises(ValueError):
            record_church(self.church, self.admin, "persona_creada", "x", email="a@b.mx")
        self.client.force_login(self.admin)
        self.client.post(reverse("people:create"), {"first_name": "Rosa", "email": "rosa@prueba.mx", "phone": "55 9999 0000",
                                                    "preferred_contact_method": "email"})
        for row in ChurchAuditEvent.objects.all():
            text = row.summary + str(row.metadata)
            self.assertNotIn("@", text)
            self.assertNotIn("9999", text)
        self.assertFalse(ChurchAuditEvent.objects.filter(church=self.church_b).exists())


class NotFoundBeforeForbiddenTests(CommunityFixture):
    """Somebody who may not manage the church's people gets 404 for an id that is not of this church (the answer the
    administration gets) and 403 only for a row that is. The id of another church never answers differently from
    an id that does not exist."""

    def setUp(self):
        from people import merge
        from people.models import EventParticipantLink, PersonMerge

        self.twin = Person.objects.create(church=self.church, first_name="Nora", last_name="Sin Cuenta Dos")
        self.twin_b = Person.objects.create(church=self.church_b, first_name="Bruno", last_name="Miembro Dos")
        self.dismissal = merge.dismiss(self.church, self.no_account.pk, self.twin.pk, self.admin)
        self.dismissal_b = merge.dismiss(self.church_b, self.member_b.pk, self.twin_b.pk, self.admin_b)
        self.link = EventParticipantLink.objects.get(person=self.member)
        self.merge_b = PersonMerge.objects.create(church=self.church_b, survivor=self.member_b, absorbed=self.twin_b, merged_by=self.admin_b)
        self.church_pastor = User.objects.create_user(username="pastora-404@prueba.mx", email="pastora-404@prueba.mx")
        UserChurchRole.objects.create(church=self.church, user=self.church_pastor, role="pastor")

    def routes(self, person, other, slug, dismissal, link_person, link, merge_id):
        return [
            ("get", reverse("people:detail", args=[person])),
            ("get", reverse("people:edit", args=[person])),
            ("post", reverse("people:edit", args=[person])),
            ("post", reverse("people:grant_access", args=[person])),
            ("post", reverse("people:access_link", args=[person])),
            ("get", reverse("people:merge_preview", args=[person, other])),
            ("post", reverse("people:merge_preview", args=[person, other])),
            ("get", reverse("people:from_event", args=[slug])),
            ("post", reverse("people:duplicate_reopen", args=[dismissal])),
            ("post", reverse("people:unlink", args=[link_person, link])),
            ("get", reverse("people:merge_detail", args=[merge_id])),
            ("post", reverse("people:merge_undo", args=[merge_id])),
        ]

    def wave10(self, person, real: bool):
        """The routes of wave 10 that name a row: a person's stage and tags, a tag, an import."""
        from people import tags
        from people.models import PeopleImport

        tag = tags.church_tags(self.church).first() or tags.create_tag(self.church, "Coro", self.admin)
        record = PeopleImport.objects.filter(church=self.church).first() or PeopleImport.objects.create(church=self.church, created_count=1)
        tag_id, import_id = (tag.pk, record.pk) if real else (987654, 987654)
        return [
            ("post", reverse("people:stage", args=[person])), ("post", reverse("people:person_tags", args=[person])),
            ("get", reverse("people:tag_delete", args=[tag_id])), ("post", reverse("people:tag_delete", args=[tag_id])),
            ("get", reverse("people:import_detail", args=[import_id])), ("post", reverse("people:import_undo", args=[import_id])),
        ]

    def test_who_may_not_manage_cannot_tell_a_row_that_exists_from_one_that_does_not(self):
        own = self.routes(self.no_account.pk, self.twin.pk, self.event.slug, self.dismissal.pk, self.member.pk, self.link.pk, 0)[:10]
        foreign = self.routes(self.member_b.pk, self.twin_b.pk, self.event_b.slug, self.dismissal_b.pk, self.member_b.pk, self.link.pk, self.merge_b.pk)
        invented = self.routes(987654, 987655, "no-existe", 987654, 987654, 987654, 987654)
        mixed = [
            ("get", reverse("people:merge_preview", args=[self.no_account.pk, self.member_b.pk])),  # one of each church
            ("post", reverse("people:unlink", args=[self.no_account.pk, self.link.pk])),  # a link of another person
        ]
        for user in (self.member_user, self.leader_user, self.church_pastor):
            self.client.force_login(user)
            pages = set()
            for method, url in own + foreign + invented + mixed + self.wave10(self.no_account.pk, True) + self.wave10(987654, False):
                with self.subTest(user=user.email, url=url):
                    response = getattr(self.client, method)(url)
                    self.assertEqual(response.status_code, 404)
                    pages.add(re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"|data-idle-remaining="\d+"', "", response.content.decode()))
            # The screens whose address names no row are the only ones that say "no puedes": they tell nothing about a row.
            for name in ("people:list", "people:create", "people:duplicates", "people:tags", "people:export", "people:import"):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
            self.assertEqual(self.client.post(reverse("people:duplicate_dismiss"), {"a": self.no_account.pk, "b": self.twin.pk}).status_code, 403)
            self.assertEqual(self.client.post(reverse("people:duplicate_dismiss"), {"a": 987654, "b": 987655}).status_code, 403)
            self.assertEqual(len(pages), 1, user.email)  # and every 404 is the same page, byte for byte
        # and nothing was written by any of it
        self.assertTrue(type(self.dismissal).objects.filter(pk=self.dismissal_b.pk).exists())
        self.merge_b.refresh_from_db()
        self.assertIsNone(self.merge_b.undone_at)

    def test_the_administration_of_another_church_still_gets_404(self):
        self.client.force_login(self.admin_b)
        for name in ("people:detail", "people:edit"):
            self.assertEqual(self.client.get(reverse(name, args=[self.no_account.pk])).status_code, 404)

    def test_a_merged_record_is_not_found_for_anybody(self):
        Person.objects.filter(pk=self.twin.pk).update(merged_into=self.no_account)
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(reverse("people:detail", args=[self.twin.pk])).status_code, 404)
