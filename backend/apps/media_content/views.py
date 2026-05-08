import os
import re
import unicodedata

import httpx
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

        from apps.media_content.tasks import download_torrent
        download_torrent.delay(str(media_item.id), str(room.id), data['magnet_link'])

        return Response({
            'media_id': str(media_item.id),
            'status': 'processing',
        }, status=status.HTTP_201_CREATED)


def _extract_rutube_id(url):
    """Extract Rutube video ID from URL."""
    match = re.search(r'rutube\.ru/video/(?:private/)?([a-f0-9]+)', url)
    return match.group(1) if match else None


def _rutube_get_json(client: httpx.Client, path: str, video_id: str) -> dict | None:
    """Fetch JSON from Rutube API. Browser-like UA is required."""
    headers = {
        'Accept': 'application/json',
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Referer': f'https://rutube.ru/video/{video_id}/',
    }
    try:
        resp = client.get(f'https://rutube.ru/{path}', headers=headers)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError):
        return None


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

        with httpx.Client(timeout=10) as client:
            options = _rutube_get_json(client, f'api/play/options/{video_id}/?format=json', video_id)
            hls_url = (options or {}).get('video_balancer', {}).get('default')

            if not hls_url:
                return Response(
                    {'error': 'Не удалось получить видео с Rutube'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            info = _rutube_get_json(client, f'api/video/{video_id}/', video_id) or {}
            title = info.get('title') or 'Rutube video'

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


_BTIH_RE = re.compile(r'urn:btih:([0-9a-fA-F]{40}|[A-Z2-7]{32})', re.IGNORECASE)


def _magnet_hash(magnet: str) -> str:
    m = _BTIH_RE.search(magnet or '')
    return m.group(1).lower() if m else ''


def _jacred_search(query: str) -> list[dict]:
    """Query jacred and map result items to the schema the frontend already
    understands, plus an inline `magnet` field so the client no longer needs
    a separate /torrent/magnet/ round-trip.
    """
    base = os.environ.get('JACRED_URL', '').rstrip('/')
    if not base:
        raise RuntimeError('JACRED_URL is not configured')

    params = {'search': query, 'sort': 'sid'}
    apikey = os.environ.get('JACRED_APIKEY', '').strip()
    if apikey:
        params['apikey'] = apikey

    resp = httpx.get(
        f'{base}/api/v1.0/torrents',
        params=params,
        headers={
            'Accept': 'application/json',
            # Public mirrors (e.g. jac.red) reject default python/httpx UA.
            'User-Agent': 'Mozilla/5.0 (Junto/1.0)',
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    # jacred returns {} when nothing found, list otherwise.
    if not isinstance(data, list):
        return []

    results = []
    for it in data:
        magnet = (it.get('magnet') or '').strip()
        if not magnet:
            continue
        results.append({
            'id': _magnet_hash(magnet),
            'provider': it.get('tracker', ''),
            'name': it.get('title') or it.get('name') or '',
            'size': it.get('sizeName', ''),
            'seeds': str(it.get('sid') or 0),
            'peers': str(it.get('pir') or 0),
            'date': it.get('updateTime', ''),
            'category': it.get('videotype', ''),
            'quality': it.get('quality', 0),
            'voices': list(it.get('voices') or []),
            # Year of the release + media kind. The recs auto-picker
            # uses these to drop sequels and series when the user
            # invited around a specific film. `relased` is the
            # upstream typo, propagated here for callers that match
            # on the original field name.
            'year': it.get('relased'),
            'types': list(it.get('types') or []),
            'magnet': magnet,
        })
    return results


class TorrentSearchView(generics.GenericAPIView):
    """Search torrents via jacred and return inline magnet links."""

    def get(self, request):
        query = request.query_params.get('q', '').strip()
        if not query:
            return Response({'error': 'Параметр q обязателен'}, status=status.HTTP_400_BAD_REQUEST)

        from django.core.cache import cache
        cache_key = f'jacred_search:{query.lower()}'
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        try:
            results = _jacred_search(query)
        except RuntimeError as e:
            return Response({'error': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except (httpx.HTTPError, ValueError) as e:
            return Response({'error': f'Поиск недоступен: {str(e)[:200]}'},
                           status=status.HTTP_502_BAD_GATEWAY)

        cache.set(cache_key, results, timeout=300)
        return Response(results)


class TorrentMagnetView(generics.GenericAPIView):
    """Deprecated: jacred returns magnet links inline in /torrent/search/.

    Old clients that still call this endpoint receive 410 with a hint;
    new clients should read `magnet` directly from search results.
    """

    def get(self, request):
        return Response(
            {'error': 'Endpoint deprecated. Use `magnet` field from /torrent/search/ results.'},
            status=status.HTTP_410_GONE,
        )


class RequestTranscodeView(generics.GenericAPIView):
    """Triggers HLS transcoding for a torrent media item on demand.

    Browser clients (Web) need HLS to play any non-MP4 container; native
    clients use the raw stream URL directly. We don't want to spend CPU on
    transcoding videos that no Web client will ever watch, so transcoding
    is deferred to first call here.

    Idempotent: if transcoding is already in flight or finished, returns
    the current state without spawning another ffmpeg.
    """

    def post(self, request, *args, **kwargs):
        from django.core.cache import cache
        from .tasks import transcode_to_hls

        media = get_object_or_404(
            MediaItem, id=kwargs['id'],
            room__members__user=request.user,
        )

        if media.hls_path:
            return Response({'state': 'ready', 'hls_url': media.hls_path})

        lock_key = f'transcode_lock_{media.id}'
        if cache.get(lock_key):
            return Response({'state': 'in_progress'})

        input_url = cache.get(f'transcode_input_{media.id}')
        if not input_url:
            return Response(
                {'error': 'No transcode input registered for this media'},
                status=status.HTTP_409_CONFLICT,
            )

        # 2h covers a full transcode + a generous safety margin; cleared on
        # success/error inside _transcode_with_progress (see tasks.py).
        cache.set(lock_key, '1', timeout=7200)
        transcode_to_hls.delay(str(media.id), str(media.room_id), input_url)
        return Response({'state': 'queued'})


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
