"""Tests for browser language-tag normalization."""

from unittest.mock import patch

from django.http import HttpRequest, HttpResponse
from django.test import SimpleTestCase, override_settings
from django.utils import translation

from common.i18n.middleware import LanguageCodeMappingMiddleware


@override_settings(
    LANGUAGE_CODE="en",
    LANGUAGES=(("en", "English"), ("es", "Español"), ("zh-hans", "Simplified Chinese")),
    LANGUAGE_CODE_MAPPING={
        "es": "es",
        "es-es": "es",
        "es-mx": "es",
        "zh": "zh-hans",
        "zh-hans": "zh-hans",
    },
)
class LanguageCodeMappingMiddlewareTests(SimpleTestCase):
    """Regional variants should follow an installed primary-language pack."""

    def test_unlisted_spanish_region_uses_installed_spanish(self) -> None:
        request = HttpRequest()
        request.META["HTTP_ACCEPT_LANGUAGE"] = "es-VE,es;q=0.9,en;q=0.8"
        middleware = LanguageCodeMappingMiddleware(lambda _request: HttpResponse())

        with patch("common.i18n.middleware.translation.activate") as activate:
            middleware(request)

        activate.assert_called_once_with("es")
        self.assertEqual(
            request.META["HTTP_ACCEPT_LANGUAGE"],
            "es,es;q=0.9,en;q=0.8",
        )

    def test_script_variant_does_not_use_regional_fallback(self) -> None:
        request = HttpRequest()
        request.META["HTTP_ACCEPT_LANGUAGE"] = "zh-Hant,en;q=0.8"
        middleware = LanguageCodeMappingMiddleware(lambda _request: HttpResponse())

        with patch("common.i18n.middleware.translation.activate") as activate:
            middleware(request)

        activate.assert_called_once_with("en")
        self.assertEqual(request.META["HTTP_ACCEPT_LANGUAGE"], "zh-Hant,en;q=0.8")

    def tearDown(self) -> None:
        translation.deactivate_all()
