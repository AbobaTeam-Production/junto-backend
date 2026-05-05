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

    class Meta:
        model = User
        fields = (
            'id', 'username', 'email', 'avatar', 'avatar_url',
            'sessions_count', 'watch_seconds',
        )
        read_only_fields = ('id', 'avatar_url', 'sessions_count', 'watch_seconds')
        extra_kwargs = {'avatar': {'write_only': True, 'required': False}}

    def get_avatar_url(self, obj):
        if obj.avatar:
            return obj.avatar.url  # relative path, frontend resolves via mediaBaseUrl
        return None
