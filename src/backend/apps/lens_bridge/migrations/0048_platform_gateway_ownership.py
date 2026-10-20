"""Move infrastructure ownership in place; never infer a business tenant."""

from django.db import migrations
from django.db.migrations.exceptions import IrreversibleError
from django.db.models import F, Q

from common.deploy.migration_safety import require_exclusive_platform_ownership_v1

LEGACY_KEY = "__platform_lens__"
MAINTENANCE_KINDS = {"agent.upgrade", "agent.uninstall", "lens.gateway.browse"}


def move_platform_gateways(apps, schema_editor):
    require_exclusive_platform_ownership_v1(apps, schema_editor)
    connection = schema_editor.connection
    alias = connection.alias
    Organization = apps.get_model("iam", "Organization")
    Node = apps.get_model("node", "Node")
    Token = apps.get_model("node", "NodeToken")
    Credential = apps.get_model("node", "NodeCredential")
    Session = apps.get_model("node", "NodeInstallationSession")
    NodeTask = apps.get_model("node", "NodeTask")
    Gateway = apps.get_model("lens_bridge", "LensGatewayLink")
    Binding = apps.get_model("lens_bridge", "LensWorkspaceBinding")
    Restore = apps.get_model("restore", "RestoreRecord")
    Mount = apps.get_model("restore", "DirectNASMount")
    Task = apps.get_model("task", "Task")
    Audit = apps.get_model("audit", "AuditLog")
    Event = apps.get_model("monitor", "OperationalEvent")
    Metric = apps.get_model("monitor", "ResourceMetric")

    def rows(model):
        return model._base_manager.using(alias)

    legacy = rows(Organization).select_for_update().filter(key=LEGACY_KEY).first()
    links = rows(Gateway).filter(scope="platform")
    if links.filter(organization__isnull=False).exclude(
        organization_id=F("gateway__organization_id"),
    ).exists():
        raise RuntimeError("Public Gateway ownership is inconsistent; upgrade stopped.")
    node_ids = set(links.values_list("gateway_id", flat=True))
    if legacy:
        legacy_nodes = rows(Node).filter(organization_id=legacy.pk)
        if legacy_nodes.exclude(role="gateway").exists():
            raise RuntimeError("The legacy platform container contains non-Gateway Nodes.")
        node_ids.update(legacy_nodes.values_list("pk", flat=True))
        if rows(Gateway).filter(organization_id=legacy.pk).exclude(scope="platform").exists():
            raise RuntimeError("The legacy platform container contains private Gateways.")
    if rows(Gateway).filter(gateway_id__in=node_ids).exclude(scope="platform").exists():
        raise RuntimeError("A public Gateway also has a private binding; upgrade stopped.")
    if rows(Node).filter(pk__in=node_ids).exclude(role="gateway").exists():
        raise RuntimeError("Public Gateway identity references a non-Gateway Node.")
    if rows(Credential).filter(node_id__in=node_ids).exclude(
        organization_id=F("node__organization_id"),
    ).exists():
        raise RuntimeError("Public Gateway credential ownership is inconsistent.")

    token_filter = Q(gateway_scope="platform")
    if legacy:
        token_filter |= Q(organization_id=legacy.pk)
    tokens = rows(Token).filter(token_filter)
    if tokens.exclude(role="gateway").exists():
        raise RuntimeError("The platform container contains non-Gateway enrollment tokens.")
    token_ids = list(tokens.values_list("pk", flat=True))
    if rows(Session).filter(enrollment_token_id__in=token_ids).exclude(
        organization_id=F("enrollment_token__organization_id"),
    ).exists():
        raise RuntimeError("Public Gateway installation session ownership is inconsistent.")

    bindings = rows(Binding).filter(gateway_link__scope="platform")
    if bindings.exclude(execution_node_id=F("gateway_link__gateway_id")).exists():
        raise RuntimeError("Workspace execution Node does not match its public Gateway.")
    if bindings.filter(execution_organization_id__isnull=False).exclude(
        execution_organization_id=F("gateway_link__gateway__organization_id"),
    ).exists():
        raise RuntimeError("Workspace execution ownership is inconsistent.")
    restores = rows(Restore).filter(target_execution_node_id__in=node_ids)
    if restores.exclude(purpose="lens_workspace").exists():
        raise RuntimeError("A user-data restore targets platform infrastructure.")
    for record in restores.iterator():
        if not bindings.filter(
            pk=record.workspace_binding_id,
            organization_id=record.organization_id,
            execution_node_id=record.target_execution_node_id,
        ).exists():
            raise RuntimeError("A public Gateway restore has no matching tenant workspace.")
    if legacy:
        if rows(Restore).filter(
            Q(organization_id=legacy.pk) | Q(requesting_organization_id=legacy.pk),
        ).exists():
            raise RuntimeError("Legacy platform restore data needs an explicit real tenant.")
        if rows(Task).filter(organization_id=legacy.pk).exclude(task_type="node_lifecycle").exists():
            raise RuntimeError("Legacy platform business tasks need an explicit real tenant.")

    # Retain installed namespaces, including orphaned Gateways whose LensNode
    # provisioning never completed. Do not rename workspace roots or Agent IDs.
    for node in rows(Node).filter(pk__in=node_ids, transport_namespace="").select_related("organization"):
        if node.organization_id is None or not node.organization.key:
            raise RuntimeError("A public Gateway has no recoverable installed namespace.")
        rows(Node).filter(pk=node.pk).update(transport_namespace=node.organization.key)
    for token in tokens.filter(transport_namespace="").select_related("organization"):
        if token.organization_id is None or not token.organization.key:
            raise RuntimeError("A public token has no recoverable installed namespace.")
        rows(Token).filter(pk=token.pk).update(transport_namespace=token.organization.key)
    for link in links.filter(workspace_root="", organization__isnull=False):
        rows(Gateway).filter(pk=link.pk).update(
            workspace_root=f"/workspace/org-{link.organization_id}/data",
        )

    # Derive business requesters only from authoritative existing references.
    # Unknown old requesters stop the upgrade instead of adopting a default org.
    platform_tasks = rows(NodeTask).filter(node_id__in=node_ids)
    if legacy:
        for task in platform_tasks.filter(requesting_organization_id=legacy.pk).select_related("parent_task"):
            requester = task.parent_task.organization_id if task.parent_task_id else None
            if requester == legacy.pk:
                requester = None
            if requester is None and task.correlation_type == "restore.record":
                record = restores.filter(
                    task_uuid=task.correlation_id,
                    target_execution_node_id=task.node_id,
                ).first()
                requester = record.requesting_organization_id if record else None
            payload = task.payload if isinstance(task.payload, dict) else {}
            if requester is None and payload.get("workspace_uid"):
                binding = bindings.filter(
                    workspace_uid=payload["workspace_uid"],
                    execution_node_id=task.node_id,
                ).first()
                requester = binding.organization_id if binding else None
            if requester is None and task.kind not in MAINTENANCE_KINDS:
                raise RuntimeError(
                    f"NodeTask {task.pk} has an ambiguous requesting tenant; upgrade stopped."
                )
            if requester is not None and not rows(Organization).filter(pk=requester).exclude(
                key=LEGACY_KEY,
            ).exists():
                raise RuntimeError("A platform task requester is not a real organization.")
            rows(NodeTask).filter(pk=task.pk).update(requesting_organization_id=requester)

    tables = set(connection.introspection.table_names())
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        if node_ids and "ee_resource_group_resource" in tables:
            placeholders = ", ".join(["%s"] * len(node_ids))
            cursor.execute(
                "SELECT 1 FROM ee_resource_group_resource "
                f"WHERE resource_type = %s AND resource_id IN ({placeholders}) LIMIT 1",
                ["node", *sorted(node_ids)],
            )
            if cursor.fetchone():
                raise RuntimeError("A public Gateway has tenant Group bindings; review them before upgrading.")
        if node_ids and "ee_resource_owner" in tables:
            placeholders = ", ".join(["%s"] * len(node_ids))
            cursor.execute(
                "SELECT id, resource_id, owner_id, created_by_id, created_at "
                f"FROM ee_resource_owner WHERE resource_type = %s AND resource_id IN ({placeholders})",
                ["node", *sorted(node_ids)],
            )
            for identifier, resource_id, owner_id, creator_id, created_at in cursor.fetchall():
                rows(Audit).create(
                    organization_id=None, action="migration.platform_resource_provenance",
                    resource_type="node", resource_id=str(resource_id),
                    metadata={
                        "resource_scope": "platform", "source_record_id": identifier,
                        "previous_owner_id": owner_id, "created_by_id": creator_id,
                        "resource_created_at": created_at.isoformat(),
                    },
                )
            cursor.execute(
                f"DELETE FROM ee_resource_owner WHERE resource_type = %s AND resource_id IN ({placeholders})",
                ["node", *sorted(node_ids)],
            )

    platform_tasks.update(organization_id=None)
    rows(Credential).filter(node_id__in=node_ids).update(organization_id=None)
    rows(Session).filter(enrollment_token_id__in=token_ids).update(organization_id=None)
    tokens.update(organization_id=None, gateway_scope="platform")
    bindings.update(execution_organization_id=None)
    restores.update(target_execution_organization_id=None)
    rows(Mount).filter(reader_node_id__in=node_ids).update(execution_organization_id=None)
    links.update(organization_id=None)
    rows(Node).filter(pk__in=node_ids).update(organization_id=None, scope="platform")
    telemetry = Q(resource_type__in=["node", "gateway"], resource_id__in=[str(pk) for pk in node_ids])
    if legacy:
        telemetry |= Q(organization_id=legacy.pk)
    rows(Event).filter(telemetry).update(organization_id=None)
    rows(Metric).filter(telemetry).update(organization_id=None)
    if not legacy:
        return

    rows(Task).filter(organization_id=legacy.pk).update(organization_id=None)
    rows(Audit).filter(organization_id=legacy.pk).update(organization_id=None)
    rows(apps.get_model("lens_bridge", "LensOrgLink")).filter(
        organization_id=legacy.pk,
    ).delete()
    # EE policy copies are not tenant resources. Immutable measurement history
    # is retained with its original organization_key snapshot.
    with connection.cursor() as cursor:
        for table in ("ee_quota", "ee_org_plan"):
            if table in tables:
                cursor.execute(f"DELETE FROM {quote(table)} WHERE organization_id = %s", [legacy.pk])
        if "ee_usage_event" in tables:
            cursor.execute(
                "UPDATE ee_usage_event SET organization_id = NULL WHERE organization_id = %s",
                [legacy.pk],
            )
        # Inspect actual FKs, including optional extensions absent from this
        # migration's historical app registry. Never cascade unknown records.
        for table in sorted(tables):
            description = connection.introspection.get_table_description(cursor, table)
            columns_present = {column.name for column in description}
            owner_columns = columns_present & {
                "organization_id", "requesting_organization_id",
                "execution_organization_id", "target_execution_organization_id",
            }
            # Frozen Host ledgers have no model binding or live authority.
            # Keep their historical rows, as required by the ledger policy.
            if table in {
                "subscription_org", "subscription_entitlement",
                "subscription_quota", "subscription_usage",
            }:
                owner_columns.clear()
            constraints = connection.introspection.get_constraints(cursor, table)
            for constraint in constraints.values():
                foreign_key = constraint.get("foreign_key")
                columns = constraint.get("columns", [])
                if not foreign_key or foreign_key[0] != Organization._meta.db_table or len(columns) != 1:
                    continue
                owner_columns.add(columns[0])
            for column in sorted(owner_columns):
                cursor.execute(
                    f"SELECT 1 FROM {quote(table)} WHERE {quote(column)} = %s LIMIT 1",
                    [legacy.pk],
                )
                if cursor.fetchone():
                    raise RuntimeError(
                        f"Legacy platform container still has records in {table}; "
                        "assign an explicit real tenant before upgrading."
                    )
    rows(Organization).filter(pk=legacy.pk)._raw_delete(alias)


def reverse_empty_database(apps, schema_editor):
    """Only schema-test/empty installations can roll back without a backup."""
    alias = schema_editor.connection.alias
    Node = apps.get_model("node", "Node")
    Token = apps.get_model("node", "NodeToken")
    Organization = apps.get_model("iam", "Organization")
    Audit = apps.get_model("audit", "AuditLog")
    if (
        Organization._base_manager.using(alias).exists()
        or Audit._base_manager.using(alias).exists()
        or Node._base_manager.using(alias).filter(scope="platform").exists()
        or Token._base_manager.using(
        alias,
        ).filter(organization__isnull=True, gateway_scope="platform").exists()
    ):
        raise IrreversibleError(
            "Platform infrastructure has been detached from tenant ownership. "
            "Restore the pre-upgrade database backup for a full downgrade."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0047_platform_gateway_schema"),
        ("node", "0022_platform_node_ownership"),
        ("restore", "0009_platform_restore_execution"),
        ("audit", "0006_platform_audit_scope"),
        ("monitor", "0008_platform_telemetry_scope"),
        ("subscription", "0012_independent_node_quota_fields"),
    ]
    operations = [
        migrations.RunPython(move_platform_gateways, reverse_empty_database),
    ]
