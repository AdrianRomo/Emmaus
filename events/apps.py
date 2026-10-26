from django.apps import AppConfig


class EventsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "events"
    verbose_name = "Eventos y campamentos"

    def ready(self):
        from . import checks  # noqa: F401
