import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('junto')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# Periodic tasks
app.conf.beat_schedule = {
    'cleanup-expired-rooms': {
        'task': 'apps.rooms.tasks.cleanup_expired_rooms',
        'schedule': 3600.0,  # every hour
    },
    'cleanup-guest-users': {
        'task': 'apps.users.tasks.cleanup_guest_users',
        'schedule': 300.0,  # every 5 minutes
    },
}
