from django.apps import AppConfig


class AgendaConfig(AppConfig):
    """Events for the people of the church: the page the administration publishes, who signed up
    and who came.

    A registration is not a camp participant: nothing here writes ``EventParticipant``,
    ``EventMembership`` or ``RoleAssignment``, and signing up opens no screen of the camp.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "agenda"
    verbose_name = "Eventos para las personas"

    def ready(self):
        from people.models import age_facts_changed

        age_facts_changed.connect(_age_facts_changed, dispatch_uid="agenda.reevaluate_person")


def _age_facts_changed(sender, person, actor=None, **kwargs):
    """A date of birth or its verification changed: settle the person's registrations again (decision 0015)."""
    from . import services

    services.reevaluate_person(person, actor)
