from django.db import migrations, models
import core.models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_sickleaveproof"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sickleaveproof",
            name="attachment",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to=core.models.sick_leave_proof_upload_to,
            ),
        ),
    ]
