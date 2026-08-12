from django.contrib import admin

from apps.notification.models import (
    NotificationChannel,
    NotificationDelivery,
    NotificationLog,
    UserNotification,
)


@admin.register(NotificationChannel)
class NotificationChannelAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "channel_type", "is_active", "updated_at")
    list_filter = ("channel_type", "is_active")


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("channel", "organization", "status", "notification_type", "sent_at")
    list_filter = ("status", "notification_type")


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = ("channel", "organization", "event_type", "status", "created_at")


@admin.register(UserNotification)
class UserNotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "organization", "event_type", "read_at", "updated_at")
    list_filter = ("organization", "event_type", "severity")
