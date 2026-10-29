from django.apps import AppConfig


class PeopleConfig(AppConfig):
    """People of a church, independent of accounts and events (decision 0002).

    Nothing in the application reads this app yet: it is the additive first step of the next
    version. The camp (`events`) and the discipleship module (`core`) are untouched.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "people"
    verbose_name = "Personas"
