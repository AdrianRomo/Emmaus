from django.apps import AppConfig


class FeedbackConfig(AppConfig):
    """What people thought of an event after it ended: three short questions, answered once and
    stored without the person's name. The administration reads totals, never who said what."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "feedback"
    verbose_name = "Opiniones de los eventos"
