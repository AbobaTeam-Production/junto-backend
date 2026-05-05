import re

from rest_framework import serializers
from .models import MediaItem


class MediaItemSerializer(serializers.ModelSerializer):
    hls_url = serializers.SerializerMethodField()
    youtube_video_id = serializers.SerializerMethodField()
    raw_stream_url = serializers.CharField(read_only=True)

    class Meta:
        model = MediaItem
        fields = ('id', 'room', 'source_type', 'original_url', 'hls_url',
                  'raw_stream_url', 'youtube_video_id', 'status',
                  'duration_seconds', 'title', 'progress', 'created_at')
        read_only_fields = ('id', 'status', 'hls_url', 'raw_stream_url',
                            'youtube_video_id', 'duration_seconds',
                            'progress', 'created_at')

    def get_hls_url(self, obj):
        if obj.hls_path:
            # External URLs (e.g. Rutube HLS) are returned as-is
            if obj.hls_path.startswith('http'):
                return obj.hls_path
            return f'/media/{obj.hls_path}'
        return None

    def get_youtube_video_id(self, obj):
        """Extract Rutube video ID from URL (field kept as youtube_video_id for compatibility)."""
        if obj.source_type == 'youtube' and obj.original_url:
            match = re.search(
                r'rutube\.ru/video/(?:private/)?([a-f0-9]+)',
                obj.original_url,
            )
            return match.group(1) if match else None
        return None


class UploadChunkSerializer(serializers.Serializer):
    chunk = serializers.FileField()
    chunk_index = serializers.IntegerField(min_value=0)
    total_chunks = serializers.IntegerField(min_value=1)
    room_id = serializers.UUIDField()
    filename = serializers.CharField(max_length=500)


class TorrentAddSerializer(serializers.Serializer):
    room_id = serializers.UUIDField()
    magnet_link = serializers.CharField(max_length=2000)


class YouTubeAddSerializer(serializers.Serializer):
    room_id = serializers.UUIDField()
    url = serializers.URLField()
