"""
Profile models for the IAM app.
"""

from django.contrib.auth.models import User
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Profile(models.Model):
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["is_installation_admin"],
                condition=models.Q(is_installation_admin=True),
                name="iam_single_installation_admin",
            ),
        ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    registration_completed = models.BooleanField(default=False)
    requires_password_change = models.BooleanField(default=False)
    is_installation_admin = models.BooleanField(default=False, editable=False)
    registration_token = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )
    registration_token_expires = models.DateTimeField(
        null=True,
        blank=True,
    )
    nickname = models.CharField(max_length=30, blank=True)
    avatar_url = models.URLField(blank=True)
    bio = models.TextField(max_length=500, blank=True)
    language = models.CharField(
        max_length=32,
        default="en",
    )
    timezone = models.CharField(max_length=50, default="UTC")
    preferred_platform = models.CharField(max_length=50, blank=True, default="")
    last_login_ip = models.CharField(max_length=45, blank=True, default="")
    last_login_location = models.CharField(max_length=120, blank=True, default="")
    previous_login_at = models.DateTimeField(null=True, blank=True)
    previous_login_ip = models.CharField(max_length=45, blank=True, default="")
    previous_login_location = models.CharField(max_length=120, blank=True, default="")
    registered_at = models.DateTimeField(null=True, blank=True)

    def clean(self) -> None:
        """Validate the preferred language against installed language packs."""
        super().clean()
        supported_languages = dict(settings.LANGUAGES)
        if self.language not in supported_languages:
            raise ValidationError(
                {"language": f"Language {self.language!r} is not installed."},
            )

    def __str__(self) -> str:
        return self.user.username
