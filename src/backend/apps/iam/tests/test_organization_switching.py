"""Host-only installations must not gain Enterprise organization switching."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase


class CommunityOrganizationSwitchTests(APITestCase):
    @patch('apps.iam.account_views.get_authz_provider', return_value=None)
    def test_host_contract_rejects_switching_without_an_enterprise_provider(self, _provider):
        user = get_user_model().objects.create_user(username='community-switch-user')
        self.client.force_authenticate(user)
        response = self.client.post('/api/v1/iam/switch-organization/', {'org_key': 'personal'}, format='json')
        self.assertEqual(response.status_code, 403)
