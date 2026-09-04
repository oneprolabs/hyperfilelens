from rest_framework import serializers

from apps.lens_bridge.models import (
    LensGatewayLink,
    LensKnowledgeSource,
    LensSessionLink,
)
from apps.lens_bridge.services import (
    conversion_display,
    gateway_chat_queue,
    gateway_readiness,
    ingest_policy,
    provisioning,
)
from apps.lens_bridge.services.chat_lifecycle_errors import (
    classify_chat_lifecycle_error,
)
from apps.protection.models import BackupConfig, BackupSourceSnapshot
from apps.protection.services.source_identity import resolve_source_display_name


class LensKnowledgeSourceSerializer(serializers.ModelSerializer):
    gateway_name = serializers.CharField(source="gateway.name", read_only=True)
    ingest_policy = serializers.SerializerMethodField()
    ingest_summary = serializers.SerializerMethodField()
    sync_phase = serializers.SerializerMethodField()
    document_conversion = serializers.SerializerMethodField()

    class Meta:
        model = LensKnowledgeSource
        fields = [
            "id",
            "name",
            "gateway",
            "gateway_name",
            "backup_source_snapshot_id",
            "backup_snapshot_directory_id",
            "source_path",
            "source_scopes_json",
            "mount_path_on_gateway",
            "workspace_path_on_lensnode",
            "linked_version_mode",
            "pinned_snapshot_id",
            "sl_assistant_uuid",
            "sl_datasource_uuid",
            "sl_lensnode_uuid",
            "status",
            "status_detail",
            "lifecycle_status",
            "sync_phase",
            "sync_state_json",
            "document_conversion",
            "last_restore_record_id",
            "ingest_policy",
            "ingest_summary",
            "scan_enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "gateway_name",
            "mount_path_on_gateway",
            "workspace_path_on_lensnode",
            "sl_assistant_uuid",
            "sl_datasource_uuid",
            "sl_lensnode_uuid",
            "status",
            "status_detail",
            "lifecycle_status",
            "sync_phase",
            "sync_state_json",
            "document_conversion",
            "last_restore_record_id",
            "ingest_policy",
            "ingest_summary",
            "created_at",
            "updated_at",
        ]

    def _normalized_policy(self, obj: LensKnowledgeSource) -> dict:
        cache = self.context.setdefault("knowledge_source_ingest_policies", {})
        if obj.pk not in cache:
            cache[obj.pk] = ingest_policy.normalize_ingest_policy(
                obj.ingest_policy_json
            )
        return cache[obj.pk]

    def get_ingest_policy(self, obj: LensKnowledgeSource) -> dict:
        return self._normalized_policy(obj)

    def get_ingest_summary(self, obj: LensKnowledgeSource) -> str:
        return ingest_policy.ingest_summary(self._normalized_policy(obj))

    def get_sync_phase(self, obj: LensKnowledgeSource) -> str:
        sync_state = obj.sync_state_json if isinstance(obj.sync_state_json, dict) else {}
        return str(sync_state.get("phase") or "")

    def get_document_conversion(self, obj: LensKnowledgeSource) -> dict | None:
        return conversion_display.document_conversion_view(
            conversion_display.conversion_state_from_knowledge_source(obj)
        )


class LensKnowledgeSourceScopeSerializer(serializers.Serializer):
    source_path = serializers.CharField(max_length=1000)
    backup_snapshot_directory_id = serializers.IntegerField(min_value=1)
    path_type = serializers.ChoiceField(
        choices=("dir", "file", "unknown"),
        required=False,
        default="unknown",
    )


class LensIngestPolicyInputSerializer(serializers.Serializer):
    """Validate tenant-controlled conversion flags and resource limits."""

    document = serializers.BooleanField(required=False)
    embedded_image = serializers.BooleanField(required=False)
    image = serializers.BooleanField(required=False)
    document_model_ref = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    vision_model_ref = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    max_images = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=ingest_policy.INGEST_POLICY_MAXIMUMS["max_images"],
    )
    max_file_size_mb = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=ingest_policy.INGEST_POLICY_MAXIMUMS["max_file_size_mb"],
    )
    max_pages = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=ingest_policy.INGEST_POLICY_MAXIMUMS["max_pages"],
    )
    pdf_extract_images = serializers.BooleanField(required=False)
    pdf_extract_images_on_text_pages = serializers.BooleanField(required=False)
    pdf_render_scanned_pages = serializers.BooleanField(required=False)
    pdf_max_pages = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=ingest_policy.INGEST_POLICY_MAXIMUMS["pdf_max_pages"],
    )
    pdf_max_images_per_page = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=ingest_policy.INGEST_POLICY_MAXIMUMS[
            "pdf_max_images_per_page"
        ],
    )
    pdf_render_dpi = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=ingest_policy.INGEST_POLICY_MAXIMUMS["pdf_render_dpi"],
    )
    pdf_min_text_chars = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=ingest_policy.INGEST_POLICY_MAXIMUMS["pdf_min_text_chars"],
    )
    pdf_min_image_area_ratio = serializers.FloatField(
        required=False,
        min_value=0.0001,
        max_value=1,
    )

    def validate(self, attrs):
        for field in ("document_model_ref", "vision_model_ref"):
            if attrs.get(field):
                raise serializers.ValidationError(
                    {field: "Conversion models are selected by the administrator."}
                )
            attrs.pop(field, None)
        return attrs


class LensKnowledgeSourceCreateSerializer(serializers.ModelSerializer):
    ingest_policy = LensIngestPolicyInputSerializer(required=False)
    source_scopes = LensKnowledgeSourceScopeSerializer(many=True, required=False)

    class Meta:
        model = LensKnowledgeSource
        fields = [
            "name",
            "gateway",
            "backup_source_snapshot_id",
            "backup_snapshot_directory_id",
            "source_path",
            "source_scopes",
            "linked_version_mode",
            "pinned_snapshot_id",
            "scan_enabled",
            "ingest_policy",
        ]

    def validate(self, attrs):
        org = self.context.get("org")
        if org is None:
            return attrs
        gateway = attrs["gateway"]
        provisioning.require_gateway_node(org, gateway.id)
        expected_scope = self.context.get(
            "gateway_scope",
            LensGatewayLink.GatewayScope.ORGANIZATION,
        )
        if expected_scope in {
            LensGatewayLink.GatewayScope.ORGANIZATION,
            LensGatewayLink.GatewayScope.USER,
        }:
            from apps.lens_bridge.services.gateway_execution import (
                require_organization_gateway_link,
            )

            link = require_organization_gateway_link(
                tenant_organization=org,
                gateway_id=gateway.id,
                require_ready=False,
            )
        else:
            link = provisioning.get_gateway_link(org, gateway.id)
            if link.scope != expected_scope:
                raise serializers.ValidationError(
                    {"gateway": "Data gateway scope is invalid for this operation."}
                )
        gateway_readiness.require_hfl_usable_gateway(link, field="gateway")

        source_scopes = attrs.pop("source_scopes", None)
        is_gateway_local = not attrs.get("backup_source_snapshot_id") and not attrs.get(
            "backup_snapshot_directory_id"
        ) and not source_scopes

        if source_scopes:
            normalized_scopes = []
            for index, scope in enumerate(source_scopes):
                path = str(scope.get("source_path") or "").strip()
                directory_id = scope.get("backup_snapshot_directory_id")
                if not path:
                    raise serializers.ValidationError(
                        {"source_scopes": {index: {"source_path": "Source path is required."}}}
                    )
                if not directory_id:
                    raise serializers.ValidationError(
                        {
                            "source_scopes": {
                                index: {
                                    "backup_snapshot_directory_id": "Select a snapshot directory root."
                                }
                            }
                        }
                    )
                normalized_scopes.append(
                    {
                        "source_path": path,
                        "backup_snapshot_directory_id": int(directory_id),
                        "path_type": str(scope.get("path_type") or "unknown"),
                    }
                )
            attrs["source_scopes_json"] = normalized_scopes
            attrs["source_path"] = normalized_scopes[0]["source_path"]
            attrs["backup_snapshot_directory_id"] = normalized_scopes[0][
                "backup_snapshot_directory_id"
            ]
            is_gateway_local = False

        source_path = (attrs.get("source_path") or "").strip()
        if not source_path:
            raise serializers.ValidationError({"source_path": "Source path is required."})
        attrs["source_path"] = source_path

        if is_gateway_local:
            from apps.lens_bridge.services.gateway_paths import (
                GatewayPathError,
                path_within_root,
            )

            try:
                source_path = path_within_root(
                    source_path,
                    link.resolved_workspace_root(),
                    allow_root=True,
                    field="source_path",
                )
            except GatewayPathError as exc:
                raise serializers.ValidationError(
                    {"source_path": "Directory must be under the gateway workspace root."}
                ) from exc
            attrs["source_path"] = source_path
        else:
            if not attrs.get("backup_source_snapshot_id"):
                raise serializers.ValidationError(
                    {"backup_source_snapshot_id": "Select a backup snapshot."}
                )
            if not attrs.get("backup_snapshot_directory_id"):
                raise serializers.ValidationError(
                    {"backup_snapshot_directory_id": "Select a snapshot directory root."}
                )

        mode = attrs.get(
            "linked_version_mode",
            LensKnowledgeSource.LinkedVersionMode.LATEST,
        )
        if mode == LensKnowledgeSource.LinkedVersionMode.PINNED:
            pinned_snapshot_id = (
                attrs.get("pinned_snapshot_id")
                or attrs.get("backup_source_snapshot_id")
            )
            if not pinned_snapshot_id:
                raise serializers.ValidationError(
                    {
                        "pinned_snapshot_id": (
                            "Pinned snapshot id is required when mode is pinned."
                        )
                    }
                )
            if (
                attrs.get("backup_source_snapshot_id")
                and pinned_snapshot_id != attrs["backup_source_snapshot_id"]
            ):
                raise serializers.ValidationError(
                    {
                        "pinned_snapshot_id": (
                            "Pinned snapshot must match the selected snapshot."
                        )
                    }
                )
            attrs["pinned_snapshot_id"] = pinned_snapshot_id
        else:
            attrs["pinned_snapshot_id"] = None

        raw_ingest_policy = attrs.pop("ingest_policy", None)
        if (
            raw_ingest_policy
            and raw_ingest_policy.get("embedded_image")
            and not raw_ingest_policy.get("document")
        ):
            raise serializers.ValidationError(
                {
                    "ingest_policy": {
                        "embedded_image": (
                            "Embedded image conversion requires documents."
                        )
                    }
                }
            )
        if raw_ingest_policy is None and not is_gateway_local:
            attrs["ingest_policy_json"] = (
                ingest_policy.managed_restore_default_policy(org)
            )
        else:
            attrs["ingest_policy_json"] = (
                ingest_policy.policy_from_user_input(
                    raw_ingest_policy,
                    org,
                )
            )
        return attrs


class LensKnowledgeSourceUpdateSerializer(serializers.ModelSerializer):
    ingest_policy = LensIngestPolicyInputSerializer(required=False)

    class Meta:
        model = LensKnowledgeSource
        fields = [
            "name",
            "linked_version_mode",
            "pinned_snapshot_id",
            "scan_enabled",
            "ingest_policy",
        ]

    def validate(self, attrs):
        org = self.context.get("org")
        if org is not None and "ingest_policy" in attrs:
            if self.instance.status == LensKnowledgeSource.Status.SYNCING:
                raise serializers.ValidationError(
                    {
                        "ingest_policy": (
                            "Conversion settings cannot change while a sync "
                            "is in progress."
                        )
                    }
                )
            current_policy = ingest_policy.normalize_ingest_policy(
                self.instance.ingest_policy_json
            )
            current_policy.update(attrs.pop("ingest_policy"))
            if current_policy.get("embedded_image") and not current_policy.get(
                "document"
            ):
                raise serializers.ValidationError(
                    {
                        "ingest_policy": {
                            "embedded_image": (
                                "Embedded image conversion requires documents."
                            )
                        }
                    }
                )
            attrs["ingest_policy_json"] = ingest_policy.policy_from_user_input(
                current_policy,
                org,
            )
        mode = attrs.get(
            "linked_version_mode",
            getattr(self.instance, "linked_version_mode", LensKnowledgeSource.LinkedVersionMode.LATEST),
        )
        if mode == LensKnowledgeSource.LinkedVersionMode.PINNED:
            pinned = attrs.get(
                "pinned_snapshot_id",
                getattr(self.instance, "pinned_snapshot_id", None),
            )
            if not pinned:
                raise serializers.ValidationError(
                    {"pinned_snapshot_id": "Pinned snapshot id is required when mode is pinned."}
                )
        return attrs


class SlLensnodeTaskSerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=True)
    title = serializers.CharField(allow_blank=True)


class LensGatewayInsightSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    role = serializers.CharField()
    status = serializers.CharField()
    ip_address = serializers.IPAddressField(allow_null=True, allow_blank=True, required=False)
    ai_enabled = serializers.BooleanField()
    sl_lensnode_uuid = serializers.UUIDField(allow_null=True)
    lensnode_status = serializers.CharField(allow_null=True)
    knowledge_source_count = serializers.IntegerField()
    workspace_root = serializers.CharField(allow_blank=True)
    sidecar_status = serializers.CharField(allow_blank=True)
    sl_name = serializers.CharField(allow_blank=True, required=False)
    sl_status = serializers.CharField(allow_blank=True, required=False)
    sl_workspace_path = serializers.CharField(allow_blank=True, required=False)
    sl_agent_version = serializers.CharField(allow_blank=True, required=False)
    sl_last_heartbeat_at = serializers.DateTimeField(allow_null=True, required=False)
    sl_registered_at = serializers.DateTimeField(allow_null=True, required=False)
    sl_tasks = SlLensnodeTaskSerializer(many=True, required=False)
    scope = serializers.CharField(required=False, allow_blank=True)
    origin = serializers.CharField(required=False, allow_blank=True)
    gateway_link_id = serializers.IntegerField(required=False, allow_null=True)
    managed_by_hfl = serializers.BooleanField(required=False)
    hfl_agent_online = serializers.BooleanField(required=False)
    hfl_sidecar_online = serializers.BooleanField(required=False)
    hfl_usable = serializers.BooleanField(required=False)
    copilot_eligible = serializers.BooleanField(required=False)
    sl_runtime_status = serializers.CharField(required=False, allow_blank=True)
    owner_user_id = serializers.IntegerField(required=False, allow_null=True)
    owner_username = serializers.CharField(required=False, allow_blank=True)
    created_by_id = serializers.IntegerField(required=False, allow_null=True)
    created_by_username = serializers.CharField(required=False, allow_blank=True)
    owner_organization_id = serializers.IntegerField(required=False, allow_null=True)
    is_platform_default = serializers.BooleanField(required=False)
    agent_release = serializers.DictField(required=False, allow_null=True)


class LensGatewayEnableAiSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True, max_length=160)


class LensGatewayChatWorkloadSerializer(serializers.Serializer):
    chat_prepare_concurrency = serializers.IntegerField(
        min_value=1,
        max_value=gateway_chat_queue.MAX_CHAT_PREPARE_CONCURRENCY,
    )
    chat_queue_capacity = serializers.IntegerField(
        min_value=0,
        max_value=gateway_chat_queue.MAX_CHAT_QUEUE_CAPACITY,
    )


class LensSessionLinkSerializer(serializers.ModelSerializer):
    knowledge_source_name = serializers.CharField(
        source="knowledge_source.name",
        read_only=True,
        allow_null=True,
    )
    assistant_name = serializers.SerializerMethodField()
    selected_task = serializers.SerializerMethodField()
    backup_source_name = serializers.SerializerMethodField()
    snapshot_created_at = serializers.SerializerMethodField()
    snapshot_size_bytes = serializers.SerializerMethodField()
    gateway_name = serializers.SerializerMethodField()
    gateway_scope = serializers.SerializerMethodField()
    has_unread = serializers.SerializerMethodField()
    document_conversion = serializers.SerializerMethodField()
    data_context = serializers.SerializerMethodField()
    lifecycle_error = serializers.SerializerMethodField()
    lifecycle_error_code = serializers.SerializerMethodField()
    lifecycle_error_message = serializers.SerializerMethodField()
    lifecycle_error_retryable = serializers.SerializerMethodField()
    lifecycle_error_meta = serializers.SerializerMethodField()
    queue_position = serializers.SerializerMethodField()
    queue_ahead = serializers.SerializerMethodField()

    class Meta:
        model = LensSessionLink
        fields = [
            "id",
            "title",
            "knowledge_source",
            "knowledge_source_name",
            "sl_session_uuid",
            "sl_assistant_uuid",
            "assistant_name",
            "selected_task",
            "agent_model_ref",
            "multimodal_model_ref",
            "analysis_type",
            "analysis_mode",
            "backup_config_id",
            "backup_source_name",
            "backup_source_snapshot_id",
            "snapshot_created_at",
            "snapshot_size_bytes",
            "source_scopes_json",
            "gateway_link",
            "gateway_selection_mode",
            "gateway_name",
            "gateway_scope",
            "status",
            "lifecycle_status",
            "provision_phase",
            "provision_detail",
            "queue_position",
            "queue_ahead",
            "cleanup_intent",
            "cleanup_status",
            "document_conversion",
            "data_context",
            "lifecycle_error",
            "lifecycle_error_code",
            "lifecycle_error_message",
            "lifecycle_error_retryable",
            "lifecycle_error_meta",
            "last_message_at",
            "last_assistant_message_at",
            "last_viewed_at",
            "has_unread",
            "active_run_uuid",
            "active_run_status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def _queue_position(self, obj: LensSessionLink) -> int:
        if obj.gateway_link_id is None:
            return 0
        gateway_cache = self.context.setdefault("gateway_session_queue_positions", {})
        if obj.gateway_link_id not in gateway_cache:
            gateway_cache[obj.gateway_link_id] = gateway_chat_queue.chat_queue_positions(
                gateway_link_id=obj.gateway_link_id,
            )
        return int(gateway_cache[obj.gateway_link_id].get(obj.id, 0))

    def get_queue_position(self, obj: LensSessionLink) -> int:
        return self._queue_position(obj)

    def get_queue_ahead(self, obj: LensSessionLink) -> int:
        cache = self.context.setdefault("session_queue_ahead", {})
        if obj.id not in cache:
            active_cache = self.context.setdefault("gateway_active_chat_slots", {})
            if obj.gateway_link_id not in active_cache:
                active_cache[obj.gateway_link_id] = (
                    gateway_chat_queue.active_chat_prepare_count(
                        gateway_link_id=obj.gateway_link_id,
                    )
                    if obj.gateway_link_id is not None
                    else 0
                )
            cache[obj.id] = gateway_chat_queue.chat_queue_ahead(
                session=obj,
                queue_position=self._queue_position(obj),
                active_count=active_cache[obj.gateway_link_id],
            )
        return int(cache[obj.id])

    def get_assistant_name(self, obj: LensSessionLink) -> str | None:
        cache = self.context.get("assistant_names") or {}
        uuid_str = str(obj.sl_assistant_uuid) if obj.sl_assistant_uuid else ""
        return cache.get(uuid_str)

    def _lifecycle_error(self, obj: LensSessionLink):
        return classify_chat_lifecycle_error(
            obj.lifecycle_error,
            obj.lifecycle_error_state_json,
        )

    def get_lifecycle_error(self, obj: LensSessionLink) -> str:
        """Keep the legacy field compatible without exposing raw diagnostics."""

        return self._lifecycle_error(obj).message if obj.lifecycle_error else ""

    def get_lifecycle_error_code(self, obj: LensSessionLink) -> str:
        return self._lifecycle_error(obj).code if obj.lifecycle_error else ""

    def get_lifecycle_error_message(self, obj: LensSessionLink) -> str:
        return self._lifecycle_error(obj).message if obj.lifecycle_error else ""

    def get_lifecycle_error_retryable(self, obj: LensSessionLink) -> bool:
        return self._lifecycle_error(obj).retryable if obj.lifecycle_error else False

    def get_lifecycle_error_meta(self, obj: LensSessionLink) -> dict:
        return self._lifecycle_error(obj).meta if obj.lifecycle_error else {}

    def get_selected_task(self, obj: LensSessionLink) -> str | None:
        cache = self.context.get("assistant_tasks") or {}
        uuid_str = str(obj.sl_assistant_uuid) if obj.sl_assistant_uuid else ""
        selected_task = cache.get(uuid_str)
        if selected_task:
            return selected_task
        if not uuid_str:
            return None
        # Direct PATCH responses may not carry the list serializer's remote
        # task cache. Fall back to the product-owned value for a stable view.
        return {
            LensSessionLink.AnalysisType.KNOWLEDGE_QA: "knowledge_qa",
            LensSessionLink.AnalysisType.CODE_ANALYSIS: "code_analysis",
        }.get(obj.analysis_type)

    def _backup_config(self, obj: LensSessionLink) -> BackupConfig | None:
        if not obj.backup_config_id:
            return None
        cache = self.context.setdefault("session_backup_configs", {})
        if obj.backup_config_id not in cache:
            cache[obj.backup_config_id] = BackupConfig.objects.filter(
                id=obj.backup_config_id,
                organization_id=obj.organization_id,
            ).first()
        return cache[obj.backup_config_id]

    def _snapshot(self, obj: LensSessionLink) -> BackupSourceSnapshot | None:
        if not obj.backup_source_snapshot_id:
            return None
        cache = self.context.setdefault("session_snapshots", {})
        if obj.backup_source_snapshot_id not in cache:
            cache[obj.backup_source_snapshot_id] = BackupSourceSnapshot.objects.filter(
                id=obj.backup_source_snapshot_id,
                organization_id=obj.organization_id,
            ).first()
        return cache[obj.backup_source_snapshot_id]

    def get_backup_source_name(self, obj: LensSessionLink) -> str | None:
        config = self._backup_config(obj)
        if config is None:
            return None
        cache = self.context.setdefault("session_source_names", {})
        source_key = (obj.organization_id, config.source_type, config.source_ref_id)
        if source_key not in cache:
            cache[source_key] = resolve_source_display_name(
                organization_id=obj.organization_id,
                source_type=config.source_type,
                source_ref_id=config.source_ref_id,
                fallback=config.name,
            )
        return cache[source_key]

    def get_snapshot_created_at(self, obj: LensSessionLink):
        snapshot = self._snapshot(obj)
        if snapshot is None:
            return None
        return snapshot.finished_at or snapshot.started_at or snapshot.created_at

    def get_snapshot_size_bytes(self, obj: LensSessionLink) -> int | None:
        snapshot = self._snapshot(obj)
        return snapshot.total_size_bytes if snapshot else None

    def get_gateway_name(self, obj: LensSessionLink) -> str | None:
        link = obj.gateway_link
        return link.gateway.name if link else None

    def get_gateway_scope(self, obj: LensSessionLink) -> str | None:
        link = obj.gateway_link
        if link is None:
            return None
        from apps.lens_bridge.services.gateway_ownership import (
            external_gateway_scope,
        )

        return external_gateway_scope(link)

    def get_has_unread(self, obj: LensSessionLink) -> bool:
        if obj.last_assistant_message_at is None:
            return False
        return obj.last_viewed_at is None or obj.last_assistant_message_at > obj.last_viewed_at

    def get_document_conversion(self, obj: LensSessionLink) -> dict | None:
        ks = obj.knowledge_source
        if ks is None:
            return None
        return conversion_display.document_conversion_view(
            conversion_display.conversion_state_from_knowledge_source(ks)
        )

    def get_data_context(self, obj: LensSessionLink) -> dict:
        return conversion_display.data_context_for_session(
            backup_config_id=obj.backup_config_id,
            backup_source_snapshot_id=obj.backup_source_snapshot_id,
            snapshot_created_at=self.get_snapshot_created_at(obj),
            gateway_scope=self.get_gateway_scope(obj),
            gateway_name=self.get_gateway_name(obj),
            gateway_selection_mode=obj.gateway_selection_mode,
        )


class LensSessionCreateSerializer(serializers.Serializer):
    """New Copilot chat configuration. Resources are provisioned asynchronously."""

    idempotency_key = serializers.CharField(max_length=128)
    title = serializers.CharField(required=False, allow_blank=True, max_length=160)
    backup_config_id = serializers.IntegerField(min_value=1)
    backup_source_snapshot_id = serializers.IntegerField(min_value=1)
    source_scopes = LensKnowledgeSourceScopeSerializer(many=True, min_length=1)
    gateway_mode = serializers.ChoiceField(
        choices=LensSessionLink.GatewaySelectionMode.choices,
        default=LensSessionLink.GatewaySelectionMode.AUTO,
    )
    gateway_link_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    analysis_type = serializers.ChoiceField(
        choices=LensSessionLink.AnalysisType.values,
        required=False,
    )
    analysis_mode = serializers.ChoiceField(
        choices=LensSessionLink.AnalysisMode.values,
        required=False,
    )
    agent_model_ref = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        mode = attrs["gateway_mode"]
        gateway_link_id = attrs.get("gateway_link_id")
        if mode == LensSessionLink.GatewaySelectionMode.MANUAL and not gateway_link_id:
            raise serializers.ValidationError(
                {"gateway_link_id": "Select a Private Data Gateway."}
            )
        if mode == LensSessionLink.GatewaySelectionMode.AUTO and gateway_link_id is not None:
            raise serializers.ValidationError(
                {
                    "gateway_link_id": (
                        "Do not select a specific Data Gateway when using the Public "
                        "Data Gateway option."
                    )
                }
            )
        return attrs


class LensSnapshotBrowseCreateSerializer(serializers.Serializer):
    """Insight-owned asynchronous snapshot browse request."""

    directory_id = serializers.IntegerField(min_value=1)
    backup_source_snapshot_id = serializers.IntegerField(min_value=1)
    gateway_link_id = serializers.IntegerField(min_value=1)
    path = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=500, default=500)


class LensScopePreviewCreateSerializer(serializers.Serializer):
    """Validate one selected snapshot path for asynchronous summarization."""

    directory_id = serializers.IntegerField(min_value=1)
    backup_source_snapshot_id = serializers.IntegerField(min_value=1)
    gateway_link_id = serializers.IntegerField(min_value=1)
    source_path = serializers.CharField(max_length=2000)
    request_token = serializers.UUIDField()
    attempt = serializers.IntegerField(required=False, min_value=0, max_value=3, default=0)


class LensAdmissionPreviewSerializer(serializers.Serializer):
    """Validate product-visible Chat selection totals."""

    gateway_mode = serializers.ChoiceField(
        choices=LensSessionLink.GatewaySelectionMode.choices,
    )
    gateway_link_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    file_count = serializers.IntegerField(min_value=0, max_value=2**63 - 1)
    size_bytes = serializers.IntegerField(min_value=0, max_value=2**63 - 1)

    def validate(self, attrs):
        mode = attrs["gateway_mode"]
        gateway_link_id = attrs.get("gateway_link_id")
        if mode == LensSessionLink.GatewaySelectionMode.MANUAL and not gateway_link_id:
            raise serializers.ValidationError(
                {"gateway_link_id": "Select a Private Data Gateway."}
            )
        if mode == LensSessionLink.GatewaySelectionMode.AUTO and gateway_link_id is not None:
            raise serializers.ValidationError(
                {
                    "gateway_link_id": (
                        "Do not select a specific Data Gateway when using the Public "
                        "Data Gateway option."
                    )
                }
            )
        return attrs


class LensSessionUpdateSerializer(serializers.Serializer):
    agent_model_ref = serializers.UUIDField(required=False, allow_null=True)
    analysis_mode = serializers.ChoiceField(
        choices=LensSessionLink.AnalysisMode.values,
        required=False,
    )
    analysis_type = serializers.ChoiceField(
        choices=LensSessionLink.AnalysisType.values,
        required=False,
    )


class LensSessionTitleSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=160, allow_blank=False, trim_whitespace=True)


class LensShareTitleSerializer(serializers.Serializer):
    title = serializers.CharField(
        max_length=200,
        allow_blank=True,
        required=False,
        default="",
    )


class LensRunCreateSerializer(serializers.Serializer):
    question = serializers.CharField(required=False, allow_blank=True)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=128)
    retry_of_run_uuid = serializers.UUIDField(required=False, allow_null=True)
    attachment_uuids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        max_length=4,
    )

    def validate(self, attrs):
        attachment_uuids = attrs.get("attachment_uuids") or []
        if len(set(attachment_uuids)) != len(attachment_uuids):
            raise serializers.ValidationError(
                {"attachment_uuids": "Attachment UUIDs must be unique."}
            )
        if not (attrs.get("question") or "").strip() and not attachment_uuids:
            raise serializers.ValidationError(
                "Provide a question or at least one attachment."
            )
        return attrs


class LensRunFeedbackSerializer(serializers.Serializer):
    """Validate feedback values supported by the SourceLens Run API."""

    feedback = serializers.ChoiceField(
        choices=("positive", "negative"),
        allow_blank=True,
    )


class LensOrgSettingsSerializer(serializers.Serializer):
    default_agent_model_ref = serializers.UUIDField(required=False, allow_null=True)
    default_multimodal_model_ref = serializers.UUIDField(
        required=False,
        allow_null=True,
    )


class LensChatBindingEnsureSerializer(serializers.Serializer):
    backup_config_id = serializers.IntegerField(min_value=1)
    backup_source_snapshot_id = serializers.IntegerField(min_value=1)
    backup_snapshot_directory_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    source_path = serializers.CharField(required=False, allow_blank=True, max_length=500)
    gateway_link_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)


class LensChatBindingSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    backup_config_id = serializers.IntegerField()
    backup_source_snapshot_id = serializers.IntegerField()
    backup_snapshot_directory_id = serializers.IntegerField(allow_null=True)
    source_path = serializers.CharField()
    gateway_link_id = serializers.IntegerField()
    gateway_name = serializers.CharField()
    gateway_scope = serializers.CharField()
    knowledge_source_id = serializers.IntegerField(allow_null=True)
    knowledge_source_status = serializers.CharField(allow_null=True)
    sl_assistant_uuid = serializers.CharField(allow_null=True)
    is_active = serializers.BooleanField()
    ready_for_chat = serializers.BooleanField(required=False)


class LensCopilotGatewayOptionSerializer(serializers.Serializer):
    gateway_link_id = serializers.IntegerField()
    gateway_id = serializers.IntegerField()
    name = serializers.CharField()
    scope = serializers.CharField()
    is_platform_default = serializers.BooleanField()
    sidecar_status = serializers.CharField()
    online = serializers.BooleanField()
    hfl_usable = serializers.BooleanField()
    copilot_eligible = serializers.BooleanField()
    analysis_types = serializers.ListField(
        child=serializers.ChoiceField(choices=LensSessionLink.AnalysisType.values),
        required=False,
    )
