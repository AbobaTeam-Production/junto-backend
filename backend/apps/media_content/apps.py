from django.apps import AppConfig


class MediaContentConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.media_content'

    def ready(self):
        from . import signals  # noqa: F401
