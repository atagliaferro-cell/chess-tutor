from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0002_userprofile_puzzle_rating'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PuzzleRatingAttempt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('category_slug', models.SlugField(max_length=80)),
                ('puzzle_index', models.PositiveSmallIntegerField()),
                ('perfect', models.BooleanField(default=False)),
                ('delta', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='puzzle_rating_attempts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['category_slug', 'puzzle_index'],
                'unique_together': {('user', 'category_slug', 'puzzle_index')},
            },
        ),
    ]
