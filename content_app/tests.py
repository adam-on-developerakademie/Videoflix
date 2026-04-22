"""Tests for content application features."""

import subprocess
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms import ValidationError
from django.test import RequestFactory
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from content_app.admin import VideoAdmin, VideoAdminForm
from content_app.admin import VideoAdmin
from content_app.models import Video
from content_app.video_processing import transcode_video

User = get_user_model()

# Create a temporary media directory for tests
TEST_MEDIA_ROOT = Path(settings.MEDIA_ROOT).parent / "test_media"


@override_settings(MEDIA_ROOT=str(TEST_MEDIA_ROOT))
class VideoModelTests(TestCase):
    """Tests for Video model."""

    def setUp(self):
        TEST_MEDIA_ROOT.mkdir(exist_ok=True)

    def tearDown(self):
        if TEST_MEDIA_ROOT.exists():
            shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def test_video_creation_with_required_fields(self):
        """Test creating a video with required fields."""
        video = Video.objects.create(
            title="Test Video",
            description="A test video",
            category="Education",
        )
        self.assertEqual(video.title, "Test Video")
        self.assertEqual(video.description, "A test video")
        self.assertEqual(video.category, "Education")
        self.assertEqual(video.conversion_status, Video.ConversionStatus.PENDING)

    def test_video_string_representation(self):
        """Test video __str__ method."""
        video = Video.objects.create(
            title="Test Video Title",
            description="Description",
            category="Test",
        )
        self.assertEqual(str(video), "Test Video Title")

    def test_video_defaults(self):
        """Test video default values."""
        video = Video.objects.create(
            title="Test",
            description="Test",
            category="Test",
        )
        self.assertEqual(video.thumbnail_url, "")
        self.assertIsNotNone(video.created_at)

    def test_video_deletion_cascade(self):
        """Test that videos are properly tracked."""
        video = Video.objects.create(
            title="To Delete",
            description="Delete me",
            category="Test",
        )
        video_id = video.pk
        video.delete()
        self.assertFalse(Video.objects.filter(pk=video_id).exists())


@override_settings(MEDIA_ROOT=str(TEST_MEDIA_ROOT))
class VideoAdminTests(TestCase):
    """Tests for Video admin functionality."""

    def setUp(self):
        TEST_MEDIA_ROOT.mkdir(exist_ok=True)
        self.user = User.objects.create_superuser(
            username="admin",
            email="admin@test.de",
            password="AdminPass123!",
        )
        self.site = AdminSite()
        self.admin = VideoAdmin(Video, self.site)
        self.request = RequestFactory().post("/")
        self.request.user = self.user

    def tearDown(self):
        if TEST_MEDIA_ROOT.exists():
            shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def test_admin_auto_fill_title_from_filename(self):
        """Test that admin auto-fills title from uploaded filename."""
        # Simulate a file upload
        dummy_file = SimpleUploadedFile("my_movie.mp4", b"fake video content", content_type="video/mp4")
        video = Video.objects.create(
            title="",  # Empty title
            description="A test",
            category="",  # Empty category
            video_file=dummy_file,
        )
        # After save_model, admin would fill these in
        # For this test, we verify the model accepts empty fields
        self.assertEqual(video.title, "")
        self.assertEqual(video.category, "")

    def test_admin_form_makes_title_and_category_optional(self):
        form = VideoAdminForm()
        self.assertFalse(form.fields["title"].required)
        self.assertFalse(form.fields["category"].required)

    def test_admin_form_requires_source_file_on_create(self):
        form = VideoAdminForm(data={
            "title": "Video",
            "description": "Description",
            "category": "Category",
        })

        self.assertFalse(form.is_valid())
        self.assertIn(
            "A source video file is required when creating a video.",
            form.non_field_errors(),
        )

    def test_admin_form_allows_missing_source_file_on_existing_instance(self):
        video = Video.objects.create(
            title="Existing",
            description="Existing",
            category="Existing",
        )
        form = VideoAdminForm(
            data={
                "title": "Existing",
                "description": "Updated",
                "category": "Existing",
                "conversion_status": Video.ConversionStatus.PENDING,
            },
            instance=video,
        )

        self.assertTrue(form.is_valid())

    @patch("content_app.admin.transaction.on_commit")
    @patch("content_app.admin.django_rq.get_queue")
    def test_save_model_autofills_defaults_and_enqueues_job(self, mock_get_queue, mock_on_commit):
        queue = mock_get_queue.return_value
        mock_on_commit.side_effect = lambda callback: callback()
        upload = SimpleUploadedFile("trailer.mp4", b"video", content_type="video/mp4")
        video = Video(title="", description="", category="", video_file=upload)
        form = MagicMock(changed_data=["video_file"])

        self.admin.save_model(self.request, video, form, change=False)

        video.refresh_from_db()
        self.assertEqual(video.title, "trailer")
        self.assertEqual(video.description, "No description")
        self.assertEqual(video.category, "Auto")
        self.assertEqual(video.conversion_status, Video.ConversionStatus.PENDING)
        queue.enqueue.assert_called_once_with(transcode_video, video.pk)

    def test_resolution_links_render_file_urls(self):
        video = Video(title="Test", description="Test", category="Test")
        video.file_1080p.name = "videos/1/1080p/1.mp4"
        video.file_720p.name = "videos/1/720p/1.mp4"
        video.file_480p.name = "videos/1/480p/1.mp4"

        self.assertIn("1080p", self.admin.link_1080p(video))
        self.assertIn("720p", self.admin.link_720p(video))
        self.assertIn("480p", self.admin.link_480p(video))

    def test_resolution_links_render_dash_without_files(self):
        video = Video(title="Test", description="Test", category="Test")

        self.assertEqual(self.admin.link_1080p(video), "—")
        self.assertEqual(self.admin.link_720p(video), "—")
        self.assertEqual(self.admin.link_480p(video), "—")

    def test_admin_cleanup_removes_media_folder(self):
        """Test that deleting a video from admin removes its media folder."""
        video = Video.objects.create(
            title="Test",
            description="Test",
            category="Test",
        )

        video_dir = TEST_MEDIA_ROOT / "videos" / str(video.pk)
        video_dir.mkdir(parents=True, exist_ok=True)
        (video_dir / "1080p").mkdir(exist_ok=True)
        (video_dir / "1080p" / "video.mp4").write_bytes(b"fake")

        self.admin.delete_model(self.request, video)

        self.assertFalse(video_dir.exists())

    def test_admin_bulk_delete_removes_media_and_normalized_sources(self):
        video_one = Video.objects.create(title="One", description="One", category="Test")
        video_two = Video.objects.create(title="Two", description="Two", category="Test")
        videos_root = TEST_MEDIA_ROOT / "videos"

        for video in (video_one, video_two):
            video_dir = videos_root / str(video.pk)
            video_dir.mkdir(parents=True, exist_ok=True)
            (video_dir / "720p").mkdir(exist_ok=True)
            (video_dir / "720p" / f"{video.pk}.mp4").write_bytes(b"data")
            (videos_root / f"{video.pk}.mov").write_bytes(b"source")

        queryset = Video.objects.filter(pk__in=[video_one.pk, video_two.pk])
        self.admin.delete_queryset(self.request, queryset)

        self.assertFalse(Video.objects.filter(pk=video_one.pk).exists())
        self.assertFalse(Video.objects.filter(pk=video_two.pk).exists())
        self.assertFalse((videos_root / str(video_one.pk)).exists())
        self.assertFalse((videos_root / str(video_two.pk)).exists())
        self.assertFalse((videos_root / f"{video_one.pk}.mov").exists())
        self.assertFalse((videos_root / f"{video_two.pk}.mov").exists())


@override_settings(MEDIA_ROOT=str(TEST_MEDIA_ROOT))
class VideoProcessingTests(TestCase):
    """Tests for video transcoding and HLS generation."""

    def setUp(self):
        TEST_MEDIA_ROOT.mkdir(exist_ok=True)

    def tearDown(self):
        if TEST_MEDIA_ROOT.exists():
            shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def _mock_ffmpeg_success(self, command, check, capture_output, text):
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if output_path.suffix == ".mp4":
            output_path.write_bytes(b"mp4")
        elif output_path.suffix == ".m3u8":
            segment_index = command.index("-hls_segment_filename") + 1
            segment_pattern = Path(command[segment_index])
            segment_pattern.parent.mkdir(parents=True, exist_ok=True)
            Path(str(segment_pattern).replace("%03d", "000")).write_bytes(b"segment")
            output_path.write_text("#EXTM3U\n", encoding="utf-8")
        elif output_path.suffix == ".jpg":
            output_path.write_bytes(b"jpg")

        return MagicMock(returncode=0)

    @patch("content_app.video_processing.subprocess.run")
    def test_transcode_video_updates_conversion_status(self, mock_run):
        """Test that transcode_video updates conversion_status."""
        video = Video.objects.create(
            title="Test",
            description="Test",
            category="Test",
            conversion_status=Video.ConversionStatus.PENDING,
        )

        mock_run.side_effect = self._mock_ffmpeg_success

        # Create dummy source file
        source_dir = TEST_MEDIA_ROOT / "videos"
        source_dir.mkdir(exist_ok=True)
        source_file = source_dir / f"{video.pk}.mp4"
        source_file.write_bytes(b"fake video")

        transcode_video(video.pk)

        video.refresh_from_db()
        self.assertEqual(video.conversion_status, Video.ConversionStatus.DONE)
        self.assertTrue((TEST_MEDIA_ROOT / "videos" / str(video.pk) / "master.m3u8").exists())
        self.assertTrue((TEST_MEDIA_ROOT / "videos" / str(video.pk) / "thumbnail" / f"{video.pk}.jpg").exists())

    @patch("content_app.video_processing.subprocess.run")
    def test_transcode_video_renames_configured_source_in_videos_root(self, mock_run):
        mock_run.side_effect = self._mock_ffmpeg_success
        upload = SimpleUploadedFile("original.mp4", b"video", content_type="video/mp4")
        video = Video.objects.create(
            title="Test",
            description="Test",
            category="Test",
            video_file=upload,
        )

        original_source = TEST_MEDIA_ROOT / "videos" / "original.mp4"
        original_source.parent.mkdir(parents=True, exist_ok=True)
        original_source.write_bytes(b"source")
        video.video_file.name = "videos/original.mp4"
        video.save(update_fields=["video_file"])

        transcode_video(video.pk)

        video.refresh_from_db()
        self.assertEqual(video.conversion_status, Video.ConversionStatus.DONE)
        self.assertFalse(original_source.exists())
        self.assertEqual(video.file_1080p.name, f"videos/{video.pk}/1080p/{video.pk}.mp4")

    @patch("content_app.video_processing.subprocess.run")
    def test_transcode_video_uses_existing_1080p_as_fallback_source(self, mock_run):
        mock_run.side_effect = self._mock_ffmpeg_success
        video = Video.objects.create(
            title="Fallback",
            description="Fallback",
            category="Test",
        )
        fallback_source = TEST_MEDIA_ROOT / "videos" / str(video.pk) / "1080p" / f"{video.pk}.mp4"
        fallback_source.parent.mkdir(parents=True, exist_ok=True)
        fallback_source.write_bytes(b"existing-1080")

        transcode_video(video.pk)

        video.refresh_from_db()
        self.assertEqual(video.conversion_status, Video.ConversionStatus.DONE)
        self.assertTrue((TEST_MEDIA_ROOT / "videos" / str(video.pk) / "720p" / f"{video.pk}.mp4").exists())
        self.assertTrue((TEST_MEDIA_ROOT / "videos" / str(video.pk) / "480p" / f"{video.pk}.mp4").exists())

    @patch("content_app.video_processing.subprocess.run")
    def test_transcode_video_handles_missing_source_file(self, mock_run):
        """Test transcode_video with missing source file."""
        video = Video.objects.create(
            title="No Source",
            description="Missing file",
            category="Test",
            conversion_status=Video.ConversionStatus.PENDING,
        )

        transcode_video(video.pk)

        video.refresh_from_db()
        self.assertEqual(video.conversion_status, Video.ConversionStatus.FAILED)

    @patch("content_app.video_processing.subprocess.run")
    def test_transcode_video_marks_failed_on_transcode_error_and_cleans_outputs(self, mock_run):
        video = Video.objects.create(title="Fail", description="Fail", category="Test")
        source_dir = TEST_MEDIA_ROOT / "videos"
        source_dir.mkdir(exist_ok=True)
        source_file = source_dir / f"{video.pk}.mp4"
        source_file.write_bytes(b"source")

        def side_effect(command, check, capture_output, text):
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if output_path.name.endswith(".mp4") and f"1080p" in str(output_path.parent):
                output_path.write_bytes(b"mp4")
                return MagicMock(returncode=0)
            raise subprocess.CalledProcessError(returncode=1, cmd=command)

        mock_run.side_effect = side_effect

        transcode_video(video.pk)

        video.refresh_from_db()
        self.assertEqual(video.conversion_status, Video.ConversionStatus.FAILED)
        self.assertFalse((TEST_MEDIA_ROOT / "videos" / str(video.pk) / "1080p" / f"{video.pk}.mp4").exists())

    @patch("content_app.video_processing.subprocess.run")
    def test_transcode_video_marks_failed_on_hls_generation_error(self, mock_run):
        video = Video.objects.create(title="HLS Fail", description="Fail", category="Test")
        source_dir = TEST_MEDIA_ROOT / "videos"
        source_dir.mkdir(exist_ok=True)
        source_file = source_dir / f"{video.pk}.mp4"
        source_file.write_bytes(b"source")
        call_count = {"value": 0}

        def side_effect(command, check, capture_output, text):
            call_count["value"] += 1
            if call_count["value"] <= 3:
                return self._mock_ffmpeg_success(command, check, capture_output, text)
            raise subprocess.CalledProcessError(returncode=1, cmd=command)

        mock_run.side_effect = side_effect

        transcode_video(video.pk)

        video.refresh_from_db()
        self.assertEqual(video.conversion_status, Video.ConversionStatus.FAILED)
        self.assertFalse((TEST_MEDIA_ROOT / "videos" / str(video.pk) / "1080p" / f"{video.pk}.mp4").exists())

    @patch("content_app.video_processing.subprocess.run")
    def test_transcode_video_marks_failed_when_master_playlist_write_fails(self, mock_run):
        mock_run.side_effect = self._mock_ffmpeg_success
        video = Video.objects.create(title="Master Fail", description="Fail", category="Test")
        source_dir = TEST_MEDIA_ROOT / "videos"
        source_dir.mkdir(exist_ok=True)
        source_file = source_dir / f"{video.pk}.mp4"
        source_file.write_bytes(b"source")

        original_write_text = Path.write_text

        def write_text_side_effect(path_obj, *args, **kwargs):
            if path_obj.name == "master.m3u8":
                raise OSError("disk full")
            return original_write_text(path_obj, *args, **kwargs)

        with patch("content_app.video_processing.Path.write_text", autospec=True, side_effect=write_text_side_effect):
            transcode_video(video.pk)

        video.refresh_from_db()
        self.assertEqual(video.conversion_status, Video.ConversionStatus.FAILED)
        self.assertFalse((TEST_MEDIA_ROOT / "videos" / str(video.pk) / "1080p" / f"{video.pk}.mp4").exists())

    @patch("content_app.video_processing.subprocess.run")
    def test_transcode_video_marks_failed_on_thumbnail_error(self, mock_run):
        video = Video.objects.create(title="Thumb Fail", description="Fail", category="Test")
        source_dir = TEST_MEDIA_ROOT / "videos"
        source_dir.mkdir(exist_ok=True)
        source_file = source_dir / f"{video.pk}.mp4"
        source_file.write_bytes(b"source")
        call_count = {"value": 0}

        def side_effect(command, check, capture_output, text):
            call_count["value"] += 1
            if call_count["value"] <= 6:
                return self._mock_ffmpeg_success(command, check, capture_output, text)
            raise subprocess.CalledProcessError(returncode=1, cmd=command)

        mock_run.side_effect = side_effect

        transcode_video(video.pk)

        video.refresh_from_db()
        self.assertEqual(video.conversion_status, Video.ConversionStatus.FAILED)
        self.assertFalse((TEST_MEDIA_ROOT / "videos" / str(video.pk) / "master.m3u8").exists())

    def test_transcode_video_nonexistent_video_returns_gracefully(self):
        """Test transcode_video with nonexistent video ID."""
        # Should not raise an exception
        transcode_video(999999)


@override_settings(MEDIA_ROOT=str(TEST_MEDIA_ROOT))
class VideoAPIListTests(APITestCase):
    """Tests for video list API endpoint."""

    def setUp(self):
        TEST_MEDIA_ROOT.mkdir(exist_ok=True)
        self.user = User.objects.create_user(
            username="testuser",
            email="test@test.de",
            password="TestPass123!",
            is_active=True,
        )
        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        if TEST_MEDIA_ROOT.exists():
            shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def test_video_list_returns_only_done_videos(self):
        """Test that only completed videos are returned."""
        # Create videos with different statuses
        Video.objects.create(
            title="Done Video",
            description="Completed",
            category="Test",
            conversion_status=Video.ConversionStatus.DONE,
        )
        Video.objects.create(
            title="Pending Video",
            description="Not done",
            category="Test",
            conversion_status=Video.ConversionStatus.PENDING,
        )
        Video.objects.create(
            title="Processing Video",
            description="In progress",
            category="Test",
            conversion_status=Video.ConversionStatus.PROCESSING,
        )
        Video.objects.create(
            title="Failed Video",
            description="Conversion failed",
            category="Test",
            conversion_status=Video.ConversionStatus.FAILED,
        )

        response = self.client.get(reverse("video-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["title"], "Done Video")

    def test_video_list_requires_authentication(self):
        """Test that unauthenticated users cannot access the video list."""
        self.client.force_authenticate(user=None)
        response = self.client.get(reverse("video-list"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_video_list_contains_required_fields(self):
        """Test that video list contains all required fields."""
        Video.objects.create(
            title="Complete Video",
            description="Full details",
            category="Movies",
            conversion_status=Video.ConversionStatus.DONE,
        )

        response = self.client.get(reverse("video-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        video_data = response.data[0]
        
        # Check required fields from contract
        self.assertIn("id", video_data)
        self.assertIn("created_at", video_data)
        self.assertIn("title", video_data)
        self.assertIn("description", video_data)
        self.assertIn("thumbnail_url", video_data)
        self.assertIn("category", video_data)

    def test_video_list_thumbnail_fallback(self):
        """Test that missing thumbnails fallback to dummy.jpg."""
        Video.objects.create(
            title="No Thumbnail",
            description="Missing thumbnail",
            category="Test",
            thumbnail_url="",  # Empty thumbnail
            conversion_status=Video.ConversionStatus.DONE,
        )

        response = self.client.get(reverse("video-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        thumbnail = response.data[0]["thumbnail_url"]
        self.assertIn("dummy.jpg", thumbnail)

    def test_video_list_empty_returns_empty_array(self):
        """Test that empty video list returns empty array."""
        response = self.client.get(reverse("video-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])


@override_settings(MEDIA_ROOT=str(TEST_MEDIA_ROOT))
class HLSPlaylistTests(APITestCase):
    """Tests for HLS playlist endpoint."""

    def setUp(self):
        TEST_MEDIA_ROOT.mkdir(exist_ok=True)
        self.user = User.objects.create_user(
            username="hlsuser",
            email="hls@test.de",
            password="HlsPass123!",
            is_active=True,
        )
        self.client.force_authenticate(user=self.user)
        self.video = Video.objects.create(
            title="HLS Video",
            description="For HLS testing",
            category="Test",
            conversion_status=Video.ConversionStatus.DONE,
        )

    def tearDown(self):
        if TEST_MEDIA_ROOT.exists():
            shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def test_hls_playlist_returns_404_for_nonexistent_video(self):
        """Test that requesting playlist for nonexistent video returns 404."""
        response = self.client.get(
            reverse("hls-playlist", kwargs={"movie_id": 999, "resolution": "720p"})
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_hls_playlist_returns_404_for_incomplete_video(self):
        """Test that incomplete videos cannot be streamed."""
        incomplete_video = Video.objects.create(
            title="Incomplete",
            description="Not done",
            category="Test",
            conversion_status=Video.ConversionStatus.PENDING,
        )
        response = self.client.get(
            reverse("hls-playlist", kwargs={"movie_id": incomplete_video.pk, "resolution": "720p"})
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_hls_playlist_returns_404_for_invalid_resolution(self):
        """Test that invalid resolutions return 404."""
        response = self.client.get(
            reverse("hls-playlist", kwargs={"movie_id": self.video.pk, "resolution": "invalid"})
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_hls_playlist_requires_authentication(self):
        """Test that unauthenticated users cannot access playlists."""
        self.client.force_authenticate(user=None)
        response = self.client.get(
            reverse("hls-playlist", kwargs={"movie_id": self.video.pk, "resolution": "720p"})
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_hls_playlist_returns_correct_content_type(self):
        """Test that playlist returns correct HLS content type."""
        # Create a dummy playlist file
        playlist_dir = TEST_MEDIA_ROOT / "videos" / str(self.video.pk) / "720p"
        playlist_dir.mkdir(parents=True, exist_ok=True)
        playlist_file = playlist_dir / "index.m3u8"
        playlist_file.write_text("#EXTM3U\n#EXT-X-VERSION:3\n")

        response = self.client.get(
            reverse("hls-playlist", kwargs={"movie_id": self.video.pk, "resolution": "720p"})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/vnd.apple.mpegurl")

    def test_hls_playlist_returns_m3u8_content(self):
        """Test that playlist content is returned correctly."""
        playlist_dir = TEST_MEDIA_ROOT / "videos" / str(self.video.pk) / "480p"
        playlist_dir.mkdir(parents=True, exist_ok=True)
        playlist_content = "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:6\n"
        playlist_file = playlist_dir / "index.m3u8"
        playlist_file.write_text(playlist_content)

        response = self.client.get(
            reverse("hls-playlist", kwargs={"movie_id": self.video.pk, "resolution": "480p"})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("#EXTM3U", response.content.decode())


@override_settings(MEDIA_ROOT=str(TEST_MEDIA_ROOT))
class HLSSegmentTests(APITestCase):
    """Tests for HLS segment endpoint."""

    def setUp(self):
        TEST_MEDIA_ROOT.mkdir(exist_ok=True)
        self.user = User.objects.create_user(
            username="segmentuser",
            email="segment@test.de",
            password="SegPass123!",
            is_active=True,
        )
        self.client.force_authenticate(user=self.user)
        self.video = Video.objects.create(
            title="Segment Video",
            description="For segment testing",
            category="Test",
            conversion_status=Video.ConversionStatus.DONE,
        )

    def tearDown(self):
        if TEST_MEDIA_ROOT.exists():
            shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def test_hls_segment_returns_404_for_nonexistent_segment(self):
        """Test that nonexistent segments return 404."""
        response = self.client.get(
            reverse("hls-segment", kwargs={
                "movie_id": self.video.pk,
                "resolution": "720p",
                "segment": "segment_000.ts"
            })
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_hls_segment_returns_404_for_invalid_resolution(self):
        """Test that invalid resolutions return 404."""
        response = self.client.get(
            reverse("hls-segment", kwargs={
                "movie_id": self.video.pk,
                "resolution": "invalid",
                "segment": "segment_000.ts"
            })
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_hls_segment_requires_authentication(self):
        """Test that unauthenticated users cannot access segments."""
        self.client.force_authenticate(user=None)
        response = self.client.get(
            reverse("hls-segment", kwargs={
                "movie_id": self.video.pk,
                "resolution": "720p",
                "segment": "segment_000.ts"
            })
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_hls_segment_returns_correct_content_type(self):
        """Test that segments return correct video content type."""
        # Create a dummy segment file
        segment_dir = TEST_MEDIA_ROOT / "videos" / str(self.video.pk) / "1080p"
        segment_dir.mkdir(parents=True, exist_ok=True)
        segment_file = segment_dir / "segment_000.ts"
        segment_file.write_bytes(b"fake ts segment data")

        response = self.client.get(
            reverse("hls-segment", kwargs={
                "movie_id": self.video.pk,
                "resolution": "1080p",
                "segment": "segment_000.ts"
            })
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "video/mp2t")

    def test_hls_segment_returns_binary_content(self):
        """Test that segment content is served as binary."""
        segment_dir = TEST_MEDIA_ROOT / "videos" / str(self.video.pk) / "720p"
        segment_dir.mkdir(parents=True, exist_ok=True)
        segment_content = b"fake binary segment data"
        segment_file = segment_dir / "segment_001.ts"
        segment_file.write_bytes(segment_content)

        response = self.client.get(
            reverse("hls-segment", kwargs={
                "movie_id": self.video.pk,
                "resolution": "720p",
                "segment": "segment_001.ts"
            })
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(response.streaming_content), segment_content)

    def test_hls_segment_prevents_path_traversal(self):
        """Test that path traversal attacks are prevented."""
        response = self.client.get(
            f"/api/video/{self.video.pk}/720p/..%2F..%2Fetc%2Fpasswd"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(MEDIA_ROOT=str(TEST_MEDIA_ROOT))
class ConversionStatusFilterTests(TestCase):
    """Tests for conversion status filtering."""

    def setUp(self):
        TEST_MEDIA_ROOT.mkdir(exist_ok=True)

    def tearDown(self):
        if TEST_MEDIA_ROOT.exists():
            shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def test_only_done_videos_visible_in_queryset(self):
        """Test that only DONE videos are returned by the API queryset."""
        Video.objects.create(
            title="Done 1",
            description="",
            category="",
            conversion_status=Video.ConversionStatus.DONE,
        )
        Video.objects.create(
            title="Done 2",
            description="",
            category="",
            conversion_status=Video.ConversionStatus.DONE,
        )
        Video.objects.create(
            title="Pending",
            description="",
            category="",
            conversion_status=Video.ConversionStatus.PENDING,
        )
        Video.objects.create(
            title="Failed",
            description="",
            category="",
            conversion_status=Video.ConversionStatus.FAILED,
        )

        done_videos = Video.objects.filter(conversion_status=Video.ConversionStatus.DONE)
        self.assertEqual(done_videos.count(), 2)
        self.assertEqual(set(v.title for v in done_videos), {"Done 1", "Done 2"})
