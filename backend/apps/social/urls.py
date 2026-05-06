from django.urls import path

from .views import (
    FriendListView,
    FriendRequestsView,
    FriendRequestSendView,
    FriendRequestAcceptView,
    FriendshipDestroyView,
    UserSearchView,
)

# Mounted in config/urls.py — see api/ patterns.
urlpatterns = [
    path('friends/', FriendListView.as_view(), name='friend-list'),
    path('friends/requests/', FriendRequestsView.as_view(), name='friend-requests'),
    path('friends/request/', FriendRequestSendView.as_view(), name='friend-request-send'),
    path('friends/<int:pk>/accept/', FriendRequestAcceptView.as_view(), name='friend-request-accept'),
    path('friends/<int:pk>/', FriendshipDestroyView.as_view(), name='friendship-destroy'),
    path('users/search/', UserSearchView.as_view(), name='user-search'),
]
