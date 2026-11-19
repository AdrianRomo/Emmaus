from django.urls import reverse

from core.models import Role
from core.services import assign_role
from events.tests.base import PASSWORD, make_user
from formularios.tests.base import FormFixture

__all__ = ["PASSWORD", "DataFixture", "url"]


def url(name, *args):
    return reverse(f"cuenta_datos:{name}", args=args)


class DataFixture(FormFixture):
    """The forms fixture (church A with an administration, a pastor who is not the administration, a guide, a
    member; church B with its own administration and member) plus a mentor. Every account signs in with ``PASSWORD``."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.mentor_user = make_user("mentor@prueba.mx", "Memo", "Mentor")
        assign_role(cls.mentor_user, cls.church, Role.MENTOR)
