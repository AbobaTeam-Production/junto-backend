"""Social-graph + activity models.

Phase B introduces only `WatchSession`. Friendship/UserDevice will land
in later phases (C and FCM rework respectively).
"""

from django.conf import settings
from django.db import models


class WatchSession(models.Model):
    """One continuous watch span by `user` inside `room`.

    A session opens when the user's WS consumer connects, closes when it
    disconnects. We keep `duration_sec` denormalised so profile-page
    aggregations can `SUM` it without joining timestamps.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='watch_sessions',
    )
    room = models.ForeignKey(
        'rooms.Room',
        on_delete=models.CASCADE,
        related_name='watch_sessions',
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    duration_sec = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'watch_sessions'
        indexes = [
            models.Index(fields=['user', '-joined_at']),
        ]
        ordering = ['-joined_at']

    def __str__(self):
        end = self.left_at.isoformat() if self.left_at else 'open'
        return f'{self.user_id}@{self.room_id} {self.joined_at:%H:%M}–{end}'
