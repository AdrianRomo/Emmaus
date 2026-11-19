"""Servir, wave 10: roles and what a date needs, the rotation's proposal, swaps and the order of a date."""

from datetime import date, datetime, time, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.db import OperationalError, connection
from django.db.models import QuerySet
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from people import flags
from people import services as people
from people.models import ChurchAuditEvent, Person
from serving import rotas, services
from serving.models import (
    Assignment, PlanItem, RoleNeed, RotationEntry, ServiceDate, ServiceReminder, SwapRequest, Team, TeamRole, Unavailable,
)

from .test_serving import ServingFixture

METHOD = Person.BirthDateMethod.values[0]


class RotasFixture(ServingFixture):
    """Bienvenida (Gala coordinates; Diego, Pía, Carlos and Nora serve) with the roles "Puerta" and
    "Niños" (adults only). ``plain``: a
    person of church A with an account and no team."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.carlos = cls.coord_person
        services.set_member(cls.team, cls.carlos, cls.admin)
        services.set_member(cls.team, cls.no_account, cls.admin)
        cls.door = rotas.save_role(cls.team, cls.leader_user, name="Puerta")
        cls.kids = rotas.save_role(cls.team, cls.leader_user, name="Niños", adults_only=True)
        cls.plain_user = cls.tutor1  # an account of church A that is in no team
        cls.plain = cls._person(cls.church, cls.plain_user)
        cls.far = cls.today + timedelta(days=3650)

    def need(self, day=None, **counts):
        rotas.set_needs(day or self.day, self.leader_user, {getattr(self, name).pk: count for name, count in counts.items()})

    def new_day(self, days: int, what="Otra fecha", team=None, hour=time(9, 30)) -> ServiceDate:
        return services.save_date(team or self.team, self.admin, date=self.today + timedelta(days=days), starts_at=hour, what=what)

    def adult(self, person):
        return people.verify_birth_date(person, self.admin, METHOD, date(1990, 1, 1))

    def held(self, day=None) -> dict:
        return dict(Assignment.objects.filter(service_date=day or self.day).values_list("person_id", "role_id"))


class RoleTests(RotasFixture):
    def test_a_date_says_what_it_needs_and_the_coordinator_sees_what_is_missing(self):
        self.client.force_login(self.leader_user)
        data = self.date_post(self.member, self.asked)
        data.update({f"necesita_{self.door.pk}": "2", f"necesita_{self.kids.pk}": "0", f"rol_{self.member.pk}": str(self.door.pk),
                     f"rol_{self.asked.pk}": ""})
        response = self.client.post(self.url("date_detail", self.day.pk), data)
        self.assertRedirects(response, self.url("team", self.team.pk))
        self.assertEqual(dict(RoleNeed.objects.filter(service_date=self.day).values_list("role_id", "count")), {self.door.pk: 2})
        self.assertEqual(self.held(), {self.member.pk: self.door.pk, self.asked.pk: None})
        page = self.client.get(self.url("team", self.team.pk))
        self.assertContains(page, "Puerta: ")
        self.assertContains(page, "1 persona de 2")
        self.assertContains(page, "Diego Hernández Cruz (Puerta)")
        form = self.client.get(self.url("date_detail", self.day.pk))
        self.assertContains(form, "van 1 de 2")
        # Who says no stops filling the place; who takes it fills it.
        services.respond(Assignment.objects.get(service_date=self.day, person=self.member), False, self.member_user)
        self.assertEqual(rotas.missing_by_date([self.day])[self.day.pk][0].missing, 2)
        services.assign(self.day, self.carlos, self.leader_user, role=self.door)
        services.assign(self.day, self.asked, self.leader_user, role=self.door)
        self.assertEqual(rotas.missing_by_date([self.day])[self.day.pk][0].missing, 0)
        self.assertContains(self.client.get(self.url("team", self.team.pk)), "Completo")

    def test_an_assignment_without_a_role_keeps_working_and_the_page_of_a_team_without_roles_is_as_before(self):
        row = self.assign()
        self.assertIsNone(row.role_id)
        self.client.force_login(self.leader_user)
        self.client.post(self.url("date_detail", self.day.pk), self.date_post(self.member, self.asked))  # no role fields at all
        self.assertEqual(self.held(), {self.member.pk: None, self.asked.pk: None})
        services.set_member(self.other_team, self.leader, self.admin, role="coordina")
        page = self.client.get(self.url("date_create", self.other_team.pk))
        self.assertNotContains(page, "necesita_")
        self.assertNotContains(page, "Rol de")

    def test_a_role_for_adults_takes_only_who_the_church_verified_as_adult(self):
        cases = []
        cases.append((self.member, "unknown"))
        Person.objects.filter(pk=self.asked.pk).update(birth_date=date(1990, 1, 1))  # typed by somebody, never verified
        cases.append((self.asked, "typed"))
        people.verify_birth_date(self.carlos, self.admin, METHOD, self.today - timedelta(days=365 * 16))
        cases.append((self.carlos, "minor"))
        for person, why in cases:
            person.refresh_from_db()
            with self.subTest(why), self.assertRaises(services.ServingError) as refused:
                services.assign(self.day, person, self.leader_user, role=self.kids)
            self.assertEqual(refused.exception.code, "adults_only")
            self.assertFalse(Assignment.objects.filter(service_date=self.day, person=person).exists())
            services.assign(self.day, person, self.leader_user, role=self.door)  # the open role takes them
        self.adult(self.leader)
        self.leader.refresh_from_db()
        self.assertEqual(services.assign(self.day, self.leader, self.leader_user, role=self.kids).role, self.kids)
        # Changing the role of somebody already asked goes through the same rule.
        with self.assertRaises(services.ServingError):
            services.assign(self.day, self.member, self.leader_user, role=self.kids)
        self.assertEqual(self.held()[self.member.pk], self.door.pk)

    def test_a_role_is_one_short_line_and_what_was_typed_is_escaped(self):
        for name, code in (("", "empty"), ("Uno\nDos", "lines"), ("x" * 61, "too_long"), ("puerta", "duplicate")):
            with self.subTest(name=name), self.assertRaises(services.ServingError) as refused:
                rotas.save_role(self.team, self.leader_user, name=name)
            self.assertEqual(refused.exception.code, code)
        self.client.force_login(self.leader_user)
        self.client.post(self.url("role_save", self.team.pk), {"nombre": "<script>alert(1)</script>"})
        self.assertTrue(TeamRole.objects.filter(team=self.team, name="<script>alert(1)</script>").exists())
        for url in (self.url("organize", self.team.pk), self.url("date_detail", self.day.pk)):
            page = self.client.get(url)
            self.assertNotContains(page, "<script>alert(1)</script>")
            self.assertContains(page, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_retiring_a_role_keeps_who_has_it_and_stops_asking_for_it(self):
        self.need(door=2)
        row = services.assign(self.day, self.member, self.leader_user, role=self.door)
        rotas.save_rotation(self.team, self.leader_user, every_weeks=1, orders={self.door.pk: [self.member.pk]})
        self.client.force_login(self.leader_user)
        self.client.post(self.url("role_retire", self.team.pk, self.door.pk))
        self.door.refresh_from_db()
        self.assertFalse(self.door.active)
        self.assertFalse(RoleNeed.objects.filter(role=self.door).exists())
        self.assertFalse(RotationEntry.objects.filter(role=self.door).exists())
        row.refresh_from_db()
        self.assertEqual(row.role, self.door)
        page = self.client.get(self.url("date_detail", self.day.pk))
        self.assertContains(page, "Puerta (ya no se usa)")
        # Saving the page again leaves the old word where it was, and nobody else can be given it.
        data = self.date_post(self.member, self.asked)
        data.update({f"rol_{self.member.pk}": str(self.door.pk), f"rol_{self.asked.pk}": str(self.door.pk)})
        response = self.client.post(self.url("date_detail", self.day.pk), data)
        self.assertContains(response, "Elige un rol de este equipo.")
        self.assertEqual(self.held(), {self.member.pk: self.door.pk})

    def test_the_settings_start_off_and_are_saved_by_who_coordinates(self):
        self.assertEqual((self.team.swap_needs_approval, self.team.reminder_days, self.team.rotation_every_weeks), (False, 0, 1))
        self.client.force_login(self.leader_user)
        self.client.post(self.url("settings_save", self.team.pk), {"visto_bueno": "si", "recordatorio": "5"})
        self.team.refresh_from_db()
        self.assertEqual((self.team.swap_needs_approval, self.team.reminder_days), (True, 5))
        self.client.post(self.url("settings_save", self.team.pk), {"recordatorio": "3"})  # not one of the choices
        self.team.refresh_from_db()
        self.assertEqual((self.team.swap_needs_approval, self.team.reminder_days), (True, 5))
        row = ChurchAuditEvent.objects.filter(action="equipo_ajustes").get()
        self.assertEqual(row.metadata, {"value": "visto_bueno,5"})


class RotationTests(RotasFixture):
    def setUp(self):
        self.days = [self.day] + [self.new_day(7 * week) for week in (2, 3, 4)]
        for day in self.days:
            self.need(day, door=1)
        self.order = [self.member, self.asked, self.carlos, self.no_account]
        rotas.save_rotation(self.team, self.leader_user, every_weeks=1, orders={self.door.pk: [person.pk for person in self.order]})

    def proposed(self, proposal=None) -> list:
        proposal = proposal or rotas.build_proposal(self.team)
        return [[slot.proposed for slot in entry.slots] for entry in proposal.dates]

    def confirm(self, proposal=None, **changes) -> int:
        proposal = proposal or rotas.build_proposal(self.team)
        choices = {slot.key: slot.proposed for entry in proposal.dates for slot in entry.slots}
        choices.update(changes)
        return rotas.confirm_proposal(self.team, self.leader_user, token=proposal.token, choices=choices)

    def test_it_proposes_in_order_and_assigns_nothing_until_confirmed(self):
        self.assertEqual(self.proposed(), [[person.pk] for person in self.order])
        self.client.force_login(self.leader_user)
        page = self.client.get(self.url("rotation", self.team.pk))
        self.assertContains(page, "Confirmar")
        self.assertContains(page, "Todavía no se le pide nada a nadie")
        self.assertFalse(Assignment.objects.filter(service_date__team=self.team).exists())
        proposal = page.context["proposal"]
        data = {"token": proposal.token}
        for entry in proposal.dates:
            for slot in entry.slots:
                data[f"lugar_{slot.key}"] = str(slot.proposed)
        # The coordinator changes one name and leaves one place empty.
        data[f"lugar_{proposal.dates[1].slots[0].key}"] = str(self.leader.pk)
        data[f"lugar_{proposal.dates[3].slots[0].key}"] = ""
        response = self.client.post(self.url("rotation", self.team.pk), data)
        self.assertRedirects(response, self.url("team", self.team.pk))
        self.assertEqual([self.held(day) for day in self.days],
                         [{self.member.pk: self.door.pk}, {self.leader.pk: self.door.pk}, {self.carlos.pk: self.door.pk}, {}])
        self.assertTrue(all(row.status == "pendiente" for row in Assignment.objects.all()))  # each person still answers
        self.assertEqual(ChurchAuditEvent.objects.filter(action="equipo_rotacion", summary__startswith="Confirmó").count(), 1)

    def test_the_proposal_is_idempotent_and_confirming_twice_adds_nothing(self):
        first, second = rotas.build_proposal(self.team), rotas.build_proposal(self.team)
        self.assertEqual((first.token, self.proposed(first)), (second.token, self.proposed(second)))
        mine = Assignment.objects.filter(service_date__team=self.team)
        self.assertEqual(self.confirm(first), 4)
        self.assertEqual(mine.count(), 4)
        self.assertEqual(self.proposed(), [])  # nothing is missing any more
        with self.assertRaises(rotas.StaleProposal):  # the same confirmation sent again
            self.confirm(first)
        self.assertEqual(self.confirm(), 0)
        self.assertEqual(mine.count(), 4)

    def test_it_never_overwrites_what_somebody_assigned_by_hand(self):
        by_hand = services.assign(self.days[0], self.carlos, self.leader_user, role=self.door)
        services.respond(by_hand, True, self.coord1)
        no_role = services.assign(self.days[1], self.asked, self.leader_user)  # asked, with no role: the place is still missing
        proposal = rotas.build_proposal(self.team)
        self.assertEqual([entry.service_date for entry in proposal.dates], self.days[1:])
        self.assertNotIn(self.asked.pk, [person.pk for person in proposal.dates[0].slots[0].options])
        self.confirm(proposal)
        by_hand.refresh_from_db()
        no_role.refresh_from_db()
        self.assertEqual((by_hand.person, by_hand.role, by_hand.status), (self.carlos, self.door, "aceptada"))
        self.assertIsNone(no_role.role_id)
        self.assertEqual(Assignment.objects.filter(service_date=self.days[0]).count(), 1)
        self.assertEqual(Assignment.objects.filter(service_date=self.days[1]).count(), 2)
        # The turn goes on after whoever had the role last, by hand or not.
        self.assertEqual(Assignment.objects.get(service_date=self.days[1], role=self.door).person, self.no_account)

    def test_it_skips_who_cannot_who_serves_elsewhere_and_a_record_joined_to_another(self):
        services.set_unavailable(self.member, self.days[0].date, self.member_user)
        services.set_member(self.other_team, self.asked, self.admin)
        elsewhere = self.new_day(7, team=self.other_team, hour=time(18, 0))
        services.assign(elsewhere, self.asked, self.admin)
        Person.objects.filter(pk=self.carlos.pk).update(merged_into=self.plain)
        proposal = rotas.build_proposal(self.team)
        self.assertEqual(self.proposed(proposal)[0], [self.no_account.pk])
        options = {person.pk for entry in proposal.dates for slot in entry.slots for person in slot.options}
        self.assertNotIn(self.carlos.pk, options)  # hidden: never proposed, never offered
        self.assertNotIn(self.carlos.pk, [pk for row in self.proposed(proposal) for pk in row])
        first = {person.pk for person in proposal.dates[0].slots[0].options}
        self.assertEqual(first, {self.leader.pk, self.no_account.pk})
        with self.assertRaises(services.ServingError):  # and cannot be forced in by a hand-made POST
            self.confirm(proposal, **{proposal.dates[0].slots[0].key: self.carlos.pk})
        self.assertFalse(Assignment.objects.filter(service_date__team=self.team).exists())
        Person.objects.filter(pk=self.carlos.pk).update(merged_into=None)

    def test_it_never_proposes_a_minor_or_an_unknown_age_for_a_role_for_adults(self):
        for day in self.days:
            self.need(day, kids=1)
        people.verify_birth_date(self.member, self.admin, METHOD, self.today - timedelta(days=365 * 15))
        self.adult(self.carlos)
        rotas.save_rotation(self.team, self.leader_user, every_weeks=1,
                            orders={self.kids.pk: [self.member.pk, self.asked.pk, self.carlos.pk]})
        proposal = rotas.build_proposal(self.team)
        self.assertEqual(self.proposed(proposal)[0], [self.carlos.pk])
        self.assertEqual({person.pk for entry in proposal.dates for slot in entry.slots for person in slot.options}, {self.carlos.pk})
        with self.assertRaises(services.ServingError):
            self.confirm(proposal, **{proposal.dates[0].slots[0].key: self.member.pk})
        with mock.patch.object(rotas, "excluded", return_value={}):  # were the rule removed, the minor would be proposed
            self.assertEqual(self.proposed()[0], [self.member.pk])

    def test_every_n_weeks_gives_each_person_a_rest(self):
        rotas.save_rotation(self.team, self.leader_user, every_weeks=3, orders={self.door.pk: [self.member.pk, self.asked.pk]})
        # Dates at weeks 1, 2, 3 and 4: Diego, Pía, nobody (both served less than three weeks before), Diego.
        self.assertEqual(self.proposed(), [[self.member.pk], [self.asked.pk], [None], [self.member.pk]])
        with self.assertRaises(services.ServingError):
            rotas.save_rotation(self.team, self.leader_user, every_weeks=0, orders={})
        with self.assertRaises(services.ServingError):  # somebody who is not in the team
            rotas.save_rotation(self.team, self.leader_user, every_weeks=1, orders={self.door.pk: [self.plain.pk]})

    def test_a_confirmation_of_a_page_that_changed_assigns_nothing_and_shows_it_again(self):
        self.client.force_login(self.leader_user)
        proposal = self.client.get(self.url("rotation", self.team.pk)).context["proposal"]
        data = {"token": proposal.token}
        for entry in proposal.dates:
            for slot in entry.slots:
                data[f"lugar_{slot.key}"] = str(slot.proposed)
        services.set_unavailable(self.member, self.days[0].date, self.member_user)  # between the page and the button
        response = self.client.post(self.url("rotation", self.team.pk), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Algo cambió")
        self.assertFalse(Assignment.objects.filter(service_date__team=self.team).exists())
        self.assertNotEqual(response.context["proposal"].token, proposal.token)
        # Without the token rule the stale page would have asked Diego for a day he cannot.
        self.assertNotIn(self.member.pk, {person.pk for person in response.context["proposal"].dates[0].slots[0].options})

    def test_one_person_cannot_be_chosen_twice_on_one_day(self):
        self.need(self.days[0], door=2)
        proposal = rotas.build_proposal(self.team)
        first, second = proposal.dates[0].slots
        with self.assertRaises(services.ServingError) as refused:
            self.confirm(proposal, **{first.key: self.member.pk, second.key: self.member.pk})
        self.assertEqual(refused.exception.code, "twice")
        self.assertFalse(Assignment.objects.filter(service_date__team=self.team).exists())

    def test_the_rotation_form_saves_the_order_typed(self):
        self.client.force_login(self.leader_user)
        data = {"cada": "2", f"orden_{self.door.pk}_{self.asked.pk}": "1", f"orden_{self.door.pk}_{self.member.pk}": "2",
                f"orden_{self.kids.pk}_{self.carlos.pk}": "1", f"orden_{self.door.pk}_{self.plain.pk}": "3"}
        self.client.post(self.url("rotation_save", self.team.pk), data)
        self.team.refresh_from_db()
        self.assertEqual(self.team.rotation_every_weeks, 2)
        self.assertEqual(rotas.rotation_orders(self.team), {self.door.pk: [self.asked.pk, self.member.pk], self.kids.pk: [self.carlos.pk]})
        page = self.client.get(self.url("organize", self.team.pk))
        self.assertContains(page, "No se califica a nadie")
        # Who leaves the team leaves its rotations.
        services.remove_member(self.team, self.asked, self.admin)
        self.assertEqual(rotas.rotation_orders(self.team)[self.door.pk], [self.member.pk])


class SwapTests(RotasFixture):
    def setUp(self):
        self.need(door=1)
        self.mine = services.assign(self.day, self.member, self.leader_user, role=self.door)

    def ask(self, to=None, now=None) -> SwapRequest:
        return rotas.request_swap(self.mine, to or self.asked, self.member_user, now=now)

    def owner(self) -> Person:
        return Assignment.objects.get(pk=self.mine.pk).person

    def test_the_date_moves_only_when_the_teammate_accepts_and_the_coordinator_sees_it(self):
        self.client.force_login(self.member_user)
        home = self.client.get(self.url("home"))
        self.assertContains(home, self.url("swap_create", self.mine.pk))
        page = self.client.get(self.url("swap_create", self.mine.pk))
        self.assertContains(page, "Pía Pidió")
        for hidden in ("pidio@prueba.mx", "tel:", "mailto:", "55 1111"):
            self.assertNotContains(page, hidden)
        response = self.client.post(self.url("swap_create", self.mine.pk), {"persona": self.asked.pk})
        self.assertRedirects(response, self.url("home") + "#cambios", fetch_redirect_response=False)
        swap = SwapRequest.objects.get()
        self.assertEqual((swap.from_person, swap.to_person, swap.status, self.owner()), (self.member, self.asked, "pendiente", self.member))
        self.assertContains(self.client.get(self.url("home")), "Todavía no responde")

        self.client.force_login(self.asked_user)
        home = self.client.get(self.url("home"))
        self.assertContains(home, "Diego Hernández Cruz te pide tomar su fecha")
        self.assertContains(home, "Rol: Puerta.")
        self.client.post(self.url("swap_answer", swap.pk), {"respuesta": "aceptar"})
        row = Assignment.objects.get(pk=self.mine.pk)
        self.assertEqual((row.person, row.status, row.role), (self.asked, "aceptada", self.door))
        self.assertEqual(SwapRequest.objects.get().status, "hecha")
        self.assertContains(self.client.get(self.url("home")), "Confirmaste")

        self.client.force_login(self.leader_user)
        team = self.client.get(self.url("team", self.team.pk))
        self.assertContains(team, "Diego Hernández Cruz → Pía Pidió")
        self.assertContains(team, "la fecha ya es de Pía")
        actions = list(ChurchAuditEvent.objects.filter(action="equipo_cambio").values_list("metadata__value", flat=True))
        self.assertEqual(sorted(actions), ["hecho", "pedido"])
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(self.url("home")), "Aceptó: la fecha ya no es tuya.")

    def test_declining_needs_no_reason_and_leaves_the_date_where_it_was(self):
        swap = self.ask()
        self.client.force_login(self.asked_user)
        self.client.post(self.url("swap_answer", swap.pk), {"respuesta": "declinar", "motivo": "No quiero"})
        swap.refresh_from_db()
        self.assertEqual((swap.status, self.owner()), ("declinada", self.member))
        self.assertNotIn("No quiero", str(list(SwapRequest.objects.values())) + str(list(ChurchAuditEvent.objects.values())))
        self.assertEqual({field.name for field in SwapRequest._meta.fields},
                         {"id", "assignment", "from_person", "to_person", "status", "created_at", "responded_at", "decided_at", "decided_by"})
        self.ask(self.carlos)  # and the asker can ask somebody else

    def test_with_the_team_setting_the_coordinator_has_the_last_word(self):
        rotas.save_settings(self.team, self.leader_user, swap_needs_approval=True, reminder_days=0)
        swap = self.ask()
        self.client.force_login(self.asked_user)
        self.client.post(self.url("swap_answer", swap.pk), {"respuesta": "aceptar"})
        swap.refresh_from_db()
        self.assertEqual((swap.status, self.owner()), ("por_aprobar", self.member))
        self.client.force_login(self.leader_user)
        self.assertContains(self.client.get(self.url("team", self.team.pk)), "Dar el visto bueno")
        self.client.post(self.url("swap_decide", swap.pk), {"decision": "aprobar"})
        swap.refresh_from_db()
        self.assertEqual((swap.status, swap.decided_by, self.owner()), ("hecha", self.leader_user, self.asked))
        with self.assertRaises(services.ServingError):  # decided twice
            rotas.decide_swap(swap, False, self.leader_user)
        self.assertEqual(self.owner(), self.asked)

    def test_a_no_from_the_coordinator_leaves_the_date_where_it_was(self):
        rotas.save_settings(self.team, self.leader_user, swap_needs_approval=True, reminder_days=0)
        swap = rotas.answer_swap(self.ask(), True, self.asked_user)
        rotas.decide_swap(swap, False, self.leader_user)
        swap.refresh_from_db()
        self.assertEqual((swap.status, self.owner()), ("rechazada", self.member))

    def test_accepting_twice_changes_nothing_the_second_time(self):
        swap = self.ask()
        stale = SwapRequest.objects.get(pk=swap.pk)  # what a second tab still holds
        rotas.answer_swap(swap, True, self.asked_user)
        # Meanwhile the coordinator gave the date to somebody else by hand.
        Assignment.objects.filter(pk=self.mine.pk).update(person=self.carlos)
        for answer in (True, False):
            with self.assertRaises(services.ServingError) as refused:
                rotas.answer_swap(stale, answer, self.asked_user)
            self.assertEqual(refused.exception.code, "gone")
        self.assertEqual((self.owner(), SwapRequest.objects.get().status), (self.carlos, "hecha"))
        self.client.force_login(self.asked_user)
        self.client.post(self.url("swap_answer", swap.pk), {"respuesta": "aceptar"})
        self.assertEqual(self.owner(), self.carlos)

    def test_a_request_expires_when_the_date_starts_or_after_seven_days(self):
        swap = self.ask()
        started = rotas.start_instant(self.day) + timedelta(minutes=1)
        with self.assertRaises(services.ServingError) as refused:
            rotas.answer_swap(swap, True, self.asked_user, now=started)
        self.assertEqual(refused.exception.code, "expired")
        swap.refresh_from_db()
        self.assertEqual((swap.status, self.owner()), ("vencida", self.member))

        far = self.new_day(30)
        mine = services.assign(far, self.member, self.leader_user)
        old = rotas.request_swap(mine, self.asked, self.member_user)
        week_later = timezone.now() + timedelta(days=7, minutes=1)
        self.assertTrue(rotas.is_live(old, timezone.now() + timedelta(days=6)))
        self.assertEqual(rotas.swaps_to_answer(self.asked, week_later), [])
        with self.assertRaises(services.ServingError):
            rotas.answer_swap(old, True, self.asked_user, now=week_later)
        self.assertEqual(Assignment.objects.get(pk=mine.pk).person, self.member)
        # An expired request does not block a new one.
        with mock.patch("django.utils.timezone.now", return_value=week_later):
            again = rotas.request_swap(mine, self.carlos, self.member_user)
        self.assertEqual(again.status, "pendiente")

    def test_a_teammate_who_meanwhile_cannot_is_refused_at_acceptance(self):
        swap = self.ask()
        day_off = services.set_unavailable(self.asked, self.day.date, self.asked_user)
        with self.assertRaises(services.ServingError) as refused:
            rotas.answer_swap(swap, True, self.asked_user)
        self.assertEqual(refused.exception.code, "unavailable")
        self.assertEqual(self.owner(), self.member)
        services.clear_unavailable(day_off, self.asked_user)

        services.set_member(self.other_team, self.asked, self.admin)
        elsewhere = services.assign(self.new_day(7, team=self.other_team, hour=time(17, 0)), self.asked, self.admin)
        with self.assertRaises(services.ServingError) as refused:
            rotas.answer_swap(swap, True, self.asked_user)
        self.assertEqual(refused.exception.code, "busy")
        self.assertEqual(self.owner(), self.member)
        swap.refresh_from_db()
        self.assertEqual(swap.status, "pendiente")  # nothing was closed: once free again, the answer is taken
        services.respond(elsewhere, False, self.asked_user)
        self.assertEqual(rotas.answer_swap(swap, True, self.asked_user).status, "hecha")
        self.assertEqual(self.owner(), self.asked)

    def test_the_same_check_runs_again_when_the_coordinator_approves(self):
        rotas.save_settings(self.team, self.leader_user, swap_needs_approval=True, reminder_days=0)
        swap = rotas.answer_swap(self.ask(), True, self.asked_user)
        services.remove_member(self.team, self.asked, self.admin)
        swap.refresh_from_db()
        self.assertEqual(swap.status, "cancelada")
        with self.assertRaises(services.ServingError):
            rotas.decide_swap(swap, True, self.leader_user)
        self.assertEqual(self.owner(), self.member)

    def test_only_who_could_take_the_date_is_offered(self):
        services.set_unavailable(self.asked, self.day.date, self.asked_user)
        Person.objects.filter(pk=self.no_account.pk).update(merged_into=self.plain)
        Person.objects.filter(pk=self.carlos.pk).update(birth_date=self.today - timedelta(days=365 * 16))  # a minor, verified or not
        self.assertEqual(rotas.swap_candidates(self.mine), [self.leader])
        for person in (self.asked, self.no_account, self.carlos, self.plain, self.member, self.member_b):
            with self.subTest(person=person.first_name), self.assertRaises(services.ServingError):
                self.ask(person)
        self.client.force_login(self.member_user)
        self.client.post(self.url("swap_create", self.mine.pk), {"persona": self.asked.pk})
        self.assertFalse(SwapRequest.objects.exists())
        Person.objects.filter(pk=self.no_account.pk).update(merged_into=None)
        # A role for adults: only who the church verified.
        kids = services.assign(self.new_day(14), self.member, self.leader_user)
        Assignment.objects.filter(pk=kids.pk).update(role=self.kids)
        kids.refresh_from_db()
        self.assertEqual(rotas.swap_candidates(kids), [])
        self.adult(self.leader)
        self.assertEqual(rotas.swap_candidates(kids), [self.leader])

    def test_a_person_the_record_says_is_a_minor_arranges_it_with_the_coordinator(self):
        Person.objects.filter(pk=self.member.pk).update(birth_date=self.today - timedelta(days=365 * 16))
        self.client.force_login(self.member_user)
        self.assertNotContains(self.client.get(self.url("home")), "Pedir un cambio")
        self.assertEqual(self.client.get(self.url("swap_create", self.mine.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url("swap_create", self.mine.pk), {"persona": self.asked.pk}).status_code, 404)
        self.mine.refresh_from_db()
        with self.assertRaises(services.ServingError):
            self.ask()

    def test_one_request_at_a_time_and_who_asked_can_take_it_back(self):
        swap = self.ask()
        with self.assertRaises(services.ServingError) as refused:
            self.ask(self.carlos)
        self.assertEqual(refused.exception.code, "open")
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(self.url("swap_create", self.mine.pk)), "Ya le pediste este cambio a Pía Pidió")
        self.client.post(self.url("swap_cancel", swap.pk))
        swap.refresh_from_db()
        self.assertEqual(swap.status, "cancelada")
        with self.assertRaises(services.ServingError):
            rotas.answer_swap(swap, True, self.asked_user)
        self.assertEqual(self.owner(), self.member)
        self.ask(self.carlos)

    def test_who_said_no_to_the_date_or_lost_it_has_nothing_to_hand_over(self):
        swap = self.ask()
        services.respond(self.mine, False, self.member_user)
        with self.assertRaises(services.ServingError):
            rotas.answer_swap(swap, True, self.asked_user)
        swap.refresh_from_db()
        self.assertEqual((swap.status, self.owner()), ("cancelada", self.member))
        self.mine.refresh_from_db()
        with self.assertRaises(services.ServingError):
            self.ask()
        # Taking somebody off a date takes their request with it.
        services.respond(self.mine, True, self.member_user)
        self.mine.refresh_from_db()
        self.ask(self.carlos)
        services.unassign(self.day, self.member, self.leader_user)
        self.assertFalse(SwapRequest.objects.filter(status="pendiente").exists())

    def test_moving_the_date_to_another_day_closes_the_request(self):
        swap = self.ask()
        services.save_date(self.team, self.leader_user, service_date=self.day, date=self.week + timedelta(days=1), what=self.day.what)
        swap.refresh_from_db()
        self.assertEqual(swap.status, "cancelada")

    def test_nobody_outside_the_two_people_and_who_coordinates_reaches_a_request(self):
        rotas.save_settings(self.team, self.leader_user, swap_needs_approval=True, reminder_days=0)
        swap = self.ask()
        services.set_member(self.other_team, self.carlos, self.admin, role="coordina")  # coordinates another team, serves in this one
        answer, cancel, decide = (self.url(name, swap.pk) for name in ("swap_answer", "swap_cancel", "swap_decide"))
        for user, expected in ((self.plain_user, (404, 404, 403)), (self.coord1, (404, 404, 403)),
                               (self.member_b_user, (404, 404, 404)), (self.admin_b, (404, 404, 404)),
                               (self.member_user, (404, None, 403)), (self.asked_user, (None, 404, 403))):
            self.client.force_login(user)
            for url, status, data in zip((answer, cancel, decide), expected,
                                         ({"respuesta": "aceptar"}, {}, {"decision": "aprobar"})):
                if status is not None:
                    self.assertEqual(self.client.post(url, data).status_code, status, (user.email, url))
            home = self.client.get(self.url("home"))
            if user not in (self.member_user, self.asked_user) and home.status_code == 200:
                self.assertNotContains(home, "te pide tomar su fecha")
                self.assertNotContains(home, "Le pediste a")
        swap.refresh_from_db()
        self.assertEqual((swap.status, self.owner()), ("pendiente", self.member))
        # Carlos serves in the team: he still sees nothing of a request between two teammates.
        self.client.force_login(self.coord1)
        self.assertNotContains(self.client.get(self.url("home")), "Pía")
        # The administration manages every team of its church, this one included.
        rotas.answer_swap(swap, True, self.asked_user)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url("team", self.team.pk)), "Espera tu visto bueno")
        self.assertEqual(self.client.post(decide, {"decision": "otra"}).status_code, 404)
        self.assertEqual(self.client.post(decide, {"decision": "aprobar"}).status_code, 302)
        self.assertEqual(self.owner(), self.asked)

    def test_a_reminder_already_sent_was_the_other_persons(self):
        ServiceReminder.objects.create(assignment=self.mine, outcome="sent", attempts=1)
        rotas.answer_swap(self.ask(), True, self.asked_user)
        self.assertFalse(ServiceReminder.objects.exists())

    def test_moments_are_compared_as_instants_in_a_zone_with_clock_changes(self):
        zone = ZoneInfo("America/New_York")
        with timezone.override(zone):
            # 8 March 2026: clocks go from 2:00 to 3:00. A date at 2:30, an hour that does not exist, still has an instant.
            gap = ServiceDate(team=self.team, date=date(2026, 3, 8), starts_at=time(2, 30), what="x")
            self.assertEqual(rotas.start_instant(gap).astimezone(ZoneInfo("UTC")).date(), date(2026, 3, 8))
            late = ServiceDate(team=self.team, date=date(2026, 11, 1), starts_at=time(23, 0), what="x")
            before = datetime(2026, 11, 1, 22, 0, tzinfo=zone)  # already 2 November in UTC
            self.assertTrue(rotas.is_coming(late, before))
            self.assertFalse(rotas.is_coming(late, before + timedelta(hours=1, minutes=1)))
            whole_day = ServiceDate(team=self.team, date=date(2026, 11, 1), starts_at=None, what="x")
            self.assertTrue(rotas.is_coming(whole_day, datetime(2026, 11, 1, 23, 59, tzinfo=zone)))
            self.assertFalse(rotas.is_coming(whole_day, datetime(2026, 11, 2, 0, 1, tzinfo=zone)))


class PlanTests(RotasFixture):
    def setUp(self):
        self.mine = services.assign(self.day, self.member, self.leader_user)
        self.plan_url = self.url("plan", self.day.pk)

    def add(self, title="Bienvenida", minutes=10, responsible=None, day=None) -> PlanItem:
        return rotas.save_plan_item(day or self.day, self.leader_user, title=title, minutes=minutes, responsible=responsible)

    def test_the_coordinator_writes_the_order_and_who_serves_that_day_reads_it(self):
        self.client.force_login(self.leader_user)
        page = self.client.get(self.plan_url)
        self.assertContains(page, "No lleva canciones, letras, acordes, archivos ni enlaces")
        self.client.post(self.plan_url, {"titulo": "Bienvenida", "minutos": "10", "responsable": str(self.member.pk)})
        self.client.post(self.plan_url, {"titulo": "Anuncios", "minutos": "5", "responsable": ""})
        self.client.post(self.plan_url, {"titulo": "Mensaje", "minutos": "40"})
        first, second, third = PlanItem.objects.order_by("position")
        self.assertEqual([(item.title, item.minutes, item.responsible) for item in (first, second, third)],
                         [("Bienvenida", 10, self.member), ("Anuncios", 5, None), ("Mensaje", 40, None)])
        self.client.post(self.url("plan_item", self.day.pk, third.pk), {"accion": "subir"})
        self.client.post(self.url("plan_item", self.day.pk, first.pk), {"accion": "quitar"})
        self.client.post(self.url("plan_item", self.day.pk, second.pk), {"accion": "guardar", "titulo": "Avisos", "minutos": "7", "responsable": str(self.leader.pk)})
        self.assertEqual(list(PlanItem.objects.order_by("position").values_list("title", "minutes", "responsible_id")),
                         [("Mensaje", 40, None), ("Avisos", 7, self.leader.pk)])
        self.assertContains(self.client.get(self.plan_url), "2 momentos, 47 minutos en total")

        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(self.url("home")), self.plan_url)
        page = self.client.get(self.plan_url)
        self.assertContains(page, "A cargo: Gala Guía")
        for hidden in ("guia@prueba.mx", "tel:", "mailto:", 'name="titulo"', "Agregar un momento", "Quitar"):
            self.assertNotContains(page, hidden)

    def test_only_who_serves_that_date_reads_it_and_only_who_coordinates_writes(self):
        item = self.add()
        other = services.assign(self.day, self.asked, self.leader_user)
        services.respond(other, False, self.asked_user)  # said no: not their date any more
        post = {"titulo": "Otro", "minutos": "5"}
        item_url, copy_url = self.url("plan_item", self.day.pk, item.pk), self.url("plan_copy", self.day.pk)
        for user, get_status, post_status in ((self.member_user, 200, 403), (self.asked_user, 403, 403), (self.coord1, 403, 403),
                                              (self.plain_user, 403, 403), (self.member_b_user, 404, 404), (self.admin_b, 404, 404),
                                              (self.leader_user, 200, 302), (self.admin, 200, 302)):
            self.client.force_login(user)
            with self.subTest(user=user.email):
                self.assertEqual(self.client.get(self.plan_url).status_code, get_status)
                self.assertEqual(self.client.post(self.plan_url, post).status_code, post_status)
                self.assertEqual(self.client.post(item_url, {"accion": "guardar", **post}).status_code, post_status)
                self.assertEqual(self.client.post(copy_url).status_code, post_status)
        # An item of another date cannot be reached through this date's address.
        elsewhere = self.add(day=self.new_day(14))
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.post(self.url("plan_item", self.day.pk, elsewhere.pk), {"accion": "quitar"}).status_code, 404)
        self.assertTrue(PlanItem.objects.filter(pk=elsewhere.pk).exists())
        # A date that passed, and one of an archived team, are closed.
        self.move(self.day, -1)
        self.assertEqual(self.client.get(self.plan_url).status_code, 404)

    def test_no_links_no_long_text_and_what_was_typed_is_escaped(self):
        for title, minutes, code in (("Canto: https://ejemplo.mx/letra", 5, "link"), ("www.acordes.mx", 5, "link"), ("", 5, "empty"),
                                     ("a\nb", 5, "lines"), ("x" * 81, 5, "too_long"), ("Canto", 0, "minutes"), ("Canto", 301, "minutes"),
                                     ("Canto", None, "minutes")):
            with self.subTest(title=title, minutes=minutes), self.assertRaises(services.ServingError) as refused:
                self.add(title, minutes)
            self.assertEqual(refused.exception.code, code)
        self.assertFalse(PlanItem.objects.exists())
        self.add("<b>Mensaje</b>")
        for user in (self.leader_user, self.member_user):
            self.client.force_login(user)
            page = self.client.get(self.plan_url)
            self.assertNotContains(page, "<b>Mensaje</b>")
            self.assertContains(page, "&lt;b&gt;Mensaje&lt;/b&gt;")
        self.assertNotIn("Mensaje", str(list(ChurchAuditEvent.objects.filter(action="equipo_orden").values())))

    def test_who_is_in_charge_is_of_the_team_and_never_somebody_the_record_says_is_a_minor(self):
        Person.objects.filter(pk=self.asked.pk).update(birth_date=self.today - timedelta(days=365 * 15))
        for person in (self.plain, self.member_b, self.asked):
            with self.subTest(person=person.first_name), self.assertRaises(services.ServingError) as refused:
                self.add(responsible=person)
            self.assertEqual(refused.exception.code, "responsible")
        self.client.force_login(self.leader_user)
        self.assertNotContains(self.client.get(self.plan_url), "Pía Pidió")
        response = self.client.post(self.plan_url, {"titulo": "Canto", "minutos": "5", "responsable": str(self.member_b.pk)})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(PlanItem.objects.exists())

    def test_copying_the_previous_order_only_into_an_empty_one(self):
        self.add("Bienvenida", 10, self.member)
        self.add("Mensaje", 40, self.asked)
        later = self.new_day(14)
        services.remove_member(self.team, self.asked, self.admin)
        self.client.force_login(self.leader_user)
        page = self.client.get(self.url("plan", later.pk))
        self.assertContains(page, "Copiar del anterior")
        self.client.post(self.url("plan_copy", later.pk))
        self.assertEqual(list(PlanItem.objects.filter(service_date=later).values_list("position", "title", "minutes", "responsible_id")),
                         [(1, "Bienvenida", 10, self.member.pk), (2, "Mensaje", 40, None)])  # who left the team is not in charge
        self.assertNotContains(self.client.get(self.url("plan", later.pk)), "Copiar del anterior")
        with self.assertRaises(services.ServingError) as refused:
            rotas.copy_previous_plan(later, self.leader_user)
        self.assertEqual(refused.exception.code, "not_empty")
        self.assertEqual(PlanItem.objects.filter(service_date=later).count(), 2)
        with self.assertRaises(services.ServingError) as refused:  # the first date has nothing before it
            PlanItem.objects.filter(service_date=self.day).delete()
            rotas.copy_previous_plan(self.day, self.leader_user)
        self.assertEqual(refused.exception.code, "no_previous")


class AccessTests(RotasFixture):
    """Every new address, for everybody who must not get in."""

    def setUp(self):
        self.need(door=1)
        self.mine = services.assign(self.day, self.member, self.leader_user, role=self.door)
        self.item = rotas.save_plan_item(self.day, self.leader_user, title="Bienvenida", minutes=5)
        self.swap = rotas.request_swap(self.mine, self.asked, self.member_user)
        team, day = self.team.pk, self.day.pk
        # (name, args, POST data, is it a page that also answers GET)
        self.coordinator_only = [
            ("organize", (team,), None), ("role_save", (team,), {"nombre": "Sonido"}), ("role_retire", (team, self.door.pk), {}),
            ("rotation_save", (team,), {"cada": "2"}), ("settings_save", (team,), {"visto_bueno": "si", "recordatorio": "2"}),
            ("rotation", (team,), None), ("rotation", (team,), {"token": "x"}),
            ("plan", (day,), {"titulo": "Otro", "minutos": "5"}), ("plan_item", (day, self.item.pk), {"accion": "quitar"}),
            ("plan_copy", (day,), {}),
        ]
        self.personal = [
            ("swap_create", (self.mine.pk,), None), ("swap_create", (self.mine.pk,), {"persona": self.asked.pk}),
            ("swap_answer", (self.swap.pk,), {"respuesta": "aceptar"}), ("swap_cancel", (self.swap.pk,), {}),
        ]

    def hit(self, name, args, data):
        url = self.url(name, *args)
        return self.client.get(url) if data is None else self.client.post(url, data)

    def unchanged(self):
        self.team.refresh_from_db()
        self.assertEqual((self.team.swap_needs_approval, self.team.reminder_days, self.team.rotation_every_weeks), (False, 0, 1))
        self.assertEqual(set(TeamRole.objects.filter(team=self.team, active=True)), {self.door, self.kids})
        self.assertEqual(list(PlanItem.objects.values_list("title", flat=True)), ["Bienvenida"])
        self.assertEqual(SwapRequest.objects.get().status, "pendiente")
        self.assertEqual(Assignment.objects.get(pk=self.mine.pk).person, self.member)
        self.assertEqual(Assignment.objects.count(), 2)  # the fixture's of church B and this one

    def test_signed_out_goes_to_sign_in(self):
        for name, args, data in [*self.coordinator_only, *self.personal, ("swap_decide", (self.swap.pk,), {"decision": "aprobar"}),
                                 ("reminders_choice", (), {"recordatorios": "no"}), ("plan", (self.day.pk,), None)]:
            response = self.hit(name, args, data)
            self.assertEqual(response.status_code, 302, name)
            self.assertTrue(response["Location"].startswith(reverse("login")), name)
        self.unchanged()

    def test_who_only_serves_a_plain_member_and_the_coordinator_of_another_team_get_403(self):
        services.set_member(self.other_team, self.plain, self.admin, role="coordina")
        for user in (self.member_user, self.asked_user, self.coord1, self.plain_user):
            self.client.force_login(user)
            for name, args, data in [*self.coordinator_only, ("swap_decide", (self.swap.pk,), {"decision": "aprobar"})]:
                if name == "plan" and data is None:
                    continue
                self.assertEqual(self.hit(name, args, data).status_code, 403, (user.email, name))
        for user in (self.coord1, self.plain_user, self.leader_user):  # not theirs: a 404, as an id that does not exist
            self.client.force_login(user)
            for name, args, data in self.personal:
                self.assertEqual(self.hit(name, args, data).status_code, 404, (user.email, name))
        self.unchanged()

    def test_another_church_is_a_404_everywhere(self):
        for user in (self.member_b_user, self.admin_b):
            self.client.force_login(user)
            for name, args, data in [*self.coordinator_only, *self.personal, ("swap_decide", (self.swap.pk,), {"decision": "aprobar"}),
                                     ("plan", (self.day.pk,), None)]:
                self.assertEqual(self.hit(name, args, data).status_code, 404, (user.email, name))
        # And the other way: church A's coordinator and administration on church B's team and date.
        role_b = rotas.save_role(self.team_b, self.admin_b, name="Puerta B")
        for user in (self.leader_user, self.admin):
            self.client.force_login(user)
            for name, args, data in (("organize", (self.team_b.pk,), None), ("rotation", (self.team_b.pk,), None),
                                     ("role_save", (self.team_b.pk,), {"nombre": "X"}), ("role_retire", (self.team_b.pk, role_b.pk), {}),
                                     ("role_retire", (self.team.pk, role_b.pk), {}), ("role_save", (self.team.pk,), {"rol": role_b.pk, "nombre": "X"}),
                                     ("plan", (self.day_b.pk,), None), ("plan", (self.day_b.pk,), {"titulo": "X", "minutos": "5"}),
                                     ("swap_create", (self.assignment_b.pk,), None)):
                self.assertEqual(self.hit(name, args, data).status_code, 404, (user.email, name))
        role_b.refresh_from_db()
        self.assertEqual((role_b.name, role_b.active), ("Puerta B", True))
        self.unchanged()

    def test_with_the_module_off_everything_is_a_404(self):
        flags.set_enabled(self.church, "community", False)
        for user in (self.leader_user, self.admin, self.member_user):
            self.client.force_login(user)
            for name, args, data in [*self.coordinator_only, *self.personal, ("swap_decide", (self.swap.pk,), {"decision": "aprobar"}),
                                     ("reminders_choice", (), {"recordatorios": "no"}), ("plan", (self.day.pk,), None)]:
                self.assertEqual(self.hit(name, args, data).status_code, 404, (user.email, name))
        self.unchanged()

    def test_an_archived_team_is_closed_to_who_coordinated_it_and_to_the_administration(self):
        services.archive_team(self.team, self.admin)
        for user in (self.leader_user, self.admin):
            self.client.force_login(user)
            for name, args, data in [*self.coordinator_only, ("swap_decide", (self.swap.pk,), {"decision": "aprobar"})]:
                self.assertEqual(self.hit(name, args, data).status_code, 404, (user.email, name))
        self.client.force_login(self.asked_user)
        self.client.post(self.url("swap_answer", self.swap.pk), {"respuesta": "aceptar"})
        self.assertEqual(Assignment.objects.get(pk=self.mine.pk).person, self.member)

    def test_every_post_needs_its_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        posts = [(name, args, data) for name, args, data in [*self.coordinator_only, *self.personal] if data is not None]
        posts += [("swap_decide", (self.swap.pk,), {"decision": "aprobar"}), ("reminders_choice", (), {"recordatorios": "no"})]
        for user in (self.leader_user, self.member_user, self.asked_user):
            client.force_login(user)
            for name, args, data in posts:
                self.assertEqual(client.post(self.url(name, *args), data).status_code, 403, name)
        self.unchanged()

    def test_get_changes_nothing_on_the_addresses_that_only_take_post(self):
        self.client.force_login(self.leader_user)
        for name, args in (("role_save", (self.team.pk,)), ("role_retire", (self.team.pk, self.door.pk)), ("rotation_save", (self.team.pk,)),
                           ("settings_save", (self.team.pk,)), ("plan_copy", (self.day.pk,)), ("plan_item", (self.day.pk, self.item.pk)),
                           ("swap_decide", (self.swap.pk,)), ("reminders_choice", ())):
            self.assertEqual(self.client.get(self.url(name, *args)).status_code, 405, name)
        self.unchanged()

    def test_a_past_date_cannot_be_handed_over(self):
        ServiceDate.objects.filter(pk=self.day.pk).update(date=self.today - timedelta(days=1))
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(self.url("swap_create", self.mine.pk)).status_code, 404)
        self.assertFalse(Unavailable.objects.filter(person=self.member).exists())


class ReadTimeTests(RotasFixture):
    """What the review of wave 10 found: eligibility is read when a page is read, not kept from the day of the assignment."""

    def minor(self, person):
        return people.verify_birth_date(person, self.admin, METHOD, date(self.today.year - 15, 1, 1))

    def test_making_a_role_adults_only_lists_who_holds_it_and_needs_a_confirmation(self):
        self.need(door=1)
        self.minor(self.member)
        self.member.refresh_from_db()
        mine = services.assign(self.day, self.member, self.leader_user, role=self.door)
        past = self.new_day(3)
        old = services.assign(past, self.member, self.leader_user, role=self.door)
        self.move(past, -2)
        self.client.force_login(self.leader_user)
        data = {"rol": self.door.pk, "nombre": "Puerta", "solo_adultos": "si"}
        page = self.client.post(self.url("role_save", self.team.pk), data)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Diego Hernández Cruz")
        self.assertContains(page, "queda sin rol")
        self.door.refresh_from_db()
        mine.refresh_from_db()
        self.assertEqual((self.door.adults_only, mine.role), (False, self.door))  # nothing changed yet
        # A confirmation of a list that changed meanwhile shows the new list instead of saving.
        services.assign(self.day, self.asked, self.leader_user, role=self.door)
        again = self.client.post(self.url("role_save", self.team.pk), {**data, "confirmar": page.context["token"]})
        self.assertEqual(again.status_code, 200)
        self.assertContains(again, "Pía Pidió")
        self.assertFalse(TeamRole.objects.get(pk=self.door.pk).adults_only)
        response = self.client.post(self.url("role_save", self.team.pk), {**data, "confirmar": again.context["token"]})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TeamRole.objects.get(pk=self.door.pk).adults_only)
        self.assertEqual(self.held(), {self.member.pk: None, self.asked.pk: None})  # they keep the date, without the role
        old.refresh_from_db()
        self.assertEqual(old.role, self.door)  # what already happened stays as it was
        self.assertEqual(rotas.missing_by_date([self.day])[self.day.pk][0].missing, 1)
        # Nobody holds it now: saving again asks nothing.
        self.assertEqual(self.client.post(self.url("role_save", self.team.pk), data).status_code, 302)

    def test_an_age_corrected_later_is_flagged_when_the_coordinator_reads_with_no_hook(self):
        self.need(kids=1)
        self.adult(self.asked)
        self.asked.refresh_from_db()
        row = services.assign(self.day, self.asked, self.leader_user, role=self.kids)
        self.client.force_login(self.leader_user)
        self.assertNotContains(self.client.get(self.url("team", self.team.pk)), "Ya no puede cubrir este rol")
        self.assertEqual(rotas.missing_by_date([self.day])[self.day.pk][0].missing, 0)
        self.minor(self.asked)  # people.services, which knows nothing about Servir
        row.refresh_from_db()
        self.assertEqual(row.role, self.kids)  # nothing ran
        self.assertContains(self.client.get(self.url("team", self.team.pk)), "Ya no puede cubrir este rol")
        self.assertContains(self.client.get(self.url("date_detail", self.day.pk)), "Ya no puede cubrir este rol")
        self.assertEqual(rotas.missing_by_date([self.day])[self.day.pk][0].missing, 1)  # the place is missing again
        self.adult(self.leader)
        rotas.save_rotation(self.team, self.leader_user, every_weeks=1, orders={self.kids.pk: [self.leader.pk]})
        self.assertEqual(rotas.build_proposal(self.team).dates[0].slots[0].proposed, self.leader.pk)
        # A record joined to another is flagged too; a date typed by the person (agenda) for an unknown age as well.
        services.assign(self.day, self.member, self.leader_user, role=self.door)
        Person.objects.filter(pk=self.member.pk).update(merged_into=self.plain)
        self.assertContains(self.client.get(self.url("team", self.team.pk)), "Ya no puede cubrir este rol", count=2)
        Person.objects.filter(pk=self.member.pk).update(merged_into=None)

    def test_the_order_never_names_somebody_who_can_no_longer_be_named(self):
        services.assign(self.day, self.asked, self.leader_user)
        for title, person in (("Bienvenida", self.member), ("Anuncios", self.carlos), ("Cierre", self.no_account)):
            rotas.save_plan_item(self.day, self.leader_user, title=title, minutes=5, responsible=person)
        self.minor(self.member)
        Person.objects.filter(pk=self.no_account.pk).update(merged_into=self.plain)
        services.remove_member(self.team, self.carlos, self.admin)
        self.assertIsNone(PlanItem.objects.get(title="Anuncios").responsible_id)  # cleared when they left
        self.assertEqual(PlanItem.objects.get(title="Bienvenida").responsible, self.member)  # nothing ran for the age: read time
        self.assertEqual([item.responsible for item in rotas.plan_items(self.day)], [None, None, None])
        for user in (self.asked_user, self.leader_user):
            self.client.force_login(user)
            page = self.client.get(self.url("plan", self.day.pk))
            for hidden in ("Diego", "Carla", "Nora", "selected"):
                self.assertNotContains(page, hidden)
        self.assertContains(page, "Bienvenida")
        self.client.force_login(self.asked_user)
        self.assertContains(self.client.get(self.url("plan", self.day.pk)), "Sin responsable", count=3)
        Person.objects.filter(pk=self.no_account.pk).update(merged_into=None)

    def test_a_request_of_who_said_no_is_closed_and_never_offered(self):
        mine = services.assign(self.day, self.member, self.leader_user)
        swap = rotas.request_swap(mine, self.asked, self.member_user)
        services.respond(mine, False, self.member_user)
        swap.refresh_from_db()
        self.assertEqual(swap.status, "cancelada")
        self.assertEqual((rotas.swaps_to_answer(self.asked), rotas.open_swap_ids([mine.pk])), ([], {}))
        # And were the row left open by an older writer, nobody is offered it and nobody can take it.
        SwapRequest.objects.filter(pk=swap.pk).update(status="pendiente")
        swap.refresh_from_db()
        self.assertFalse(rotas.is_live(swap))
        self.assertEqual(rotas.swaps_to_answer(self.asked), [])
        with self.assertRaises(services.ServingError):
            rotas.answer_swap(swap, True, self.asked_user)
        self.assertEqual(Assignment.objects.get(pk=mine.pk).person, self.member)

    def test_every_swap_writer_holds_the_team_and_a_removed_member_never_ends_with_the_date(self):
        mine = services.assign(self.day, self.member, self.leader_user)
        swap = rotas.request_swap(mine, self.asked, self.member_user)
        services.remove_member(self.team, self.asked, self.admin)
        with self.assertRaises(services.ServingError):
            rotas.answer_swap(swap, True, self.asked_user)
        self.assertEqual(Assignment.objects.get(pk=mine.pk).person, self.member)

        # The order the locks are asked in (SQLite takes none): team, date, assignment, swap, for every writer.
        asked_for = []
        real = QuerySet.select_for_update

        def spy(queryset, *args, **kwargs):
            asked_for.append(queryset.model.__name__)
            return real(queryset, *args, **kwargs)

        order = ["Team", "ServiceDate", "Assignment", "SwapRequest"]

        def in_order(work) -> list:
            asked_for.clear()
            with mock.patch.object(QuerySet, "select_for_update", spy):
                work()
            locks = [name for name in asked_for if name in order]
            self.assertEqual(locks, sorted(locks, key=order.index), locks)
            return locks

        self.assertEqual(in_order(lambda: rotas.request_swap(mine, self.carlos, self.member_user)), ["Team", "ServiceDate", "Assignment"])
        swap = SwapRequest.objects.get(status="pendiente")
        self.assertEqual(in_order(lambda: rotas.answer_swap(swap, True, self.coord1)), order)
        mine.refresh_from_db()
        again = rotas.request_swap(mine, self.member, self.coord1)
        self.assertEqual(in_order(lambda: rotas.cancel_swap(again, self.coord1)), order)
        rotas.request_swap(mine, self.member, self.coord1)
        # Deleting takes the assignment before the cascade reaches its swap; taking somebody out starts at the team.
        self.assertEqual(in_order(lambda: services.unassign(self.day, self.carlos, self.leader_user)), ["Team", "ServiceDate", "Assignment"])
        services.assign(self.day, self.member, self.leader_user)
        self.assertEqual(in_order(lambda: services.remove_member(self.team, self.member, self.admin))[0], "Team")
        self.need(door=1)
        proposal = rotas.build_proposal(self.team)
        in_order(lambda: rotas.confirm_proposal(self.team, self.leader_user, token=proposal.token, choices={}))

    def test_a_lock_the_database_gave_up_on_is_a_plain_try_again(self):
        mine = services.assign(self.day, self.member, self.leader_user)
        swap = rotas.request_swap(mine, self.asked, self.member_user)
        self.client.force_login(self.asked_user)
        with mock.patch.object(rotas, "answer_swap", side_effect=OperationalError("deadlock detected")):
            response = self.client.post(self.url("swap_answer", swap.pk), {"respuesta": "aceptar"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "inténtalo de nuevo")
        self.assertNotContains(response, "deadlock")
        self.assertEqual(Assignment.objects.get(pk=mine.pk).person, self.member)

    def test_the_date_form_warns_about_who_serves_elsewhere_and_lets_the_coordinator_go_on(self):
        services.set_member(self.other_team, self.asked, self.admin)
        services.assign(self.new_day(7, team=self.other_team, hour=time(18, 0)), self.asked, self.admin)
        self.client.force_login(self.leader_user)
        data = self.date_post(self.member, self.asked)
        page = self.client.post(self.url("date_detail", self.day.pk), data)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Pía Pidió ya sirve ese día en otra fecha")
        self.assertEqual(self.held(), {})  # nothing was saved, Diego neither
        # Saying yes to another list than the one shown warns again.
        self.assertEqual(self.client.post(self.url("date_detail", self.day.pk), {**data, "ocupados": "999"}).status_code, 200)
        response = self.client.post(self.url("date_detail", self.day.pk), {**data, "ocupados": page.context["warned_ids"]})
        self.assertRedirects(response, self.url("team", self.team.pk))
        self.assertEqual(set(self.held()), {self.member.pk, self.asked.pk})
        # Saving the page again does not ask again about who is already on the date.
        self.assertEqual(self.client.post(self.url("date_detail", self.day.pk), data).status_code, 302)
        # The hard rules stay hard: a role for adults is not something to confirm past.
        data[f"rol_{self.carlos.pk}"] = str(self.kids.pk)
        data["sirve"] = [*data["sirve"], self.carlos.pk]
        refused = self.client.post(self.url("date_detail", self.day.pk), {**data, "ocupados": ""})
        self.assertContains(refused, "solo para personas adultas")
        self.assertNotIn(self.carlos.pk, self.held())

    def test_the_team_columns_keep_their_default_in_the_database(self):
        for name, value in (("swap_needs_approval", False), ("reminder_days", 0), ("rotation_every_weeks", 1)):
            self.assertEqual(Team._meta.get_field(name).db_default, value, name)
        with connection.cursor() as cursor:  # as the previous release's code would: without the new columns
            cursor.execute(
                "INSERT INTO serving_team (church_id, name, description, status, created_at, updated_at) VALUES (%s, 'Antes', '', 'activo', %s, %s)",
                [self.church.pk, timezone.now(), timezone.now()],
            )
        team = Team.objects.get(name="Antes")
        self.assertEqual((team.swap_needs_approval, team.reminder_days, team.rotation_every_weeks), (False, 0, 1))


class ProposalWordsTests(RotasFixture):
    def test_two_places_of_one_role_are_told_apart_and_an_empty_list_says_why_without_naming_anybody(self):
        self.need(door=2, kids=1)
        other = self.new_day(14)
        self.need(other, door=1)
        for person in (self.leader, self.member, self.asked, self.carlos, self.no_account):
            services.set_unavailable(person, other.date, self.admin)
        self.client.force_login(self.leader_user)
        page = self.client.get(self.url("rotation", self.team.pk))
        self.assertContains(page, "Puerta, lugar 1 de 2")
        self.assertContains(page, "Puerta, lugar 2 de 2")
        self.assertContains(page, "la iglesia no ha confirmado la edad de nadie del equipo")
        self.assertContains(page, "Nadie del equipo puede ese día")
        slots = {slot.label: slot for entry in page.context["proposal"].dates for slot in entry.slots}
        self.assertEqual(slots["Puerta, lugar 1 de 2"].why, "")
        for slot in (slots["Niños"], slots["Puerta"]):
            self.assertEqual(slot.options, [])
            for person in (self.leader, self.member, self.asked, self.carlos, self.no_account):
                self.assertNotIn(person.first_name, slot.why)
        self.assertContains(page, '<h2 class="heading" id="fecha-', count=2)
