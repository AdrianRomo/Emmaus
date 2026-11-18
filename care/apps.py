from django.apps import AppConfig


class CareConfig(AppConfig):
    """Acompañar: the church notices somebody and one named person reaches out by a date. A task
    list with an owner and a due date, not a counselling record: nothing said in a conversation is
    written here, and nothing is e-mailed."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "care"
    verbose_name = "Acompañar"
