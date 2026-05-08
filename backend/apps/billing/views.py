"""Mock billing endpoints — no real payment provider.

The flow exists to demonstrate the subscription lifecycle for the
ВКР demo:

    GET  /api/billing/plans/                    — list plans
    GET  /api/billing/subscription/             — caller's current sub
    POST /api/billing/checkout/                 — create pending session
    POST /api/billing/checkout/<id>/complete/   — flip to paid + activate
    POST /api/billing/cancel/                   — cancel current sub
"""

from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CheckoutSession, Subscription, SubscriptionPlan
from .serializers import (
    CheckoutCompleteSerializer,
    CheckoutCreateSerializer,
    CheckoutSessionSerializer,
    SubscriptionPlanSerializer,
    SubscriptionSerializer,
)


class PlanListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        plans = SubscriptionPlan.objects.order_by('position')
        return Response(SubscriptionPlanSerializer(plans, many=True).data)


class CurrentSubscriptionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sub = Subscription.objects.filter(user=request.user).first()
        if not sub:
            return Response({'plan': None, 'status': 'free'})
        return Response(SubscriptionSerializer(sub).data)


class CheckoutCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CheckoutCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = get_object_or_404(
            SubscriptionPlan, slug=serializer.validated_data['plan_slug'])
        if plan.slug == SubscriptionPlan.SLUG_FREE:
            return Response(
                {'detail': 'Free plan does not need checkout'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        session = CheckoutSession.objects.create(
            user=request.user,
            plan=plan,
            billing_period=serializer.validated_data['billing_period'],
        )
        return Response(CheckoutSessionSerializer(session).data)


class CheckoutCompleteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id: int):
        session = get_object_or_404(
            CheckoutSession, pk=session_id, user=request.user)
        if session.status == 'completed':
            return Response(
                {'detail': 'already completed'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = CheckoutCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        card_last4 = serializer.validated_data['card_last4']

        now = timezone.now()
        period_delta = (
            timedelta(days=365) if session.billing_period == 'yearly'
            else timedelta(days=30)
        )
        sub, _ = Subscription.objects.update_or_create(
            user=request.user,
            defaults={
                'plan': session.plan,
                'status': Subscription.STATUS_ACTIVE,
                'billing_period': session.billing_period,
                'activated_at': now,
                'expires_at': now + period_delta,
                'last_card_last4': card_last4,
            },
        )
        session.status = 'completed'
        session.completed_at = now
        session.save(update_fields=['status', 'completed_at'])
        return Response(SubscriptionSerializer(sub).data)


class CancelSubscriptionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        sub = Subscription.objects.filter(user=request.user).first()
        if not sub or sub.status == Subscription.STATUS_CANCELLED:
            return Response(
                {'detail': 'no active subscription'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        sub.status = Subscription.STATUS_CANCELLED
        sub.expires_at = timezone.now()
        sub.save(update_fields=['status', 'expires_at'])
        return Response(SubscriptionSerializer(sub).data)
