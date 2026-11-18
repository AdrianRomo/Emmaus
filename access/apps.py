from django.apps import AppConfig


class AccessConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "access"
    verbose_name = "Roles por ámbito"

    def ready(self):
        from django.db.models.signals import pre_save

        from people.models import Person

        from .sources import remember_account

        # What the sources' post_save hooks compare with, to re-derive roles only when the account changed.
        pre_save.connect(remember_account, sender=Person, dispatch_uid="access.remember_account")
