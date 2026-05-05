from rest_framework import serializers
from .models import Room, RoomMember, ChatMessage
from apps.users.serializers import UserSerializer
from apps.media_content.serializers import MediaItemSerializer


class RoomMemberSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = RoomMember
        fields = ('user', 'joined_at', 'is_host')


class RoomSerializer(serializers.ModelSerializer):
    members = RoomMemberSerializer(many=True, read_only=True)
    host = UserSerializer(read_only=True)
    member_count = serializers.SerializerMethodField()
    media = serializers.SerializerMethodField()

    class Meta:
        model = Room
        fields = ('id', 'invite_code', 'host', 'created_at', 'expires_at',
                  'status', 'members', 'member_count', 'media')
        read_only_fields = ('id', 'invite_code', 'host', 'created_at',
                            'expires_at', 'status')

    def get_member_count(self, obj):
        # Use prefetched members to avoid extra COUNT query.
        return len(obj.members.all())

    def get_media(self, obj):
        # Relies on Room.media_items related manager (Meta orders by created_at).
        return MediaItemSerializer(obj.media_items.all(), many=True).data


class RoomCreateSerializer(serializers.Serializer):
    """No input needed — room is created with auto-generated code."""
    pass


class JoinRoomSerializer(serializers.Serializer):
    invite_code = serializers.CharField(max_length=6, min_length=6)


class ChatMessageSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = ChatMessage
        fields = ('id', 'username', 'text', 'sent_at')
