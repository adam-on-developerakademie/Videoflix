"""Admin registrations for authentication domain models."""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.admin.sites import NotRegistered
from django.utils.html import format_html

from auth_app.api.utils import build_activation_url
from auth_app.models import RevokedToken

User = get_user_model()


@admin.register(RevokedToken)
class RevokedTokenAdmin(admin.ModelAdmin):
	"""Admin configuration for revoked JWT token records."""

	list_display = ("token_type", "user", "jti", "revoked_at", "expires_at")
	search_fields = ("jti", "user__email", "user__username")
	list_filter = ("token_type", "revoked_at", "expires_at")
	readonly_fields = ("jti", "token_type", "user", "revoked_at", "expires_at")


class VideoflixUserAdmin(UserAdmin):
	"""Expose account activation links for inactive users in Django admin."""

	list_display = UserAdmin.list_display + ("activation_link_status",)
	readonly_fields = UserAdmin.readonly_fields + ("admin_activation_link",)

	@admin.display(description="Activation")
	def activation_link_status(self, obj):
		"""Show a short activation-link indicator in the user changelist."""
		if obj.is_active:
			return "Already active"

		activation_url = build_activation_url(obj)
		return format_html(
			'<a href="{}" target="_blank" rel="noopener noreferrer">Open link</a>',
			activation_url,
		)

	@admin.display(description="Activation link")
	def admin_activation_link(self, obj):
		"""Expose the full activation link on the user detail page."""
		if not obj or obj.is_active:
			return "Account already active"

		activation_url = build_activation_url(obj)
		return format_html(
			'<a href="{0}" target="_blank" rel="noopener noreferrer">{0}</a>',
			activation_url,
		)

	def get_fieldsets(self, request, obj=None):
		"""Append the activation link to the user detail form."""
		fieldsets = list(super().get_fieldsets(request, obj))
		if obj is not None:
			fieldsets.append(("Videoflix", {"fields": ("admin_activation_link",)}))
		return fieldsets


try:
	admin.site.unregister(User)
except NotRegistered:
	pass

admin.site.register(User, VideoflixUserAdmin)
