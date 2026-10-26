"""Churches created under 0001 kept its English participant notice: 0003 changed only the column
default. Clear that exact text so their participants read the Spanish notice (core.views.participant_notice).
A notice the church wrote itself is left alone. Nothing to undo: the reverse is a no-op."""

from django.db import migrations

LEGACY_ENGLISH_NOTICE = (
    "Emmaus supports discipleship follow-up through your church. It is not emergency support, therapy, "
    "medical advice, legal advice, or a replacement for pastoral care."
)


def forwards(apps, schema_editor):
    Church = apps.get_model("core", "Church")
    Church.objects.filter(basic_disclaimer_text=LEGACY_ENGLISH_NOTICE).update(basic_disclaimer_text="")


class Migration(migrations.Migration):
    dependencies = [("core", "0005_alter_aidraft_options_alter_church_options_and_more")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
