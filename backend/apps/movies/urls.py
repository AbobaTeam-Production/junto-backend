from django.urls import path

from .views import (
    OnboardingTasteView,
    RecsFeedView,
    RecsMatchView,
    RecsMoodView,
    RecsSearchView,
    RecsTitleInviteView,
    RecsTitleIntentView,
    RecsTitleView,
)


urlpatterns = [
    path('feed/', RecsFeedView.as_view(), name='recs-feed'),
    path('match/<int:friend_id>/', RecsMatchView.as_view(), name='recs-match'),
    path('mood/<slug:slug>/', RecsMoodView.as_view(), name='recs-mood'),
    path('search/', RecsSearchView.as_view(), name='recs-search'),
    path('title/<int:movie_id>/', RecsTitleView.as_view(), name='recs-title'),
    path('title/<int:movie_id>/intent/', RecsTitleIntentView.as_view(), name='recs-title-intent'),
    path('title/<int:movie_id>/invite/', RecsTitleInviteView.as_view(), name='recs-title-invite'),
    path('onboarding/taste/', OnboardingTasteView.as_view(), name='recs-onboarding-taste'),
]
