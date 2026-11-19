import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def fill_author(apps, schema_editor):
    """The account of the person record that wrote each request, where it still has one."""
    PrayerRequest = apps.get_model("oracion", "PrayerRequest")
    for row in PrayerRequest.objects.filter(author__isnull=True, person__user__isnull=False).select_related("person"):
        PrayerRequest.objects.filter(pk=row.pk).update(author_id=row.person.user_id)


class Migration(migrations.Migration):

    dependencies = [
        ("oracion", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="prayerrequest",
            name="author",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="+", to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(fill_author, migrations.RunPython.noop),
    ]
