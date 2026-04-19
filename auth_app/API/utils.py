"""Utility helpers for JWT cookie handling and token revocation."""

import logging
from datetime import datetime, timezone

from django.conf import settings
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
