from datetime import timedelta

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


@shared_task
def cleanup_guest_users():
    """Delete guest users who are not in any active room and were created 5+ minutes ago."""
    threshold = timezone.now() - timedelta(minutes=5)
    guests = User.objects.filter(
        username__startswith='Гость_',
        date_joined__lt=threshold,
    ).exclude(
        roommember__room__status='active',
    )
    count = guests.count()
    guests.delete()
    return f'Deleted {count} inactive guest users'
