"""Friend presence — 'free' / 'busy' / 'idle' for the recs feed.

Source of truth is Redis: when a WS consumer connects to a room it
adds the user to `room_<id>_online`. The `presence_<user_id>` key is
written when the user opens any tab; not present → idle.

For the MVP we approximate:
- `busy`  — user is online and currently inside an active room
- `free`  — user has been seen recently (last 10 min) but isn't in a room
- `idle`  — anything else (no recent presence)
"""

import time

from django.core.cache import cache

from apps.rooms.models import Room


_FREE_WINDOW_SEC = 10 * 60


def _user_in_active_room(user_id: int) -> str | None:
    """Returns the invite_code of the room user is currently inside,
    or None if not in any active room."""
    # Cheap-but-thorough scan: iterate over the user's recent rooms
    # and check the per-room online cache. RoomMember points at the
    # link so this stays O(membership) per check.
    for room in (
        Room.objects
        .filter(members__user_id=user_id, status='active')
        .only('id', 'invite_code')[:25]
    ):
        online = cache.get(f'room_{room.id}_online') or {}
        if any(uid == user_id for uid in online.keys()):
            return room.invite_code
    return None


def _last_seen_at(user_id: int) -> int | None:
    raw = cache.get(f'presence_{user_id}')
    return int(raw) if raw else None


def compute_presence(user_id: int) -> str:
    if _user_in_active_room(user_id):
        return 'busy'
    last = _last_seen_at(user_id)
    if last is None:
        return 'idle'
    if (time.time() - last) <= _FREE_WINDOW_SEC:
        return 'free'
    return 'idle'


def mark_seen(user_id: int) -> None:
    cache.set(f'presence_{user_id}', int(time.time()), timeout=_FREE_WINDOW_SEC * 2)
