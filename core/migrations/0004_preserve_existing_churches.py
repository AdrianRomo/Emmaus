"""Keep existing churches working after the camp redesign.

* Churches that already use discipleship paths keep the discipleship module visible.
* The demo participant login (participant@example.org) stops working outside development:
  camperos never have accounts, and a shared password must not survive in production.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    Church = apps.get_model("core", "Church")
    DiscipleshipPath = apps.get_model("core", "DiscipleshipPath")
    User = apps.get_model("auth", "User")
    church_ids = DiscipleshipPath.objects.values_list("church_id", flat=True).distinct()
    Church.objects.filter(pk__in=list(church_ids)).update(discipleship_enabled=True)
    User.objects.filter(email__iexact="participant@example.org").update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [("core", "0003_church_custom_theme_enabled_and_more")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
