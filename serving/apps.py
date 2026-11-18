from django.apps import AppConfig


class ServingConfig(AppConfig):
    """Servir: the church's volunteer teams, the dates they serve, who is asked and what they answer.

    Known deviation from decision 0003: who may do what is decided by the rows of this app (an
    active ``TeamMembership`` with the role "coordina") and by church ``manage``, not by
    ``core.authz.has_scope`` over ``access.RoleAssignment``. A ministry scope there needs a new
    scope type and a migration in ``access``; until that is decided, a team membership writes no
    ``RoleAssignment``, no ``UserChurchRole`` and no group or event role, and opens nothing
    outside this app.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "serving"
    verbose_name = "Servir"
