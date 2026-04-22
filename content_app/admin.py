"""Admin registrations for content domain models."""

import os
import shutil
from pathlib import Path

import django_rq
from django import forms
from django.conf import settings
from django.contrib import admin
from django.db import transaction
from django.utils.html import format_html

from .models import Video
from .video_processing import transcode_video


class VideoAdminForm(forms.ModelForm):
    """Customize validation behavior for the Video admin form."""

    class Meta:
        """Bind the form to the Video model."""

        model = Video
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        """Allow title and category to be auto-filled after file selection."""
        super().__init__(*args, **kwargs)
        self.fields["title"].required = False
        self.fields["category"].required = False

    def clean(self):
        """Require a source file only when creating a new record."""
        cleaned_data = super().clean()
        video_file = cleaned_data.get("video_file")
        if not self.instance.pk and not video_file:
            raise forms.ValidationError(
                "A source video file is required when creating a video."
            )
        return cleaned_data


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    """Configure Video model editing and processing behavior in Django admin."""

    form = VideoAdminForm
    list_display = ("title", "category", "conversion_status", "created_at")
    search_fields = ("title", "description", "category")
    list_filter = ("category", "conversion_status", "created_at")
    readonly_fields = (
        "thumbnail_url",
        "conversion_status",
        "created_at",
        "link_1080p",
        "link_720p",
        "link_480p",
    )
    fields = (
        "title",
        "description",
        "thumbnail_url",
        "category",
        "video_file",
        "link_1080p",
        "link_720p",
        "link_480p",
        "conversion_status",
        "created_at",
    )

    class Media:
        """Inject admin JavaScript for auto-filling form fields from filename."""

        js = ("content_app/admin/video_autofill.js",)

    def save_model(self, request, obj, form, change):
        """Persist model changes and enqueue conversion when a new source is uploaded."""
        new_file_uploaded = "video_file" in form.changed_data and bool(obj.video_file)
        if not obj.title and obj.video_file:
            filename = os.path.basename(obj.video_file.name)
            obj.title = os.path.splitext(filename)[0]
        if not obj.category:
            obj.category = "Auto"
        if not obj.description:
            obj.description = "No description"
        if new_file_uploaded:
            obj.conversion_status = Video.ConversionStatus.PENDING
        super().save_model(request, obj, form, change)
        if new_file_uploaded:

            def enqueue_conversion_job():
                queue = django_rq.get_queue("default")
                queue.enqueue(transcode_video, obj.pk)

            transaction.on_commit(enqueue_conversion_job)

    def _cleanup_video_media(self, video_id):
        """Remove generated media directory and normalized source leftovers for a video."""
        video_root = Path(settings.MEDIA_ROOT) / "videos"
        video_dir = video_root / str(video_id)
        if video_dir.exists():
            shutil.rmtree(video_dir, ignore_errors=True)

        for source_file in video_root.glob(f"{video_id}.*"):
            if source_file.is_file():
                source_file.unlink(missing_ok=True)

    def delete_model(self, request, obj):
        """Delete one video and clean all derived media files from disk."""
        video_id = obj.pk
        super().delete_model(request, obj)
        self._cleanup_video_media(video_id)

    def delete_queryset(self, request, queryset):
        """Delete multiple videos and clean media folders for each removed object."""
        video_ids = list(queryset.values_list("pk", flat=True))
        super().delete_queryset(request, queryset)
        for video_id in video_ids:
            self._cleanup_video_media(video_id)

    def _file_link(self, field, label, obj):
        """Render an admin link for generated output files when available."""
        file_field = getattr(obj, field)
        if file_field:
            return format_html('<a href="{}" target="_blank">{}</a>', file_field.url, label)
        return "—"

    def link_1080p(self, obj):
        """Render link to the 1080p converted file."""
        return self._file_link("file_1080p", "1080p", obj)

    link_1080p.short_description = "1080p"

    def link_720p(self, obj):
        """Render link to the 720p converted file."""
        return self._file_link("file_720p", "720p", obj)

    link_720p.short_description = "720p"

    def link_480p(self, obj):
        """Render link to the 480p converted file."""
        return self._file_link("file_480p", "480p", obj)

    link_480p.short_description = "480p"
