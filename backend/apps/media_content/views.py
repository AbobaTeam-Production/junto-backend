import os
import re
import unicodedata
import json as _json
import urllib.request
import urllib.error
from rest_framework import generics, status
from rest_framework.response import Response
from django.conf import settings
from django.shortcuts import get_object_or_404
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from apps.rooms.models import Room, RoomMember
from .models import MediaItem
from .serializers import (
    MediaItemSerializer,
    UploadChunkSerializer,
    TorrentAddSerializer,
    YouTubeAddSerializer,
)
from .tasks import transcode_to_hls


def _check_host(user, room_id):
    """Returns (room, error_response). If error_response is not None, return it."""
    room = get_object_or_404(Room, id=room_id, status='active')
    is_host = RoomMember.objects.filter(
        room=room, user=user, is_host=True
    ).exists()
    if not is_host:
        return room, Response(
            {'error': 'Только хост может добавлять контент'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return room, None


class UploadChunkView(generics.CreateAPIView):
    serializer_class = UploadChunkSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        room, err = _check_host(request.user, data['room_id'])
        if err:
            return err

        room_id = str(data['room_id'])
        upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', room_id)
        os.makedirs(upload_dir, exist_ok=True)

        chunk_path = os.path.join(upload_dir, f"chunk_{data['chunk_index']}")
        with open(chunk_path, 'wb') as f:
            for part in data['chunk'].chunks():
                f.write(part)

        # Check if all chunks received
        if data['chunk_index'] == data['total_chunks'] - 1:
            # Reassemble file — normalize Unicode to NFC to avoid macOS NFD mismatch
            filename = unicodedata.normalize('NFC', data['filename'])
            final_path = os.path.join(upload_dir, filename)
            with open(final_path, 'wb') as outfile:
                for i in range(data['total_chunks']):
                    cp = os.path.join(upload_dir, f'chunk_{i}')
                    with open(cp, 'rb') as infile:
                        outfile.write(infile.read())
                    os.remove(cp)

            # Create media item and start transcoding
            media_item = MediaItem.objects.create(
                room=room,
                source_type='upload',
                title=filename,
            )
            transcode_to_hls.delay(str(media_item.id), room_id, final_path)

            return Response({
                'media_id': str(media_item.id),
                'status': 'processing',
            }, status=status.HTTP_201_CREATED)

        return Response({
            'chunk_index': data['chunk_index'],
            'received': True,
        })


class TorrentAddView(generics.CreateAPIView):
    serializer_class = TorrentAddSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        room, err = _check_host(request.user, data['room_id'])
        if err:
            return err

        media_item = MediaItem.objects.create(
            room=room,
            source_type='torrent',
            original_url=data['magnet_link'],
            title='Torrent download...',
        )

        # TODO: torrent_worker.delay(str(media_item.id), str(room.id), data['magnet_link'])

        return Response({
            'media_id': str(media_item.id),
            'status': 'processing',
        }, status=status.HTTP_201_CREATED)


def _extract_rutube_id(url):
    """Extract Rutube video ID from URL."""
    match = re.search(r'rutube\.ru/video/(?:private/)?([a-f0-9]+)', url)
    return match.group(1) if match else None


class YouTubeAddView(generics.CreateAPIView):
    """Handles Rutube video links (endpoint kept as /youtube/ for compatibility)."""
    serializer_class = YouTubeAddSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        room, err = _check_host(request.user, data['room_id'])
        if err:
            return err

        video_id = _extract_rutube_id(data['url'])
        if not video_id:
            return Response(
                {'error': 'Неверная ссылка Rutube'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Fetch HLS URL from Rutube API
        try:
            req = urllib.request.Request(
                f'https://rutube.ru/api/play/options/{video_id}/',
                headers={'Accept': 'application/json'},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data_json = _json.loads(resp.read())
            hls_url = data_json.get('video_balancer', {}).get('default')
        except Exception:
            hls_url = None

        if not hls_url:
            return Response(
                {'error': 'Не удалось получить видео с Rutube'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get title from Rutube API
        title = 'Rutube video'
        try:
            req = urllib.request.Request(
                f'https://rutube.ru/api/video/{video_id}/',
                headers={'Accept': 'application/json'},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                info = _json.loads(resp.read())
                title = info.get('title', title)
        except Exception:
            pass

        media_item = MediaItem.objects.create(
            room=room,
            source_type='youtube',
            original_url=data['url'],
            hls_path=hls_url,
            title=title,
            status='ready',
        )

        # Notify room via WebSocket — send as regular HLS
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'room_{room.id}',
            {
                'type': 'media.ready',
                'hls_url': hls_url,
                'title': title,
                'source_type': 'upload',
            }
        )

        return Response({
            'media_id': str(media_item.id),
            'status': 'ready',
        }, status=status.HTTP_201_CREATED)


class MediaStatusView(generics.RetrieveDestroyAPIView):
    serializer_class = MediaItemSerializer
    lookup_field = 'id'

    def get_queryset(self):
        return MediaItem.objects.filter(room__members__user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        media = self.get_object()
        is_host = RoomMember.objects.filter(
            room=media.room, user=request.user, is_host=True
        ).exists()
        if not is_host:
            return Response(
                {'error': 'Только хост может удалять контент'},
                status=status.HTTP_403_FORBIDDEN,
            )
        media.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
