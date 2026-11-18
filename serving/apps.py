from django.apps import AppConfig


class ServingConfig(AppConfig):
    """Servir: the church's volunteer teams, the dates they serve, who is asked and what they answer.

    Who may manage a team is ``core.authz.has_scope`` over ``access.RoleAssignment`` (scope
    ``ministry``, decision 0016), projected from ``TeamMembership`` by ``serving.services``. A team
    membership writes no ``UserChurchRole`` and no group or event role, and opens nothing outside
    its team.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "serving"
    verbose_name = "Servir"

    def ready(self):
        from django.db.models.signals import post_save

        from people.models import Person

        from .services import sync_person_assignments

        # An account linked to (or unlinked from) a person changes who holds that person's team roles.
        post_save.connect(sync_person_assignments, sender=Person, dispatch_uid="serving.sync_person_assignments")
