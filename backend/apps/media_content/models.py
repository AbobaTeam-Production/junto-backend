import uuid
from django.db import models
from apps.rooms.models import Room


class MediaItem(models.Model):
    SOURCE_CHOICES = [
        ('upload', 'Upload'),
        ('torrent', 'Torrent'),
        ('youtube', 'YouTube'),
    ]
    STATUS_CHOICES = [
        ('processing', 'Processing'),
        ('ready', 'Ready'),
        ('error', 'Error'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='media_items')
    source_type = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    original_url = models.URLField(max_length=2000, blank=True, default='')
    # HLS playlist for browsers; transcoded by ffmpeg.
    hls_path = models.CharField(max_length=2000, blank=True, default='')
    # Raw stream URL — used by native clients (libmpv) which can play any
    # container/codec directly without going through HLS.
    raw_stream_url = models.URLField(max_length=2000, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='processing')
    duration_seconds = models.IntegerField(null=True, blank=True)
    title = models.CharField(max_length=500, blank=True, default='')
    progress = models.IntegerField(default=0)
    error_message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'media_items'
        ordering = ['created_at']

    def __str__(self):
        return f'{self.source_type}: {self.title or self.id}'
