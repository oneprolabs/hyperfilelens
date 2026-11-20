from django.apps import AppConfig


class LensBridgeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.lens_bridge"
    label = "lens_bridge"
    verbose_name = "Lens Bridge"

    def ready(self):
        from django.conf import settings
        from django.db.models.signals import pre_delete

        from apps.lens_bridge.services.usage_attribution import preserve_user_usage_before_deletion

        pre_delete.connect(
            preserve_user_usage_before_deletion,
            sender=settings.AUTH_USER_MODEL,
            dispatch_uid="lens-usage-preserve-billing-user",
        )
