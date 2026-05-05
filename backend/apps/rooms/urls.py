from django.urls import path
from .views import (
    CreateRoomView,
    JoinRoomView,
    LiveKitTokenView,
    MyRoomsListView,
    RoomDetailView,
)

urlpatterns = [
    path('', MyRoomsListView.as_view(), name='room-list'),
    path('create/', CreateRoomView.as_view(), name='room-create'),
    path('join/', JoinRoomView.as_view(), name='room-join'),
    path('<uuid:id>/', RoomDetailView.as_view(), name='room-detail'),
    path('<uuid:id>/livekit-token/', LiveKitTokenView.as_view(), name='room-livekit-token'),
]
