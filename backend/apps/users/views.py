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
        # Annotate session count + summed watch duration so the profile
        # endpoint serves the user's stats in a single query. Fall back
        # to 0 when there are no sessions (Coalesce on Sum).
        qs = User.objects.filter(pk=self.request.user.pk).annotate(
            sessions_count=Count('watch_sessions', distinct=True),
            watch_seconds=Coalesce(Sum('watch_sessions__duration_sec'), 0),
        )
        return qs.get()
