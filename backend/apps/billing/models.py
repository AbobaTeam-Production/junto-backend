from django.conf import settings
from django.db import models
from django.utils import timezone


class SubscriptionPlan(models.Model):
    SLUG_FREE = 'free'
    SLUG_PRO = 'pro'
    SLUG_CINEMA = 'cinema'

    slug = models.CharField(max_length=32, unique=True)
    title_ru = models.CharField(max_length=64)
    title_en = models.CharField(max_length=64)
    subtitle_ru = models.CharField(max_length=128, blank=True)
    subtitle_en = models.CharField(max_length=128, blank=True)

    price_rub_monthly = models.PositiveIntegerField(default=0)
    price_rub_yearly = models.PositiveIntegerField(default=0)
    price_usd_monthly_cents = models.PositiveIntegerField(default=0)
    price_usd_yearly_cents = models.PositiveIntegerField(default=0)

    features_ru = models.JSONField(default=list)
    features_en = models.JSONField(default=list)

    max_room_guests = models.PositiveIntegerField(default=2)
    includes_ads = models.BooleanField(default=True)
    max_history_days = models.PositiveIntegerField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'subscription_plans'
        ordering = ['position']

    def __str__(self):
        return f'{self.slug} ({self.title_ru})'


class Subscription(models.Model):
    PERIOD_MONTHLY = 'monthly'
    PERIOD_YEARLY = 'yearly'
    STATUS_ACTIVE = 'active'
    STATUS_CANCELLED = 'cancelled'
    STATUS_EXPIRED = 'expired'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='subscription',
    )
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, default=STATUS_ACTIVE)
    billing_period = models.CharField(max_length=16, default=PERIOD_MONTHLY)
    activated_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_card_last4 = models.CharField(max_length=4, blank=True)

    class Meta:
        db_table = 'subscriptions'

    @property
    def is_active(self) -> bool:
        if self.status != self.STATUS_ACTIVE:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True


class CheckoutSession(models.Model):
    """Mock checkout session — no real payment provider integration."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='checkout_sessions',
    )
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)
    billing_period = models.CharField(max_length=16, default='monthly')
    status = models.CharField(max_length=16, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'checkout_sessions'
