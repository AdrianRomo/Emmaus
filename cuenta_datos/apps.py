from django.apps import AppConfig


class CuentaDatosConfig(AppConfig):
    """«Mis datos en Emmaus» and «Quién puede qué» (first bounded parts of issue #98).

    What a person can know and take about themself in one church (``registry``: one list of
    categories, and for every foreign key to a person or an account either a category or a written
    reason why it is not the person's to download), the request to be removed (a task for the
    administration, never a deletion), and the read-only review of who holds a power in the church
    (``powers``, from the tables authorization reads). Nothing here deletes or changes another app's row."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "cuenta_datos"
    verbose_name = "Mis datos y permisos"
