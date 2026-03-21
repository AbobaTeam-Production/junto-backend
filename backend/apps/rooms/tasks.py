import os
import shutil

from celery import shared_task
from django.utils import timezone


@shared_task
def cleanup_expired_rooms():
    """Remove expired rooms and their media files. Runs hourly via Celery Beat."""
    from apps.rooms.models import Room
    from django.conf import settings

    expired = Room.objects.filter(expires_at__lt=timezone.now(), status='active')
    count = 0

    for room in expired:
        # Clean up HLS files
        hls_path = os.path.join(settings.MEDIA_ROOT, 'hls', str(room.id))
        if os.path.exists(hls_path):
            shutil.rmtree(hls_path)

        # Clean up torrent files
        torrent_path = os.path.join(settings.MEDIA_ROOT, 'torrents', str(room.id))
        if os.path.exists(torrent_path):
            shutil.rmtree(torrent_path)

        # Clean up uploaded files
        upload_path = os.path.join(settings.MEDIA_ROOT, 'uploads', str(room.id))
        if os.path.exists(upload_path):
            shutil.rmtree(upload_path)

        # Clean up YouTube downloads
        yt_path = os.path.join(settings.MEDIA_ROOT, 'youtube', str(room.id))
        if os.path.exists(yt_path):
            shutil.rmtree(yt_path)

        room.status = 'expired'
        room.save(update_fields=['status'])
        count += 1

    return f'Cleaned up {count} expired rooms'
