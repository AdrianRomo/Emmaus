from django.apps import AppConfig


class SitioConfig(AppConfig):
    """The church's public home (issue #87, a prototype): what a visitor reads at ``/iglesia/<slug>/``
    without an account. It shows only what the administration published on purpose: the church's
    name, a welcome, when it meets, where, how to ask, the events whose page is public and the
    resources marked one by one. Never a person, a count or anything behind the sign-in."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "sitio"
    verbose_name = "Página pública de la iglesia"
