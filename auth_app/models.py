"""Database models for authentication-related state."""

from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class RevokedToken(models.Model):
	"""Persist revoked JWT token identifiers for explicit invalidation."""

	jti = models.CharField(max_length=255, unique=True, db_index=True)
	token_type = models.CharField(max_length=32)
	user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
	expires_at = models.DateTimeField(null=True, blank=True)
	revoked_at = models.DateTimeField(auto_now_add=True)
	source_ip = models.GenericIPAddressField(null=True, blank=True)

	def __str__(self):
		"""Return a compact display value for admin and logs."""
		return f"{self.token_type}:{self.jti}"
