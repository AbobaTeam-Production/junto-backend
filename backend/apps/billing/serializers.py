from rest_framework import serializers

from .models import CheckoutSession, Subscription, SubscriptionPlan


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = [
            'slug',
            'title_ru', 'title_en',
            'subtitle_ru', 'subtitle_en',
            'price_rub_monthly', 'price_rub_yearly',
            'price_usd_monthly_cents', 'price_usd_yearly_cents',
            'features_ru', 'features_en',
            'max_room_guests', 'includes_ads', 'max_history_days',
            'position',
        ]


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = SubscriptionPlanSerializer(read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Subscription
        fields = [
            'plan', 'status', 'billing_period',
            'activated_at', 'expires_at',
            'last_card_last4', 'is_active',
        ]


class CheckoutCreateSerializer(serializers.Serializer):
    plan_slug = serializers.CharField()
    billing_period = serializers.ChoiceField(
        choices=[('monthly', 'monthly'), ('yearly', 'yearly')],
    )


class CheckoutCompleteSerializer(serializers.Serializer):
    card_last4 = serializers.CharField(max_length=4, min_length=1)


class CheckoutSessionSerializer(serializers.ModelSerializer):
    plan = SubscriptionPlanSerializer(read_only=True)

    class Meta:
        model = CheckoutSession
        fields = [
            'id', 'plan', 'billing_period', 'status',
            'created_at', 'completed_at',
        ]
