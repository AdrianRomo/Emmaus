from django.apps import AppConfig


class ResourcesConfig(AppConfig):
    """Devocional y Recursos: the church writes one devotional per day and lists what it preached,
    sang or taught as links to the site that hosts it. A person reads, and only that person ever
    sees what they read. Nothing is e-mailed, embedded or fetched from another site."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "resources"
    verbose_name = "Devocional y recursos"
