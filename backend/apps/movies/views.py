"""Recommendations endpoints.

GET    /api/recs/feed/                       → Today's feed
GET    /api/recs/match/<friend_id>/          → Pair match
GET    /api/recs/mood/<slug>/                → Mood list
GET    /api/recs/title/<movie_id>/           → Movie detail + invite
POST   /api/recs/title/<movie_id>/intent/    → Toggle WatchIntent
POST   /api/recs/title/<movie_id>/invite/    → Create room w/ media expectation
"""

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rooms.models import Room
from apps.social.models import Friendship
from apps.social import push as fcm_push

from . import matching, presence
from .models import Movie, MoodList, WatchIntent
from .serializers import (
    MoodListSerializer,
    MoodEntrySerializer,
    MovieDetailSerializer,
    MovieMiniSerializer,
)


def _avatar_url(user) -> str | None:
    avatar = getattr(user, 'avatar', None)
    if not avatar:
        return None
    try:
        return avatar.url
    except (AttributeError, ValueError):
        return None


def _friends_of(user):
    """Returns User instances that share an `accepted` friendship with
    the caller, regardless of who initiated."""
    rows = (
        Friendship.objects
        .filter(status=Friendship.ACCEPTED)
        .filter(Q(from_user=user) | Q(to_user=user))
        .select_related('from_user', 'to_user')
    )
    out = []
    for r in rows:
        peer = r.to_user if r.from_user_id == user.id else r.from_user
        out.append(peer)
    return out


def _friend_payload(peer) -> dict:
    return {
        'id': peer.id,
        'username': peer.username,
        'avatar_url': _avatar_url(peer),
        'presence': presence.compute_presence(peer.id),
    }


class RecsFeedView(APIView):
    """The 'Что посмотреть вечером?' screen.

    Wraps three rows + a moods grid. Friends-online is computed from
    Redis; hero/social rows lean on WatchIntent + match%.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        me = request.user
        presence.mark_seen(me.id)

        # ── Friends with their presence ──
        friends = _friends_of(me)
        friends_payload = [_friend_payload(f) for f in friends]

        # ── Hero: top-rated unwatched feature-length movie ──
        # `duration_min >= 60` strips shorts and trailers — without
        # this poiskkino's top-N occasionally surfaces a 6-minute
        # student film that has 8.x rating from 200 votes.
        watched_ids = list(me.movie_views.values_list('movie_id', flat=True)[:200])
        hero_qs = (
            Movie.objects
            .filter(is_series=False, duration_min__gte=60)
            .exclude(id__in=watched_ids)
            .order_by('-kp_rating')[:5]
        )
        hero_movie = hero_qs.first()
        hero_payload = None
        if hero_movie is not None:
            # Friends to whom the hero would resonate — match%>=70
            hero_friends = []
            for f in friends[:6]:
                pct, insufficient = matching.match_percent(me.id, f.id)
                if pct is not None and pct >= 70:
                    hero_friends.append({**_friend_payload(f), 'match_percent': pct})
            hero_payload = {
                # Detail serializer here so the FE has synopsis +
                # backdrop without needing a follow-up fetch.
                'movie': MovieDetailSerializer(hero_movie).data,
                'match_percent': 0 if not hero_friends else max(p['match_percent'] for p in hero_friends),
                'why': '',
                'friends': hero_friends,
            }

        # ── Social row: a friend's WatchIntents ──
        social_row = None
        intent_friend = next((f for f in friends if WatchIntent.objects.filter(user=f).exists()), None)
        if intent_friend is not None:
            intent_movies = (
                Movie.objects
                .filter(watch_intents__user=intent_friend)
                .exclude(id__in=watched_ids)
                .distinct()[:8]
            )
            if intent_movies:
                social_row = {
                    'friend': _friend_payload(intent_friend),
                    'movies': MovieMiniSerializer(intent_movies, many=True).data,
                }

        # ── Top by KP — covers the case when social/intent signal is
        # thin (everyone's just signed up). 12 highest-rated unwatched
        # films excluding the hero so we don't show it twice.
        excluded = set(watched_ids)
        if hero_movie is not None:
            excluded.add(hero_movie.id)
        top_kp = (
            Movie.objects
            .filter(is_series=False, duration_min__gte=60)
            .exclude(id__in=excluded)
            .order_by('-kp_rating')[:12]
        )

        # ── Mood lists ──
        moods = (
            MoodList.objects
            .annotate(num=Count('entries'))
            .filter(num__gt=0)
            .order_by('position', 'title')[:8]
        )

        return Response({
            'friends_online': friends_payload,
            'hero': hero_payload,
            'social_row': social_row,
            'top_by_kp': MovieMiniSerializer(top_kp, many=True).data,
            'moods': MoodListSerializer(moods, many=True).data,
        })


class RecsMatchView(APIView):
    """Pair-match screen — Venn + shared tags + 'recommend together'."""
    permission_classes = [IsAuthenticated]

    def get(self, request, friend_id: int):
        me = request.user
        # Validate friendship
        is_friend = Friendship.objects.filter(
            status=Friendship.ACCEPTED,
        ).filter(
            (Q(from_user=me) & Q(to_user_id=friend_id))
            | (Q(from_user_id=friend_id) & Q(to_user=me))
        ).exists()
        if not is_friend:
            return Response({'detail': 'not friends'}, status=403)

        from apps.users.models import User
        peer = get_object_or_404(User, pk=friend_id)

        pct, insufficient = matching.match_percent(me.id, peer.id)
        shared, other = matching.shared_genres(me.id, peer.id)

        # Joint titles: movies in either user's recent views (or top KP)
        # the other hasn't seen, sorted by combined recency-weight.
        seen_a = set(me.movie_views.values_list('movie_id', flat=True)[:200])
        seen_b = set(peer.movie_views.values_list('movie_id', flat=True)[:200])
        candidates = (
            Movie.objects
            .filter(genres__in=shared)
            .exclude(id__in=seen_a | seen_b)
            .distinct()
            .order_by('-kp_rating')[:6]
            if shared else
            Movie.objects.none()
        )
        titles = []
        for movie in candidates:
            titles.append({
                'movie': MovieMiniSerializer(movie).data,
                'match_percent': pct,
                'why': f'Похоже на ваши общие жанры — {", ".join(g.name_ru for g in shared[:2])}'
                       if shared else '',
            })

        return Response({
            'friend': _friend_payload(peer),
            'match_percent': pct,
            'insufficient_data': insufficient,
            'shared_tags': [g.name_ru for g in shared],
            'not_shared_tags': [g.name_ru for g in other][:8],
            'titles': titles,
        })


class RecsMoodView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug: str):
        mood = get_object_or_404(MoodList, slug=slug)
        entries = mood.entries.select_related('movie').prefetch_related('movie__genres').order_by('position')
        return Response({
            'mood': MoodListSerializer(mood).data,
            'items': MoodEntrySerializer(entries, many=True).data,
        })


class RecsTitleView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, movie_id: int):
        me = request.user
        movie = get_object_or_404(Movie.objects.prefetch_related('genres'), pk=movie_id)
        why_reasons = self._why(me, movie)

        # Friends who'd like this — top-N by match%
        friends_payload = []
        for f in _friends_of(me):
            pct, insufficient = matching.match_percent(me.id, f.id)
            friends_payload.append({
                **_friend_payload(f),
                'match_percent': pct or 0,
                'insufficient_data': insufficient,
            })
        friends_payload.sort(key=lambda x: x['match_percent'], reverse=True)

        return Response({
            'movie': MovieDetailSerializer(movie).data,
            'match_percent': max((f['match_percent'] for f in friends_payload), default=0),
            'why_reasons': why_reasons,
            'friends_who_would_like': friends_payload[:6],
            'has_intent': WatchIntent.objects.filter(user=me, movie=movie).exists(),
        })

    @staticmethod
    def _why(user, movie) -> list[dict]:
        out = []
        target_genres = set(movie.genres.values_list('id', flat=True))

        # 1. "Похоже на: …" — pick the user's most-similar past watch
        # by Jaccard score (intersection / union of genres). Counting
        # raw overlap led to "everything similar to «Зелёная миля»"
        # because that movie is multi-genre and dominated the tie.
        # Jaccard rewards precision: a 2-genre match against a 2-genre
        # candidate beats a 2-genre match against a 6-genre candidate.
        if target_genres:
            best_match = None
            best_score = 0.0
            recent_views = (
                user.movie_views
                .select_related('movie')
                .prefetch_related('movie__genres')
                .order_by('-watched_at')[:30]
            )
            seen_movie_ids: set[int] = set()
            for view in recent_views:
                if view.movie_id == movie.id:
                    continue
                if view.movie_id in seen_movie_ids:
                    continue
                seen_movie_ids.add(view.movie_id)
                their_genres = set(view.movie.genres.values_list('id', flat=True))
                if not their_genres:
                    continue
                inter = len(target_genres & their_genres)
                if inter == 0:
                    continue
                union = len(target_genres | their_genres)
                jaccard = inter / union if union else 0
                if jaccard > best_score:
                    best_score = jaccard
                    best_match = view.movie
            # Require at least 0.34 overlap — keeps us from saying
            # «похоже на» when the only thing in common is one broad
            # bucket like 'drama'.
            if best_match is not None and best_score >= 0.34:
                out.append({
                    'icon': 'heart',
                    'text': f'Похоже на: «{best_match.title_ru}»',
                })

        # 2. Friends also intent on this same title.
        intent_friends = (
            WatchIntent.objects
            .filter(movie=movie)
            .exclude(user=user)
            .select_related('user')[:3]
        )
        if intent_friends:
            names = ', '.join(i.user.username for i in intent_friends)
            out.append({'icon': 'people', 'text': f'{names} тоже хочет'})

        # 3. Genre line — only show if we have at least one and we
        # didn't already explain similarity above (avoid stacking two
        # genre-shaped reasons).
        if movie.genres.exists() and not any(o['icon'] == 'heart' for o in out):
            genres = ', '.join(g.name_ru for g in movie.genres.all()[:2])
            out.append({'icon': 'film', 'text': f'Жанр близок твоим избранным: {genres}'})

        return out


class RecsTitleIntentView(APIView):
    """Toggle WatchIntent — POST creates, second POST removes."""
    permission_classes = [IsAuthenticated]

    def post(self, request, movie_id: int):
        movie = get_object_or_404(Movie, pk=movie_id)
        existing = WatchIntent.objects.filter(user=request.user, movie=movie).first()
        if existing:
            existing.delete()
            return Response({'has_intent': False})
        WatchIntent.objects.create(user=request.user, movie=movie)
        return Response({'has_intent': True}, status=201)


def _pick_best_torrent(results: list[dict]) -> dict | None:
    """Heuristic: aim for 1080p / FullHD with the most seeders.

    Tier order (best first):
      1. quality == 1080 or title hints at 1080p / FullHD
      2. quality == 2160 (4K — falls back if no FHD)
      3. quality == 720 / HD
      4. anything else with seeders
    Within each tier we sort by seed count desc.
    """
    if not results:
        return None

    def tier(it: dict) -> int:
        q = it.get('quality')
        try:
            qn = int(q) if q is not None else 0
        except (TypeError, ValueError):
            qn = 0
        name = (it.get('name') or '').lower()
        if qn == 1080 or '1080' in name or 'fullhd' in name or 'fhd' in name:
            return 0
        if qn == 2160 or '2160' in name or '4k' in name:
            return 1
        if qn == 720 or '720' in name or 'hd' in name:
            return 2
        return 3

    def seeds(it: dict) -> int:
        try:
            return int(it.get('seeds') or 0)
        except (TypeError, ValueError):
            return 0

    sortable = [r for r in results if (r.get('magnet') or '').strip()]
    if not sortable:
        return None
    sortable.sort(key=lambda r: (tier(r), -seeds(r)))
    # Need at least 1 seeder to be worth attaching automatically;
    # otherwise the user gets a stuck download in their fresh room.
    best = sortable[0]
    return best if seeds(best) >= 1 else None


def _attach_torrent_to_room(room, movie, request_user) -> dict | None:
    """Search jacred + pick best + spin up the download task. Returns
    a {magnet, name, quality, seeds} payload or None on failure."""
    from apps.media_content.views import _jacred_search
    from apps.media_content.models import MediaItem
    from apps.media_content.tasks import download_torrent

    # Prefer the original (English) title — trackers index in English far
    # more reliably than in Russian. Fall back to Russian if missing.
    queries = [q for q in [movie.title_orig, movie.title_ru] if q.strip()]
    chosen = None
    for q in queries:
        try:
            results = _jacred_search(q)
        except Exception:
            continue
        chosen = _pick_best_torrent(results)
        if chosen:
            break
    if chosen is None:
        return None

    media_item = MediaItem.objects.create(
        room=room,
        source_type='torrent',
        original_url=chosen['magnet'],
        title=chosen.get('name') or movie.title_ru,
    )
    download_torrent.delay(str(media_item.id), str(room.id), chosen['magnet'])
    return {
        'magnet': chosen['magnet'],
        'name': chosen.get('name', ''),
        'quality': chosen.get('quality'),
        'seeds': chosen.get('seeds'),
        'provider': chosen.get('provider', ''),
    }


class RecsTitleInviteView(APIView):
    """Creates a room around a movie + auto-attaches the best torrent.

    Flow:
      1. Create empty room + RoomMember (host = caller).
      2. Search jacred by movie title_orig (fallback title_ru).
      3. Pick best result (FHD-first, then by seeds).
      4. Spin up the download task — the room fills in itself.
      5. Mark WatchIntent so the social row picks the movie up.

    If jacred has nothing usable, the room is still created — the FE
    will open AddMediaSheet pre-filled with the title so the host can
    pick a torrent manually.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, movie_id: int):
        movie = get_object_or_404(Movie, pk=movie_id)
        room = Room.objects.create(host=request.user)
        from apps.rooms.models import RoomMember
        RoomMember.objects.create(room=room, user=request.user, is_host=True)
        WatchIntent.objects.get_or_create(user=request.user, movie=movie)

        attached = _attach_torrent_to_room(room, movie, request.user)

        # Friend invitations — drop pushes to every selected friend so
        # they get a tap-to-join notification. Only friends in an
        # accepted Friendship with the host are notified, so the field
        # can't be used to spam strangers.
        raw_ids = request.data.get('friend_ids') or []
        invited = _send_room_invites(
            host=request.user,
            room=room,
            movie=movie,
            requested_ids=raw_ids,
        )

        return Response({
            'room_id': str(room.id),
            'invite_code': room.invite_code,
            'movie': MovieMiniSerializer(movie).data,
            'media_attached': attached is not None,
            'attached_torrent': attached,
            'invited_friend_ids': invited,
        }, status=201)


def _send_room_invites(*, host, room, movie, requested_ids: list) -> list[int]:
    """Pushes a `room_invite` to every selected friend, returns the
    user_ids that actually got an invite (after the friendship check)."""
    if not requested_ids:
        return []
    try:
        ids = [int(i) for i in requested_ids]
    except (TypeError, ValueError):
        return []
    if not ids:
        return []

    # Ensure every requested id is a real friend of the host.
    pairs = (
        Friendship.objects
        .filter(status=Friendship.ACCEPTED)
        .filter(
            (Q(from_user=host) & Q(to_user_id__in=ids))
            | (Q(from_user_id__in=ids) & Q(to_user=host))
        )
        .values_list('from_user_id', 'to_user_id')
    )
    flat: set[int] = set()
    for f, t in pairs:
        if f == host.id:
            flat.add(t)
        elif t == host.id:
            flat.add(f)

    valid = list(flat & set(ids))
    if not valid:
        return []

    from django.contrib.auth import get_user_model
    user_model = get_user_model()
    invitees = list(user_model.objects.filter(id__in=valid))

    title_for_push = movie.title_ru or movie.title_orig or 'фильм'
    for u in invitees:
        try:
            fcm_push.send_to_user(
                u,
                title='Junto',
                body=f'{host.username} зовёт смотреть «{title_for_push}»',
                data={
                    'type': 'room_invite',
                    'room_id': str(room.id),
                    'invite_code': room.invite_code,
                    'inviter_id': host.id,
                    'inviter': host.username,
                    'movie_id': movie.id,
                    'movie_title': title_for_push,
                },
            )
        except Exception:
            # FCM failures are non-fatal — the invitee can still join
            # by following an invite link or seeing the room in their
            # Recs feed when it's live.
            pass
    return [u.id for u in invitees]
