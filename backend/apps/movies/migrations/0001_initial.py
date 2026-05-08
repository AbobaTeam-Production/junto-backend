import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Genre',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(max_length=64, unique=True)),
                ('name_ru', models.CharField(max_length=64)),
            ],
            options={
                'db_table': 'movie_genres',
                'ordering': ['name_ru'],
            },
        ),
        migrations.CreateModel(
            name='Movie',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kp_id', models.BigIntegerField(db_index=True, unique=True)),
                ('title_ru', models.CharField(max_length=255)),
                ('title_orig', models.CharField(blank=True, default='', max_length=255)),
                ('year', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('poster_url', models.URLField(blank=True, default='', max_length=500)),
                ('poster_preview_url', models.URLField(blank=True, default='', max_length=500)),
                ('backdrop_url', models.URLField(blank=True, default='', max_length=500)),
                ('duration_min', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('synopsis_ru', models.TextField(blank=True, default='')),
                ('short_synopsis', models.TextField(blank=True, default='')),
                ('kp_rating', models.DecimalField(blank=True, decimal_places=2, max_digits=4, null=True)),
                ('imdb_rating', models.DecimalField(blank=True, decimal_places=2, max_digits=4, null=True)),
                ('is_series', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('genres', models.ManyToManyField(blank=True, related_name='movies', to='movies.genre')),
            ],
            options={
                'db_table': 'movies',
                'ordering': ['-kp_rating', '-year'],
            },
        ),
        migrations.CreateModel(
            name='MoodList',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(max_length=64, unique=True)),
                ('title', models.CharField(max_length=128)),
                ('subtitle', models.CharField(blank=True, default='', max_length=255)),
                ('hue', models.PositiveSmallIntegerField(default=75)),
                ('position', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'mood_lists',
                'ordering': ['position', 'title'],
            },
        ),
        migrations.CreateModel(
            name='MoodEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('position', models.PositiveSmallIntegerField(default=0)),
                ('why_text', models.CharField(blank=True, default='', max_length=255)),
                ('mood', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entries', to='movies.moodlist')),
                ('movie', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='movies.movie')),
            ],
            options={
                'db_table': 'mood_entries',
                'ordering': ['position'],
            },
        ),
        migrations.AddField(
            model_name='moodlist',
            name='movies',
            field=models.ManyToManyField(related_name='mood_lists', through='movies.MoodEntry', to='movies.movie'),
        ),
        migrations.AddConstraint(
            model_name='moodentry',
            constraint=models.UniqueConstraint(fields=('mood', 'movie'), name='moodentry_unique_mood_movie'),
        ),
        migrations.CreateModel(
            name='WatchIntent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('movie', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='watch_intents', to='movies.movie')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='watch_intents', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'watch_intents',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='watchintent',
            constraint=models.UniqueConstraint(fields=('user', 'movie'), name='watchintent_unique_user_movie'),
        ),
        migrations.CreateModel(
            name='MovieView',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('watched_at', models.DateTimeField(auto_now_add=True)),
                ('duration_sec', models.PositiveIntegerField(default=0)),
                ('movie', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='views', to='movies.movie')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='movie_views', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'movie_views',
                'ordering': ['-watched_at'],
                'indexes': [
                    models.Index(fields=['user', '-watched_at'], name='movie_views_user_id_watched_idx'),
                    models.Index(fields=['movie'], name='movie_views_movie_id_idx'),
                ],
            },
        ),
    ]
