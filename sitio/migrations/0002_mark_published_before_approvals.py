from django.db import migrations

LEGACY = "anterior"  # resources.models.ContentApproval.State.LEGACY
KIND = "sitio"       # resources.models.ContentApproval.Kind.HOME


def mark_published_before(apps, schema_editor):
    """A public home that is published today was published before approvals existed. It stays public and
    gets a row that says so, with no approver (nobody is guessed), for a pastor to approve."""
    ContentApproval = apps.get_model("resources", "ContentApproval")
    published = apps.get_model("sitio", "PublicHome").objects.filter(published=True).values_list("church_id", "pk")
    ContentApproval.objects.bulk_create(
        [ContentApproval(church_id=church_id, kind=KIND, object_id=pk, state=LEGACY) for church_id, pk in published.iterator()],
        ignore_conflicts=True,
    )


def unmark(apps, schema_editor):
    apps.get_model("resources", "ContentApproval").objects.filter(state=LEGACY, kind=KIND).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("resources", "0004_mark_published_before_approvals"),
        ("sitio", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(mark_published_before, reverse_code=unmark),
    ]
