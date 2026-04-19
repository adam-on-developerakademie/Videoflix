"""Serializers for registration and login in the auth API."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


AuthUser = get_user_model()


class RegistrationSerializer(serializers.ModelSerializer):
    """Validate and create a new user account."""

    confirmed_password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ["username", "email", "password", "confirmed_password"]
        extra_kwargs = {
            "password": {
                "write_only": True,
            },
            "email": {
                "required": True,
            },
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
        """Create the account with a hashed password and return it."""
        password = self.validated_data["password"]
        account = User(
            email=self.validated_data["email"],
            username=self.validated_data["username"],
        )
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
