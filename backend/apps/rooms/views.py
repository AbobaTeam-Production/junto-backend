import datetime

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

        # Re-fetch with prefetch so RoomSerializer doesn't issue per-member queries.
        room = _rooms_with_relations(Room.objects.filter(pk=room.pk)).get()

        return Response(
            RoomSerializer(room).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


def _rooms_with_relations(qs):
    """Apply prefetch needed by RoomSerializer to avoid N+1 on members/media."""
    return qs.select_related('host').prefetch_related(
        'members__user',
        'media_items',
    )


class MyRoomsListView(generics.ListAPIView):
    serializer_class = RoomSerializer

    def get_queryset(self):
        return _rooms_with_relations(
            Room.objects.filter(
                members__user=self.request.user,
                status='active',
            )
        ).order_by('-created_at').distinct()


class RoomDetailView(generics.RetrieveDestroyAPIView):
    serializer_class = RoomSerializer
    lookup_field = 'id'

    def get_queryset(self):
        return _rooms_with_relations(
            Room.objects.filter(members__user=self.request.user)
        )

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


class LiveKitTokenView(generics.GenericAPIView):
    """Issues a short-lived LiveKit access token for the requesting member.

    The client connects to LIVEKIT_WS_URL with this token to join the LiveKit
    room (room name = Junto room UUID). Voice publish + subscribe permissions
    only — no admin/record/etc.
    """

    def get(self, request, *args, **kwargs):
        if not (settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET):
            return Response(
                {'error': 'LiveKit is not configured on the server'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        room = get_object_or_404(Room, id=kwargs['id'], status='active')
        if not RoomMember.objects.filter(room=room, user=request.user).exists():
            return Response(
                {'error': 'Not a member of this room'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from livekit import api as lk_api

        grants = lk_api.VideoGrants(
            room_join=True,
            room=str(room.id),
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        )
        token = (
            lk_api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
            .with_identity(str(request.user.id))
            .with_name(request.user.username)
            .with_grants(grants)
            .with_ttl(datetime.timedelta(hours=6))
        )

        # Build the WebSocket URL using the same host the client used to
        # reach us — this lets the same deployment serve clients on
        # junto.local, a LAN IP, or any other hostname without per-host
        # config. An explicit LIVEKIT_WS_URL overrides if set (useful when
        # LiveKit is reached via a different domain than the API).
        if settings.LIVEKIT_WS_URL:
            ws_url = settings.LIVEKIT_WS_URL
        else:
            ws_scheme = 'wss' if request.is_secure() else 'ws'
            ws_url = f'{ws_scheme}://{request.get_host()}/livekit'

        return Response({
            'url': ws_url,
            'token': token.to_jwt(),
            'room': str(room.id),
            'identity': str(request.user.id),
        })
