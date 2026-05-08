"""Friends + devices + history endpoints.

POST   /api/friends/request/                  → send friend request
POST   /api/friends/<id>/accept/              → accept incoming request
DELETE /api/friends/<id>/                     → decline pending OR unfriend accepted
GET    /api/friends/                          → list of accepted friendships
GET    /api/friends/requests/                 → pending requests inbox
GET    /api/users/search/?q=...               → search users (paginated)

POST   /api/users/devices/                    → register/refresh FCM token
DELETE /api/users/devices/<id>/               → unregister device (logout)
POST   /api/users/devices/test-push/          → DEBUG-only: send a push to self

GET    /api/profile/sessions/?limit=&offset=  → my watch-session history
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Friendship, UserDevice, WatchSession
from . import push
from .serializers import (
    FriendshipSerializer,
    UserDeviceSerializer,
    UserSearchSerializer,
    WatchSessionSerializer,
)

User = get_user_model()


def _accepted_q(user):
    return Q(status=Friendship.ACCEPTED) & (Q(from_user=user) | Q(to_user=user))


class FriendListView(generics.ListAPIView):
    serializer_class = FriendshipSerializer

    def get_queryset(self):
        return (
            Friendship.objects
            .filter(_accepted_q(self.request.user))
            .select_related('from_user', 'to_user')
            .order_by('-accepted_at')
        )


class FriendRequestsView(generics.ListAPIView):
    """Incoming pending requests only. Outgoing pending state is exposed
    via the user-search `relation` field so the UI can flip "Add" to
    "Заявка отправлена" without a separate outbox endpoint.
    """
    serializer_class = FriendshipSerializer

    def get_queryset(self):
        return (
            Friendship.objects
            .filter(to_user=self.request.user, status=Friendship.PENDING)
            .select_related('from_user', 'to_user')
            .order_by('-created_at')
        )


class FriendRequestSendView(APIView):
    def post(self, request):
        target_id = request.data.get('user_id')
        if not target_id:
            return Response({'detail': 'user_id required'}, status=400)
        try:
            target = User.objects.get(pk=int(target_id))
        except (User.DoesNotExist, ValueError, TypeError):
            return Response({'detail': 'no such user'}, status=404)
        if target.id == request.user.id:
            return Response({'detail': 'cannot friend yourself'}, status=400)
        if target.username.startswith('Гость_'):
            return Response({'detail': 'guests cannot be friended'}, status=400)

        # If they already sent us a request, our "send" auto-accepts theirs
        # — saves a click for the common "we both pressed Add at once" case.
        reverse = Friendship.objects.filter(
            from_user=target, to_user=request.user
        ).first()
        if reverse is not None:
            if reverse.status == Friendship.PENDING:
                reverse.status = Friendship.ACCEPTED
                reverse.accepted_at = timezone.now()
                reverse.save(update_fields=['status', 'accepted_at'])
                # Auto-accept path: notify the original sender that their
                # request just turned into a friendship.
                push.send_to_user(
                    target,
                    title='Junto',
                    body=f'{request.user.username} принял(а) вашу заявку',
                    data={'type': 'friend_accepted',
                          'friendship_id': reverse.id,
                          'from_id': request.user.id},
                )
            return Response(
                FriendshipSerializer(reverse, context={'request': request}).data,
                status=200,
            )

        existing = Friendship.objects.filter(
            from_user=request.user, to_user=target
        ).first()
        if existing is not None:
            return Response(
                FriendshipSerializer(existing, context={'request': request}).data,
                status=200,
            )

        f = Friendship.objects.create(from_user=request.user, to_user=target)
        push.send_to_user(
            target,
            title='Junto',
            body=f'{request.user.username} хочет добавить вас в друзья',
            data={'type': 'friend_request',
                  'friendship_id': f.id,
                  'from_id': request.user.id},
        )
        return Response(
            FriendshipSerializer(f, context={'request': request}).data,
            status=201,
        )


class FriendRequestAcceptView(APIView):
    def post(self, request, pk):
        f = get_object_or_404(
            Friendship, pk=pk, to_user=request.user, status=Friendship.PENDING
        )
        f.status = Friendship.ACCEPTED
        f.accepted_at = timezone.now()
        f.save(update_fields=['status', 'accepted_at'])
        push.send_to_user(
            f.from_user,
            title='Junto',
            body=f'{request.user.username} принял(а) вашу заявку',
            data={'type': 'friend_accepted',
                  'friendship_id': f.id,
                  'from_id': request.user.id},
        )
        return Response(
            FriendshipSerializer(f, context={'request': request}).data
        )


class FriendshipDestroyView(generics.DestroyAPIView):
    """Used for both 'decline pending' and 'unfriend accepted'. Either
    side of the relationship may delete it.
    """

    def get_queryset(self):
        u = self.request.user
        return Friendship.objects.filter(Q(from_user=u) | Q(to_user=u))


class UserSearchView(generics.ListAPIView):
    """Paginated user search by username substring. Excludes self and
    guest accounts. Each row carries a `relation` field (`none` /
    `pending_outgoing` / `pending_incoming` / `accepted`) so the UI can
    pick the right action button without a follow-up call.
    """
    serializer_class = UserSearchSerializer
    pagination_class = LimitOffsetPagination

    def get_queryset(self):
        q = (self.request.query_params.get('q') or '').strip()
        if not q:
            return User.objects.none()
        me = self.request.user
        return (
            User.objects
            .filter(is_active=True, username__icontains=q)
            .exclude(pk=me.pk)
            .exclude(username__startswith='Гость_')
            .order_by('username')
        )

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(qs)
        users = list(page) if page is not None else list(qs[:20])
        self._annotate_relations(users, request.user)
        data = self.get_serializer(users, many=True).data
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    @staticmethod
    def _annotate_relations(users, me):
        """Set `_relation` on each user object based on Friendship rows
        that involve `me` and the user. Two queries total regardless of
        page size."""
        ids = [u.pk for u in users]
        if not ids:
            return
        sent = {
            f.to_user_id: f.status
            for f in Friendship.objects.filter(from_user=me, to_user_id__in=ids)
        }
        received = {
            f.from_user_id: f.status
            for f in Friendship.objects.filter(to_user=me, from_user_id__in=ids)
        }
        for u in users:
            s = sent.get(u.pk)
            r = received.get(u.pk)
            if s == Friendship.ACCEPTED or r == Friendship.ACCEPTED:
                u._relation = 'accepted'
            elif s == Friendship.PENDING:
                u._relation = 'pending_outgoing'
            elif r == Friendship.PENDING:
                u._relation = 'pending_incoming'
            else:
                u._relation = 'none'


class UserDeviceRegisterView(APIView):
    """Upsert (user, fcm_token) → bumps `last_seen_at` on duplicate.

    Request body: {"fcm_token": str, "platform": "android"|"ios"|"web"}.
    Returns the device row id so the client can DELETE it on logout.
    """

    def post(self, request):
        token = (request.data.get('fcm_token') or '').strip()
        platform = (request.data.get('platform') or '').strip()
        if not token:
            return Response({'detail': 'fcm_token required'}, status=400)
        if platform not in dict(UserDevice.PLATFORMS):
            return Response({'detail': 'invalid platform'}, status=400)

        device, _created = UserDevice.objects.update_or_create(
            user=request.user,
            fcm_token=token,
            defaults={'platform': platform},
        )
        return Response(UserDeviceSerializer(device).data, status=200)


class UserDeviceDestroyView(generics.DestroyAPIView):
    """Unregister a device — used on logout / disabling notifications."""

    def get_queryset(self):
        return UserDevice.objects.filter(user=self.request.user)


class WatchHistoryView(generics.ListAPIView):
    """Paginated list of the current user's watch sessions, newest first.

    Fed into the "Последние сеансы" sheet on the profile screen — the
    UI taps `sessions_count` and pages 20 rows at a time.
    """
    serializer_class = WatchSessionSerializer
    pagination_class = LimitOffsetPagination

    def get_queryset(self):
        return (
            WatchSession.objects
            .filter(user=self.request.user)
            .select_related('room')
            .order_by('-joined_at')
        )


class UserDeviceTestPushView(APIView):
    """DEBUG-only smoke-test — sends "Hello" to every device of the
    current user. Returns the FCM-reported success count.
    """

    def post(self, request):
        if not settings.DEBUG:
            return Response(status=status.HTTP_404_NOT_FOUND)
        sent = push.send_to_user(
            request.user,
            title='Junto',
            body='Test push — если видите это, FCM работает.',
            data={'type': 'test_push'},
        )
        return Response({'sent': sent})
