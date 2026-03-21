from django.urls import path
from .views import UploadChunkView, TorrentAddView, YouTubeAddView, MediaStatusView

urlpatterns = [
    path('upload/', UploadChunkView.as_view(), name='media-upload'),
    path('torrent/', TorrentAddView.as_view(), name='media-torrent'),
    path('youtube/', YouTubeAddView.as_view(), name='media-youtube'),
    path('<uuid:id>/status/', MediaStatusView.as_view(), name='media-status'),
    path('<uuid:id>/', MediaStatusView.as_view(), name='media-delete'),
]
