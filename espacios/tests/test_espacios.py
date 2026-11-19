"""Espacios: rooms and equipment of a church, who may ask for them, and the rule that two bookings
of one space never share a minute (setup and teardown included)."""

from datetime import datetime, time, timedelta
import re
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Role
from core.services import assign_role
from core.test_shell import rail
from espacios import access, services
from espacios.models import Booking, Space
from events.models import Event
from events.tests.base import make_user
from groups.tests.base import CommunityFixture
from people import flags
from people.models import ChurchAuditEvent
from serving import services as serving

Status = Booking.Status
LIST = reverse("espacios:list")
QUEUE = reverse("espacios:queue")
MANAGE = reverse("espacios:manage")
USAGE = reverse("espacios:usage")
CREATE = reverse("espacios:space_create")
SECRET = "Consejería de la familia Robles"


def url(name, obj):
    return reverse(f"espacios:{name}", args=[obj.pk])


class SpacesFixture(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tz = services.church_tz(cls.church)
        cls.day = timezone.now().astimezone(cls.tz).date() + timedelta(days=7)
        cls.room = Space.objects.create(church=cls.church, name="Salón 2", capacity=40, notes="La llave está en la oficina.")
        cls.projector = Space.objects.create(church=cls.church, name="Proyector", kind=Space.Kind.EQUIPMENT, needs_approval=False)
        cls.room_b = Space.objects.create(church=cls.church_b, name="Salón de la iglesia B")
        cls.pastor_user = make_user("pastor-iglesia@prueba.mx", "Pablo", "Pastor")
        assign_role(cls.pastor_user, cls.church, Role.PASTOR)
        cls.pastor_person = cls._person(cls.church, cls.pastor_user)
        cls.coordinator_user = make_user("coordina@prueba.mx", "Coral", "Coordina")
        cls.coordinator = cls._person(cls.church, cls.coordinator_user)
        cls.team = serving.save_team(cls.church, cls.admin, name="Bienvenida")
        serving.set_member(cls.team, cls.coordinator, cls.admin, role="coordina")
        serving.set_member(cls.team, cls.member, cls.admin, role="sirve")
        cls.leader_b_user = make_user("guia-b@prueba.mx", "Blas", "Guía")
        cls.leader_b = cls._person(cls.church_b, cls.leader_b_user)

    def at(self, hour, minute=0, day=None) -> datetime:
        return datetime.combine(day or self.day, time(hour, minute), tzinfo=self.tz)

    def book(self, start=18, end=20, *, space=None, person=None, actor=None, title="Ensayo", **extra) -> Booking:
        person = person or self.leader
        return services.request_booking(
            space or self.room, person, actor or person.user, title=title, starts_at=self.at(start), ends_at=self.at(end), **extra,
        )

    def form(self, start="18:00", end="20:00", **changes) -> dict:
        return {"title": "Ensayo", "day": self.day.isoformat(), "starts": start, "ends": end, "setup_minutes": "0",
                "teardown_minutes": "0", **changes}

    def status(self, booking) -> str:
        return Booking.objects.get(pk=booking.pk).status


class ClashTests(SpacesFixture):
    """The rule itself, in ``espacios.services``."""

    def test_a_request_holds_its_hours_and_a_second_one_is_refused(self):
        first = self.book()
        self.assertEqual(first.status, Status.REQUESTED)
        with self.assertRaises(services.Clash) as refused:
            self.book(19, 21, person=self.coordinator)
        self.assertEqual([row.pk for row in refused.exception.bookings], [first.pk])
        self.assertEqual(Booking.objects.count(), 1)

    def test_setup_and_teardown_count(self):
        self.book(18, 20, setup_minutes=30, teardown_minutes=60)   # blocked 17:30 to 21:00
        for start, end, extra in ((16, 17, {"teardown_minutes": 60}), (21, 22, {"setup_minutes": 15}), (20, 21, {}), (17, 18, {})):
            with self.assertRaises(services.Clash, msg=(start, end)):
                self.book(start, end, person=self.coordinator, **extra)
        # Touching ends do not overlap: one ends at 17:30 with its teardown, the other starts at 21:00 with its setup.
        self.book(16, 17, person=self.coordinator, teardown_minutes=30)
        self.book(21, 22, person=self.coordinator)
        self.assertEqual(Booking.objects.count(), 3)

    def test_another_space_and_another_day_are_free(self):
        self.book()
        self.book(space=self.projector, person=self.coordinator)
        services.request_booking(self.room, self.coordinator, self.coordinator_user, title="Otro día",
                                 starts_at=self.at(18) + timedelta(days=1), ends_at=self.at(20) + timedelta(days=1))
        self.assertEqual(Booking.objects.count(), 3)

    def test_a_rejected_or_cancelled_booking_frees_its_hours(self):
        first = self.book()
        services.reject(first, self.admin, "Hay mantenimiento.")
        second = self.book(person=self.coordinator)
        services.cancel(second, self.coordinator_user, by_admin=False)
        third = self.book(person=self.pastor_person)
        self.assertEqual([self.status(row) for row in (first, second, third)], [Status.REJECTED, Status.CANCELLED, Status.REQUESTED])

    def test_two_requests_at_once_the_second_one_reads_the_first_after_the_lock(self):
        """What PostgreSQL does with two simultaneous requests: the second waits at the space's lock
        and, when it gets it, the first one's booking is there. Simulated by committing the rival's
        booking at the moment the lock is taken: a check made before the lock would not see it."""
        take_lock = services._locked_space

        def rival_wins_the_lock(space_id):
            space = take_lock(space_id)
            Booking.objects.create(space=space, title="Rival", starts_at=self.at(19), ends_at=self.at(21), requested_by=self.coordinator)
            return space

        with mock.patch.object(services, "_locked_space", side_effect=rival_wins_the_lock):
            with self.assertRaises(services.Clash) as refused:
                self.book()
        self.assertEqual([row.title for row in refused.exception.bookings], ["Rival"])
        # (The rival's row was written inside the refused transaction of this test, so it went with it.)
        self.assertFalse(Booking.objects.filter(title="Ensayo").exists())

    def test_every_write_that_can_take_hours_locks_the_space_first(self):
        booking = self.book()
        for action in (lambda: self.book(8, 9, person=self.coordinator), lambda: services.approve(booking, self.admin)):
            with mock.patch.object(services, "_locked_space", wraps=services._locked_space) as lock, \
                 mock.patch.object(services, "overlapping", wraps=services.overlapping) as read:
                order = mock.Mock()
                order.attach_mock(lock, "lock")
                order.attach_mock(read, "read")
                action()
            self.assertEqual([call[0] for call in order.mock_calls[:2]], ["lock", "read"])

    def test_the_space_is_read_again_under_the_lock(self):
        stale = Space.objects.get(pk=self.room.pk)           # read while the space could be booked
        Space.objects.filter(pk=self.room.pk).update(active=False)
        with self.assertRaisesMessage(services.SpaceError, "ya no se puede reservar"):
            self.book(space=stale)
        Space.objects.filter(pk=self.room.pk).update(active=True, needs_approval=False)
        self.assertEqual(self.book(space=stale).status, Status.APPROVED)   # and so is whether it asks for approval

    def test_what_a_booking_must_be(self):
        cases = [
            ({"starts_at": self.at(20), "ends_at": self.at(18)}, "después de la hora en que empieza"),
            ({"starts_at": self.at(18), "ends_at": self.at(18)}, "después de la hora en que empieza"),
            ({"starts_at": timezone.now() - timedelta(hours=1), "ends_at": timezone.now() + timedelta(hours=1)}, "ya pasó"),
            ({"starts_at": self.at(8), "ends_at": self.at(8) + timedelta(hours=25)}, "a lo más un día"),
            ({"starts_at": self.at(8), "ends_at": self.at(9), "setup_minutes": 500}, "minutos a lo más"),
            ({"starts_at": self.at(8), "ends_at": self.at(9), "title": "   "}, "para qué es"),
        ]
        for fields, message in cases:
            values = {"title": "Ensayo", **fields}
            with self.assertRaisesMessage(services.SpaceError, message):
                services.request_booking(self.room, self.leader, self.leader_user, **values)
        with self.assertRaises(ValueError):   # a naive hour is a bug of the caller, never a booking
            services.request_booking(self.room, self.leader, self.leader_user, title="Ensayo",
                                     starts_at=datetime.combine(self.day, time(8)), ends_at=datetime.combine(self.day, time(9)))
        self.assertEqual(Booking.objects.count(), 0)

    def test_nothing_of_another_church_fits_in_a_booking(self):
        with self.assertRaisesMessage(services.SpaceError, "no es de esta iglesia"):
            self.book(person=self.member_b, actor=self.member_b_user)
        with self.assertRaisesMessage(services.SpaceError, "evento no es de esta iglesia"):
            self.book(person=self.admin_person, actor=self.admin, event=self.event_b, approve=True)
        self.assertEqual(Booking.objects.count(), 0)

    def test_a_space_without_approval_books_at_once_and_the_administration_does_not_ask_itself(self):
        direct = self.book(space=self.projector)
        own = self.book(person=self.admin_person, actor=self.admin, approve=True)
        self.assertEqual((direct.status, direct.decided_by), (Status.APPROVED, None))
        self.assertEqual((own.status, own.decided_by), (Status.APPROVED, self.admin))
        with self.assertRaises(services.Clash):   # approved bookings hold the hours as requests do
            self.book(space=self.projector, person=self.coordinator)

    def test_the_hours_are_the_churchs_own(self):
        late = services.request_booking(self.room, self.leader, self.leader_user, title="Vigilia",
                                        starts_at=self.at(22, 30), ends_at=self.at(23, 30))
        self.assertNotEqual(late.starts_at.astimezone(ZoneInfo("UTC")).date(), self.day)   # in UTC it is already tomorrow
        days = services.week(self.room, services.week_of(self.day), self.tz)
        found = {day["date"]: [row.title for row in day["bookings"]] for day in days if day["bookings"]}
        self.assertEqual(found, {self.day: ["Vigilia"]})
        shown = days[self.day.weekday()]["bookings"][0]
        self.assertEqual((shown.start_time, shown.end_time), (time(22, 30), time(23, 30)))


class DecisionTests(SpacesFixture):
    def test_approve_reject_cancel_and_their_audit_rows(self):
        first, second = self.book(8, 9, title=SECRET), self.book(10, 11)
        services.approve(first, self.admin)
        services.reject(second, self.admin, "El salón está en mantenimiento.")
        services.cancel(first, self.leader_user, by_admin=False)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.status, second.status, second.decision_reason), (Status.CANCELLED, Status.REJECTED, "El salón está en mantenimiento."))
        rows = list(ChurchAuditEvent.objects.filter(action__startswith="espacio_").order_by("id"))
        self.assertEqual(
            [row.action for row in rows],
            ["espacio_reserva_solicitada", "espacio_reserva_solicitada", "espacio_reserva_aprobada", "espacio_reserva_rechazada",
             "espacio_reserva_cancelada"],
        )
        # Ids and fixed words: never what the booking is for, the reason, or the name of the room.
        for row in rows:
            text = f"{row.summary} {row.metadata}"
            for private in (SECRET, "mantenimiento", "Salón 2", "Ensayo"):
                self.assertNotIn(private, text)
            self.assertEqual((row.church, row.object_type, row.metadata["space_id"]), (self.church, "Booking", self.room.pk))
        self.assertEqual(rows[-1].metadata["source"], "propia")

    def test_a_stale_copy_never_brings_a_decision_back(self):
        booking = self.book()
        stale = Booking.objects.get(pk=booking.pk)              # what a second administrator's page read
        services.reject(booking, self.admin, "No hay quien abra.")
        with self.assertRaisesMessage(services.SpaceError, "ya no está por decidir"):
            services.approve(stale, self.admin)
        with self.assertRaisesMessage(services.SpaceError, "ya no está por decidir"):
            services.reject(stale, self.admin, "Otro motivo")
        with self.assertRaisesMessage(services.SpaceError, "ya estaba cancelada o rechazada"):
            services.cancel(stale, self.leader_user, by_admin=False)
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.decision_reason), (Status.REJECTED, "No hay quien abra."))

    def test_a_cancellation_by_who_asked_wins_over_a_late_approval(self):
        booking = self.book()
        stale = Booking.objects.get(pk=booking.pk)
        services.cancel(booking, self.leader_user, by_admin=False)
        with self.assertRaises(services.SpaceError):
            services.approve(stale, self.admin)
        self.assertEqual(self.status(booking), Status.CANCELLED)
        self.book(person=self.coordinator)   # and the hours are free

    def test_an_approval_from_a_stale_copy_keeps_what_changed_meanwhile(self):
        booking = self.book()
        stale = Booking.objects.get(pk=booking.pk)
        Booking.objects.filter(pk=booking.pk).update(title="Título corregido")
        services.approve(stale, self.admin)
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.title), (Status.APPROVED, "Título corregido"))

    def test_a_booking_that_ended_cannot_be_cancelled(self):
        booking = self.book()
        now = timezone.now()
        Booking.objects.filter(pk=booking.pk).update(
            starts_at=now - timedelta(hours=3), ends_at=now - timedelta(hours=1),
            blocked_from=now - timedelta(hours=3), blocked_until=now - timedelta(hours=1),
        )
        with self.assertRaisesMessage(services.SpaceError, "ya terminó"):
            services.cancel(booking, self.admin, by_admin=True)
        self.assertEqual(self.status(booking), Status.REQUESTED)

    def test_a_reason_is_needed_to_reject(self):
        booking = self.book()
        with self.assertRaisesMessage(services.SpaceError, "Escribe el motivo"):
            services.reject(booking, self.admin, "  ")
        self.assertEqual(self.status(booking), Status.REQUESTED)

    # ---- the override ---------------------------------------------------------------------------
    def test_the_administration_books_over_a_clash_only_naming_what_it_saw(self):
        first = self.book()
        with self.assertRaises(services.Clash):
            self.book(person=self.admin_person, actor=self.admin, approve=True)
        with self.assertRaises(services.Clash):   # an empty or wrong list is not "I saw it"
            self.book(person=self.admin_person, actor=self.admin, approve=True, seen=[])
        with self.assertRaises(services.Clash):
            self.book(person=self.admin_person, actor=self.admin, approve=True, seen=[first.pk + 100])
        over = self.book(person=self.admin_person, actor=self.admin, approve=True, seen=[first.pk])
        self.assertEqual((over.status, over.overrode_clash), (Status.APPROVED, True))
        row = ChurchAuditEvent.objects.get(action="espacio_reserva_sobre_choque")
        self.assertEqual((row.actor, row.object_id, row.metadata), (self.admin, str(over.pk), {"space_id": self.room.pk, "override": "empalme"}))

    def test_seen_is_ignored_for_who_is_not_the_administration(self):
        first = self.book()
        with self.assertRaises(services.Clash):
            self.book(person=self.coordinator, seen=[first.pk])
        self.assertFalse(ChurchAuditEvent.objects.filter(action="espacio_reserva_sobre_choque").exists())

    def test_approving_over_a_clash_needs_the_list_as_it_is_now(self):
        request = self.book()
        other = self.book(person=self.admin_person, actor=self.admin, approve=True, seen=[request.pk])
        with self.assertRaises(services.Clash) as listed:
            services.approve(request, self.admin)
        self.assertEqual([row.pk for row in listed.exception.bookings], [other.pk])
        # While the administrator read that list, a third booking was kept over the same hours.
        third = self.book(19, 21, person=self.admin_person, actor=self.admin, approve=True, seen=[request.pk, other.pk])
        with self.assertRaises(services.Clash) as again:
            services.approve(request, self.admin, seen=[other.pk])
        self.assertEqual({row.pk for row in again.exception.bookings}, {other.pk, third.pk})
        self.assertEqual(self.status(request), Status.REQUESTED)
        approved = services.approve(request, self.admin, seen=[other.pk, third.pk])
        self.assertEqual((approved.status, approved.overrode_clash), (Status.APPROVED, True))
        self.assertEqual(ChurchAuditEvent.objects.filter(action="espacio_reserva_sobre_choque").count(), 3)

    # ---- usage ----------------------------------------------------------------------------------
    def test_usage_counts_approved_hours_inside_the_period(self):
        services.approve(self.book(18, 20, setup_minutes=30, teardown_minutes=30), self.admin)
        services.approve(self.book(8, 9, title="Otro"), self.admin)
        self.book(10, 12, person=self.coordinator)                               # not decided: not counted
        services.cancel(self.book(13, 14, space=self.projector), self.admin, by_admin=True)
        late = services.request_booking(self.projector, self.leader, self.leader_user, title="Noche",
                                        starts_at=self.at(22), ends_at=self.at(22) + timedelta(hours=4))   # two hours fall on the next day
        self.assertEqual(late.status, Status.APPROVED)
        rows = {row["space"].name: (row["bookings"], row["used_text"]) for row in services.usage(self.church, self.day, self.day, self.tz)}
        self.assertEqual(rows, {"Salón 2": (2, "3 h"), "Proyector": (1, "2 h")})   # booked time: the buffers do not count
        before = services.usage(self.church, self.day - timedelta(days=9), self.day - timedelta(days=1), self.tz)
        self.assertEqual({row["bookings"] for row in before}, {0})
        self.assertNotIn("Salón de la iglesia B", [row["space"].name for row in before])


class ViewTests(SpacesFixture):
    def every_request(self, booking):
        """Each address of the module, as (method, url, data)."""
        return [
            ("get", LIST, {}), ("get", url("week", self.room), {}), ("get", url("request", self.room), {}),
            ("post", url("request", self.room), self.form("08:00", "09:00")),
            ("get", url("cancel", booking), {}), ("post", url("cancel", booking), {}),
            ("get", QUEUE, {}), ("post", url("approve", booking), {}),
            ("get", url("reject", booking), {}), ("post", url("reject", booking), {"reason": "No"}),
            ("get", MANAGE, {}), ("get", CREATE, {}), ("post", CREATE, {"name": "Cocina", "kind": "salon"}),
            ("get", url("space_edit", self.room), {}), ("post", url("space_edit", self.room), {"name": "Otro", "kind": "salon"}),
            ("get", USAGE, {}),
        ]

    def answers(self, user, booking) -> dict:
        if user is not None:
            self.client.force_login(user)
        found = {}
        for method, address, data in self.every_request(booking):
            found[(method, address)] = getattr(self.client, method)(address, data).status_code
        return found

    # ---- denials --------------------------------------------------------------------------------
    def test_without_an_account_everything_asks_to_sign_in(self):
        booking = self.book()
        for method, address, data in self.every_request(booking):
            response = getattr(self.client, method)(address, data)
            self.assertEqual(response.status_code, 302, address)
            self.assertTrue(response.url.startswith(reverse("login")), address)
        self.assertEqual((Space.objects.count(), self.status(booking)), (3, Status.REQUESTED))

    def test_with_the_module_off_nothing_exists_for_anybody(self):
        booking = self.book()
        flags.set_enabled(self.church, "community", False)
        for user in (self.admin, self.leader_user, self.member_user):
            self.assertEqual(set(self.answers(user, booking).values()), {404}, user.email)
        flags.set_enabled(self.church, "community", True)
        with override_settings(EMMAUS_V2_MODULES=False):
            self.assertEqual(set(self.answers(self.admin, booking).values()), {404})
        self.assertEqual((Space.objects.filter(church=self.church).count(), self.status(booking), Booking.objects.count()),
                         (2, Status.REQUESTED, 1))

    def test_a_plain_member_reads_the_calendar_and_nothing_else(self):
        booking = self.book(title=SECRET)
        for user in (self.member_user, self.coord1):   # coord1: an event role, which gives nothing in the church
            found = self.answers(user, booking)
            readable = {("get", LIST), ("get", url("week", self.room))}
            self.assertEqual({key for key, code in found.items() if code == 200}, readable, user.email)
            self.assertEqual(found[("get", url("cancel", booking))], 404)    # somebody else's booking does not exist
            self.assertEqual(found[("post", url("cancel", booking))], 404)
            rest = {code for key, code in found.items() if key not in readable and "cancelar" not in key[1]}
            self.assertEqual(rest, {403}, user.email)
        self.assertEqual((self.status(booking), Booking.objects.count(), Space.objects.count()), (Status.REQUESTED, 1, 3))

    def test_who_guides_or_coordinates_asks_but_does_not_decide(self):
        booking = self.book(person=self.pastor_person)
        for user in (self.leader_user, self.coordinator_user, self.pastor_user):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url("request", self.room)).status_code, 200, user.email)
            for method, address, data in self.every_request(booking)[6:]:
                self.assertEqual(getattr(self.client, method)(address, data).status_code, 403, f"{user.email} {address}")
        self.assertEqual(self.status(booking), Status.REQUESTED)

    def test_who_may_ask(self):
        allowed = [self.admin, self.pastor_user, self.leader_user, self.coordinator_user]
        refused = [self.member_user, self.coord1, self.asked_user, self.outsider, self.admin_b]
        for user in allowed:
            self.assertTrue(access.can_request(user, self.church), user.email)
        for user in refused:
            self.assertFalse(access.can_request(user, self.church), user.email)
        # Guiding a group that was archived, or coordinating a team that was, is no longer being responsible for it.
        type(self.group).objects.filter(pk__in=[self.group.pk]).update(status="archivado")
        serving.archive_team(self.team, self.admin)
        self.assertFalse(access.can_request(self.leader_user, self.church))
        self.assertFalse(access.can_request(self.coordinator_user, self.church))
        self.leader_user.is_active = False
        self.assertFalse(access.can_request(self.leader_user, self.church, is_church_admin=True))

    def test_another_church_gets_404_for_every_id(self):
        booking = self.book()
        for user in (self.admin_b, self.leader_b_user, self.member_b_user):
            self.client.force_login(user)
            for method, address, data in self.every_request(booking):
                if address in (LIST, QUEUE, MANAGE, USAGE, CREATE):
                    continue
                code = getattr(self.client, method)(address, data).status_code
                # Who is not the administration of their own church is told so before any id is looked up.
                staff_only = user != self.admin_b and any(word in address for word in ("aprobar", "rechazar", "administrar"))
                self.assertEqual(code, 403 if staff_only else 404, f"{user.email} {method} {address}")
        self.client.force_login(self.admin_b)
        for address in (LIST, QUEUE, MANAGE, USAGE):
            page = self.client.get(address)
            self.assertEqual(page.status_code, 200)
            self.assertNotContains(page, "Salón 2")
            self.assertNotContains(page, "Ensayo")
        self.room.refresh_from_db()
        self.assertEqual((self.status(booking), self.room.name, Booking.objects.count()), (Status.REQUESTED, "Salón 2", 1))

    def test_an_event_of_another_church_cannot_be_chosen(self):
        self.client.force_login(self.admin)
        response = self.client.post(url("request", self.room), self.form(event=str(self.event_b.pk)))
        self.assertContains(response, "Elige una opción de la lista.")
        self.assertEqual(Booking.objects.count(), 0)

    def test_posts_need_the_csrf_token(self):
        booking = self.book()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        for method, address, data in self.every_request(booking):
            if method == "post":
                self.assertEqual(client.post(address, data).status_code, 403, address)
        self.assertEqual((self.status(booking), Booking.objects.count(), Space.objects.count()), (Status.REQUESTED, 1, 3))

    def test_approving_is_a_post(self):
        booking = self.book()
        self.client.force_login(self.admin)
        # A GET (a reload or «atrás» on the clash page an action in place left at this address) approves nothing and
        # goes back to the queue; who is not the administration, or is of another church, gets what they always got.
        self.assertRedirects(self.client.get(url("approve", booking)), reverse("espacios:queue"))
        self.assertEqual(self.status(booking), Status.REQUESTED)
        for user, status in ((self.member_user, 403), (self.admin_b, 404)):
            self.client.force_login(user)
            response = self.client.get(url("approve", booking))
            self.assertEqual(response.status_code, status)
            self.assertNotContains(response, booking.space.name, status_code=status)
        self.assertEqual(self.status(booking), Status.REQUESTED)

    # ---- what each reader sees --------------------------------------------------------------------
    def test_the_title_is_for_the_administration_and_for_who_asked(self):
        event = Event.objects.create(church=self.church, name="Retiro de matrimonios", slug="retiro-matrimonios",
                                     starts_on=self.day, ends_on=self.day)
        booking = self.book(18, 20, person=self.admin_person, actor=self.admin, approve=True, title=SECRET, event=event, setup_minutes=30)
        mine = self.book(8, 9, title="Ensayo de alabanza")
        week = url("week", self.room) + f"?semana={self.day.isoformat()}"
        for user in (self.member_user, self.coordinator_user, self.pastor_user, self.leader_user):
            self.client.force_login(user)
            page = self.client.get(week)
            self.assertContains(page, "Ocupado")
            self.assertContains(page, "De 17:30 a 20:00.")     # the hours nobody else can have, setup included
            for private in (SECRET, "Ada Admin", "Retiro de matrimonios", "La llave está en la oficina"):
                self.assertNotContains(page, private)
            self.assertNotContains(page, url("cancel", booking))
            self.assertEqual("Ensayo de alabanza" in page.content.decode(), user == self.leader_user, user.email)
            self.assertNotContains(self.client.get(LIST), SECRET)
        self.assertContains(self.client.get(LIST), url("cancel", mine))    # the guide: her own, with the way to cancel it
        self.client.force_login(self.admin)
        page = self.client.get(week)
        for shown in (SECRET, "La pidió Ada Admin.", "Para el evento Retiro de matrimonios.", "La pidió Gala Guía.",
                      "Apartado de 17:30 a 20:00", "La llave está en la oficina"):
            self.assertContains(page, shown)

    def test_what_a_person_typed_is_escaped(self):
        nasty = '<script>alert("x")</script>'
        self.room.notes = nasty
        self.room.save()
        booking = self.book(title=nasty)
        self.client.force_login(self.admin)
        for address in (QUEUE, url("week", self.room) + f"?semana={self.day.isoformat()}", url("reject", booking), url("cancel", booking)):
            page = self.client.get(address)
            self.assertNotContains(page, nasty, msg_prefix=address)
            self.assertContains(page, "&lt;script&gt;", msg_prefix=address)
        services.reject(booking, self.admin, nasty)
        page = self.client.get(QUEUE + "?estado=decididas")
        self.assertNotContains(page, nasty)
        self.assertContains(page, "Motivo: &lt;script&gt;")
        self.client.post(CREATE, {"name": nasty, "kind": "salon", "needs_approval": "on"})
        self.assertNotContains(self.client.get(MANAGE), nasty)
        self.assertContains(self.client.get(LIST), "&lt;script&gt;")

    def test_the_reason_of_a_rejection_stays_with_the_administration(self):
        booking = self.book()
        self.client.force_login(self.admin)
        self.assertContains(self.client.post(url("reject", booking), {"reason": ""}), "Escribe el motivo")
        self.assertEqual(self.status(booking), Status.REQUESTED)
        self.client.post(url("reject", booking), {"reason": "Hay una junta de ancianos."})
        self.assertEqual(self.status(booking), Status.REJECTED)
        self.assertContains(self.client.get(QUEUE + "?estado=decididas"), "Hay una junta de ancianos.")
        self.client.force_login(self.leader_user)
        page = self.client.get(LIST)
        self.assertContains(page, "Rechazada")
        self.assertNotContains(page, "junta de ancianos")
        self.assertEqual(self.client.get(url("reject", booking)).status_code, 403)

    def test_a_space_switched_off_exists_only_for_the_administration(self):
        booking = self.book()
        Space.objects.filter(pk=self.room.pk).update(active=False)
        self.client.force_login(self.leader_user)
        self.assertNotContains(self.client.get(LIST), url("week", self.room))
        for address in (url("week", self.room), url("request", self.room)):
            self.assertEqual(self.client.get(address).status_code, 404)
        self.assertEqual(self.client.post(url("request", self.room), self.form("08:00", "09:00")).status_code, 404)
        self.client.post(url("cancel", booking))          # her own booking can still be given back
        self.assertEqual(self.status(booking), Status.CANCELLED)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url("week", self.room)), "Este espacio está apagado")
        self.assertEqual(self.client.get(url("request", self.room)).status_code, 404)
        self.assertEqual(Booking.objects.count(), 1)

    # ---- the journeys ---------------------------------------------------------------------------
    def test_a_guide_asks_the_administration_approves_and_the_guide_cancels(self):
        self.client.force_login(self.leader_user)
        response = self.client.post(url("request", self.room), self.form(setup_minutes="30"), follow=True)
        self.assertContains(response, "El horario queda apartado mientras la administración decide.")
        booking = Booking.objects.get()
        self.assertEqual((booking.status, booking.requested_by, booking.starts_at, booking.blocked_from, booking.event),
                         (Status.REQUESTED, self.leader, self.at(18), self.at(17, 30), None))
        self.assertContains(response, "Por decidir")
        self.client.force_login(self.admin)
        waiting = self.client.get(LIST)                                # the count that leads to the requests
        self.assertContains(waiting, '<span class="decision__cifra">1</span>', html=True)
        self.assertContains(waiting, "Solicitud por decidir")
        self.assertContains(self.client.get(QUEUE), "La pidió Gala Guía.")
        response = self.client.post(url("approve", booking), follow=True)
        self.assertContains(response, "Aprobaste la reserva de Salón 2")
        self.assertContains(response, "No hay solicitudes por decidir.")
        self.assertEqual(self.status(booking), Status.APPROVED)
        self.client.force_login(self.leader_user)
        self.assertContains(self.client.get(LIST), "Aprobada")
        self.assertContains(self.client.get(url("cancel", booking)), "¿Cancelar la reserva de Salón 2?")
        response = self.client.post(url("cancel", booking), follow=True)
        self.assertContains(response, "El horario quedó libre.")
        self.assertContains(response, "Libre todo el día.")
        self.assertEqual(self.status(booking), Status.CANCELLED)
        self.assertEqual(self.client.get(url("cancel", booking)).status_code, 404)

    def test_a_guide_who_finds_the_hours_taken_reads_when_and_not_what(self):
        self.book(person=self.pastor_person, title=SECRET, teardown_minutes=30)
        self.client.force_login(self.leader_user)
        response = self.client.post(url("request", self.room), self.form("20:00", "21:00", sobre="1", visto="1"))
        self.assertContains(response, "Ese horario ya está apartado")
        self.assertContains(response, "de 18:00 a 20:30: Ocupado.")
        for hidden in (SECRET, "Pablo Pastor", "Reservar aunque se empalme", 'name="visto"'):
            self.assertNotContains(response, hidden)
        self.assertEqual(Booking.objects.count(), 1)

    def test_the_administration_sees_the_clash_before_it_can_book_over_it(self):
        first = self.book(title=SECRET)
        self.client.force_login(self.admin)
        blind = self.client.post(url("request", self.room), self.form(sobre="1"))                       # "anyway", without the list
        wrong = self.client.post(url("request", self.room), self.form(sobre="1", visto=str(first.pk + 50)))
        for response in (blind, wrong):
            self.assertContains(response, f'<input type="hidden" name="visto" value="{first.pk}">', html=True)
            self.assertContains(response, SECRET)
        self.assertEqual(Booking.objects.count(), 1)
        response = self.client.post(url("request", self.room), self.form(sobre="1", visto=str(first.pk)), follow=True)
        self.assertContains(response, "Reservaste Salón 2")
        over = Booking.objects.exclude(pk=first.pk).get()
        self.assertEqual((over.status, over.overrode_clash, over.decided_by), (Status.APPROVED, True, self.admin))
        self.assertEqual(ChurchAuditEvent.objects.filter(action="espacio_reserva_sobre_choque", actor=self.admin).count(), 1)
        # Approving the first one now meets the second: the list first, then the explicit approval.
        self.assertContains(self.client.get(QUEUE), "Se empalma con:")
        listed = self.client.post(url("approve", first))
        self.assertContains(listed, "La reserva se empalma con otra")
        self.assertContains(listed, f'<input type="hidden" name="visto" value="{over.pk}">', html=True)
        self.assertEqual(self.status(first), Status.REQUESTED)
        self.client.post(url("approve", first), {"sobre": "1", "visto": str(over.pk)})
        first.refresh_from_db()
        self.assertEqual((first.status, first.overrode_clash), (Status.APPROVED, True))

    def test_the_form_refuses_what_makes_no_sense(self):
        self.client.force_login(self.leader_user)
        for data, message in (
            (self.form("20:00", "18:00"), "después de la hora en que empieza"),
            (self.form(day=(self.day - timedelta(days=30)).isoformat()), "Esa hora ya pasó"),
            (self.form(starts="las seis"), "Escribe la hora así: 18:30."),
            (self.form(title=""), "Escribe para qué es la reserva."),
            (self.form(setup_minutes="7"), "Elige una opción de la lista."),
        ):
            self.assertContains(self.client.post(url("request", self.room), data), message)
        # A guide cannot link an event: the field is the administration's.
        self.client.post(url("request", self.room), self.form(event=str(self.event.pk)))
        self.assertIsNone(Booking.objects.get().event)

    def test_the_administration_manages_the_spaces(self):
        self.client.force_login(self.admin)
        response = self.client.post(CREATE, {"name": "  Cocina  ", "kind": "salon", "capacity": "12", "needs_approval": "on", "notes": ""}, follow=True)
        self.assertContains(response, "Agregaste Cocina.")
        kitchen = Space.objects.get(name="Cocina")
        self.assertEqual((kitchen.church, kitchen.capacity, kitchen.active, kitchen.needs_approval), (self.church, 12, True, True))
        self.assertContains(self.client.post(CREATE, {"name": "cocina", "kind": "salon"}), "Ya hay un espacio con ese nombre")
        self.assertContains(self.client.post(CREATE, {"name": "Bodega", "kind": "salon", "capacity": "0"}), "igual o mayor que 1")
        # The same name in another church is another space.
        self.client.post(CREATE, {"name": "Salón de la iglesia B", "kind": "salon"})
        self.assertEqual(Space.objects.filter(name="Salón de la iglesia B").count(), 2)
        self.client.post(url("space_edit", kitchen), {"name": "Cocina", "kind": "equipo", "notes": "Cerrar el gas."})
        kitchen.refresh_from_db()
        self.assertEqual((kitchen.kind, kitchen.active, kitchen.needs_approval, kitchen.notes), ("equipo", False, False, "Cerrar el gas."))
        self.assertContains(self.client.get(MANAGE), "Apagado")
        self.assertEqual(list(ChurchAuditEvent.objects.filter(action__startswith="espacio_").values_list("action", flat=True).order_by("id")),
                         ["espacio_creado", "espacio_creado", "espacio_modificado"])

    def test_the_usage_page(self):
        services.approve(self.book(18, 20, title=SECRET), self.admin)
        self.client.force_login(self.admin)
        page = self.client.get(USAGE, {"desde": self.day.isoformat(), "hasta": self.day.isoformat()})
        self.assertContains(page, '<td role="cell" class="num" data-label="Tiempo de uso">2 h</td>', html=True)
        for hidden in (SECRET, "Gala", "Guía"):
            self.assertNotContains(page, hidden)
        self.assertContains(page, f"El {self.day.day} de")          # one day is "El", not "Del"
        self.assertContains(page, "Sin uso")                          # the projector
        self.assertContains(self.client.get(USAGE), "Salón 2")
        self.assertContains(self.client.get(USAGE, {"desde": self.day.isoformat(), "hasta": (self.day - timedelta(days=1)).isoformat()}),
                            "igual o posterior al primero")
        self.assertContains(self.client.get(USAGE, {"desde": "2020-01-01", "hasta": "2026-01-01"}), "un año o menos")
        self.assertContains(self.client.get(USAGE, {"desde": "ayer", "hasta": ""}), "Hay un problema")

    def test_a_week_that_is_not_a_date_is_this_week(self):
        self.client.force_login(self.member_user)
        for value in ("basura", "9999-12-31", "0001-01-01", ""):
            page = self.client.get(url("week", self.room), {"semana": value})
            self.assertContains(page, "Esta semana")


class ShellTests(SpacesFixture):
    HOME = reverse("community:home")

    def nav(self, user) -> str:
        self.client.force_login(user)
        return re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", rail(self.client.get(self.HOME)))

    def test_the_link_is_for_who_may_ask_and_for_the_administration(self):
        for user in (self.leader_user, self.coordinator_user, self.pastor_user):
            nav = self.nav(user)
            self.assertIn(f'href="{LIST}"', nav, user.email)
            self.assertIn(">Espacios<", nav)
        self.assertNotIn(LIST, self.nav(self.member_user))
        self.book()
        nav = self.nav(self.admin)
        self.assertIn(">Espacios y equipo<", nav)
        self.assertRegex(nav, r'Espacios y equipo</span><span class="badge">1<span class="visually-hidden"> solicitud de reserva por decidir')
        self.client.force_login(self.leader_user)
        self.assertEqual(rail(self.client.get(LIST)).count('aria-current="page"'), 1)

    def test_a_church_without_spaces_keeps_its_navigation(self):
        before = self.nav(self.leader_b_user)
        self.assertNotIn(LIST, before)
        Space.objects.filter(church=self.church_b).update(active=False)
        self.assertEqual(self.nav(self.leader_b_user), before)
        Space.objects.filter(church=self.church).update(active=False)
        self.assertNotIn(LIST, self.nav(self.leader_user))   # nothing to ask for: no link under "A tu cargo"


class ReviewRoundTests(SpacesFixture):
    """Defects found by the independent review of the slice."""

    TIJUANA = ZoneInfo("America/Tijuana")
    SPRING = (2027, 3, 14)   # 02:00 jumps to 03:00
    AUTUMN = (2027, 11, 7)   # 02:00 goes back to 01:00

    def tijuana(self):
        type(self.church).objects.filter(pk=self.church.pk).update(time_zone="America/Tijuana")

    # ---- 1. the night the clock changes -----------------------------------------------------------
    def test_an_hour_the_clock_skips_is_refused_with_words_not_a_server_error(self):
        self.tijuana()
        self.client.force_login(self.leader_user)
        response = self.client.post(url("request", self.room), self.form("02:30", "03:15", day="2027-03-14"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Esa hora no existe ese día")
        with self.assertRaisesMessage(services.SpaceError, "no existe ese día"):
            services.request_booking(self.room, self.leader, self.leader_user, title="Ensayo",
                                     starts_at=datetime(*self.SPRING, 2, 30, tzinfo=self.TIJUANA),
                                     ends_at=datetime(*self.SPRING, 3, 15, tzinfo=self.TIJUANA))
        self.assertEqual(Booking.objects.count(), 0)

    def test_hours_are_compared_as_moments_across_the_change(self):
        self.tijuana()
        # 01:30 to 03:30 on the wall is one real hour that night; the booking and its block say so.
        booking = services.request_booking(self.room, self.leader, self.leader_user, title="Vigilia", setup_minutes=30,
                                           starts_at=datetime(*self.SPRING, 1, 30, tzinfo=self.TIJUANA),
                                           ends_at=datetime(*self.SPRING, 3, 30, tzinfo=self.TIJUANA))
        self.assertEqual(booking.ends_at - booking.starts_at, timedelta(hours=1))
        self.assertEqual(booking.starts_at - booking.blocked_from, timedelta(minutes=30))
        # The hour that happens twice is the first of the two, every time.
        twice = services.local_instant(datetime(*self.AUTUMN).date(), time(1, 30), self.TIJUANA)
        self.assertEqual(twice, datetime(2027, 11, 7, 8, 30, tzinfo=ZoneInfo("UTC")))
        self.assertEqual(services.instant(datetime(*self.AUTUMN, 1, 30, tzinfo=self.TIJUANA, fold=1)), twice)
        self.client.force_login(self.leader_user)
        self.client.post(url("request", self.projector), self.form("01:30", "02:30", day="2027-11-07"))
        saved = Booking.objects.get(space=self.projector)
        self.assertEqual((saved.starts_at, saved.ends_at - saved.starts_at), (twice, timedelta(hours=2)))

    def test_a_row_the_database_refuses_is_a_message(self):
        self.client.force_login(self.leader_user)
        from django.db import IntegrityError

        with mock.patch.object(Booking, "save", side_effect=IntegrityError("CHECK constraint failed")):
            response = self.client.post(url("request", self.room), self.form())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No pudimos guardar la reserva con esas horas.")
        self.assertEqual((Booking.objects.count(), ChurchAuditEvent.objects.filter(action__startswith="espacio_").count()), (0, 0))

    # ---- 2. the week shows the buffers on the day they fall ---------------------------------------
    def test_setup_that_falls_on_the_day_before_is_listed_on_that_day(self):
        monday = services.week_of(self.day) + timedelta(days=7)
        tuesday = monday + timedelta(days=1)
        booking = services.request_booking(
            self.room, self.pastor_person, self.pastor_user, title=SECRET, setup_minutes=120,
            starts_at=datetime.combine(tuesday, time(0, 30), tzinfo=self.tz), ends_at=datetime.combine(tuesday, time(2, 0), tzinfo=self.tz),
        )
        days = {day["date"]: day["bookings"] for day in services.week(self.room, monday, self.tz)}
        self.assertEqual([(row.pk, row.day_part, row.day_hours) for row in days[monday]], [(booking.pk, "preparacion", "22:30 a 24:00")])
        self.assertEqual([(row.pk, row.day_part, row.day_hours) for row in days[tuesday]], [(booking.pk, "uso", "00:00 a 02:00")])
        with self.assertRaises(services.Clash):   # and what the page says agrees with what a request gets
            services.request_booking(self.room, self.leader, self.leader_user, title="Otra",
                                     starts_at=datetime.combine(monday, time(23, 0), tzinfo=self.tz),
                                     ends_at=datetime.combine(monday, time(23, 30), tzinfo=self.tz))
        week = url("week", self.room) + f"?semana={monday.isoformat()}"
        self.client.force_login(self.member_user)
        page = self.client.get(week)
        self.assertContains(page, "Ocupado (preparación)")
        self.assertContains(page, "De 22:30 a 24:00.")
        self.assertContains(page, "Libre todo el día.", count=5)
        self.assertNotContains(page, SECRET)
        self.client.force_login(self.admin)
        page = self.client.get(week)
        self.assertContains(page, f"{SECRET} (preparación)")
        self.assertContains(page, "Este día, de 22:30 a 24:00, para preparar.")
        # The week before: its Sunday is not touched; a block that ends at midnight stays in its own day.
        late = services.request_booking(self.projector, self.leader, self.leader_user, title="Cine", teardown_minutes=60,
                                        starts_at=datetime.combine(monday, time(21, 0), tzinfo=self.tz),
                                        ends_at=datetime.combine(monday, time(23, 0), tzinfo=self.tz))
        days = {day["date"]: [row.pk for row in day["bookings"]] for day in services.week(self.projector, monday, self.tz)}
        self.assertEqual((days[monday], days[tuesday]), ([late.pk], []))

    def test_teardown_after_midnight_is_listed_on_the_next_day(self):
        monday = services.week_of(self.day) + timedelta(days=7)
        services.request_booking(self.room, self.leader, self.leader_user, title="Vigilia", teardown_minutes=90,
                                 starts_at=datetime.combine(monday, time(21, 0), tzinfo=self.tz),
                                 ends_at=datetime.combine(monday, time(23, 30), tzinfo=self.tz))
        days = services.week(self.room, monday, self.tz)
        self.assertEqual([(row.day_part, row.day_hours) for row in days[1]["bookings"]], [("recogida", "00:00 a 01:00")])
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(url("week", self.room) + f"?semana={monday.isoformat()}"), "Ocupado (recogida)")

    # ---- 3. the lock is the space's row alone -----------------------------------------------------
    def test_the_lock_names_only_the_space(self):
        manager = type(Space.objects)
        with mock.patch.object(manager, "select_for_update", autospec=True, side_effect=manager.select_for_update) as lock:
            self.book()
        self.assertEqual([call.kwargs for call in lock.call_args_list], [{"of": ("self",)}])

    # ---- 4. a person absorbed by a merge ----------------------------------------------------------
    def test_a_person_merged_into_another_asks_for_nothing(self):
        stale = type(self.leader).objects.get(pk=self.leader.pk)     # read before the merge
        type(self.leader).objects.filter(pk=self.leader.pk).update(merged_into=self.coordinator)
        with self.assertRaisesMessage(services.SpaceError, "no es de esta iglesia"):
            self.book(person=stale, actor=self.leader_user)
        self.assertEqual(Booking.objects.count(), 0)
        # The screens take the person from the account (people.services.ensure_own_person), which never
        # answers with an absorbed one; the form has no field that lists people.
        from espacios.forms import BookingForm

        form = BookingForm(church=self.church, tz=self.tz, with_event=True)
        # (wave 12: how often it repeats, its last day and a series of events; still nobody to pick)
        self.assertEqual(set(form.fields), {"title", "day", "starts", "ends", "setup_minutes", "teardown_minutes", "event",
                                            "repeat", "until", "event_series"})

    # ---- 5. an account of two churches ------------------------------------------------------------
    def test_the_church_is_the_one_the_session_acts_in(self):
        from core.authz import SESSION_KEY

        both = make_user("dos-iglesias@prueba.mx", "Dora", "Dos")
        self._person(self.church, both)
        self._person(self.church_b, both)
        self.book(title=SECRET)
        self.client.force_login(both)
        for church, mine, other in ((self.church_b, self.room_b, self.room), (self.church, self.room, self.room_b)):
            session = self.client.session
            session[SESSION_KEY] = church.pk
            session.save()
            self.assertEqual(self.client.get(url("week", mine)).status_code, 200, church.slug)
            self.assertEqual(self.client.get(url("week", other)).status_code, 404, church.slug)
            self.assertEqual(self.client.get(url("request", other)).status_code, 404, church.slug)
            page = self.client.get(LIST)
            self.assertContains(page, mine.name)
            self.assertNotContains(page, other.name)


class PolishRoundTests(SpacesFixture):
    def test_time_two_approved_bookings_share_counts_once(self):
        first = self.book(18, 20, person=self.admin_person, actor=self.admin, approve=True, setup_minutes=60)
        second = self.book(19, 20, title="Encima", person=self.admin_person, actor=self.admin, approve=True, seen=[first.pk])
        services.request_booking(self.room, self.admin_person, self.admin, title="Otra", approve=True, seen=[first.pk, second.pk],
                                 starts_at=self.at(19, 30), ends_at=self.at(21, 30))
        row = next(row for row in services.usage(self.church, self.day, self.day, self.tz) if row["space"] == self.room)
        self.assertEqual((row["bookings"], row["used_text"]), (3, "3 h 30 min"))   # 18:00 to 21:30 once, not 5 h

    def test_durations_in_words(self):
        for minutes, text in ((0, "Sin uso"), (45, "45 min"), (60, "1 h"), (90, "1 h 30 min"), (120, "2 h"), (1500, "25 h")):
            self.assertEqual(services.duration_text(timedelta(minutes=minutes)), text)

    def test_the_clash_and_the_two_choices_are_together(self):
        first = self.book()
        self.client.force_login(self.admin)
        html = self.client.post(url("request", self.room), self.form()).content.decode()
        self.assertEqual(html.count('role="alert"'), 1)                 # one summary, and the list is inside it
        summary = html[html.index('role="alert"'):html.index("</div>", html.index('role="alert"'))]
        self.assertIn("de 18:00 a 20:00: Ensayo", summary)
        self.assertLess(html.index("Reservar con el horario nuevo"), html.index("Reservar aunque se empalme"))
        self.assertIn(f'name="visto" value="{first.pk}"', html)

    def test_approving_in_place_returns_to_the_list_it_was_pressed_on_and_never_over_a_clash(self):
        """The queue approves in place (form[data-inline]) only a request that overlaps nothing; one that overlaps
        is sent as a page, and whatever the form said the server still answers a clash with the clash page."""
        free, taken = self.book(8, 9), self.book(18, 20)
        self.client.force_login(self.admin)
        over = self.client.post(url("request", self.room), self.form())
        self.client.post(url("request", self.room), self.form(sobre="1", visto=str(taken.pk)))
        self.assertContains(over, "Ese horario ya está apartado")
        html = self.client.get(QUEUE).content.decode()
        forms = {pk: html[html.index(f'action="{url("approve", pk)}"'):].split(">", 1)[0] for pk in (free, taken)}
        self.assertIn('data-inline="solicitudes"', forms[free])
        self.assertNotIn("data-inline", forms[taken])
        self.assertIn('<div id="solicitudes">', html)
        # A clash is the clash page, with or without «siguiente»; nothing is approved
        self.assertContains(self.client.post(url("approve", taken), {"siguiente": QUEUE}), "La reserva se empalma con otra")
        self.assertEqual(self.status(taken), Status.REQUESTED)
        # Back to the page it was pressed on; an address of another site is ignored
        self.assertRedirects(self.client.post(url("approve", free), {"siguiente": "https://otro.example/"}), QUEUE)
        self.assertEqual(self.status(free), Status.APPROVED)
        other = self.book(10, 11)
        self.assertRedirects(self.client.post(url("approve", other), {"siguiente": QUEUE + "?pagina=1"}), QUEUE + "?pagina=1")

    def test_every_row_of_what_you_asked_for_is_one_style(self):
        self.book(8, 9)
        self.book(10, 11, space=self.projector)
        self.client.force_login(self.leader_user)
        html = self.client.get(LIST).content.decode()
        self.assertNotIn("row--attention", html)
        self.assertEqual(html.count('<li class="row row--lead row--stack">'), 2)   # the day leads each one, its state under the title
        self.assertEqual(html.count('class="status '), 2)
