from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation
from django.utils.translation import override

from apps.iam.services.verification_email import (
    VerificationEmailKind,
    send_verification_code_email,
)


@override_settings(DEFAULT_FROM_EMAIL="HyperFileLens <noreply@test.local>")
class VerificationEmailTests(TestCase):
    def setUp(self):
        super().setUp()
        translation.activate("en")
        self.addCleanup(translation.deactivate_all)

    def test_registration_email_subject_starts_with_code(self):
        send_verification_code_email(
            recipient="user@example.com",
            code="617005",
            minutes=15,
            kind=VerificationEmailKind.REGISTRATION,
        )

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.subject, "617005 is your HyperFileLens verification code")
        self.assertIn("617005", message.body)
        self.assertIn("617005\n", message.body)
        self.assertNotIn("Hello 2776998293", message.body)
        self.assertEqual(len(message.alternatives), 1)
        html, mime = message.alternatives[0]
        self.assertEqual(mime, "text/html")
        self.assertIn("617005", html)
        self.assertIn("HyperFileLens", html)
        self.assertIn("letter-spacing: 5px", html)

    def test_password_reset_email_subject_starts_with_code(self):
        send_verification_code_email(
            recipient="user@example.com",
            code="123456",
            minutes=10,
            kind=VerificationEmailKind.PASSWORD_RESET,
        )

        message = mail.outbox[0]
        self.assertEqual(message.subject, "123456 is your HyperFileLens password reset code")
        self.assertIn("password reset", message.body.lower())

    @skipUnless(
        any(code == "zh-hans" for code, _name in settings.LANGUAGES),
        "bundled Simplified Chinese runtime catalog is not loaded",
    )
    def test_chinese_catalog_translates_verification_email(self):
        with override("zh-hans"):
            send_verification_code_email(
                recipient="user@example.com",
                code="123456",
                minutes=10,
                kind=VerificationEmailKind.LOGIN,
            )

        message = mail.outbox[0]
        self.assertTrue(message.subject.startswith("123456 "))
        self.assertNotIn("sign-in code", message.subject.lower())
        self.assertNotIn("Use the verification code", message.body)
        self.assertNotIn("This code will expire", message.body)
        self.assertTrue(any(ord(character) > 127 for character in message.body))

    @skipUnless(
        any(code == "es" for code, _name in settings.LANGUAGES),
        "bundled Spanish runtime catalog is not loaded",
    )
    def test_spanish_catalog_translates_verification_email(self):
        with override("es"):
            send_verification_code_email(
                recipient="user@example.com",
                code="123456",
                minutes=10,
                kind=VerificationEmailKind.LOGIN,
            )

        message = mail.outbox[0]
        self.assertEqual(
            message.subject,
            "123456 es su código de inicio de sesión de HyperFileLens",
        )
        self.assertIn("iniciar sesión", message.body)
        self.assertIn("caducará en 10 minutos", message.body)

    @skipUnless(
        any(code == "zh-hans" for code, _name in settings.LANGUAGES),
        "bundled Simplified Chinese runtime catalog is not loaded",
    )
    @patch(
        "apps.iam.auth.views.email_code_login.email_delivery_configured",
        return_value=True,
    )
    @patch(
        "apps.iam.auth.views.email_code_login.email_code_login_enabled",
        return_value=True,
    )
    def test_chinese_accept_language_translates_api_error(
        self,
        _email_code_login_enabled,
        _email_delivery_configured,
    ):
        response = self.client.post(
            reverse("email_code_login_send"),
            {"email": "not-an-email"},
            content_type="application/json",
            HTTP_ACCEPT_LANGUAGE="zh-Hans",
            HTTP_X_HFL_SITE_ROLE="tenant",
            HTTP_X_FORWARDED_PROTO="https",
            secure=True,
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        translated_error = payload["data"]["error"]
        translated_message = translated_error["message"]
        self.assertNotEqual(translated_message, "Invalid email format")
        self.assertTrue(any(ord(character) > 127 for character in translated_message))
        self.assertEqual(translated_error["fields"]["email"], [translated_message])

    @skipUnless(
        any(code == "es" for code, _name in settings.LANGUAGES),
        "bundled Spanish runtime catalog is not loaded",
    )
    @patch(
        "apps.iam.auth.views.email_code_login.email_delivery_configured",
        return_value=True,
    )
    @patch(
        "apps.iam.auth.views.email_code_login.email_code_login_enabled",
        return_value=True,
    )
    def test_spanish_accept_language_alias_translates_api_error(
        self,
        _email_code_login_enabled,
        _email_delivery_configured,
    ):
        response = self.client.post(
            reverse("email_code_login_send"),
            {"email": "not-an-email"},
            content_type="application/json",
            HTTP_ACCEPT_LANGUAGE="es-MX",
            HTTP_X_HFL_SITE_ROLE="tenant",
            HTTP_X_FORWARDED_PROTO="https",
            secure=True,
        )

        self.assertEqual(response.status_code, 400)
        translated_error = response.json()["data"]["error"]
        self.assertEqual(
            translated_error["message"],
            "Formato de correo electrónico inválido",
        )
        self.assertEqual(
            translated_error["fields"]["email"],
            [translated_error["message"]],
        )
