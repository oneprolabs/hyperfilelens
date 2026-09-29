"""Resumable enrollment sessions and long-lived per-node credentials."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.iam.models import Organization
from apps.node.models import (
    Node,
    NodeCredential,
    NodeInstallationSession,
    NodeToken,
)
from apps.node.services.internal.agent_download_slots import release_session_slot

INSTALLATION_SESSION_IDLE_SECONDS = 6 * 60 * 60
INSTALLATION_SESSION_ABSOLUTE_SECONDS = 48 * 60 * 60


@dataclass(frozen=True)
class EnrollmentAuthorization:
    token: NodeToken
    session: NodeInstallationSession | None = None


@dataclass(frozen=True)
class ArtifactDownloadAuthorization:
    """Authorization for signed agent release downloads.

    Enrollment tokens/sessions cover install-time pulls. Registered nodes use
    their long-lived NodeCredential for remote upgrades.
    """

    token: NodeToken | None = None
    session: NodeInstallationSession | None = None
    credential: NodeCredential | None = None


def _token_matches(row: NodeToken, secret: str) -> bool:
    return bool(secret) and secrets.compare_digest(row.token, secret)


def active_enrollment_token(
    *,
    org: Organization,
    secret: str,
    role: str,
    lock: bool = False,
) -> NodeToken | None:
    """Return a valid time-bounded enrollment token."""
    now = timezone.now()
    queryset = NodeToken.objects.filter(
        organization=org,
        role=role,
        is_active=True,
    )
    if lock:
        queryset = queryset.select_for_update()
    for row in queryset.iterator():
        if not _token_matches(row, secret):
            continue
        if row.expires_at and row.expires_at <= now:
            return None
        return row
    return None


def active_installation_session(
    *,
    org: Organization,
    secret: str,
    role: str,
    touch: bool = True,
) -> NodeInstallationSession | None:
    """Resolve an active session secret and renew its idle lease."""
    if not secret:
        return None
    now = timezone.now()
    rows = NodeInstallationSession.objects.select_related("enrollment_token").filter(
        organization=org,
        role=role,
        status=NodeInstallationSession.Status.ACTIVE,
        secret_prefix=secret[:12],
        idle_expires_at__gt=now,
        absolute_expires_at__gt=now,
    )
    for row in rows.iterator():
        if not row.matches(secret):
            continue
        if touch:
            renewed = min(
                now + timedelta(seconds=INSTALLATION_SESSION_IDLE_SECONDS),
                row.absolute_expires_at,
            )
            updated = NodeInstallationSession.objects.filter(
                pk=row.pk,
                status=NodeInstallationSession.Status.ACTIVE,
                idle_expires_at__gt=now,
                absolute_expires_at__gt=now,
            ).update(
                last_activity_at=now,
                idle_expires_at=renewed,
                updated_at=now,
            )
            if updated != 1:
                return None
            row.last_activity_at = now
            row.idle_expires_at = renewed
        return row
    return None


@transaction.atomic
def release_installation_session(
    *,
    org: Organization,
    secret: str,
    role: str,
    installation_id: str,
) -> bool:
    """Release an unfinished host reservation after a failed installation."""
    # Expired or already released sessions still own their Redis slot. Match
    # the actual session secret and installation identity before cleaning it.
    sessions = NodeInstallationSession.objects.select_for_update().filter(
        organization=org,
        role=role,
        installation_id=installation_id.strip(),
        secret_prefix=secret[:12],
        status__in=(
            NodeInstallationSession.Status.ACTIVE,
            NodeInstallationSession.Status.RELEASED,
        ),
    )
    session = next((row for row in sessions if row.matches(secret)), None)
    if session is None:
        return False
    if session.status == NodeInstallationSession.Status.ACTIVE:
        now = timezone.now()
        NodeInstallationSession.objects.filter(
            pk=session.pk,
            status=NodeInstallationSession.Status.ACTIVE,
        ).update(
            status=NodeInstallationSession.Status.RELEASED,
            last_activity_at=now,
            updated_at=now,
        )
    transaction.on_commit(
        lambda org_key=org.key, session_id=session.pk: release_session_slot(
            org_key, session_id
        )
    )
    return True


def resolve_enrollment_authorization(
    *,
    org: Organization,
    secret: str,
    role: str,
) -> EnrollmentAuthorization | None:
    """Accept an installation session or a backward-compatible enrollment token."""
    session = active_installation_session(org=org, secret=secret, role=role)
    if session is not None:
        return EnrollmentAuthorization(token=session.enrollment_token, session=session)
    token = active_enrollment_token(org=org, secret=secret, role=role)
    if token is None:
        return None
    return EnrollmentAuthorization(token=token)


@transaction.atomic
def open_installation_session(
    *,
    org: Organization,
    enrollment_secret: str,
    role: str,
    installation_id: str,
) -> tuple[NodeInstallationSession, str]:
    """Issue a resumable installation session secret."""
    installation_id = installation_id.strip()
    if not installation_id:
        raise ValueError("installation_id is required")

    token = active_enrollment_token(
        org=org,
        secret=enrollment_secret,
        role=role,
        lock=True,
    )
    if token is None:
        raise PermissionError("invalid or expired enrollment token")

    now = timezone.now()
    expired_session_ids = list(
        NodeInstallationSession.objects.select_for_update()
        .filter(
            enrollment_token=token,
            status=NodeInstallationSession.Status.ACTIVE,
        )
        .filter(Q(idle_expires_at__lte=now) | Q(absolute_expires_at__lte=now))
        .values_list("pk", flat=True)
    )
    if expired_session_ids:
        NodeInstallationSession.objects.filter(
            pk__in=expired_session_ids,
            status=NodeInstallationSession.Status.ACTIVE,
        ).update(status=NodeInstallationSession.Status.RELEASED, updated_at=now)
        for session_id in expired_session_ids:
            transaction.on_commit(
                lambda org_key=org.key, session_id=session_id: release_session_slot(
                    org_key, session_id
                )
            )

    session = (
        NodeInstallationSession.objects.select_for_update()
        .filter(
            enrollment_token=token,
            installation_id=installation_id,
            role=role,
            status=NodeInstallationSession.Status.ACTIVE,
        )
        .first()
    )
    if session is None:
        session = NodeInstallationSession(
            organization=org,
            enrollment_token=token,
            role=role,
            installation_id=installation_id,
            last_activity_at=now,
            idle_expires_at=now + timedelta(seconds=INSTALLATION_SESSION_IDLE_SECONDS),
            absolute_expires_at=now
            + timedelta(seconds=INSTALLATION_SESSION_ABSOLUTE_SECONDS),
        )

    secret = NodeInstallationSession.generate_secret()
    session.set_secret(secret)
    session.last_activity_at = now
    session.idle_expires_at = min(
        now + timedelta(seconds=INSTALLATION_SESSION_IDLE_SECONDS),
        session.absolute_expires_at,
    )
    session.save()
    return session, secret


def validate_node_credential(node: Node, secret: str, *, touch: bool = True) -> bool:
    """Validate the long-lived credential assigned to ``node``."""
    if not secret:
        return False
    row = NodeCredential.objects.filter(
        node=node,
        organization_id=node.organization_id,
        is_active=True,
        secret_prefix=secret[:12],
    ).first()
    if row is None or not row.matches(secret):
        return False
    if touch:
        now = timezone.now()
        NodeCredential.objects.filter(pk=row.pk).update(
            last_used_at=now,
            updated_at=now,
        )
    return True


def active_node_credential(
    *,
    org: Organization,
    secret: str,
    role: str,
    touch: bool = True,
) -> NodeCredential | None:
    """Resolve an active long-lived credential by org, role, and secret."""
    if not secret:
        return None
    rows = NodeCredential.objects.filter(
        organization=org,
        role=role,
        is_active=True,
        secret_prefix=secret[:12],
    )
    for row in rows.iterator():
        if not row.matches(secret):
            continue
        if touch:
            now = timezone.now()
            NodeCredential.objects.filter(pk=row.pk).update(
                last_used_at=now,
                updated_at=now,
            )
        return row
    return None


def legacy_enrollment_token_for_node(
    node: Node,
    secret: str,
    *,
    expected_role: str | None = None,
    expected_gateway_scope: str | None = None,
) -> NodeToken | None:
    """Resolve a legacy credential so an existing Agent can migrate once.

    After a long-lived NodeCredential exists for the node, the shared
    enrollment link must no longer authenticate or rotate that node.
    """
    if not secret:
        return None
    if NodeCredential.objects.filter(node=node, is_active=True).exists():
        return None
    now = timezone.now()
    for row in NodeToken.objects.filter(
        organization_id=node.organization_id,
        enrollment_mode=NodeToken.EnrollmentMode.LEGACY,
    ).iterator():
        if not _token_matches(row, secret):
            continue
        if row.role != node.role:
            continue
        if expected_role is not None and row.role != expected_role:
            continue
        if (
            expected_gateway_scope is not None
            and row.gateway_scope != expected_gateway_scope
        ):
            continue
        # A legacy link that already enrolled a node remains a one-time bridge
        # to the new per-node credential even if the old link later expired.
        # Without this, Agents that were offline during the migration window
        # could never reconnect to rotate their credential.
        if row.used_at is not None:
            return row
        if row.is_active:
            if row.expires_at and row.expires_at <= now:
                continue
            return row
    return None


def issue_node_credential(
    *,
    node: Node,
    enrollment_token: NodeToken | None,
    installation_id: str,
) -> str:
    """Create or rotate the credential for a registered node."""
    secret = NodeCredential.generate_secret()
    row = NodeCredential.objects.filter(node=node).first()
    if row is None:
        row = NodeCredential(node=node)
    row.organization = node.organization
    row.role = node.role
    row.installation_id = installation_id
    row.enrollment_token = enrollment_token
    row.is_active = True
    row.revoked_at = None
    row.set_secret(secret)
    row.save()
    return secret


def complete_enrollment_authorization(
    authorization: EnrollmentAuthorization,
) -> None:
    """Mark an enrollment token used and complete its installation session."""
    now = timezone.now()
    # An installation session is an independent lease. It may complete after
    # its originating link is hidden or revoked while a long download is active.
    token = NodeToken.all_objects.select_for_update().get(pk=authorization.token.pk)
    if token.used_at is None:
        token.used_at = now
    token.save(update_fields=["used_at", "updated_at"])

    if authorization.session is not None:
        NodeInstallationSession.objects.filter(pk=authorization.session.pk).update(
            status=NodeInstallationSession.Status.COMPLETED,
            completed_at=now,
            last_activity_at=now,
            updated_at=now,
        )
        transaction.on_commit(
            lambda org_key=authorization.token.organization.key,
            session_id=authorization.session.pk: release_session_slot(
                org_key, session_id
            )
        )
