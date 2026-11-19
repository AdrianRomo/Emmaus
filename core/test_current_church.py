"""An account of more than one church chooses the church it works in (issue #32, decision 0003 rule 6).

The session holds a church id and nothing else; every request checks it again against what the
account belongs to at that moment. Choosing never gives a permission: each screen asks about the
church of its own request. An account of one church, and every camp screen, see no change.
"""

import re
from datetime import timedelta

from django.contrib.auth import SESSION_KEY as AUTH_SESSION_KEY
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from agenda import services as agenda
from core.authz import SESSION_KEY, church_choices, current_church, has_scope
from core.models import Church, Role, UserChurchRole
from core.services import assign_role
from events.models import Event
from events.tests.base import PASSWORD, make_user
from groups.tests.base import CommunityFixture
from notices import services as notices
from people import flags
from people.models import ChurchAuditEvent, Person

SWITCH = reverse("switch_church")
# One page per module, and what only church A or only church B has on it.
PAGES = {
    "people:list": ("Nora", "Bruno"),
    "community:groups": ("Jóvenes Roca", "Grupo de la iglesia B"),
    "agenda:list": ("Retiro de la iglesia A", "Vigilia de la iglesia B"),
    "notices:list": ("Aviso de la iglesia A", "Aviso de la iglesia B"),
    "overview:panorama": ("Jóvenes Roca", "Grupo de la iglesia B"),
}


def masked(response) -> str:
    html = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", response.content.decode())
    html = re.sub(r'data-idle-remaining="\d+"', "", html)
    return re.sub(r"\b\d\d:\d\d(:\d\d)?\b", "HH:MM", html)


class TwoChurchFixture(CommunityFixture):
    """``dual`` administers church A (the older role: the default) and church B, and is a person of both.
    ``dual_member`` is a person of both and holds no role anywhere."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.dual = make_user("dos-iglesias@prueba.mx", "Dalia", "Dos Iglesias")
        assign_role(cls.dual, cls.church, Role.CHURCH_ADMIN)
        assign_role(cls.dual, cls.church_b, Role.CHURCH_ADMIN)
        cls.dual_a = cls._person(cls.church, cls.dual)
        cls.dual_b = cls._person(cls.church_b, cls.dual)
        cls.dual_member = make_user("miembro-dos@prueba.mx", "Mateo", "Dos Iglesias")
        cls.dual_member_a = cls._person(cls.church, cls.dual_member)
        cls.dual_member_b = cls._person(cls.church_b, cls.dual_member)

        today = timezone.localdate()
        for church, admin, name, slug in (
            (cls.church, cls.admin, "Retiro de la iglesia A", "retiro-iglesia-a"),
            (cls.church_b, cls.admin_b, "Vigilia de la iglesia B", "vigilia-iglesia-b"),
        ):
            event = Event.objects.create(
                church=church, name=name, slug=slug, kind=Event.Kind.RETREAT,
                starts_on=today + timedelta(days=10), ends_on=today + timedelta(days=11),
            )
            agenda.save_page(event, admin, published=True, registration="abierta")
        notices.publish(cls.church, cls.admin, title="Aviso de la iglesia A", body="Texto A")
        notices.publish(cls.church_b, cls.admin_b, title="Aviso de la iglesia B", body="Texto B")

    def choose(self, church, client=None, **extra):
        return (client or self.client).post(SWITCH, {"church": church.pk, **extra})

    def chosen(self, client=None):
        return (client or self.client).session.get(SESSION_KEY)

    def assert_only(self, mine: int, client=None):
        """Every module's page shows the church at index ``mine`` (0 is A, 1 is B) and nothing of the other."""
        for name, words in PAGES.items():
            response = (client or self.client).get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertContains(response, words[mine], msg_prefix=name)
            self.assertNotContains(response, words[1 - mine], msg_prefix=name)


class ChoosingTests(TwoChurchFixture):
    def test_each_church_shows_only_its_own_data(self):
        self.client.force_login(self.dual)
        self.assert_only(0)  # no choice yet: the first role's church
        self.assertRedirects(self.choose(self.church_b), reverse("community:home"), fetch_redirect_response=False)
        self.assertEqual(self.chosen(), self.church_b.pk)
        self.assert_only(1)
        self.choose(self.church)
        self.assert_only(0)

    def test_the_session_keeps_an_id_and_nothing_else(self):
        self.client.force_login(self.dual)
        before = set(self.client.session.keys())
        self.choose(self.church_b)
        session = self.client.session
        self.assertIs(type(session[SESSION_KEY]), int)
        self.assertEqual(set(session.keys()) - before - {"_messages", "last_seen"}, {SESSION_KEY})

    def test_a_person_without_a_role_chooses_too_and_gains_nothing(self):
        self.client.force_login(self.dual_member)
        self.assertContains(self.client.get(reverse("community:groups")), "Jóvenes Roca")
        self.choose(self.church_b)
        response = self.client.get(reverse("community:groups"))
        self.assertContains(response, "Grupo de la iglesia B")
        self.assertNotContains(response, "Jóvenes Roca")
        for name in ("people:list", "community:group_admin_list", "agenda:admin_list", "overview:panorama"):
            self.assertIn(self.client.get(reverse(name)).status_code, (403, 404), name)

    def test_choosing_carries_no_permission_from_the_other_church(self):
        """Administrator of A, plain person of B: after choosing B, nothing of the administration answers."""
        UserChurchRole.objects.filter(user=self.dual, church=self.church_b).delete()
        self.client.force_login(self.dual)
        self.assertEqual(self.client.get(reverse("people:list")).status_code, 200)
        self.choose(self.church_b)
        for name in ("people:list", "community:group_admin_list", "agenda:admin_list", "serving:team_admin", "care:admin_list",
                     "church_policies", "sitio:edit", "overview:panorama"):
            self.assertIn(self.client.get(reverse(name)).status_code, (403, 404), name)
        home = self.client.get(reverse("community:home"))
        self.assertEqual(home.status_code, 200)
        self.assertNotContains(home, "Administración")
        self.assertFalse(has_scope(self.dual, self.church_b, "church", self.church_b.pk, "manage"))
        # An object of church A by its id, from church B: it does not exist there
        self.assertEqual(self.client.get(reverse("community:group_detail", args=[self.group.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("people:detail", args=[self.no_account.pk])).status_code, 403)

    def test_an_object_of_the_other_church_is_not_found_by_its_id(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        self.assertEqual(self.client.get(reverse("community:group_detail", args=[self.group.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("people:detail", args=[self.no_account.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("community:group_detail", args=[self.group_b.pk])).status_code, 200)
        self.choose(self.church)
        self.assertEqual(self.client.get(reverse("community:group_detail", args=[self.group_b.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("people:detail", args=[self.member_b.pk])).status_code, 404)

    def test_one_audit_row_with_ids_only(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        row = ChurchAuditEvent.objects.get(action="iglesia_elegida")
        self.assertEqual((row.church_id, row.actor_id), (self.church_b.pk, self.dual.pk))
        self.assertEqual((row.metadata, row.object_type, row.object_id), ({}, "", ""))
        self.assertEqual(row.summary, "Eligió trabajar en esta iglesia.")
        self.choose(self.church_b)  # already there: nothing happened, nothing is written
        self.assertEqual(ChurchAuditEvent.objects.filter(action="iglesia_elegida").count(), 1)
        self.choose(self.church)
        self.assertEqual(ChurchAuditEvent.objects.filter(action="iglesia_elegida", church=self.church).count(), 1)


class RefusedTests(TwoChurchFixture):
    def assert_nothing_changed(self, client=None):
        self.assertIsNone(self.chosen(client))
        self.assertFalse(ChurchAuditEvent.objects.filter(action="iglesia_elegida").exists())

    def test_signed_out(self):
        response = self.choose(self.church_b)
        self.assertRedirects(response, f"{reverse('login')}?next={SWITCH}", fetch_redirect_response=False)
        self.assert_nothing_changed()

    def test_get_is_not_allowed(self):
        self.client.force_login(self.dual)
        self.assertEqual(self.client.get(SWITCH, {"church": self.church_b.pk}).status_code, 405)
        self.assert_nothing_changed()

    def test_without_the_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.dual)
        self.assertEqual(self.choose(self.church_b, client).status_code, 403)
        self.assert_nothing_changed(client)
        self.assert_only(0, client)

    def test_a_church_the_account_is_not_part_of(self):
        """Another church's id, an invented one, and rubbish: the same 404, and the account stays where it was."""
        church_c = Church.objects.create(name="Iglesia C", slug="iglesia-c")
        for user in (self.member_user, self.admin, self.dual):
            self.client.force_login(user)
            for value in (church_c.pk, 987654, "", "abc", f"{self.church_b.pk} OR 1=1", "-1"):
                self.assertEqual(self.client.post(SWITCH, {"church": value}).status_code, 404, value)
            self.assert_nothing_changed()
        for user in (self.member_user, self.admin):  # accounts of church A only
            self.client.force_login(user)
            self.assertEqual(self.choose(self.church_b).status_code, 404)
            self.assert_nothing_changed()
            self.assertContains(self.client.get(reverse("community:groups")), "Jóvenes Roca")

    def test_a_superuser_gets_no_church_this_way(self):
        root = make_user("root@prueba.mx", "Raíz", "Total")
        root.is_superuser = root.is_staff = True
        root.save()
        self.client.force_login(root)
        self.assertEqual(self.choose(self.church_b).status_code, 404)
        self.assert_nothing_changed()
        session = self.client.session
        session[SESSION_KEY] = self.church_b.pk
        session.save()
        self.assertEqual(self.client.get(reverse("community:groups")).status_code, 404)
        self.assertIsNone(self.chosen())
        # With a role in A: A is theirs, B still is not
        assign_role(root, self.church, Role.CHURCH_ADMIN)
        self.assertEqual(self.choose(self.church_b).status_code, 404)
        self.assert_only(0)

    def test_an_event_role_alone_is_no_church(self):
        self.client.force_login(self.coord2)  # an event role in church A and no person: no church for the new modules
        self.assertEqual(self.choose(self.church).status_code, 404)
        self.assert_nothing_changed()

    def test_next_never_leaves_the_site(self):
        self.client.force_login(self.dual)
        home = reverse("community:home")
        for target in ("https://otro.sitio/", "//otro.sitio/", "/\\otro.sitio", "\\\\otro.sitio", "http://testserver/mi-iglesia/",
                       "javascript:alert(1)", "mi-iglesia/", " /mi-iglesia/", "///otro.sitio", "/\\/otro.sitio"):
            response = self.choose(self.church_b, next=target)
            self.assertEqual((response.status_code, response["Location"]), (302, home), target)
        response = self.choose(self.church, next="/mi-iglesia/grupos/?tipo=estudio")
        self.assertEqual(response["Location"], "/mi-iglesia/grupos/?tipo=estudio")


class ValidatedOnEveryRequestTests(TwoChurchFixture):
    def forge(self, value):
        session = self.client.session
        session[SESSION_KEY] = value
        session.save()

    def test_a_forged_church_is_dropped_and_the_default_answers(self):
        self.client.force_login(self.member_user)  # of church A only
        for value in (self.church_b.pk, str(self.church_b.pk), [self.church_b.pk], {"pk": self.church_b.pk}, True, 987654):
            self.forge(value)
            response = self.client.get(reverse("community:groups"))
            self.assertContains(response, "Jóvenes Roca")
            self.assertNotContains(response, "Grupo de la iglesia B")
            self.assertIsNone(self.chosen(), value)

    def test_a_forged_church_changes_no_byte_for_an_account_of_one_church(self):
        self.client.force_login(self.member_user)
        urls = [reverse("community:home"), reverse("community:groups"), reverse("notices:list"), reverse("agenda:list"), reverse("account")]
        reference = [masked(self.client.get(url)) for url in urls]
        self.forge(self.church_b.pk)
        self.assertEqual([masked(self.client.get(url)) for url in urls], reference)

    def test_a_role_and_a_person_that_end_take_the_choice_with_them(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        self.assert_only(1)
        # The role ends: still a person of B, so still there, without the administration
        UserChurchRole.objects.filter(user=self.dual, church=self.church_b).delete()
        self.assertEqual(self.client.get(reverse("people:list")).status_code, 403)
        self.assertEqual(self.chosen(), self.church_b.pk)
        # The person is unlinked too: on the very next request the choice is gone and church A answers
        Person.objects.filter(pk=self.dual_b.pk).update(user=None)
        self.assert_only(0)
        self.assertIsNone(self.chosen())

    def test_a_person_merged_away_no_longer_belongs(self):
        self.client.force_login(self.dual_member)
        self.choose(self.church_b)
        Person.objects.filter(pk=self.dual_member_b.pk).update(merged_into=self.member_b)
        response = self.client.get(reverse("community:groups"))
        self.assertContains(response, "Jóvenes Roca")
        self.assertIsNone(self.chosen())

    def test_a_church_that_is_closed_is_no_longer_a_choice(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        Church.objects.filter(pk=self.church_b.pk).update(active=False)
        self.assert_only(0)
        self.assertIsNone(self.chosen())
        self.assertEqual(self.choose(self.church_b).status_code, 404)

    def test_the_module_off_in_the_chosen_church_answers_404_not_the_other_church(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        flags.set_enabled(self.church_b, "community", False)
        flags.set_enabled(self.church_b, "people", False)
        for name in PAGES:
            self.assertEqual(self.client.get(reverse(name)).status_code, 404, name)
        self.assertEqual(self.chosen(), self.church_b.pk)  # still theirs: it is the module that is off

    def test_signing_in_again_starts_from_the_default(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        self.client.post(reverse("logout"))
        self.assertNotIn(AUTH_SESSION_KEY, self.client.session)
        self.assertIsNone(self.chosen())
        # A value left in the session before signing in (Django keeps an anonymous session's data) is not kept either
        self.forge(self.church_b.pk)
        response = self.client.post(reverse("login"), {"username": self.dual.email, "password": PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertIn(AUTH_SESSION_KEY, self.client.session)
        self.assertIsNone(self.chosen())
        self.assert_only(0)

    def test_the_resolver_answers_the_church_and_the_choices(self):
        request = RequestFactory().get("/")
        request.user, request.session = self.dual, {}
        self.assertEqual(church_choices(request), (self.church, [self.church, self.church_b]))
        request.session[SESSION_KEY] = self.church_b.pk
        self.assertEqual(current_church(request), self.church_b)
        request.user = self.member_user  # the same session in another account's hands
        self.assertEqual(church_choices(request), (self.church, [self.church]))
        self.assertNotIn(SESSION_KEY, request.session)


class SwitcherTests(TwoChurchFixture):
    """The switcher exists only for an account of more than one church, and never on a camp screen."""

    MARK = f'action="{SWITCH}"'

    def test_an_account_of_one_church_never_sees_it(self):
        pages = [
            (self.member_user, "community:home"), (self.member_user, "account"), (self.member_user, "community:groups"),
            (self.admin, "community:home"), (self.admin, "account"), (self.admin, "people:list"), (self.admin, "church_settings"),
            (self.admin_b, "community:home"), (self.leader_user, "community:home"), (self.coord2, "account"),
        ]
        for user, name in pages:
            self.client.force_login(user)
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertNotContains(response, self.MARK, msg_prefix=name)
            self.assertNotContains(response, "Cambiar a ", msg_prefix=name)
            self.assertNotContains(response, "más de una iglesia", msg_prefix=name)

    def test_an_account_of_two_churches_is_offered_the_other_one(self):
        self.client.force_login(self.dual)
        response = self.client.get(reverse("community:home"))
        self.assertContains(response, self.MARK, count=2)  # the rail and the "Más" panel
        self.assertContains(response, f'<input type="hidden" name="church" value="{self.church_b.pk}">', count=2)
        self.assertContains(response, "Cambiar a Iglesia B", count=2)
        self.assertNotContains(response, "Cambiar a Iglesia de prueba")
        self.choose(self.church_b)
        response = self.client.get(reverse("community:home"))
        self.assertContains(response, "Cambiar a Iglesia de prueba", count=2)
        self.assertNotContains(response, "Cambiar a Iglesia B")
        self.assertContains(response, '<p class="rail__church">Iglesia B</p>')

    def test_my_account_names_the_church_and_offers_the_other(self):
        self.client.force_login(self.dual)
        self.choose(self.church_b)
        response = self.client.get(reverse("account"))
        self.assertContains(response, "Ahora estás en <strong>Iglesia B</strong>")
        self.assertContains(response, 'Cambiar<span class="visually-hidden"> a Iglesia de prueba</span>')

    def test_it_also_shows_where_the_module_is_off(self):
        """Church A without the community module has no shell: "Mi cuenta" is the way to church B."""
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.dual)
        response = self.client.get(reverse("account"))
        self.assertContains(response, self.MARK, count=1)
        self.assertRedirects(self.choose(self.church_b), reverse("community:home"), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("community:home")), "Iglesia B")
        self.assertRedirects(self.choose(self.church), reverse("dashboard"), fetch_redirect_response=False)  # no "Mi iglesia" there

    def test_a_church_name_is_escaped(self):
        Church.objects.filter(pk=self.church_b.pk).update(name="<b>Iglesia</b> & Cía")
        self.client.force_login(self.dual)
        for name in ("community:home", "account"):
            response = self.client.get(reverse(name))
            self.assertContains(response, "&lt;b&gt;Iglesia&lt;/b&gt; &amp; Cía")
            self.assertNotContains(response, "<b>Iglesia</b>")
        response = self.client.post(SWITCH, {"church": self.church_b.pk}, follow=True)
        self.assertContains(response, "Ahora estás en &lt;b&gt;Iglesia&lt;/b&gt; &amp; Cía.")

    def test_camp_screens_are_the_same_whatever_was_chosen(self):
        """An event page is its event's: the choice changes no byte of the page, its title or its logo, and it has no
        switcher. (The general links of the rail are the chosen church's, as everywhere.)"""
        from events.models import EventMembership, MembershipRole

        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.ACTIVE)
        EventMembership.objects.create(event=self.event, user=self.dual_member, role=MembershipRole.COORDINATOR)
        self.client.force_login(self.dual_member)
        urls = [reverse("events:home", args=[self.event.slug]), reverse("events:coord_home", args=[self.event.slug])]

        def parts(url):
            html = masked(self.client.get(url, follow=True))
            self.assertNotIn(self.MARK, html)
            self.assertNotIn("Cambiar a ", html)
            brand = html.split('<div class="rail__brand">', 1)[1].split("<p", 1)[0]
            return html.split("<title>", 1)[1].split("</title>", 1)[0], brand, html.split("<main", 1)[1].split("</main>", 1)[0]

        reference = [parts(url) for url in urls]
        self.assertIn("Campamento A", reference[0][0])
        self.choose(self.church_b)
        self.assertContains(self.client.get(reverse("account")), "Ahora estás en Iglesia B.")  # the message, read once
        self.assertEqual([parts(url) for url in urls], reference)
        # Being a person of church B opens none of its events
        self.assertIn(self.client.get(reverse("events:home", args=[self.event_b.slug])).status_code, (403, 404))


class LegacyDiscipleshipTests(TwoChurchFixture):
    """The discipleship screens follow the account's first church role (core/access.py, not edited here). While the
    account works in another church they are neither offered nor answered: church A's data never shows under church B."""

    def test_they_answer_only_in_the_church_of_the_first_role(self):
        self.client.force_login(self.dual)
        self.assertEqual(self.client.get(reverse("church_settings")).status_code, 200)
        self.assertContains(self.client.get(reverse("community:home")), reverse("church_settings"))
        self.choose(self.church_b)
        for name in ("church_settings", "user_list", "church_dashboard", "participants_list", "path_list", "invitations_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 404, name)
        self.assertEqual(self.client.post(reverse("church_settings"), {"name": "Otra"}).status_code, 404)
        self.church.refresh_from_db()
        self.assertEqual(self.church.name, "Iglesia de prueba")
        home = self.client.get(reverse("community:home"))
        self.assertNotContains(home, reverse("church_settings"))
        # What does follow the request stays within reach, and is church B's
        self.assertContains(home, reverse("church_policies"))
        self.assertContains(home, reverse("sitio:edit"))
        for name in ("church_policies", "sitio:edit"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertNotContains(response, "Volver a Configuración", msg_prefix=name)
        self.choose(self.church)
        self.assertEqual(self.client.get(reverse("church_settings")).status_code, 200)
        self.assertContains(self.client.get(reverse("church_policies")), "Volver a Configuración")

    def test_the_dashboard_leads_to_the_chosen_church(self):
        participant = make_user("participa-a@prueba.mx", "Pablo", "Participa")
        assign_role(participant, self.church, Role.PARTICIPANT)
        self._person(self.church, participant)
        self._person(self.church_b, participant)
        self.client.force_login(participant)
        self.assertRedirects(self.client.get(reverse("dashboard")), reverse("participant_dashboard"), fetch_redirect_response=False)
        self.choose(self.church_b)
        self.assertRedirects(self.client.get(reverse("dashboard")), reverse("community:home"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("participant_dashboard")).status_code, 404)
        home = self.client.get(reverse("community:home"))
        self.assertNotContains(home, reverse("participant_dashboard"))
        self.assertNotContains(home, "Participante")  # the role of church A is not this church's
