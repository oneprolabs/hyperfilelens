"""Notification domain exceptions."""


class NotificationError(Exception):
    """Base notification error."""


class ChannelError(NotificationError):
    """Outbound channel failure."""


class ChannelConfigError(ChannelError):
    """Invalid or incomplete channel configuration."""


class DingTalkDeliveryError(ChannelError):
    """DingTalk accepted the HTTP request but rejected the message."""

    def __init__(self, errcode: object, errmsg: object):
        self.errcode = str(errcode)
        self.errmsg = str(errmsg or "unknown DingTalk error")
        super().__init__(
            f"DingTalk rejected the message (errcode={self.errcode}): {self.errmsg}"
        )


class WebhookTestError(ChannelError):
    """A classified failure from an interactive webhook connection test."""

    def __init__(self, code: str, message: str, diagnostic: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.diagnostic = diagnostic


class RenderError(NotificationError):
    """Template rendering failure."""
