from django.contrib import admin

from .models import CheckoutSession, Subscription, SubscriptionPlan


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ('slug', 'title_ru', 'price_rub_monthly',
                    'max_room_guests', 'includes_ads', 'position')
    ordering = ('position',)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan', 'status', 'billing_period',
                    'activated_at', 'expires_at')
    list_filter = ('status', 'plan')
    search_fields = ('user__username',)


@admin.register(CheckoutSession)
class CheckoutSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'plan', 'status', 'created_at')
    list_filter = ('status',)
