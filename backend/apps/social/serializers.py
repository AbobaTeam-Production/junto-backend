"""Serializers for the friends endpoints.

We always return *the other side* of a friendship (relative to the
authenticated user) plus the ID and status of the row itself, so the
frontend can build "Принять / Отклонить / Удалить" actions without
extra lookups.
"""

from rest_framework import serializers
from django.contrib.auth import get_user_model

from .models import Friendship, UserDevice, WatchSession

User = get_user_model()


class _PeerSerializer(serializers.ModelSerializer):
    """Compact user payload used inside friendship/search responses."""

    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('id', 'username', 'avatar_url')

    def get_avatar_url(self, obj):
        return obj.avatar.url if obj.avatar else None


class FriendshipSerializer(serializers.ModelSerializer):
    """Wraps a Friendship row + the *other* user (peer).

    Used both for the friend list and the pending requests inbox. Caller
    decides which side is "the peer" by passing `request.user` in context.
    """

    peer = serializers.SerializerMethodField()
    direction = serializers.SerializerMethodField()

    class Meta:
        model = Friendship
        fields = ('id', 'status', 'created_at', 'accepted_at', 'peer', 'direction')

    def _me(self):
        return self.context['request'].user

    def get_peer(self, obj):
        me = self._me()
        peer = obj.to_user if obj.from_user_id == me.id else obj.from_user
        return _PeerSerializer(peer).data

    def get_direction(self, obj):
        # 'outgoing' = current user initiated, 'incoming' = current user is the receiver.
        return 'outgoing' if obj.from_user_id == self._me().id else 'incoming'


class UserSearchSerializer(_PeerSerializer):
    """Search result row — adds a per-row friendship status hint so the
    UI can swap "Добавить" for "Заявка отправлена" / "Уже друзья" without
    a second roundtrip.
    """

    relation = serializers.SerializerMethodField()

    class Meta(_PeerSerializer.Meta):
        fields = (*_PeerSerializer.Meta.fields, 'relation')

    def get_relation(self, obj):
        # Annotated by UserSearchView (`_relation`) to avoid N+1.
        return getattr(obj, '_relation', 'none')


class UserDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDevice
        fields = ('id', 'fcm_token', 'platform', 'last_seen_at')
        read_only_fields = ('id', 'last_seen_at')


class WatchSessionSerializer(serializers.ModelSerializer):
    """Wraps a WatchSession + minimal Room context.

    Room has no human-readable title, so we ship `invite_code` and let
    the client render "Комната <code>". `room_status` lets the UI
    enable the Зайти button only for still-active rooms.
    """

    room_id = serializers.UUIDField(source='room.id', read_only=True)
    room_invite_code = serializers.CharField(source='room.invite_code', read_only=True)
    room_status = serializers.SerializerMethodField()

    class Meta:
        model = WatchSession
        fields = (
            'id',
            'room_id',
            'room_invite_code',
            'room_status',
            'joined_at',
            'duration_sec',
        )

    def get_room_status(self, obj):
        # Effective status: a room past its expires_at is reported as
        # 'expired' even if the cron hasn't flipped its status field yet.
        room = obj.room
        if room.status == 'active' and room.is_expired:
            return 'expired'
        return room.status
