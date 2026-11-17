from django.apps import AppConfig


class OverviewConfig(AppConfig):
    """"Panorama": the pastor's view of the church in totals. No models and no writes: it counts
    what ``groups``, ``agenda``, ``serving`` and ``care`` hold, and never says who."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "overview"
    verbose_name = "Panorama"
