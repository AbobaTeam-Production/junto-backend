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
        return obj.members.count()

    def get_media(self, obj):
        from apps.media_content.models import MediaItem
        items = MediaItem.objects.filter(room=obj).order_by('created_at')
        return MediaItemSerializer(items, many=True).data


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
