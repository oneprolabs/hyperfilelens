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


class FeishuDeliveryError(ChannelError):
    """Feishu accepted the HTTP request but did not confirm message delivery."""

    def __init__(self, code: object, message: object):
        super().__init__(f"Feishu rejected the message (code={code}): {message}")


class ChannelTestError(ChannelError):
    """A classified failure from an interactive channel connection test."""

    def __init__(self, code: str, message: str, diagnostic: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.diagnostic = diagnostic


class WebhookTestError(ChannelTestError):
    """A classified failure from an interactive webhook connection test."""


class EmailTestError(ChannelTestError):
    """A classified failure from an interactive email connection test."""


class RenderError(NotificationError):
    """Template rendering failure."""
