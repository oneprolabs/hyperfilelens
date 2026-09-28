"""Notification domain exceptions."""


class NotificationError(Exception):
    """Base notification error."""


class ChannelError(NotificationError):
    """Outbound channel failure."""


class ChannelConfigError(ChannelError):
    """Invalid or incomplete channel configuration."""


class WebhookTestError(ChannelError):
    """A classified failure from an interactive webhook connection test."""

    def __init__(self, code: str, message: str, diagnostic: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.diagnostic = diagnostic


class RenderError(NotificationError):
    """Template rendering failure."""
