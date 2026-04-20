"""URL routes for content API endpoints."""

from django.urls import path
from .views import VideoListView

urlpatterns = [
	path('video/', VideoListView.as_view(), name='video-list'),
]
