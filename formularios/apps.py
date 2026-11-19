from django.apps import AppConfig


class FormulariosConfig(AppConfig):
    """Formularios: the general forms of a church (issue #92), what people answered, the one task an
    answer may leave for the staff, and "Pendientes", the read-only list of what waits for one person.

    Not the camp's questionnaires (``events``: confidential, never read from here) and not the
    anonymous opinions (``feedback``). Nothing here is scored, calculated, e-mailed or sent anywhere."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "formularios"
    verbose_name = "Formularios"
