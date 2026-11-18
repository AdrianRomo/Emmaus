from django.apps import AppConfig


class AgendaConfig(AppConfig):
    """Events for the people of the church: the page the administration publishes, who signed up
    and who came.

    A registration is not a camp participant: nothing here writes ``EventParticipant``,
    ``EventMembership`` or ``RoleAssignment``, and signing up opens no screen of the camp.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "agenda"
    verbose_name = "Eventos para las personas"

    def ready(self):
        from people.models import age_facts_changed

        age_facts_changed.connect(_age_facts_changed, dispatch_uid="agenda.reevaluate_person")
        # Who is a minor is counted on the event's first day: when that day moves, the registrations
        # are settled again. On the real model only, so nothing fires inside a migration.
        from django.db.models.signals import post_save, pre_save

        from events.models import Event

        pre_save.connect(_event_first_day_before, sender=Event, dispatch_uid="agenda.event_first_day_before")
        post_save.connect(_event_first_day_after, sender=Event, dispatch_uid="agenda.event_first_day_after")


def _age_facts_changed(sender, person, actor=None, **kwargs):
    """A date of birth or its verification changed: settle the person's registrations again (decision 0015)."""
    from . import services

    services.reevaluate_person(person, actor)


_UNKNOWN = object()


def _event_first_day_before(sender, instance, raw=False, update_fields=None, **kwargs):
    """Before an event is saved: the first day the database has, when this save may change it. One
    cheap query; none for a new event, a fixture or a save that does not write ``starts_on``."""
    instance._agenda_first_day_before = _UNKNOWN
    if raw or instance._state.adding or (update_fields is not None and "starts_on" not in update_fields):
        return
    stored = sender.objects.filter(pk=instance.pk).values_list("starts_on", flat=True).first()
    instance._agenda_first_day_before = stored


def _event_first_day_after(sender, instance, created=False, raw=False, **kwargs):
    before = instance.__dict__.pop("_agenda_first_day_before", _UNKNOWN)
    if raw or created or before is _UNKNOWN or before == instance.starts_on:
        return
    from . import services

    services.event_first_day_moved(instance)
