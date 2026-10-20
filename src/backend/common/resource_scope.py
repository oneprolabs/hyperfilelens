"""Ownership boundaries shared by platform-capable resource types."""

from django.db import models


class ResourceScope(models.TextChoices):
    """Resource ownership, not a user's permission or assignment scope."""

    PLATFORM = "platform", "Platform"
    ORGANIZATION = "organization", "Organization"
