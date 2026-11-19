from django.db import migrations

LEGACY = "anterior"  # ContentApproval.State.LEGACY


def mark_published_before(apps, schema_editor):
    """What is published today was published before approvals existed. It stays published; each one gets
    a row that says so, with no approver (nobody is guessed), for a pastor to approve item by item."""
    ContentApproval = apps.get_model("resources", "ContentApproval")
    for kind, model in (("devocional", "Devotional"), ("plan", "ReadingPlan")):
        published = apps.get_model("resources", model).objects.filter(status="publicado").values_list("church_id", "pk")
        ContentApproval.objects.bulk_create(
            [ContentApproval(church_id=church_id, kind=kind, object_id=pk, state=LEGACY) for church_id, pk in published.iterator()],
            ignore_conflicts=True,
        )


def unmark(apps, schema_editor):
    apps.get_model("resources", "ContentApproval").objects.filter(state=LEGACY, kind__in=("devocional", "plan")).delete()


class Migration(migrations.Migration):
    # Its own migration: on PostgreSQL, rows are not written in the transaction that creates their table.

    dependencies = [
        ("resources", "0003_content_approval"),
    ]

    operations = [
        migrations.RunPython(mark_published_before, reverse_code=unmark),
    ]
