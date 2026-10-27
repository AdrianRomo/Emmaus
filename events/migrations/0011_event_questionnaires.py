# Several questionnaires per event: Event.questionnaire_version becomes EventQuestionnaire rows (ordered), and
# Interview gets its questionnaire, so "one live interview" is per campero AND questionnaire.
#
# Expand/contract: the model loses Event.questionnaire_version here, but the column events_event.questionnaire_version_id
# stays in the database (nullable, emptied below) for one release; a migration of the next release drops it.

from collections import defaultdict

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Count, OuterRef, Subquery


def link_existing_versions(apps, schema_editor):
    """Each event's questionnaire_version becomes its first link; each interview learns its questionnaire."""
    Event = apps.get_model("events", "Event")
    EventQuestionnaire = apps.get_model("events", "EventQuestionnaire")
    Interview = apps.get_model("events", "Interview")
    QuestionnaireVersion = apps.get_model("events", "QuestionnaireVersion")
    EventQuestionnaire.objects.bulk_create(
        [
            EventQuestionnaire(
                event_id=event.pk,
                questionnaire_id=event.questionnaire_version.questionnaire_id,
                version_id=event.questionnaire_version_id,
                order=1,
            )
            for event in Event.objects.exclude(questionnaire_version=None).select_related("questionnaire_version")
        ]
    )
    Interview.objects.update(
        questionnaire_id=Subquery(QuestionnaireVersion.objects.filter(pk=OuterRef("version_id")).values("questionnaire_id")[:1])
    )
    # The column outlives the field: empty it, so no version is held through a value nothing reads any more.
    Event.objects.update(questionnaire_version=None)
    recompute_interview_statuses(apps)
    _check_deferred_constraints_now(schema_editor)


def recompute_interview_statuses(apps) -> int:
    """Store the status ``services.event_questionnaires.derive_interview_status`` gives each campero now.

    The old single field let an event switch to another questionnaire after camperos completed the first one: they
    stayed "completada" although the questionnaire the event uses is unanswered. Same rule as the service: a live
    interview -> "en_curso"; every linked questionnaire completed (at least one linked) -> "completada"; else
    "pendiente". Writes only the rows that change (``updated_at`` untouched: nobody edited them); returns how many."""
    EventParticipant = apps.get_model("events", "EventParticipant")
    EventQuestionnaire = apps.get_model("events", "EventQuestionnaire")
    Interview = apps.get_model("events", "Interview")
    linked = defaultdict(set)
    for event_id, questionnaire_id in EventQuestionnaire.objects.values_list("event_id", "questionnaire_id"):
        linked[event_id].add(questionnaire_id)
    live = set(Interview.objects.filter(status="en_curso").values_list("participant_id", flat=True))
    done = defaultdict(set)
    for participant_id, questionnaire_id in Interview.objects.filter(status="completada").values_list(
        "participant_id", "questionnaire_id"
    ):
        done[participant_id].add(questionnaire_id)
    changed = 0
    # A list, not .iterator(): SQLite gives no isolation between a cursor and updates to the table it reads.
    rows = list(EventParticipant.objects.order_by("pk").values_list("pk", "event_id", "interview_status"))
    for pk, event_id, stored in rows:
        if pk in live:
            status = "en_curso"
        elif linked[event_id] and linked[event_id] <= done[pk]:
            status = "completada"
        else:
            status = "pendiente"
        if status != stored:
            EventParticipant.objects.filter(pk=pk).update(interview_status=status)
            changed += 1
    return changed


def restore_single_version(apps, schema_editor):
    """Back to one version per event: the one of its lowest-order link (none: no questionnaire)."""
    Event = apps.get_model("events", "Event")
    EventQuestionnaire = apps.get_model("events", "EventQuestionnaire")
    Event.objects.update(
        questionnaire_version_id=Subquery(
            EventQuestionnaire.objects.filter(event_id=OuterRef("pk")).order_by("order", "id").values("version_id")[:1]
        )
    )
    _check_deferred_constraints_now(schema_editor)


def _refuse_reverse_with_multiple_live_interviews(apps, schema_editor):
    """Reverse only: the old index allows one non-discarded interview per campero, so re-adding it over camperos with
    interviews of several questionnaires fails half-way with an IntegrityError. Say what to do instead; delete nothing.
    The last operation of this migration, so on reverse it runs before the indexes are swapped."""
    Interview = apps.get_model("events", "Interview")
    n = (
        Interview.objects.exclude(status="descartada")
        .order_by()
        .values("participant")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .count()
    )
    if n:
        raise RuntimeError(
            f"No se puede revertir events/0011: {n} campero{'s' if n != 1 else ''} {'tienen' if n != 1 else 'tiene'} "
            "entrevistas de varios cuestionarios. Descarta las sobrantes antes de revertir."
        )


def _check_deferred_constraints_now(schema_editor):
    """PostgreSQL refuses ALTER TABLE on a table with pending deferred foreign-key checks, and the later operations of
    this migration (both ways) alter these tables in the same transaction: run the checks now."""
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        schema_editor.execute("SET CONSTRAINTS ALL DEFERRED")


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0010_privacy_notice_who_reads"),
    ]

    operations = [
        migrations.CreateModel(
            name="EventQuestionnaire",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order", models.PositiveSmallIntegerField(default=1, verbose_name="Orden")),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="questionnaire_links", to="events.event"
                    ),
                ),
                (
                    "questionnaire",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="event_links", to="events.questionnaire"
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="event_links", to="events.questionnaireversion"
                    ),
                ),
            ],
            options={
                "verbose_name": "cuestionario del evento",
                "verbose_name_plural": "cuestionarios del evento",
                "ordering": ["order", "id"],
                "constraints": [
                    models.UniqueConstraint(fields=("event", "questionnaire"), name="one_version_per_questionnaire_per_event")
                ],
            },
        ),
        migrations.AddField(
            model_name="event",
            name="questionnaires_in_order",
            field=models.BooleanField(
                db_default=False,
                default=False,
                help_text="Con varios cuestionarios, el siguiente se abre cuando el campero completó el anterior.",
                verbose_name="Los camperos los responden en este orden",
            ),
        ),
        migrations.AddField(
            model_name="interview",
            name="questionnaire",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="interviews",
                to="events.questionnaire",
                verbose_name="cuestionario",
            ),
        ),
        migrations.RunPython(link_existing_versions, restore_single_version),
        migrations.AlterField(
            model_name="interview",
            name="questionnaire",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="interviews",
                to="events.questionnaire",
                verbose_name="cuestionario",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="interview",
            name="one_live_interview_per_participant",
        ),
        migrations.AddConstraint(
            model_name="interview",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "descartada"), _negated=True),
                fields=("participant", "questionnaire"),
                name="one_live_interview_per_questionnaire",
            ),
        ),
        # Contract later: the field leaves the model now, the column leaves the database in the next release.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="event",
                    name="questionnaire_version",
                ),
            ],
            database_operations=[],
        ),
        # Keep LAST: reversed operations run last-first, so this check runs before the old index comes back.
        migrations.RunPython(migrations.RunPython.noop, _refuse_reverse_with_multiple_live_interviews),
    ]
