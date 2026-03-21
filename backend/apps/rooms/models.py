import random
import string
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Room(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('expired', 'Expired'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invite_code = models.CharField(max_length=6, unique=True, db_index=True)
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hosted_rooms',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    class Meta:
        db_table = 'rooms'
        ordering = ['-created_at']

    def __str__(self):
        return f'Room {self.invite_code}'

    def save(self, *args, **kwargs):
        if not self.invite_code:
            self.invite_code = self._generate_invite_code()
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(hours=24)
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_invite_code():
        for _ in range(100):
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            if not Room.objects.filter(invite_code=code).exists():
                return code
        raise RuntimeError('Failed to generate unique invite code')

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at


class RoomMember(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='room_memberships',
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    is_host = models.BooleanField(default=False)

    class Meta:
        db_table = 'room_members'
        unique_together = ('room', 'user')

    def __str__(self):
        role = 'host' if self.is_host else 'viewer'
        return f'{self.user.username} in {self.room.invite_code} ({role})'


class ChatMessage(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='messages')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chat_messages',
    )
    text = models.TextField(max_length=1000)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'chat_messages'
        ordering = ['sent_at']

    def __str__(self):
        return f'{self.user.username}: {self.text[:50]}'
