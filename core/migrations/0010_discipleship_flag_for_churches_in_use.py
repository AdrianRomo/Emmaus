"""Turn the discipleship module on for every church that already uses it.

Church.discipleship_enabled now gates the leaders' discipleship bar and where /dashboard/ sends a pastor. 0004 turned
it on only for churches that had paths at the time; a church that got its first path or participant later, or has
participants but no path, would lose its bar. Idempotent (only turns the flag on); the reverse leaves it as it is.
"""

from django.db import migrations


def turn_on_where_in_use(apps, schema_editor):
    Church = apps.get_model("core", "Church")
    DiscipleshipPath = apps.get_model("core", "DiscipleshipPath")
    ParticipantProfile = apps.get_model("core", "ParticipantProfile")
    in_use = set(DiscipleshipPath.objects.values_list("church_id", flat=True)) | set(
        ParticipantProfile.objects.values_list("church_id", flat=True)
    )
    Church.objects.filter(pk__in=in_use, discipleship_enabled=False).update(discipleship_enabled=True)


class Migration(migrations.Migration):
    dependencies = [("core", "0009_alter_followuprequest_status")]

    operations = [migrations.RunPython(turn_on_where_in_use, migrations.RunPython.noop)]
