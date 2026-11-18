from django.apps import AppConfig


class PoliciesConfig(AppConfig):
    """Church policies (decision 0015): what a church decides for itself, stored in one row and read
    only through ``people.policy``, which enforces the platform's floors whatever the row says."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "policies"
    verbose_name = "Políticas de la iglesia"
