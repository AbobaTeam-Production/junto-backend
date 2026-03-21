from django.urls import path
from .views import CreateRoomView, JoinRoomView, MyRoomsListView, RoomDetailView, RoomConfigView

urlpatterns = [
    path('', MyRoomsListView.as_view(), name='room-list'),
    path('create/', CreateRoomView.as_view(), name='room-create'),
    path('join/', JoinRoomView.as_view(), name='room-join'),
    path('<uuid:id>/', RoomDetailView.as_view(), name='room-detail'),
    path('config/ice/', RoomConfigView.as_view(), name='room-ice-config'),
]
