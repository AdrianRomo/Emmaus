from django.utils import timezone

from core.models import Church, Role
from core.services import assign_role
from events.models import Event, EventMembership, EventParticipant, MembershipRole
from events.tests.base import CampFixture, make_user
from groups import services
from groups.models import Group, GroupMembership
from people import flags
from people.models import Person
from people.services import link_participant


class CommunityFixture(CampFixture):
    """The camp fixture after the camp: event A is closed, church A uses both modules.

    * ``member``: Diego (campero p2), linked to a person with an account and no role anywhere.
    * ``leader``: guides ``group``; holds nothing else.
    * ``coord1``: the former coordinator of Diego, now a person of the church with an account.
    * ``asked``: a person with an account whose request to join ``group`` is pending.
    * Church B (modules on) has its own admin, member, group and closed event.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Event.objects.filter(pk=cls.event.pk).update(status=Event.Status.CLOSED)
        cls.event.refresh_from_db()
        flags.set_enabled(cls.church, "people", True)
        flags.set_enabled(cls.church, "community", True)

        cls.admin_person = cls._person(cls.church, cls.admin)
        cls.member_user = make_user("diego@prueba.mx", "Diego", "Hernández Cruz")
        cls.member = cls._person(cls.church, cls.member_user, phone="55 1111 2222")
        link_participant(cls.member, cls.p2, cls.admin)
        cls.leader_user = make_user("guia@prueba.mx", "Gala", "Guía")
        cls.leader = cls._person(cls.church, cls.leader_user)
        cls.coord_person = cls._person(cls.church, cls.coord1)
        cls.asked_user = make_user("pidio@prueba.mx", "Pía", "Pidió")
        cls.asked = cls._person(cls.church, cls.asked_user)
        cls.no_account = Person.objects.create(church=cls.church, first_name="Nora", last_name="Sin Cuenta")

        cls.group = cls._group(cls.church, "Jóvenes Roca", meeting_day=3, meeting_place="Salón 2")
        cls.other_group = cls._group(cls.church, "Estudio de Romanos")
        services.set_membership(cls.group, cls.leader, cls.admin, role=GroupMembership.Role.LEADER)
        cls.pending, _ = services.request_to_join(cls.group, cls.asked, cls.asked_user)

        cls.church_b = Church.objects.create(name="Iglesia B", slug="iglesia-b")
        flags.set_enabled(cls.church_b, "people", True)
        flags.set_enabled(cls.church_b, "community", True)
        cls.admin_b = make_user("admin-b@prueba.mx", "Berta", "Admin")
        assign_role(cls.admin_b, cls.church_b, Role.CHURCH_ADMIN)
        cls.admin_b_person = cls._person(cls.church_b, cls.admin_b)
        cls.member_b_user = make_user("miembro-b@prueba.mx", "Bruno", "Miembro")
        cls.member_b = cls._person(cls.church_b, cls.member_b_user)
        cls.group_b = cls._group(cls.church_b, "Grupo de la iglesia B", meeting_place="Casa secreta B")
        cls.event_b = Event.objects.create(
            church=cls.church_b, name="Retiro B", slug="retiro-b", status=Event.Status.CLOSED,
            starts_on=timezone.localdate(), ends_on=timezone.localdate(),
        )
        cls.campero_b = EventParticipant.objects.create(
            event=cls.event_b, number=1, full_name="Bárbara Campera Be", age=16, room="1", church_group="B"
        )
        cls.m_coord_b = EventMembership.objects.create(event=cls.event_b, user=cls.member_b_user, role=MembershipRole.COORDINATOR)

    @staticmethod
    def _person(church, user, **extra) -> Person:
        return Person.objects.create(
            church=church, user=user, first_name=user.first_name, last_name=user.last_name, email=user.email, **extra
        )

    @staticmethod
    def _group(church, name, **extra) -> Group:
        return Group.objects.create(church=church, name=name, **extra)
