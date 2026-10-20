from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase, APIRequestFactory

from apps.iam.auth.personal_api_key import PersonalApiKeyAuthentication
from apps.iam.models import PersonalApiKey
from apps.iam.profile_models import Profile
from apps.iam.services.registration_service import provision_registered_user_tenant

User = get_user_model()


class PersonalApiKeyAuthenticationTests(APITestCase):
    def test_installation_identity_key_can_target_foreign_org_without_membership(self):
        root = User.objects.create_user(username="key-root", is_staff=True)
        Profile.objects.update_or_create(user=root, defaults={"is_installation_admin": True})
        key = PersonalApiKey.objects.create(user=root, name="root")
        request = self.factory.get(
            "/", HTTP_X_API_KEY=key.token, HTTP_X_ORG_KEY=self.org.key,
        )
        user, _auth = PersonalApiKeyAuthentication().authenticate(request)
        self.assertEqual(user.pk, root.pk)

    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = User.objects.create_user(
            username="api-key@test.local",
            email="api-key@test.local",
            password="Pass1234",
            is_active=True,
        )
        self.org, _ = provision_registered_user_tenant(self.user)
        self.api_key = PersonalApiKey.objects.create(user=self.user, name="ci")

    def test_api_key_with_matching_org_succeeds(self):
        request = self.factory.get(
            "/",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key.token}",
            HTTP_X_ORG_KEY=self.org.key,
        )

        user, _auth = PersonalApiKeyAuthentication().authenticate(request)
        self.assertEqual(user.id, self.user.id)

    def test_api_key_with_foreign_org_fails(self):
        other = User.objects.create_user(
            username="other@test.local",
            email="other@test.local",
            password="Pass1234",
            is_active=True,
        )
        other_org, _ = provision_registered_user_tenant(other)

        request = self.factory.get(
            "/",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key.token}",
            HTTP_X_ORG_KEY=other_org.key,
        )

        with self.assertRaises(Exception):
            PersonalApiKeyAuthentication().authenticate(request)
