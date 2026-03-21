from rest_framework import generics, permissions, status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.conf import settings

from .models import Room, RoomMember
from .serializers import RoomSerializer, RoomCreateSerializer, JoinRoomSerializer


class CreateRoomView(generics.CreateAPIView):
    serializer_class = RoomCreateSerializer

    def create(self, request, *args, **kwargs):
        room = Room.objects.create(host=request.user)
        RoomMember.objects.create(room=room, user=request.user, is_host=True)

        return Response({
            'room_id': str(room.id),
            'invite_code': room.invite_code,
            'expires_at': room.expires_at.isoformat(),
            'ws_url': f'/ws/room/{room.id}/',
        }, status=status.HTTP_201_CREATED)


class JoinRoomView(generics.CreateAPIView):
    serializer_class = JoinRoomSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        code = serializer.validated_data['invite_code'].upper()
        room = get_object_or_404(Room, invite_code=code, status='active')

        if room.is_expired:
            return Response(
                {'error': 'Комната истекла'},
                status=status.HTTP_410_GONE,
            )

        if room.members.count() >= 10:
            return Response(
                {'error': 'Комната заполнена (максимум 10 участников)'},
                status=status.HTTP_403_FORBIDDEN,
            )

        member, created = RoomMember.objects.get_or_create(
            room=room, user=request.user,
            defaults={'is_host': False},
        )

        return Response(
            RoomSerializer(room).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MyRoomsListView(generics.ListAPIView):
    serializer_class = RoomSerializer

    def get_queryset(self):
        return Room.objects.filter(
            members__user=self.request.user,
            status='active',
        ).order_by('-created_at')


class RoomDetailView(generics.RetrieveDestroyAPIView):
    serializer_class = RoomSerializer
    lookup_field = 'id'

    def get_queryset(self):
        return Room.objects.filter(members__user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        room = self.get_object()
        if room.host != request.user:
            return Response(
                {'error': 'Только хост может закрыть комнату'},
                status=status.HTTP_403_FORBIDDEN,
            )
        room.status = 'expired'
        room.save(update_fields=['status'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class RoomConfigView(generics.GenericAPIView):
    """Returns TURN server config for WebRTC."""

    def get(self, request, *args, **kwargs):
        return Response({
            'ice_servers': [
                {'urls': 'stun:stun.l.google.com:19302'},
                {
                    'urls': settings.TURN_SERVER_URL,
                    'username': settings.TURN_USERNAME,
                    'credential': settings.TURN_CREDENTIAL,
                },
            ] if settings.TURN_SERVER_URL else [
                {'urls': 'stun:stun.l.google.com:19302'},
            ]
        })
