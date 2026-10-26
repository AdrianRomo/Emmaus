"""The church's own logo: two optional files under MEDIA_ROOT (core.logos validates them). Existing churches
get empty values and keep the painted "Emmaus" wordmark. Both columns have a database default, so code from
before this migration can still create a church. Reversing drops the columns; the files stay on disk."""

import core.logos
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0006_clear_legacy_english_notice")]

    operations = [
        migrations.AddField(
            model_name="church",
            name="logo",
            field=models.FileField(
                blank=True,
                db_default="",
                default="",
                upload_to="church-logos/",
                validators=[core.logos.validate_church_logo],
                verbose_name="Logotipo",
            ),
        ),
        migrations.AddField(
            model_name="church",
            name="logo_dark",
            field=models.FileField(
                blank=True,
                db_default="",
                default="",
                upload_to="church-logos/",
                validators=[core.logos.validate_church_logo],
                verbose_name="Logotipo para fondo oscuro",
            ),
        ),
    ]
