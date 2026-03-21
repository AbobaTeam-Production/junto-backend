import json
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

        # Track user channel for direct signaling
        await self._set_user_channel(user.id, self.channel_name)

        # Track online users
        self._add_online_user(str(user.id), self.username)

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
            await self._remove_user_channel(self.scope['user'].id)
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

        elif event_type in ('webrtc_offer', 'webrtc_answer', 'ice_candidate'):
            target_user_id = content.get('target')
            if target_user_id:
                target_channel = await self._get_user_channel(target_user_id)
                if target_channel:
                    await self.channel_layer.send(target_channel, {
                        'type': 'webrtc.signal',
                        'event': event_type,
                        'from_user': str(user.id),
                        'from_username': self.username,
                        'sdp': content.get('sdp'),
                        'candidate': content.get('candidate'),
                    })

    # ─── Group message handlers ─────────────────────────

    async def player_event(self, event):
        await self.send_json({
            'event': event.get('event'),
            'position': event.get('position', 0),
            'timestamp': event.get('timestamp', ''),
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
        }
        if event.get('hls_url'):
            payload['hls_url'] = event['hls_url']
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
            'title': event.get('title', ''),
            'source_type': event.get('source_type', 'upload'),
            'media_id': event.get('media_id', ''),
        })

    async def webrtc_signal(self, event):
        # Strip Django Channels internal 'type' key before sending to client
        payload = {k: v for k, v in event.items() if k != 'type'}
        await self.send_json(payload)

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
        })
        cache.set(f'room_state_{self.room_id}', state, timeout=86400)

    async def _set_user_channel(self, user_id, channel_name):
        cache.set(f'room_{self.room_id}_user_{user_id}_channel', channel_name, timeout=86400)

    async def _remove_user_channel(self, user_id):
        cache.delete(f'room_{self.room_id}_user_{user_id}_channel')

    async def _get_user_channel(self, user_id):
        return cache.get(f'room_{self.room_id}_user_{user_id}_channel')

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
