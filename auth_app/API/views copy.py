"""API views for registration, login, refresh, and logout flows."""

import logging

from rest_framework import status
from rest_framework.permissions import (
    AllowAny,
)
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import (
    AccessToken,
    RefreshToken,
)
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)
from rest_framework.views import APIView

from auth_app.models import RevokedToken
from .serializers import (
    CustomTokenObtainPairSerializer,
    RegistrationSerializer,
)
from .utils import (
    clear_auth_cookies,
    revoke_token,
    set_auth_cookies,
)


logger = logging.getLogger(__name__)


class RegistrationView(APIView):
    """Create new user accounts for anonymous clients."""

    permission_classes = [AllowAny]

    def post(self, request):
        """Register a new user unless the caller is already authenticated."""
        if request.user.is_authenticated:
            return Response(
                {"error": "Authenticated users cannot create a new account."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = RegistrationSerializer(data=request.data)

        if serializer.is_valid():
            serializer.save()
            return Response(
                {"detail": "User created successfully!"},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CookieTokenObtainPairView(TokenObtainPairView):
    """Authenticate a user and store JWT tokens in HttpOnly cookies."""

    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        """Issue token pair and return basic user profile information."""
        serialiser = self.get_serializer(data=request.data)
        serialiser.is_valid(raise_exception=True)

        user = serialiser.user
        response = Response(
            {
                "detail": "Login successfully!",
                "user": {
                    "id": user.pk,
                    "username": user.username,
                    "email": user.email,
                },
            }
        )
        refresh = serialiser.validated_data["refresh"]
        access = serialiser.validated_data["access"]

        response = set_auth_cookies(
            response,
            access_token=access,
            refresh_token=refresh,
        )

        return response


class CookieTokenRefreshView(TokenRefreshView):
    """Refresh access token from refresh-token cookie with revocation checks."""

    def post(self, request, *args, **kwargs):
        """Validate refresh cookie and rotate access token cookie.

        Before delegating to simplejwt's built-in validation, the refresh
        token's JTI is checked against the local revocation table. This
        ensures that tokens revoked at logout are rejected immediately,
        without relying solely on the simplejwt blacklist mechanism.
        """
        refresh_token = request.COOKIES.get("refresh_token")
        if not refresh_token:
            return Response(
                {"error": "No refresh token provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            refresh_obj = RefreshToken(refresh_token)
        except TokenError:
            return Response(
                {"error": "Invalid refresh token"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        refresh_jti = refresh_obj.get("jti")
        if (
            refresh_jti
            and RevokedToken.objects.filter(jti=refresh_jti).exists()
        ):
            return Response(
                {"error": "Refresh token has been revoked"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        data = {"refresh": refresh_token}
        serializer = self.get_serializer(data=data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        access = serializer.validated_data.get("access")

        response = Response({"detail": "Token refreshed"})
        response = set_auth_cookies(response, access_token=access)

        return response


class LogoutView(APIView):
    """Revoke tokens and clear authentication cookies."""

    permission_classes = [AllowAny]

    def post(self, request):
        """Persist token revocation state and return a logout confirmation.

        Both access and refresh tokens are written to the local revocation
        table for fast synchronous rejection in subsequent requests. The
        refresh token is additionally blacklisted through simplejwt so that
        its middleware also rejects it even when the custom check is bypassed.
        Errors from the simplejwt blacklist step are silently ignored because
        the token may already be present or may have expired.
        """
        source_ip = request.META.get("REMOTE_ADDR")
        access_token = request.COOKIES.get("access_token")
        refresh_token = request.COOKIES.get("refresh_token")

        revoked_access = revoke_token(access_token, AccessToken, source_ip)
        revoked_refresh = revoke_token(refresh_token, RefreshToken, source_ip)
        if refresh_token:
            try:
                RefreshToken(refresh_token).blacklist()
            except Exception:
                pass

        logger.info(
            "Logout processed. access_revoked=%s refresh_revoked=%s ip=%s",
            revoked_access,
            revoked_refresh,
            source_ip,
        )

        response = Response(
            {
                "detail": (
                    "Log-Out successfully! All Tokens will be deleted. "
                    "Refresh token is now invalid."
                )
            },
            status=status.HTTP_200_OK,
        )
        response = clear_auth_cookies(response)
        return response
