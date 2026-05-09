import logging
import os
import shutil

from django.conf import settings
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import MediaItem

logger = logging.getLogger(__name__)


@receiver(pre_delete, sender=MediaItem)
def remove_hls_files_on_delete(sender, instance, **kwargs):
    """When a MediaItem is deleted (manual or via Room.delete cascade),
    remove its HLS output dir at MEDIA_ROOT/hls/<media_id>/.

    Transcode writes segments under hls/<media_id>/ — keyed by media UUID
    not room UUID — so the room-level cleanup task alone never touched
    them. This handler closes that gap.
    """
    hls_dir = os.path.join(settings.MEDIA_ROOT, 'hls', str(instance.id))
    if not os.path.exists(hls_dir):
        return
    try:
        shutil.rmtree(hls_dir)
    except OSError as e:
        logger.warning('Failed to remove %s: %s', hls_dir, e)
