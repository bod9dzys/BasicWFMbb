from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_alter_sickleaveproof_attachment"),
    ]

    operations = [
        migrations.AddField(
            model_name="sickleaveproof",
            name="proof_type",
            field=models.CharField(
                choices=[("sick_leave", "Запит на лікарняний")],
                default="sick_leave",
                max_length=32,
            ),
        ),
    ]
