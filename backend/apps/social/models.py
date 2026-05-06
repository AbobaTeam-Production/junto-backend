"""Social-graph + activity models.

Adds `WatchSession` (phase B — recorded per WS connect to a room) and
`Friendship` (phase C — mutual friend relation with pending/accepted
states). UserDevice lands later when push lands.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q


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


class Friendship(models.Model):
    """Mutual friendship between two users.

    Stored as a single row `(from_user → to_user)` capturing who initiated
    the request. While `status='pending'` only the sender sees the request
    in their outbox and the receiver sees it in their inbox. Accepting
    flips status to `accepted`; declining or unfriending deletes the row.
    """

    PENDING = 'pending'
    ACCEPTED = 'accepted'
    STATUSES = [(PENDING, 'Pending'), (ACCEPTED, 'Accepted')]

    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='friendships_sent',
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='friendships_received',
    )
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'friendships'
        constraints = [
            models.UniqueConstraint(
                fields=['from_user', 'to_user'],
                name='friendship_unique_pair',
            ),
            # Symmetric reverse uniqueness (B→A also blocks A→B from existing
            # in parallel) is enforced in the view layer because Django can't
            # express "swapped pair" as a CHECK on a single row.
            models.CheckConstraint(
                check=~Q(from_user=models.F('to_user')),
                name='friendship_no_self',
            ),
        ]
        indexes = [
            models.Index(fields=['to_user', 'status']),
            models.Index(fields=['from_user', 'status']),
        ]

    def __str__(self):
        return f'{self.from_user_id}→{self.to_user_id} {self.status}'
