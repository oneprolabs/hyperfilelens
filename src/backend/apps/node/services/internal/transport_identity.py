"""Route Agent traffic without equating a routing namespace with tenant ownership.

Namespace resolution is not authentication. Every caller must still validate
the token, session, signed URL or node credential against the selected record.
"""

from dataclasses import dataclass

from django.db.models import BigIntegerField, CharField, F, Q, Value

from apps.iam.models import Organization
from apps.node.models import Node, NodeCredential, NodeInstallationSession, NodeToken
from common.resource_scope import ResourceScope

PLATFORM_TRANSPORT_NAMESPACE = "platform:hfl"


@dataclass(frozen=True)
class TransportScope:
    namespace: str
    scope: ResourceScope
    organization: Organization | None


def resolve_transport_scope(
    namespace: str,
    *,
    secret: str = "",
    node_id: int | str | None = None,
    token_id: int | str | None = None,
    credential_id: int | str | None = None,
) -> TransportScope | None:
    """Resolve a namespace without manufacturing a platform tenant."""
    if not namespace or len(namespace) > 128:
        return None
    fields = ("owner_scope", "owner_id", "transport_key_value", "owner_name")
    tenants = Organization.objects.filter(key=namespace, is_active=True).annotate(
        owner_scope=Value("organization"), owner_id=F("pk"),
        transport_key_value=F("key"), owner_name=F("name"),
    ).order_by().values_list(*fields)
    platform_values = {
        "owner_scope": Value("platform"),
        "owner_id": Value(None, output_field=BigIntegerField()),
        "transport_key_value": Value(namespace, output_field=CharField()),
        "owner_name": Value("", output_field=CharField()),
    }
    tokens = NodeToken.all_objects.filter(
        transport_namespace=namespace, gateway_scope="platform",
        role="gateway", organization__isnull=True,
    ).annotate(**platform_values).order_by().values_list(*fields)
    nodes = Node.all_objects.filter(
        transport_namespace=namespace, scope="platform", organization__isnull=True,
    ).annotate(**platform_values).order_by().values_list(*fields)
    candidates = list(tenants.union(tokens, nodes)[:2])
    if not candidates:
        return None
    if len(candidates) > 1:
        return _resolve_bound_transport_scope(
            namespace, secret=secret, node_id=node_id,
            token_id=token_id, credential_id=credential_id,
        )
    scope, identifier, key, name = candidates[0]
    organization = None
    if scope == ResourceScope.ORGANIZATION:
        organization = Organization.from_db(
            "default", ["id", "key", "name", "is_active"], [identifier, key, name, True],
        )
    return TransportScope(namespace, ResourceScope(scope), organization)


def _identifier(value: object) -> int | None:
    raw = str(value or "")
    if not raw.isascii() or not raw.isdigit() or len(raw) > 19:
        return None
    identifier = int(raw)
    return identifier if 0 < identifier <= 2**63 - 1 else None


def _resolve_bound_transport_scope(
    namespace: str, *, secret: str,
    node_id: int | str | None,
    token_id: int | str | None,
    credential_id: int | str | None,
) -> TransportScope | None:
    """Disambiguate historical shared keys by a resource, never by a claimed scope.

    These are routing hints only. Expiry, revocation, role and cryptographic
    authorization remain mandatory in the calling endpoint.
    """
    from apps.node.models.node_credential import hash_node_secret

    def for_node(node) -> TransportScope | None:
        if node is None or node.transport_key != namespace:
            return None
        if node.scope == ResourceScope.PLATFORM and node.organization_id is None:
            return TransportScope(namespace, ResourceScope.PLATFORM, None)
        if node.organization_id is not None and node.organization.is_active:
            return TransportScope(namespace, ResourceScope.ORGANIZATION, node.organization)
        return None

    def for_token(token) -> TransportScope | None:
        if token is None or token.transport_key != namespace:
            return None
        if token.gateway_scope == "platform" and token.organization_id is None:
            return TransportScope(namespace, ResourceScope.PLATFORM, None)
        if token.organization_id is not None and token.organization.is_active:
            return TransportScope(namespace, ResourceScope.ORGANIZATION, token.organization)
        return None

    if _identifier(node_id):
        return for_node(Node.all_objects.select_related("organization").filter(
            pk=_identifier(node_id),
        ).first())
    if _identifier(token_id):
        return for_token(NodeToken.all_objects.select_related("organization").filter(
            pk=_identifier(token_id),
        ).first())
    if _identifier(credential_id):
        credential = NodeCredential.objects.select_related("node__organization").filter(
            pk=_identifier(credential_id),
        ).first()
        return for_node(credential.node) if credential else None
    if not secret or len(secret) > 512:
        return None
    token = NodeToken.all_objects.select_related("organization").filter(token=secret).first()
    if token:
        return for_token(token)
    digest = hash_node_secret(secret)
    session = NodeInstallationSession.objects.select_related(
        "enrollment_token__organization",
    ).filter(secret_hash=digest).first()
    if session:
        return for_token(session.enrollment_token)
    credential = NodeCredential.objects.select_related("node__organization").filter(
        secret_hash=digest,
    ).first()
    return for_node(credential.node) if credential else None


def node_for_transport(*, namespace: str, node_id: int) -> Node | None:
    """Resolve one installed Node, preserving organization lifecycle fences."""
    node = Node.objects.select_related("organization").filter(
        Q(scope="platform", organization__isnull=True)
        | Q(scope="organization", organization__is_active=True),
        pk=node_id,
    ).first()
    if node is None or node.transport_key != namespace:
        return None
    return node
