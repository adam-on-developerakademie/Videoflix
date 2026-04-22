"""Tests for the auth_app API endpoints."""

from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from auth_app.admin import VideoflixUserAdmin
from auth_app.api.authentication import CookieJWTAuthentication
from auth_app.models import RevokedToken
from auth_app.api.utils import (
    build_activation_url,
    exp_to_datetime,
    revoke_token,
    send_activation_email,
    send_activation_email_task,
    build_password_reset_url,
    send_password_reset_email,
    send_password_reset_email_task,
)

User = get_user_model()

REGISTER_URL = reverse("registration")
LOGIN_URL = reverse("login")
LOGOUT_URL = reverse("logout")
REFRESH_URL = reverse("token_refresh")
PASSWORD_RESET_URL = reverse("password_reset")


def _activate_url(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return reverse("activate", kwargs={"uidb64": uid, "token": token}), uid, token


def _password_confirm_url(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return reverse("password_confirm", kwargs={"uidb64": uid, "token": token}), uid, token


def _make_active_user(email="active@test.de", password="Test1234!"):
    return User.objects.create_user(
        username=email.split("@")[0],
        email=email,
        password=password,
        is_active=True,
    )


def _make_inactive_user(email="inactive@test.de", password="Test1234!"):
    return User.objects.create_user(
        username=email.split("@")[0],
        email=email,
        password=password,
        is_active=False,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class RegistrationViewTests(APITestCase):

    @patch("auth_app.api.views.send_activation_email", return_value=("uid123", "tok123"))
    def test_register_success_returns_201(self, mock_send):
        data = {"email": "new@test.de", "password": "Test1234!", "confirmed_password": "Test1234!"}
        response = self.client.post(REGISTER_URL, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("user", response.data)
        self.assertIn("token", response.data)
        self.assertEqual(response.data["user"]["email"], "new@test.de")
        self.assertEqual(response.data["token"], "tok123")

    @patch("auth_app.api.views.send_activation_email", return_value=("uid", "tok"))
    def test_register_creates_inactive_user(self, mock_send):
        self.client.post(REGISTER_URL,
            {"email": "inactive_check@test.de", "password": "Test1234!", "confirmed_password": "Test1234!"},
            format="json")
        user = User.objects.get(email="inactive_check@test.de")
        self.assertFalse(user.is_active)

    @patch("auth_app.api.views.send_activation_email", return_value=("uid", "tok"))
    def test_register_sends_activation_email(self, mock_send):
        self.client.post(REGISTER_URL,
            {"email": "mail_check@test.de", "password": "Test1234!", "confirmed_password": "Test1234!"},
            format="json")
        mock_send.assert_called_once()

    def test_register_duplicate_email_returns_400(self):
        _make_inactive_user(email="dup@test.de")
        response = self.client.post(REGISTER_URL,
            {"email": "dup@test.de", "password": "Test1234!", "confirmed_password": "Test1234!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_password_mismatch_returns_400(self):
        response = self.client.post(REGISTER_URL,
            {"email": "mismatch@test.de", "password": "Test1234!", "confirmed_password": "Wrong!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_missing_email_returns_400(self):
        response = self.client.post(REGISTER_URL,
            {"password": "Test1234!", "confirmed_password": "Test1234!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_authenticated_user_returns_403(self):
        user = _make_active_user()
        self.client.force_authenticate(user=user)
        response = self.client.post(REGISTER_URL,
            {"email": "other@test.de", "password": "Test1234!", "confirmed_password": "Test1234!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

class ActivationViewTests(APITestCase):

    def test_activation_success(self):
        user = _make_inactive_user(email="toactivate@test.de")
        url, _, _ = _activate_url(user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "Account successfully activated.")
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_activation_invalid_uid_returns_400(self):
        _, _, token = _activate_url(_make_inactive_user(email="uid400@test.de"))
        url = reverse("activate", kwargs={"uidb64": "notvalidbase64!!", "token": token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_activation_invalid_token_returns_400(self):
        user = _make_inactive_user(email="tok400@test.de")
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        url = reverse("activate", kwargs={"uidb64": uid, "token": "completely-invalid-token"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_activation_nonexistent_user_returns_400(self):
        # Build a uidb64 for a PK that does not exist
        uid = urlsafe_base64_encode(force_bytes(999999))
        url = reverse("activate", kwargs={"uidb64": uid, "token": "sometoken"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

class LoginViewTests(APITestCase):

    def setUp(self):
        self.user = _make_active_user(email="login@test.de", password="Test1234!")

    def test_login_success_returns_200_with_cookies(self):
        response = self.client.post(LOGIN_URL,
            {"email": "login@test.de", "password": "Test1234!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access_token", response.cookies)
        self.assertIn("refresh_token", response.cookies)

    def test_login_with_confirmed_password_still_returns_200(self):
        response = self.client.post(LOGIN_URL,
            {"email": "login@test.de", "password": "Test1234!", "confirmed_password": "Test1234!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_login_wrong_password_returns_401(self):
        response = self.client.post(LOGIN_URL,
            {"email": "login@test.de", "password": "WrongPass!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_inactive_user_returns_401(self):
        _make_inactive_user(email="blocked@test.de", password="Test1234!")
        response = self.client.post(LOGIN_URL,
            {"email": "blocked@test.de", "password": "Test1234!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_with_username_returns_400(self):
        response = self.client.post(LOGIN_URL,
            {"username": "login", "password": "Test1234!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_missing_email_returns_400(self):
        response = self.client.post(LOGIN_URL,
            {"password": "Test1234!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_nonexistent_email_returns_4xx(self):
        response = self.client.post(LOGIN_URL,
            {"email": "ghost@test.de", "password": "Test1234!"},
            format="json")
        self.assertIn(response.status_code, (status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED))


# ---------------------------------------------------------------------------
# Token Refresh
# ---------------------------------------------------------------------------

class TokenRefreshViewTests(APITestCase):

    def setUp(self):
        self.user = _make_active_user(email="refresh@test.de")

    def _set_refresh_cookie(self, user=None):
        u = user or self.user
        refresh = RefreshToken.for_user(u)
        self.client.cookies["refresh_token"] = str(refresh)
        return refresh

    def test_refresh_success_returns_200(self):
        self._set_refresh_cookie()
        response = self.client.post(REFRESH_URL, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access_token", response.cookies)

    def test_refresh_without_cookie_returns_400(self):
        response = self.client.post(REFRESH_URL, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_refresh_revoked_token_returns_401(self):
        refresh = self._set_refresh_cookie()
        RevokedToken.objects.create(
            jti=refresh["jti"],
            token_type="refresh",
            user=self.user,
        )
        response = self.client.post(REFRESH_URL, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

class LogoutViewTests(APITestCase):

    def setUp(self):
        self.user = _make_active_user(email="logout@test.de")

    def _login(self):
        response = self.client.post(LOGIN_URL,
            {"email": "logout@test.de", "password": "Test1234!"},
            format="json")
        return response

    def test_logout_returns_200_and_clears_cookies(self):
        self._login()
        response = self.client.post(LOGOUT_URL, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Cookies cleared via max_age=0
        self.assertEqual(response.cookies.get("access_token", {}).get("max-age", -1), 0)
        self.assertEqual(response.cookies.get("refresh_token", {}).get("max-age", -1), 0)

    def test_logout_without_tokens_returns_200(self):
        response = self.client.post(LOGOUT_URL, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Password Reset
# ---------------------------------------------------------------------------

class PasswordResetViewTests(APITestCase):

    def setUp(self):
        self.user = _make_active_user(email="resetme@test.de")

    @patch("auth_app.api.views.send_password_reset_email")
    def test_known_email_returns_200_and_sends_email(self, mock_send):
        response = self.client.post(PASSWORD_RESET_URL, {"email": "resetme@test.de"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("detail", response.data)
        mock_send.assert_called_once()

    @patch("auth_app.api.views.send_password_reset_email")
    def test_unknown_email_also_returns_200(self, mock_send):
        response = self.client.post(PASSWORD_RESET_URL, {"email": "ghost@test.de"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_send.assert_not_called()

    def test_missing_email_returns_400(self):
        response = self.client.post(PASSWORD_RESET_URL, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Password Confirm
# ---------------------------------------------------------------------------

class PasswordConfirmViewTests(APITestCase):

    def setUp(self):
        self.user = _make_active_user(email="confirmpw@test.de", password="OldPass1!")

    def test_valid_token_resets_password(self):
        url, _, _ = _password_confirm_url(self.user)
        response = self.client.post(url,
            {"new_password": "NewPass1!", "confirm_password": "NewPass1!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("detail", response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewPass1!"))

    def test_passwords_mismatch_returns_400(self):
        url, _, _ = _password_confirm_url(self.user)
        response = self.client.post(url,
            {"new_password": "NewPass1!", "confirm_password": "Other99!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_passwords_returns_400(self):
        url, _, _ = _password_confirm_url(self.user)
        response = self.client.post(url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_token_returns_400(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        url = reverse("password_confirm", kwargs={"uidb64": uid, "token": "bad-token"})
        response = self.client.post(url,
            {"new_password": "NewPass1!", "confirm_password": "NewPass1!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_uid_returns_400(self):
        url = reverse("password_confirm", kwargs={"uidb64": "invaliduid!!", "token": "sometoken"})
        response = self.client.post(url,
            {"new_password": "NewPass1!", "confirm_password": "NewPass1!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nonexistent_user_returns_400(self):
        uid = urlsafe_base64_encode(force_bytes(999999))
        url = reverse("password_confirm", kwargs={"uidb64": uid, "token": "sometoken"})
        response = self.client.post(url,
            {"new_password": "NewPass1!", "confirm_password": "NewPass1!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_token_cannot_be_reused_after_password_change(self):
        """After the first use the token is invalidated (password changed → different hash)."""
        url, _, _ = _password_confirm_url(self.user)
        # First use — should succeed
        self.client.post(url,
            {"new_password": "NewPass1!", "confirm_password": "NewPass1!"},
            format="json")
        # Second use with the same URL — token now invalid
        response = self.client.post(url,
            {"new_password": "AnotherPass1!", "confirm_password": "AnotherPass1!"},
            format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Email URL generation (frontend links)
# ---------------------------------------------------------------------------

DUMMY_FRONTEND = "http://frontend.test:5500"


@override_settings(
    FRONTEND_BASE_URL=DUMMY_FRONTEND,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class EmailUtilsTests(APITestCase):
    """Verify that activation and password-reset emails link to the frontend."""

    def setUp(self):
        self.user = _make_inactive_user(email="emailutil@test.de")
        self.request = RequestFactory().get("/")

    def _html_body(self, index=0):
        """Return the HTML alternative body of the sent email at index."""
        return mail.outbox[index].alternatives[0][0]

    @patch("auth_app.api.utils.django_rq.get_queue")
    def test_activation_email_links_to_frontend(self, mock_get_queue):
        send_activation_email(self.user, self.request)
        queue = mock_get_queue.return_value
        queue.enqueue.assert_called_once()
        args = queue.enqueue.call_args[0]
        self.assertEqual(args[0].__name__, "send_activation_email_task")
        self.assertEqual(args[1], self.user.email)
        self.assertEqual(args[2], self.user.username)
        html = args[3]
        self.assertIn(DUMMY_FRONTEND, html)
        self.assertIn("/pages/auth/activate.html", html)
        self.assertIn("uid=", html)
        self.assertIn("token=", html)

    @patch("auth_app.api.utils.django_rq.get_queue")
    def test_activation_email_does_not_link_to_backend(self, mock_get_queue):
        send_activation_email(self.user, self.request)
        html = mock_get_queue.return_value.enqueue.call_args[0][3]
        self.assertNotIn("/api/activate/", html)

    def test_activation_email_subject_and_recipient(self):
        send_activation_email_task(self.user.email, self.user.username, f"{DUMMY_FRONTEND}/pages/auth/activate.html?uid=x&token=y")
        sent = mail.outbox[0]
        self.assertEqual(sent.subject, "Confirm your email")
        self.assertIn(self.user.email, sent.to)

    @patch("auth_app.api.utils.django_rq.get_queue")
    def test_password_reset_email_links_to_frontend(self, mock_get_queue):
        send_password_reset_email(self.user, self.request)
        queue = mock_get_queue.return_value
        queue.enqueue.assert_called_once()
        args = queue.enqueue.call_args[0]
        self.assertEqual(args[0].__name__, "send_password_reset_email_task")
        self.assertEqual(args[1], self.user.email)
        html = args[2]
        self.assertIn(DUMMY_FRONTEND, html)
        self.assertIn("/pages/auth/confirm_password.html", html)
        self.assertIn("uid=", html)
        self.assertIn("token=", html)

    @patch("auth_app.api.utils.django_rq.get_queue")
    def test_password_reset_email_does_not_link_to_backend(self, mock_get_queue):
        send_password_reset_email(self.user, self.request)
        html = mock_get_queue.return_value.enqueue.call_args[0][2]
        self.assertNotIn("/api/password_confirm/", html)

    def test_password_reset_email_subject_and_recipient(self):
        send_password_reset_email_task(self.user.email, f"{DUMMY_FRONTEND}/pages/auth/confirm_password.html?uid=x&token=y")
        sent = mail.outbox[0]
        self.assertEqual(sent.subject, "Reset your Password")
        self.assertIn(self.user.email, sent.to)

    def test_build_activation_url_points_to_frontend(self):
        """Activation URL builder should match the frontend activation page format."""
        activation_url = build_activation_url(self.user)
        self.assertIn(DUMMY_FRONTEND, activation_url)
        self.assertIn("/pages/auth/activate.html", activation_url)
        self.assertIn("uid=", activation_url)
        self.assertIn("token=", activation_url)

    def test_build_password_reset_url_points_to_frontend(self):
        """Password-reset URL builder should match the frontend reset page format."""
        reset_url = build_password_reset_url(self.user)
        self.assertIn(DUMMY_FRONTEND, reset_url)
        self.assertIn("/pages/auth/confirm_password.html", reset_url)
        self.assertIn("uid=", reset_url)
        self.assertIn("token=", reset_url)


@override_settings(FRONTEND_BASE_URL=DUMMY_FRONTEND)
class UserAdminActivationLinkTests(APITestCase):
    """Verify that Django admin exposes activation links for inactive users."""

    def setUp(self):
        self.site = AdminSite()
        self.admin = VideoflixUserAdmin(User, self.site)

    def test_inactive_user_gets_clickable_activation_link(self):
        """Inactive users should expose a frontend activation link in admin."""
        user = _make_inactive_user(email="adminlink@test.de")
        html = self.admin.admin_activation_link(user)
        self.assertIn(DUMMY_FRONTEND, html)
        self.assertIn("/pages/auth/activate.html", html)

    def test_active_user_shows_already_active_message(self):
        """Active users should not display an activation link in admin."""
        user = _make_active_user(email="adminalreadyactive@test.de")
        self.assertEqual(self.admin.admin_activation_link(user), "Account already active")


# ---------------------------------------------------------------------------
# Admin – activation_link_status and get_fieldsets
# ---------------------------------------------------------------------------

@override_settings(FRONTEND_BASE_URL=DUMMY_FRONTEND)
class UserAdminExtraTests(APITestCase):
    """Cover activation_link_status column and get_fieldsets injection."""

    def setUp(self):
        self.site = AdminSite()
        self.admin = VideoflixUserAdmin(User, self.site)
        self.request = RequestFactory().get("/")

    def test_activation_link_status_active_user_returns_text(self):
        """Active users show 'Already active' in the changelist column."""
        user = _make_active_user(email="activestatus@test.de")
        self.assertEqual(self.admin.activation_link_status(user), "Already active")

    def test_activation_link_status_inactive_user_returns_link(self):
        """Inactive users get a clickable frontend activation link in the column."""
        user = _make_inactive_user(email="inactivestatus@test.de")
        html = self.admin.activation_link_status(user)
        self.assertIn(DUMMY_FRONTEND, html)
        self.assertIn("Open link", html)

    def test_get_fieldsets_with_obj_appends_videoflix_section(self):
        """A user instance triggers the Videoflix fieldset injection."""
        user = _make_active_user(email="fieldset@test.de")
        fieldsets = self.admin.get_fieldsets(self.request, obj=user)
        names = [fs[0] for fs in fieldsets]
        self.assertIn("Videoflix", names)

    def test_get_fieldsets_without_obj_no_videoflix_section(self):
        """Passing obj=None must not inject the Videoflix fieldset."""
        fieldsets = self.admin.get_fieldsets(self.request, obj=None)
        names = [fs[0] for fs in fieldsets]
        self.assertNotIn("Videoflix", names)


# ---------------------------------------------------------------------------
# CookieJWTAuthentication
# ---------------------------------------------------------------------------

class CookieJWTAuthenticationTests(APITestCase):
    """Test CookieJWTAuthentication with and without a valid access cookie."""

    def setUp(self):
        self.user = _make_active_user(email="cookieauth@test.de")
        self.auth = CookieJWTAuthentication()
        self.factory = RequestFactory()

    def test_no_cookie_returns_none(self):
        """Missing access_token cookie must return None (unauthenticated)."""
        request = self.factory.get("/")
        self.assertIsNone(self.auth.authenticate(request))

    def test_valid_cookie_returns_user_and_token(self):
        """A valid access_token cookie authenticates the user."""
        access = str(AccessToken.for_user(self.user))
        request = self.factory.get("/")
        request.COOKIES = {"access_token": access}
        user, token = self.auth.authenticate(request)
        self.assertEqual(user.pk, self.user.pk)

    def test_revoked_cookie_raises_invalid_token(self):
        """A revoked access_token cookie must raise InvalidToken."""
        from rest_framework_simplejwt.exceptions import InvalidToken
        access_obj = AccessToken.for_user(self.user)
        RevokedToken.objects.create(
            jti=access_obj["jti"],
            token_type="access",
            user=self.user,
        )
        request = self.factory.get("/")
        request.COOKIES = {"access_token": str(access_obj)}
        with self.assertRaises(InvalidToken):
            self.auth.authenticate(request)


# ---------------------------------------------------------------------------
# Utils unit tests
# ---------------------------------------------------------------------------

class UtilsUnitTests(APITestCase):
    """Low-level unit tests for JWT utility helpers."""

    def test_exp_to_datetime_none_returns_none(self):
        """exp_to_datetime(None) must return None."""
        self.assertIsNone(exp_to_datetime(None))

    def test_revoke_token_invalid_string_returns_none(self):
        """revoke_token with an unparseable token must return None gracefully."""
        result = revoke_token("not.a.valid.token", RefreshToken, "127.0.0.1")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# RevokedToken model __str__
# ---------------------------------------------------------------------------

class RevokedTokenModelTests(APITestCase):
    """Test RevokedToken model string representation."""

    def test_str_includes_type_and_jti(self):
        """__str__ must return '<type>:<jti>'."""
        user = _make_active_user(email="modelstr@test.de")
        token = RevokedToken.objects.create(
            jti="test-jti-xyz",
            token_type="access",
            user=user,
        )
        self.assertEqual(str(token), "access:test-jti-xyz")


# ---------------------------------------------------------------------------
# Username deduplication during registration
# ---------------------------------------------------------------------------

class UsernameDeduplicationTests(APITestCase):
    """Verify the serializer increments duplicate usernames."""

    @patch("auth_app.api.views.send_activation_email", return_value=("uid", "tok"))
    def test_duplicate_username_prefix_gets_incremented(self, mock_send):
        """Second registration sharing a username prefix must resolve to '<name>_1'."""
        User.objects.create_user(username="dup", email="dup@a.de", password="Pass1234!")
        response = self.client.post(
            REGISTER_URL,
            {"email": "dup@b.de", "password": "Test1234!", "confirmed_password": "Test1234!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(username="dup_1").exists())


# ---------------------------------------------------------------------------
# TokenRefresh – invalid token value in cookie
# ---------------------------------------------------------------------------

class TokenRefreshInvalidTokenTests(APITestCase):
    """Test TokenRefreshView with a malformed token cookie."""

    def test_malformed_refresh_cookie_returns_400(self):
        """A cookie containing garbage should yield HTTP 400."""
        self.client.cookies["refresh_token"] = "not.a.valid.token.at.all"
        response = self.client.post(REFRESH_URL, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_serializer_exception_returns_400(self):
        """An unexpected exception from serializer.is_valid must return HTTP 400."""
        user = _make_active_user(email="serializer_exc@test.de")
        refresh = RefreshToken.for_user(user)
        self.client.cookies["refresh_token"] = str(refresh)
        with patch(
            "auth_app.api.views.TokenRefreshView.get_serializer"
        ) as mock_get_serializer:
            mock_serializer = mock_get_serializer.return_value
            mock_serializer.is_valid.side_effect = Exception("unexpected error")
            response = self.client.post(REFRESH_URL, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Logout – blacklist exception is swallowed
# ---------------------------------------------------------------------------

class LogoutBlacklistExceptionTests(APITestCase):
    """Logout must succeed even when the simplejwt blacklist step raises."""

    def setUp(self):
        self.user = _make_active_user(email="blacklistfail@test.de")

    def test_logout_proceeds_when_blacklist_raises(self):
        """An exception from blacklist() must be silently swallowed."""
        self.client.post(
            LOGIN_URL,
            {"email": "blacklistfail@test.de", "password": "Test1234!"},
            format="json",
        )
        with patch("auth_app.api.views.RefreshToken") as mock_rt:
            mock_rt.return_value.get.return_value = None
            mock_rt.return_value.blacklist.side_effect = Exception("already blacklisted")
            response = self.client.post(LOGOUT_URL, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
