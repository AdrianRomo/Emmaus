from django.apps import AppConfig


class NoticesConfig(AppConfig):
    """Avisos: the church's administration tells the whole church something, who guides a group
    tells their group, and each person reads what is for them in "Mi iglesia". Nothing is e-mailed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "notices"
    verbose_name = "Avisos"
