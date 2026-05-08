"""Movie metadata + curated lists + per-user signals.

Backbone for the recommendations system. Movies are upserted from
poiskkino.dev (see poiskkino.py), genres are mirrored from the same
source, mood lists are hand-curated through the admin.
"""

from django.conf import settings
from django.db import models


class Genre(models.Model):
    """Кинопоиск-grade genre slug + Russian display name."""
    slug = models.SlugField(max_length=64, unique=True)
    name_ru = models.CharField(max_length=64)

    class Meta:
        db_table = 'movie_genres'
        ordering = ['name_ru']

    def __str__(self):
        return self.name_ru


class Movie(models.Model):
    """One movie with metadata pulled from poiskkino.dev.

    `tmdb_id` is the upstream The Movie DB ID — primary external key.
    We upsert by it on every fetch so refresh-on-demand keeps posters
    / ratings fresh. Source moved from poiskkino.dev → TMDb (via the
    cf-worker-tmdb reverse-proxy).
    """
    tmdb_id = models.BigIntegerField(unique=True, db_index=True)
    title_ru = models.CharField(max_length=255)
    title_orig = models.CharField(max_length=255, blank=True, default='')
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    poster_url = models.URLField(max_length=500, blank=True, default='')
    poster_preview_url = models.URLField(max_length=500, blank=True, default='')
    backdrop_url = models.URLField(max_length=500, blank=True, default='')
    duration_min = models.PositiveSmallIntegerField(null=True, blank=True)
    synopsis_ru = models.TextField(blank=True, default='')
    short_synopsis = models.TextField(blank=True, default='')
    kp_rating = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
    )
    imdb_rating = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
    )
    is_series = models.BooleanField(default=False)
    # Rutube video id for the trailer. Lazy-fetched on the first
    # /api/recs/title/<id>/ request and cached on the row, so the
    # title detail page can show a real play button. Empty when we
    # have searched and found nothing.
    trailer_rutube_id = models.CharField(max_length=64, blank=True, default='')
    trailer_lookup_at = models.DateTimeField(null=True, blank=True)
    genres = models.ManyToManyField(Genre, related_name='movies', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'movies'
        ordering = ['-kp_rating', '-year']

    def __str__(self):
        return f'{self.title_ru} ({self.year or "?"})'


class MoodList(models.Model):
    """Curated themed list (e.g. «Уютная пятница»).

    `hue` is a 0-360 degree number the frontend maps to a tinted
    accent for the tile background — keeps the bento grid visually
    varied without storing CSS colours.
    """
    slug = models.SlugField(max_length=64, unique=True)
    title = models.CharField(max_length=128)
    subtitle = models.CharField(max_length=255, blank=True, default='')
    hue = models.PositiveSmallIntegerField(default=75)
    position = models.PositiveSmallIntegerField(default=0)
    movies = models.ManyToManyField(
        Movie,
        through='MoodEntry',
        related_name='mood_lists',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mood_lists'
        ordering = ['position', 'title']

    def __str__(self):
        return self.title


class MoodEntry(models.Model):
    """A movie's place inside a MoodList plus a curator-written 'why'.

    The why-text becomes the magazine-list line on the Mood screen.
    """
    mood = models.ForeignKey(
        MoodList, on_delete=models.CASCADE, related_name='entries',
    )
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE)
    position = models.PositiveSmallIntegerField(default=0)
    why_text = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'mood_entries'
        ordering = ['position']
        constraints = [
            models.UniqueConstraint(
                fields=['mood', 'movie'],
                name='moodentry_unique_mood_movie',
            ),
        ]


class WatchIntent(models.Model):
    """Per-user 'хочу посмотреть' flag.

    Drives the 'Аня хочет посмотреть' social row on the feed and the
    rec-detail invite flow ('Аня смотрит трейлер уже 3 раз').
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='watch_intents',
    )
    movie = models.ForeignKey(
        Movie, on_delete=models.CASCADE, related_name='watch_intents',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'watch_intents'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'movie'],
                name='watchintent_unique_user_movie',
            ),
        ]


class MovieView(models.Model):
    """Real-world watch event — feeds the match% genre-overlap.

    Distinct from `social.WatchSession`: WatchSession is a per-room WS
    connection span, MovieView is one user actually watched one movie.
    A future job can roll WatchSession + MediaItem into MovieView; for
    now we write it from the room screen on first play.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='movie_views',
    )
    movie = models.ForeignKey(
        Movie, on_delete=models.CASCADE, related_name='views',
    )
    watched_at = models.DateTimeField(auto_now_add=True)
    duration_sec = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'movie_views'
        ordering = ['-watched_at']
        indexes = [
            models.Index(fields=['user', '-watched_at']),
            models.Index(fields=['movie']),
        ]
