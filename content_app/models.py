"""Database models for content domain objects."""

from django.db import models


class Video(models.Model):
	class ConversionStatus(models.TextChoices):
		PENDING = "pending", "Pending"
		PROCESSING = "processing", "Processing"
		DONE = "done", "Done"
		FAILED = "failed", "Failed"

	title = models.CharField(max_length=255)
	description = models.TextField()
	thumbnail_url = models.URLField(blank=True, default="")
	category = models.CharField(max_length=100)
	video_file = models.FileField(upload_to="videos/", null=True, blank=True)
	file_1080p = models.FileField(upload_to="videos/", null=True, blank=True)
	file_720p = models.FileField(upload_to="videos/", null=True, blank=True)
	file_480p = models.FileField(upload_to="videos/", null=True, blank=True)
	conversion_status = models.CharField(
		max_length=20,
		choices=ConversionStatus.choices,
		default=ConversionStatus.PENDING,
	)
	created_at = models.DateTimeField(auto_now_add=True)

	def __str__(self):
		return self.title
