from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.iam.profile_models import Profile


class ProfileLanguageTests(SimpleTestCase):
    def test_profile_uses_locale_neutral_english_defaults(self) -> None:
        profile = Profile()

        self.assertEqual(profile.language, "en")
        self.assertEqual(profile.timezone, "UTC")

    @override_settings(LANGUAGES=(("en", "English"), ("fr", "French")))
    def test_installed_language_pack_locale_is_valid(self) -> None:
        profile = Profile(language="fr")

        profile.clean()

    @override_settings(LANGUAGES=(("en", "English"),))
    def test_uninstalled_language_pack_locale_is_rejected(self) -> None:
        profile = Profile(language="fr")

        with self.assertRaisesMessage(ValidationError, "Language 'fr' is not installed"):
            profile.clean()


@override_settings(LANGUAGES=(("en", "English"), ("zh-hans", "Simplified Chinese")))
class ProfileLanguageAPITests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="language-user",
            email="language-user@example.com",
            password="not-used",
        )
        Profile.objects.create(user=self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_user_can_update_installed_profile_language(self) -> None:
        response = self.client.patch(
            "/api/v1/auth/user",
            {"language": "zh-hans"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.language, "zh-hans")
        self.assertEqual(response.data["language"], "zh-hans")

    def test_user_cannot_select_uninstalled_profile_language(self) -> None:
        response = self.client.patch(
            "/api/v1/auth/user",
            {"language": "de"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.language, "en")
        errors = response.data["data"]["errors"]
        self.assertTrue(any(error["field"] == "language" for error in errors))

    def test_user_without_profile_still_receives_english_fallback(self) -> None:
        profileless_user = User.objects.create_user(
            username="profileless-language-user",
            email="profileless-language-user@example.com",
        )
        self.client.force_authenticate(profileless_user)

        response = self.client.get("/api/v1/auth/user")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["language"], "en")
