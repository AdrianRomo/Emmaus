from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from .authz import forget_choice

        user_logged_in.connect(forget_choice, dispatch_uid="core.forget_church_choice")
