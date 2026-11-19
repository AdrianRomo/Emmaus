from django.apps import AppConfig


class OracionConfig(AppConfig):
    """Peticiones de oración: what an adult person of the church writes for its pastors to pray about.

    The most sensitive free text in Emmaus (issue #97). Nothing is collected until a pastor of the
    church switched it on after accepting, on screen, what the church promises. Only the church's
    pastors read; nothing here is e-mailed, counted for anybody else, sent anywhere or registered in
    the Django admin. Not ``care``: a follow-up is a task with fixed reasons and no words; this is
    words and no task."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "oracion"
    verbose_name = "Peticiones de oración"
