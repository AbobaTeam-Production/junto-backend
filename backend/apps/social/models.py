"""Social-graph + activity models.

Adds `WatchSession` (phase B — recorded per WS connect to a room),
`Friendship` (phase C — mutual friend relation with pending/accepted
states) and `UserDevice` (phase E — FCM tokens for push delivery).
"""

from django.conf import settings
from django.db import models
from django.db.models import Q


class UserDevice(models.Model):
    """A push-capable device the user is signed in on.

    One row per (user, fcm_token). FCM tokens rotate (new install, app
    data clear, manual refresh) so the same device produces many rows
    over time — invalid tokens are pruned by `apps.social.push` when
    send returns UNREGISTERED / INVALID_ARGUMENT.
    """

    PLATFORM_ANDROID = 'android'
    PLATFORM_IOS = 'ios'
    PLATFORM_WEB = 'web'
    PLATFORMS = [
        (PLATFORM_ANDROID, 'Android'),
        (PLATFORM_IOS, 'iOS'),
        (PLATFORM_WEB, 'Web'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='devices',
    )
    fcm_token = models.TextField()
    platform = models.CharField(max_length=10, choices=PLATFORMS)
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_devices'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'fcm_token'],
                name='userdevice_unique_token_per_user',
            ),
        ]
        indexes = [
            models.Index(fields=['user']),
        ]

    def __str__(self):
        return f'{self.user_id}/{self.platform} {self.fcm_token[:12]}…'


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
