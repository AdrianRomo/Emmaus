from django.apps import AppConfig


class GroupsConfig(AppConfig):
    """Groups of a church: small groups people ask to join, guided by a leader (decision 0002).

    Group leadership is never a church role or an event membership, and nothing here reads an
    interview, an answer or a camp follow-up: a campero's answers stay out of a group by construction.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "groups"
    verbose_name = "Grupos"

    def ready(self):
        from django.db.models.signals import post_save

        from people.models import Person

        from .services import sync_person_assignments

        # An account linked to (or unlinked from) a person changes who holds that person's group roles.
        post_save.connect(sync_person_assignments, sender=Person, dispatch_uid="groups.sync_person_assignments")
