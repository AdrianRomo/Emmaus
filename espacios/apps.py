from django.apps import AppConfig


class EspaciosConfig(AppConfig):
    """Espacios y equipo: the rooms and the equipment a church lends to its own activities, and who
    booked each one when. Nothing here is public, nothing is e-mailed, and a booking's title is read
    only by the administration and by who asked for it (``espacios.access``).

    The media library is ``resources`` ("Recursos"): this app never uses that word for a room."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "espacios"
    verbose_name = "Espacios y equipo"
