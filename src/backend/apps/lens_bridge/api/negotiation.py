"""Content negotiation helpers for binary SourceLens proxy endpoints."""

from __future__ import annotations

from rest_framework.exceptions import NotAcceptable
from rest_framework.negotiation import DefaultContentNegotiation


class PDFDownloadContentNegotiation(DefaultContentNegotiation):
    """Accept PDF download requests while retaining JSON error rendering.

    The endpoint returns a Django ``StreamingHttpResponse`` on success, so no
    renderer is needed for the PDF bytes themselves.  DRF still performs
    content negotiation before the action runs, however.  When a client asks
    specifically for ``application/pdf``, fall back to the normal JSON
    renderer for error responses instead of rejecting the request with 406.
    """

    def select_renderer(self, request, renderers, format_suffix=None):
        try:
            return super().select_renderer(request, renderers, format_suffix)
        except NotAcceptable:
            if not self._requests_pdf(request):
                raise
            json_renderer = next(
                (
                    renderer
                    for renderer in renderers
                    if renderer.media_type == "application/json"
                ),
                None,
            )
            if json_renderer is None:
                raise
            return json_renderer, json_renderer.media_type

    @staticmethod
    def _requests_pdf(request) -> bool:
        """Return whether the client explicitly accepts PDF with q > 0."""

        for token in request.META.get("HTTP_ACCEPT", "*/*").split(","):
            media_type, *parameters = token.strip().lower().split(";")
            if media_type.strip() != "application/pdf":
                continue
            quality = 1.0
            for parameter in parameters:
                key, separator, value = parameter.strip().partition("=")
                if separator and key == "q":
                    try:
                        quality = float(value)
                    except ValueError:
                        quality = 0.0
                    break
            if quality > 0:
                return True
        return False
