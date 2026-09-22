"""
Runtime configuration models.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class GlobalConfig(models.Model):
    """
    Store runtime configuration values as JSON.

    Resolution order at read time (see ``selectors.interface.get_config``):
    tenant row → global Runtime row → Deployment (env/settings) → caller
    ``default``. Explicit empty Runtime values win and do not fall through.
    """

    class Scope(models.TextChoices):
        GLOBAL = "global", "Global"
        TENANT = "tenant", "Tenant"

    class ValueType(models.TextChoices):
        STRING = "string", "String"
        NUMBER = "number", "Number"
        BOOLEAN = "boolean", "Boolean"
        OBJECT = "object", "Object"
        ARRAY = "array", "Array"

    key = models.CharField(max_length=255, db_index=True)
    scope = models.CharField(
        max_length=20,
        choices=Scope.choices,
        default=Scope.GLOBAL,
        db_index=True,
    )
    tenant_key = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_index=True,
    )
    value = models.JSONField(blank=True)
    value_type = models.CharField(
        max_length=20,
        choices=ValueType.choices,
        default=ValueType.STRING,
    )
    category = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
    )
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_app_configs",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_app_configs",
    )

    class Meta:
        db_table = "app_config_globalconfig"
        verbose_name = "Global Configuration"
        verbose_name_plural = "Global Configurations"
        ordering = ["category", "key", "scope", "tenant_key", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["key", "scope", "tenant_key"],
                name="uniq_app_config_key_scope_tenant",
            )
        ]

    def __str__(self) -> str:
        suffix = ""
        if self.scope == self.Scope.TENANT:
            suffix = f" ({self.tenant_key or '-'})"
        return f"{self.key}{suffix}"

    def clean(self) -> None:
        super().clean()

        if self.scope == self.Scope.TENANT and not self.tenant_key.strip():
            raise ValidationError(
                {"tenant_key": "tenant_key is required for tenant scope"},
            )
        if self.scope == self.Scope.GLOBAL and self.tenant_key.strip():
            raise ValidationError(
                {"tenant_key": "tenant_key must be empty for global scope"},
            )

        if self.value_type == self.ValueType.STRING and not isinstance(
            self.value, str
        ):
            raise ValidationError(
                {"value": 'Value must be a string for value_type="string"'},
            )
        if self.value_type == self.ValueType.NUMBER and not isinstance(
            self.value, (int, float)
        ):
            raise ValidationError(
                {"value": 'Value must be a number for value_type="number"'},
            )
        if self.value_type == self.ValueType.BOOLEAN and not isinstance(
            self.value, bool
        ):
            raise ValidationError(
                {"value": 'Value must be a boolean for value_type="boolean"'},
            )
        if self.value_type == self.ValueType.OBJECT and not isinstance(
            self.value, dict
        ):
            raise ValidationError(
                {"value": 'Value must be an object for value_type="object"'},
            )
        if self.value_type == self.ValueType.ARRAY and not isinstance(
            self.value, list
        ):
            raise ValidationError(
                {"value": 'Value must be an array for value_type="array"'},
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
