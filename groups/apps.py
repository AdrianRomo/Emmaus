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
        from django.db.models.signals import post_delete, post_save

        from access.sources import forget_scope, forget_source_row, register
        from people.models import Person

        from .models import Group, GroupMembership
        from .services import SOURCE, live_memberships, sync_person_assignments

        register("group", SOURCE, live_memberships)
        # The projection has no foreign key to its source: a deleted membership (or one that went with
        # its person or its group) takes its role row with it.
        post_delete.connect(forget_source_row(SOURCE), sender=GroupMembership, dispatch_uid="groups.forget_role", weak=False)
        post_delete.connect(forget_scope("group"), sender=Group, dispatch_uid="groups.forget_scope", weak=False)

        # An account linked to (or unlinked from) a person changes who holds that person's group roles.
        post_save.connect(sync_person_assignments, sender=Person, dispatch_uid="groups.sync_person_assignments")
