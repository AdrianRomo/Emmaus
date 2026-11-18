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
