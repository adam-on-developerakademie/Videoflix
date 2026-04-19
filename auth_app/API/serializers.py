"""Serializers for registration and login in the auth API."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


AuthUser = get_user_model()


class RegistrationSerializer(serializers.ModelSerializer):
    """Validate and create a new inactive user account."""

    confirmed_password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ["email", "password", "confirmed_password"]
        extra_kwargs = {
            "password": {"write_only": True},
            "email": {"required": True},
        }

    def validate_confirmed_password(self, value):
        """Ensure password confirmation matches the provided password."""
        password = self.initial_data.get("password")
        if password and value and password != value:
            raise serializers.ValidationError("Passwords do not match")
        return value

    def validate_email(self, value):
        """Reject duplicate e-mail addresses."""
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Email already exists")
        return value

    def save(self):
        """Create an inactive account and return it.

        The username is derived from the local part of the e-mail address.
        A numeric suffix is appended when a collision exists.
        The account is set inactive; activation via e-mail is required.
        """
        email = self.validated_data["email"]
        password = self.validated_data["password"]

        base_username = email.split("@")[0]
        username = base_username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}_{counter}"
            counter += 1

        account = User(email=email, username=username, is_active=False)
        account.set_password(password)
        account.save()
        return account


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Authenticate via e-mail and password."""

    email = serializers.EmailField(required=False, allow_blank=True)
    username = serializers.CharField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True)
    confirmed_password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
    )

    def __init__(self, *args, **kwargs):
        """Make username optional because e-mail login is supported."""
        super().__init__(*args, **kwargs)
        username_field = self.username_field
        if username_field in self.fields:
            self.fields[username_field].required = False
            self.fields[username_field].allow_blank = True

    def validate(self, attrs):
        """Resolve e-mail to username and delegate token validation."""
        email = attrs.get("email")
        username = attrs.get("username")
        password = attrs.get("password")

        # Keep compatibility with register-form payloads.
        attrs.pop("confirmed_password", None)

        if username:
            raise serializers.ValidationError(
                {"username": "Please log in with e-mail and password only."}
            )

        if not email:
            raise serializers.ValidationError(
                {"email": "This field is required."}
            )

        try:
            user = AuthUser.objects.get(email=email)
            username = user.username
        except AuthUser.DoesNotExist:
            raise serializers.ValidationError("Invalid credentials")

        data = super().validate({"username": username, "password": password})
        return data
