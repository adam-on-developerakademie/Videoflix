"""API views for content endpoints."""

from rest_framework.views import APIView
from rest_framework.response import Response

from rest_framework.permissions import IsAuthenticated
from content_app.models import Video
from .serializers import VideoSerializer
from auth_app.api.authentication import CookieJWTAuthentication


class VideoListView(APIView):
	authentication_classes = [CookieJWTAuthentication]
	permission_classes = [IsAuthenticated]

	def get(self, request):
		videos = Video.objects.filter(conversion_status=Video.ConversionStatus.DONE)
		serializer = VideoSerializer(videos, many=True, context={"request": request})
		return Response(serializer.data)
