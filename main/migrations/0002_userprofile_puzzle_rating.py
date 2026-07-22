from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='puzzle_rating',
            field=models.PositiveIntegerField(default=100),
        ),
    ]
