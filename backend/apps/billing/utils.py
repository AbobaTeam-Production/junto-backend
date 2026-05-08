"""Helpers used by other apps to gate features behind the billing tier.

Keeps the imports / lookups in one place so the rest of the codebase
can stay agnostic of `apps.billing` internals.
"""

from .models import Subscription, SubscriptionPlan


def active_plan(user) -> SubscriptionPlan:
    """Return the user's currently-active SubscriptionPlan.

    Falls back to the free plan if no active subscription exists,
    or — as a last resort if seed_plans hasn't been run — a synthetic
    plan with the most conservative limits so the caller never crashes.
    """
    sub = Subscription.objects.filter(user=user).select_related('plan').first()
    if sub and sub.is_active:
        return sub.plan
    free = SubscriptionPlan.objects.filter(slug='free').first()
    if free:
        return free
    # Synthetic fallback — never persists. Mirrors free-tier defaults
    # from the seed.
    return SubscriptionPlan(
        slug='free', max_room_guests=2, includes_ads=True,
        max_history_days=30,
    )


def max_room_guests(user) -> int:
    return active_plan(user).max_room_guests


def includes_ads(user) -> bool:
    return active_plan(user).includes_ads


def is_pro(user) -> bool:
    return active_plan(user).slug != 'free'
