import logging

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


class SocialConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.social'

    def ready(self):
        path = getattr(settings, 'FIREBASE_CREDENTIALS_PATH', '')
        if not path:
            # Dev mode without push — push.send_to_user becomes a no-op.
            return
        try:
            import firebase_admin
            from firebase_admin import credentials
        except ImportError:
            logger.warning(
                'FIREBASE_CREDENTIALS_PATH set but firebase-admin not '
                'installed; push notifications disabled.'
            )
            return
        # `ready()` runs once per process (web, daphne, celery worker).
        # Guard against re-init if Django reloads us (autoreload, tests).
        if firebase_admin._apps:
            settings.FCM_ENABLED = True
            return
        try:
            firebase_admin.initialize_app(credentials.Certificate(path))
            settings.FCM_ENABLED = True
            logger.info('Firebase admin SDK initialized from %s', path)
        except Exception:
            logger.exception('Firebase admin SDK init failed')
