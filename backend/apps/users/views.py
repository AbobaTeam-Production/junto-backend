import uuid

from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model
from django.db.models import Count, Sum
from django.db.models.functions import Coalesce

from .serializers import RegisterSerializer, UserSerializer

User = get_user_model()


class GuestLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        tag = uuid.uuid4().hex[:6].upper()
        username = f'Гость_{tag}'
        user = User.objects.create_user(
            username=username,
            password=None,
        )
        refresh = RefreshToken.for_user(user)
        return Response({
            'user': UserSerializer(user, context={'request': request}).data,
            'tokens': {
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            }
        }, status=status.HTTP_201_CREATED)


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        refresh = RefreshToken.for_user(user)
        return Response({
            'user': UserSerializer(user, context={'request': request}).data,
            'tokens': {
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            }
        }, status=status.HTTP_201_CREATED)


class ProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        # Annotate session count + summed watch duration + friend counts
        # so the profile endpoint serves all stats in a single query.
        from django.db.models import Q as _Q
        from apps.social.models import Friendship
        u = self.request.user

        friends_count = Friendship.objects.filter(
            _Q(from_user=u) | _Q(to_user=u),
            status=Friendship.ACCEPTED,
        ).count()
        pending_requests_count = Friendship.objects.filter(
            to_user=u, status=Friendship.PENDING,
        ).count()

        qs = User.objects.filter(pk=u.pk).annotate(
            sessions_count=Count('watch_sessions', distinct=True),
            watch_seconds=Coalesce(Sum('watch_sessions__duration_sec'), 0),
        )
        user = qs.get()
        # Counts are kept as plain attributes — DRF's serializer fields with
        # `default=0` happily read them via `getattr`.
        user.friends_count = friends_count
        user.pending_requests_count = pending_requests_count
        return user
