import time

from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.decorators.cache import never_cache


@never_cache
def server_time(request):
    """Returns server clock as Unix epoch in milliseconds.

    Used by clients for NTP-style clock synchronization (Cristian's algorithm)
    to reduce drift when applying timestamped player events.
    """
    return JsonResponse({'server_time': int(time.time() * 1000)})


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/time/', server_time, name='server-time'),
    path('api/auth/', include('apps.users.urls')),
    path('api/rooms/', include('apps.rooms.urls')),
    path('api/media/', include('apps.media_content.urls')),
    path('api/', include('apps.social.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
