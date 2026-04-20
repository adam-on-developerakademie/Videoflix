"""Authentication helpers for API views protected by JWT access tokens."""

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

from auth_app.models import RevokedToken


class CookieJWTAuthentication(JWTAuthentication):
    """Authenticate requests from either bearer headers or HttpOnly cookies."""

    def authenticate(self, request):
        """
        Return the user and validated token when a JWT is present in the HttpOnly cookie.
        Only the 'access_token' cookie is accepted; Authorization header is ignored.
        """
        raw_token = request.COOKIES.get("access_token")

        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)
        jti = validated_token.get("jti")
        if jti and RevokedToken.objects.filter(jti=jti).exists():
            raise InvalidToken("Token has been revoked")

        return self.get_user(validated_token), validated_token
