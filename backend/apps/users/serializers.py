from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ('username', 'email', 'password', 'password_confirm')

    def validate(self, attrs):
        if attrs['password'] != attrs.pop('password_confirm'):
            raise serializers.ValidationError({'password_confirm': 'Пароли не совпадают'})
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class UserSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()
    # Stats — populated via annotations in ProfileView.get_object. Default to
    # 0 so the serializer also works on user objects fetched without the
    # annotation (e.g. nested in chat / room responses).
    sessions_count = serializers.IntegerField(read_only=True, default=0)
    watch_seconds = serializers.IntegerField(read_only=True, default=0)
    friends_count = serializers.IntegerField(read_only=True, default=0)
    pending_requests_count = serializers.IntegerField(read_only=True, default=0)
    # True once the user expressed any movie interest (via the
    # onboarding taste-capture screen, a heart on a title detail, or a
    # finished watch session). Used by the router to redirect new
    # accounts to /onboarding/taste before they hit the empty feed.
    has_taste_signal = serializers.SerializerMethodField()
    # Subscription state — derived from apps.billing.Subscription.
    # `tier` is the plan slug ('free' if no active sub).
    tier = serializers.SerializerMethodField()
    is_pro = serializers.SerializerMethodField()
    subscription_expires_at = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id', 'username', 'email', 'avatar', 'avatar_url',
            'sessions_count', 'watch_seconds',
            'friends_count', 'pending_requests_count',
            'has_taste_signal',
            'tier', 'is_pro', 'subscription_expires_at',
        )
        read_only_fields = (
            'id', 'avatar_url',
            'sessions_count', 'watch_seconds',
            'friends_count', 'pending_requests_count',
            'has_taste_signal',
            'tier', 'is_pro', 'subscription_expires_at',
        )
        extra_kwargs = {'avatar': {'write_only': True, 'required': False}}

    def get_avatar_url(self, obj):
        if obj.avatar:
            return obj.avatar.url  # relative path, frontend resolves via mediaBaseUrl
        return None

    def get_has_taste_signal(self, obj):
        # Cheap exists() — fires once per profile fetch, not per row.
        from apps.movies.models import WatchIntent, MovieView
        if WatchIntent.objects.filter(user=obj).exists():
            return True
        if MovieView.objects.filter(user=obj).exists():
            return True
        return False

    def _active_sub(self, obj):
        sub = getattr(obj, 'subscription', None)
        if sub and sub.is_active:
            return sub
        return None

    def get_tier(self, obj):
        sub = self._active_sub(obj)
        return sub.plan.slug if sub else 'free'

    def get_is_pro(self, obj):
        sub = self._active_sub(obj)
        return bool(sub and sub.plan.slug != 'free')

    def get_subscription_expires_at(self, obj):
        sub = self._active_sub(obj)
        return sub.expires_at.isoformat() if sub and sub.expires_at else None
