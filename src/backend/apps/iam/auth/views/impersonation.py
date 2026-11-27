"""Community-safe routes forwarding only to an installed Enterprise provider."""

from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView


class ImpersonationControlView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    operation = "session"

    def forward(self, request):
        from common import impersonation

        handler = getattr(impersonation.get_impersonation_provider(), "control", None)
        if not callable(handler):
            raise NotFound()
        return handler(request, self.operation)

    def get(self, request):
        return self.forward(request)

    def post(self, request):
        return self.forward(request)

    def delete(self, request):
        return self.forward(request)
