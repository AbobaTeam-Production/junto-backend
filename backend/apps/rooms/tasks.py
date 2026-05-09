import logging
import os
import shutil

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def cleanup_expired_rooms():
    """Delete media + per-room files for rooms whose `expires_at` has
    passed. Runs daily via Celery Beat.

    Walks `room.media_items` and calls `.delete()` on each so the
    `pre_delete` signal in apps.media_content.signals fires and removes
    `hls/<media_id>/`. Then nukes `uploads/<room_id>/` which holds
    reassembled chunks + yt-dlp downloads.
    """
    from apps.rooms.models import Room
    from django.conf import settings

    expired = Room.objects.filter(expires_at__lt=timezone.now(), status='active')
    rooms_count = 0
    media_count = 0

    for room in expired:
        for media in room.media_items.all():
            media.delete()  # triggers pre_delete -> hls/<media_id>/ removed
            media_count += 1

        upload_path = os.path.join(settings.MEDIA_ROOT, 'uploads', str(room.id))
        if os.path.exists(upload_path):
            try:
                shutil.rmtree(upload_path)
            except OSError as e:
                logger.warning('Failed to remove %s: %s', upload_path, e)

        room.status = 'expired'
        room.save(update_fields=['status'])
        rooms_count += 1

    return f'Cleaned up {rooms_count} expired rooms, {media_count} media items'


@shared_task
def prune_orphan_hls():
    """Walk MEDIA_ROOT/hls/ and remove dirs whose UUID isn't in MediaItem.

    Catches anything the pre_delete signal missed: directories left
    behind by transcode failures, manual DB rows nuked outside the ORM,
    or older deploys that pre-date the signal handler.
    """
    from apps.media_content.models import MediaItem
    from django.conf import settings

    hls_root = os.path.join(settings.MEDIA_ROOT, 'hls')
    if not os.path.isdir(hls_root):
        return 'hls/ does not exist'

    live_ids = {str(mid) for mid in MediaItem.objects.values_list('id', flat=True)}
    pruned = 0
    for entry in os.listdir(hls_root):
        full = os.path.join(hls_root, entry)
        if not os.path.isdir(full):
            continue
        if entry in live_ids:
            continue
        try:
            shutil.rmtree(full)
            pruned += 1
        except OSError as e:
            logger.warning('Failed to remove orphan %s: %s', full, e)

    return f'Pruned {pruned} orphan HLS dirs'
