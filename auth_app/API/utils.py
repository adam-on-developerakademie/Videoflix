"""Utility helpers for JWT cookie handling, token revocation, and email dispatch."""

import logging
from datetime import datetime, timezone

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.html import strip_tags
from django.utils.http import urlsafe_base64_encode
from rest_framework_simplejwt.exceptions import TokenError
from auth_app.models import RevokedToken


logger = logging.getLogger(__name__)


def exp_to_datetime(exp):
    """Convert JWT ``exp`` claim to UTC datetime."""
    if exp is None:
        return None
    return datetime.fromtimestamp(int(exp), tz=timezone.utc)


def revoke_token(raw_token, token_class, source_ip):
    """Persist token revocation and return token identity info if available."""
    if not raw_token:
        return None

    try:
        token = token_class(raw_token)
    except TokenError:
        return None

    token_jti = token.get("jti")
    user_id = token.get("user_id")
    expires_at = exp_to_datetime(token.get("exp"))

    if token_jti:
        RevokedToken.objects.get_or_create(
            jti=token_jti,
            defaults={
                "token_type": token.get("token_type", "unknown"),
                "user_id": user_id,
                "expires_at": expires_at,
                "source_ip": source_ip,
            },
        )

    return {"jti": token_jti, "user_id": user_id}


def set_auth_cookies(response, access_token=None, refresh_token=None):
    """Set HttpOnly auth cookies for access and/or refresh tokens."""
    cookie_secure = getattr(settings, "JWT_COOKIE_SECURE", False)

    if access_token:
        response.set_cookie(
            key="access_token",
            value=str(access_token),
            httponly=True,
            secure=cookie_secure,
            samesite="Lax",
        )

    if refresh_token:
        response.set_cookie(
            key="refresh_token",
            value=str(refresh_token),
            httponly=True,
            secure=cookie_secure,
            samesite="Lax",
        )

    return response


def clear_auth_cookies(response):
    """Clear auth cookies by replacing them with empty, expired values."""
    cookie_secure = getattr(settings, "JWT_COOKIE_SECURE", False)

    response.set_cookie(
        key="access_token",
        value="",
        httponly=True,
        secure=cookie_secure,
        samesite="Lax",
        max_age=0,
    )
    response.set_cookie(
        key="refresh_token",
        value="",
        httponly=True,
        secure=cookie_secure,
        samesite="Lax",
        max_age=0,
    )

    return response


def _make_uid_and_token(user):
    """Return a (uidb64, token) tuple for the given user."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return uid, token


def send_activation_email(user, request):
    """Send an account-activation e-mail and return (uidb64, token)."""
    uid, token = _make_uid_and_token(user)
    frontend_base = getattr(settings, "FRONTEND_BASE_URL", "http://127.0.0.1:5500")
    activation_url = f"{frontend_base}/pages/auth/activate.html?uid={uid}&token={token}"
    html_message = render_to_string(
        "auth_app/emails/activation.html",
        {"user": user, "activation_url": activation_url},
    )
    send_mail(
        subject="Confirm your email",
        message=strip_tags(html_message),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
        fail_silently=False,
    )
    logger.info("Activation email sent to %s", user.email)
    return uid, token


def send_password_reset_email(user, request):
    """Send a password-reset e-mail and return (uidb64, token)."""
    uid, token = _make_uid_and_token(user)
    frontend_base = getattr(settings, "FRONTEND_BASE_URL", "http://127.0.0.1:5500")
    reset_url = f"{frontend_base}/pages/auth/confirm_password.html?uid={uid}&token={token}"
    html_message = render_to_string(
        "auth_app/emails/password_reset.html",
        {"reset_url": reset_url},
    )
    send_mail(
        subject="Reset your Password",
        message=strip_tags(html_message),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
        fail_silently=False,
    )
    logger.info("Password reset email sent to %s", user.email)
