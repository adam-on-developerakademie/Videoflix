"""API views for content endpoints."""

from pathlib import Path
from django.conf import settings
from django.http import FileResponse, HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.status import HTTP_404_NOT_FOUND

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


class HLSPlaylistView(APIView):
	authentication_classes = [CookieJWTAuthentication]
	permission_classes = [IsAuthenticated]
	VALID_RESOLUTIONS = {"480p", "720p", "1080p"}

	def get(self, request, movie_id, resolution):
		if resolution not in self.VALID_RESOLUTIONS:
			return Response({"detail": "Invalid resolution."}, status=HTTP_404_NOT_FOUND)

		try:
			video = Video.objects.get(pk=movie_id, conversion_status=Video.ConversionStatus.DONE)
			# Master playlist request
			if resolution == "master":
				playlist_path = Path(settings.MEDIA_ROOT) / "videos" / str(movie_id) / "master.m3u8"
			else:
				playlist_path = Path(settings.MEDIA_ROOT) / "videos" / str(movie_id) / resolution / "index.m3u8"

			if not playlist_path.exists():
				return Response({"detail": "Playlist not found."}, status=HTTP_404_NOT_FOUND)

			playlist_content = playlist_path.read_text(encoding="utf-8")
			return HttpResponse(playlist_content, content_type="application/vnd.apple.mpegurl")

		except Video.DoesNotExist:
			return Response({"detail": "Video not found."}, status=HTTP_404_NOT_FOUND)


class HLSSegmentView(APIView):
	authentication_classes = [CookieJWTAuthentication]
	permission_classes = [IsAuthenticated]
	VALID_RESOLUTIONS = {"480p", "720p", "1080p"}

	def get(self, request, movie_id, resolution, segment):
		if resolution not in self.VALID_RESOLUTIONS:
			return Response({"detail": "Invalid resolution."}, status=HTTP_404_NOT_FOUND)

		try:
			video = Video.objects.get(pk=movie_id, conversion_status=Video.ConversionStatus.DONE)
			if not video:
				return Response({"detail": "Video not found."}, status=HTTP_404_NOT_FOUND)

			segment_path = Path(settings.MEDIA_ROOT) / "videos" / str(movie_id) / resolution / segment

			if not segment_path.exists() or not segment_path.is_file():
				return Response({"detail": "Segment not found."}, status=HTTP_404_NOT_FOUND)

			return FileResponse(segment_path.open("rb"), content_type="video/mp2t")

		except Video.DoesNotExist:
			return Response({"detail": "Video not found."}, status=HTTP_404_NOT_FOUND)
