from django.urls import path

from .views import (
    CancelSubscriptionView,
    CheckoutCompleteView,
    CheckoutCreateView,
    CurrentSubscriptionView,
    PlanListView,
)

urlpatterns = [
    path('plans/', PlanListView.as_view(), name='billing-plans'),
    path('subscription/', CurrentSubscriptionView.as_view(), name='billing-subscription'),
    path('checkout/', CheckoutCreateView.as_view(), name='billing-checkout-create'),
    path('checkout/<int:session_id>/complete/', CheckoutCompleteView.as_view(), name='billing-checkout-complete'),
    path('cancel/', CancelSubscriptionView.as_view(), name='billing-cancel'),
]
