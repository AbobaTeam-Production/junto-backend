import json
import time

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from django.core.cache import cache


class RoomConsumer(AsyncJsonWebsocketConsumer):

    async def connect(self):
        self.room_id = self.scope['url_route']['kwargs']['room_id']
        self.room_group = f'room_{self.room_id}'
        user = self.scope.get('user')

        if not user or user.is_anonymous:
            await self.close()
            return

        self.username = user.username

        if not await self._user_in_room(user.id):
            await self.close()
            return

        await self.channel_layer.group_add(self.room_group, self.channel_name)

        # Track online users
        self._add_online_user(str(user.id), self.username)

        # Open (or resume) a WatchSession so the user's profile stats reflect
        # this session. Reconnects within 30s reuse the same row (page
        # reload, transient network drops) — see _open_watch_session.
        await self._open_watch_session(user.id)

        await self.accept()

        # Send current player state + current media
        state = await self._get_room_state()
        if state:
            await self.send_json({'event': 'state_sync', **state})

        current_media = self._get_current_media()
        if current_media:
            await self.send_json({'event': 'play_media', **current_media})

        # Send initial online users list
        online = self._get_online_users()
        await self.send_json({'event': 'online_users', 'users': online})

        # Notify others
        await self.channel_layer.group_send(self.room_group, {
            'type': 'user.joined',
            'username': self.username,
            'user_id': str(user.id),
        })

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group'):
            user_id = str(self.scope['user'].id)
            await self._close_watch_session(user_id)
            self._remove_online_user(user_id)
            await self.channel_layer.group_send(self.room_group, {
                'type': 'user.left',
                'username': self.username,
                'user_id': user_id,
            })
            await self.channel_layer.group_discard(self.room_group, self.channel_name)

    async def receive_json(self, content):
        event_type = content.get('event')
        user = self.scope['user']

        if event_type in ('play', 'pause', 'seek'):
            if not await self._is_host(user.id):
                return
            # Stamp server time so clients can drift-correct against their
            # own clock offset (Cristian's algorithm via /api/time/).
            content = {**content, 'server_ts': int(time.time() * 1000)}
            await self._save_room_state(content)
            await self.channel_layer.group_send(self.room_group, {
                'type': 'player.event',
                **content,
            })

        elif event_type == 'chat':
            text = content.get('text', '').strip()
            if not text:
                return
            await self._save_chat_message(user.id, text)
            await self.channel_layer.group_send(self.room_group, {
                'type': 'chat.message',
                'username': self.username,
                'text': text,
            })

        elif event_type == 'reaction':
            await self.channel_layer.group_send(self.room_group, {
                'type': 'reaction.event',
                'username': self.username,
                'emoji': content.get('emoji', ''),
                'x': content.get('x', 0.5),
                'y': content.get('y', 0.5),
            })

        elif event_type == 'play_media':
            if not await self._is_host(user.id):
                return
            media_info = {
                'hls_url': content.get('hls_url', ''),
                'raw_stream_url': content.get('raw_stream_url', ''),
                'title': content.get('title', ''),
                'source_type': content.get('source_type', 'upload'),
                'media_id': content.get('media_id', ''),
            }
            await self._save_room_state({
                'event': 'pause',
                'position': 0,
                'timestamp': '',
            })
            self._save_current_media(media_info)
            await self.channel_layer.group_send(self.room_group, {
                'type': 'media.play_item',
                **media_info,
            })

    # ─── Group message handlers ─────────────────────────

    async def player_event(self, event):
        await self.send_json({
            'event': event.get('event'),
            'position': event.get('position', 0),
            'timestamp': event.get('timestamp', ''),
            'server_ts': event.get('server_ts'),
        })

    async def chat_message(self, event):
        await self.send_json({
            'event': 'chat',
            'username': event['username'],
            'text': event['text'],
        })

    async def reaction_event(self, event):
        await self.send_json({
            'event': 'reaction',
            'username': event['username'],
            'emoji': event['emoji'],
            'x': event['x'],
            'y': event['y'],
        })

    async def user_joined(self, event):
        await self.send_json({
            'event': 'user_joined',
            'username': event['username'],
            'user_id': event['user_id'],
        })

    async def user_left(self, event):
        await self.send_json({
            'event': 'user_left',
            'username': event['username'],
            'user_id': event['user_id'],
        })

    async def media_ready(self, event):
        payload = {
            'event': 'media_ready',
            'title': event.get('title', ''),
            'source_type': event.get('source_type', 'upload'),
            'media_id': event.get('media_id', ''),
            'hls_url': event.get('hls_url', ''),
            'raw_stream_url': event.get('raw_stream_url', ''),
        }
        if event.get('youtube_video_id'):
            payload['youtube_video_id'] = event['youtube_video_id']
        await self.send_json(payload)

    async def media_progress(self, event):
        await self.send_json({
            'event': 'media_progress',
            'progress': event['progress'],
        })

    async def media_play_item(self, event):
        await self.send_json({
            'event': 'play_media',
            'hls_url': event.get('hls_url', ''),
            'raw_stream_url': event.get('raw_stream_url', ''),
            'title': event.get('title', ''),
            'source_type': event.get('source_type', 'upload'),
            'media_id': event.get('media_id', ''),
        })

    # ─── Helpers ────────────────────────────────────────

    @database_sync_to_async
    def _user_in_room(self, user_id):
        from apps.rooms.models import RoomMember
        return RoomMember.objects.filter(room_id=self.room_id, user_id=user_id).exists()

    @database_sync_to_async
    def _is_host(self, user_id):
        from apps.rooms.models import RoomMember
        return RoomMember.objects.filter(
            room_id=self.room_id, user_id=user_id, is_host=True
        ).exists()

    @database_sync_to_async
    def _save_chat_message(self, user_id, text):
        from apps.rooms.models import ChatMessage
        ChatMessage.objects.create(room_id=self.room_id, user_id=user_id, text=text)

    async def _get_room_state(self):
        state = cache.get(f'room_state_{self.room_id}')
        if state:
            return json.loads(state)
        return None

    async def _save_room_state(self, content):
        state = json.dumps({
            'status': content.get('event'),  # play, pause
            'position': content.get('position', 0),
            'timestamp': content.get('timestamp', ''),
            'server_ts': content.get('server_ts'),
        })
        cache.set(f'room_state_{self.room_id}', state, timeout=86400)

    def _save_current_media(self, media_info):
        cache.set(f'room_media_{self.room_id}', json.dumps(media_info), timeout=86400)

    def _get_current_media(self):
        data = cache.get(f'room_media_{self.room_id}')
        if data:
            return json.loads(data)
        return None

    def _add_online_user(self, user_id, username):
        key = f'room_{self.room_id}_online'
        online = cache.get(key) or {}
        online[user_id] = username
        cache.set(key, online, timeout=86400)

    def _remove_online_user(self, user_id):
        key = f'room_{self.room_id}_online'
        online = cache.get(key) or {}
        online.pop(user_id, None)
        cache.set(key, online, timeout=86400)

    def _get_online_users(self):
        key = f'room_{self.room_id}_online'
        return cache.get(key) or {}

    # ─── WatchSession lifecycle ────────────────────────────
    #
    # On connect we either resume the user's previous WatchSession (if it
    # closed less than 30 sec ago — page reload, network blip) or open a
    # fresh one. On disconnect we close the row and stash its id in the
    # cache for `_RECONNECT_TOLERANCE_SEC` so a fast reopen reuses it.

    _RECONNECT_TOLERANCE_SEC = 30

    async def _open_watch_session(self, user_id):
        cache_key = f'last_watch_session_{user_id}_{self.room_id}'
        candidate = cache.get(cache_key)
        sid = await self._reuse_or_create_session(user_id, candidate)
        self._watch_session_id = sid
        cache.delete(cache_key)

    async def _close_watch_session(self, user_id):
        sid = getattr(self, '_watch_session_id', None)
        if not sid:
            return
        await self._finalize_session(sid)
        cache.set(
            f'last_watch_session_{user_id}_{self.room_id}',
            sid,
            timeout=self._RECONNECT_TOLERANCE_SEC,
        )
        self._watch_session_id = None

    @database_sync_to_async
    def _reuse_or_create_session(self, user_id, candidate_id):
        from apps.social.models import WatchSession
        if candidate_id:
            session = WatchSession.objects.filter(
                id=candidate_id, user_id=user_id, room_id=self.room_id
            ).first()
            if session is not None:
                # Reopen — duration is recomputed on the next disconnect
                # against the original joined_at, so wiping `left_at` is
                # enough to mark it active again.
                session.left_at = None
                session.duration_sec = 0
                session.save(update_fields=['left_at', 'duration_sec'])
                return session.id
        return WatchSession.objects.create(
            user_id=user_id, room_id=self.room_id
        ).id

    @database_sync_to_async
    def _finalize_session(self, session_id):
        from django.utils import timezone
        from apps.social.models import WatchSession
        session = WatchSession.objects.filter(id=session_id).first()
        if session is None:
            return
        now = timezone.now()
        session.left_at = now
        session.duration_sec = max(0, int((now - session.joined_at).total_seconds()))
        session.save(update_fields=['left_at', 'duration_sec'])
