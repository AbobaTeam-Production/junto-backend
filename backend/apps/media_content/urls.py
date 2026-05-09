from django.urls import path
from .views import (
    UploadChunkView, TorrentAddView, YouTubeAddView, MediaStatusView,
    TorrentSearchView, TorrentMagnetView, RequestTranscodeView,
    MediaProxyView,
)

urlpatterns = [
    path('upload/', UploadChunkView.as_view(), name='media-upload'),
    path('torrent/', TorrentAddView.as_view(), name='media-torrent'),
    path('torrent/search/', TorrentSearchView.as_view(), name='torrent-search'),
    path('torrent/magnet/', TorrentMagnetView.as_view(), name='torrent-magnet'),
    path('youtube/', YouTubeAddView.as_view(), name='media-youtube'),
    path('proxy/', MediaProxyView.as_view(), name='media-proxy'),
    path('<uuid:id>/status/', MediaStatusView.as_view(), name='media-status'),
    path('<uuid:id>/transcode/', RequestTranscodeView.as_view(), name='media-transcode'),
    path('<uuid:id>/', MediaStatusView.as_view(), name='media-delete'),
]
