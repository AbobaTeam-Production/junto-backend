"""Serializers for the recommendations system."""

from rest_framework import serializers

from apps.social.models import Friendship, WatchSession
from apps.users.models import User
from .models import Genre, Movie, MoodList, MoodEntry


class GenreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Genre
        fields = ('id', 'slug', 'name_ru')


class MovieMiniSerializer(serializers.ModelSerializer):
    """Compact payload for grid / row cards."""
    genres = serializers.SerializerMethodField()

    class Meta:
        model = Movie
        fields = (
            'id', 'tmdb_id', 'title_ru', 'title_orig', 'year',
            'poster_url', 'poster_preview_url',
            'duration_min', 'kp_rating', 'genres',
        )

    def get_genres(self, obj):
        return [g.name_ru for g in obj.genres.all()]


class MovieDetailSerializer(MovieMiniSerializer):
    trailer_embed_url = serializers.SerializerMethodField()

    class Meta(MovieMiniSerializer.Meta):
        fields = MovieMiniSerializer.Meta.fields + (
            'backdrop_url', 'synopsis_ru', 'short_synopsis',
            'imdb_rating', 'is_series', 'trailer_embed_url',
        )

    def get_trailer_embed_url(self, obj):
        if obj.trailer_rutube_id:
            return f'https://rutube.ru/play/embed/{obj.trailer_rutube_id}/'
        return None


class FriendPresenceSerializer(serializers.Serializer):
    """User shape used inside recs payloads — name + tiny presence flag.

    `presence` is one of 'free' / 'busy' / 'idle' — see
    `apps.movies.presence.compute_presence`.
    """
    id = serializers.IntegerField()
    username = serializers.CharField()
    avatar_url = serializers.SerializerMethodField()
    presence = serializers.CharField()

    def get_avatar_url(self, obj):
        # `obj` is a dict carried through from the view, not a User.
        return obj.get('avatar_url')


class MoodListSerializer(serializers.ModelSerializer):
    count = serializers.SerializerMethodField()

    class Meta:
        model = MoodList
        fields = ('id', 'slug', 'title', 'subtitle', 'hue', 'count')

    def get_count(self, obj):
        return obj.entries.count()


class MoodEntrySerializer(serializers.ModelSerializer):
    movie = MovieMiniSerializer(read_only=True)

    class Meta:
        model = MoodEntry
        fields = ('id', 'position', 'why_text', 'movie')
