from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('movies', '0003_movie_trailer'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='movieview',
            new_name='movie_views_user_id_180219_idx',
            old_name='movie_views_user_id_watched_idx',
        ),
        migrations.RenameIndex(
            model_name='movieview',
            new_name='movie_views_movie_i_2329cb_idx',
            old_name='movie_views_movie_id_idx',
        ),
    ]
