from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('movies', '0002_rename_kp_id_to_tmdb_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='movie',
            name='trailer_rutube_id',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='movie',
            name='trailer_lookup_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
