<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { ArrowUpRight } from 'lucide-vue-next'
import { api } from '../../lib/api'
import { unwrapApiPayload } from '../../lib/parse'
import { formatLocalDateTime } from '../../lib/dateTime'

interface AuditLogEntry {
  id: number
  action: string
  user_display: string
  result: string
  created_at: string | null
  ip_address: string | null
  error_message: string | null
  correlation_id: string | null
  metadata: Record<string, unknown> | null
}

const props = defineProps<{
  nodeId: number
}>()

const { t } = useI18n()
const router = useRouter()

const logs = ref<AuditLogEntry[]>([])
const loading = ref(false)
const loadError = ref('')
const correlationId = ref<string | null>(null)

let cancelled = false
let requestSeq = 0

async function fetchLogs() {
  const seq = ++requestSeq
  loading.value = true
  loadError.value = ''
  try {
    const raw = await api<unknown>(`/api/v1/node/nodes/${props.nodeId}/audit-logs/`)
    if (cancelled || seq !== requestSeq) return
    const data = unwrapApiPayload<{ results: AuditLogEntry[] }>(raw)
    logs.value = (data.results || []).slice(0, 5)
    if (logs.value.length > 0) {
      correlationId.value = logs.value[0].correlation_id
    }
  } catch {
    if (!cancelled && seq === requestSeq) {
      loadError.value = t('nodeAuditLogSummary.loadFailed') || 'Failed to load audit logs'
    }
  } finally {
    if (!cancelled && seq === requestSeq) {
      loading.value = false
    }
  }
}

function resultTagType(result: string) {
  if (result === 'success') return 'success'
  if (result === 'failure') return 'danger'
  return 'info'
}

function actionLabel(action: string) {
  if (action === 'node.lifecycle.upgrade') return t('nodeAuditLogSummary.actionUpgrade') || 'Upgrade'
  if (action === 'node.lifecycle.upgrade.complete') return t('nodeAuditLogSummary.actionUpgradeComplete') || 'Upgrade Complete'
  if (action === 'node.lifecycle.upgrade.failed') return t('nodeAuditLogSummary.actionUpgradeFailed') || 'Upgrade Failed'
  return action
}

function goToFullAudit() {
  const query: Record<string, string> = {}
  if (correlationId.value) {
    query.correlation_id = correlationId.value
  }
  void router.push({ path: '/ops/audit-logs', query })
}

watch(() => props.nodeId, () => {
  if (props.nodeId) {
    void fetchLogs()
  }
})

onMounted(() => {
  if (props.nodeId) {
    void fetchLogs()
  }
})

onUnmounted(() => {
  cancelled = true
})
</script>

<template>
  <div class="node-audit-summary">
    <h4 class="node-audit-summary__title">
      {{ t('nodeAuditLogSummary.title') || 'Operation Records' }}
    </h4>

    <div
      v-if="loadError"
      class="node-audit-summary__error"
    >
      {{ loadError }}
    </div>

    <div
      v-if="loading && !logs.length"
      class="node-audit-summary__empty"
    >
      {{ t('nodeAuditLogSummary.loading') || 'Loading...' }}
    </div>

    <div
      v-else-if="!logs.length && !loadError"
      class="node-audit-summary__empty"
    >
      {{ t('nodeAuditLogSummary.empty') || 'No audit logs' }}
    </div>

    <ul
      v-else
      class="node-audit-summary__list"
    >
      <li
        v-for="log in logs"
        :key="log.id"
        class="node-audit-summary__item"
      >
        <span
          class="node-audit-summary__item-result"
          :class="`node-audit-summary__item-result--${resultTagType(log.result)}`"
        />
        <div class="node-audit-summary__item-body">
          <span class="node-audit-summary__item-action">{{ actionLabel(log.action) }}</span>
          <span class="node-audit-summary__item-meta">
            <span>{{ log.user_display || '—' }}</span>
            <span class="node-audit-summary__item-sep">|</span>
            <span>{{ formatLocalDateTime(log.created_at) }}</span>
          </span>
          <span
            v-if="log.error_message"
            class="node-audit-summary__item-error"
          >{{ log.error_message }}</span>
        </div>
      </li>
    </ul>

    <div class="node-audit-summary__footer">
      <button
        type="button"
        class="node-audit-summary__link"
        @click="goToFullAudit"
      >
        <span>{{ t('nodeAuditLogSummary.viewFullLogs') || 'View full audit logs' }}</span>
        <ArrowUpRight
          :size="14"
          aria-hidden="true"
        />
      </button>
    </div>
  </div>
</template>

<style scoped>
.node-audit-summary {
  padding: 0;
}

.node-audit-summary__title {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-title, #303133);
}

.node-audit-summary__empty {
  color: var(--color-text-tertiary, #909399);
  font-size: 13px;
  padding: 8px 0;
}

.node-audit-summary__error {
  color: var(--color-error, #f56c6c);
  font-size: 12px;
  padding: 8px 0;
}

.node-audit-summary__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
}

.node-audit-summary__item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--color-border-extra-light, #f0f0f0);
}

.node-audit-summary__item:last-child {
  border-bottom: none;
}

.node-audit-summary__item-result {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-top: 5px;
  background: var(--color-grey-5, #bfbfbf);
}

.node-audit-summary__item-result--success {
  background: var(--color-success, #67c23a);
}

.node-audit-summary__item-result--danger {
  background: var(--color-error, #f56c6c);
}

.node-audit-summary__item-result--info {
  background: var(--color-primary, #457ab0);
}

.node-audit-summary__item-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.node-audit-summary__item-action {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-title, #303133);
}

.node-audit-summary__item-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--color-text-tertiary, #909399);
}

.node-audit-summary__item-sep {
  color: var(--color-border, #d9d9d9);
}

.node-audit-summary__item-error {
  font-size: 12px;
  color: var(--color-error, #f56c6c);
}

.node-audit-summary__footer {
  margin-top: 12px;
  padding-top: 8px;
  border-top: 1px solid var(--color-border-extra-light, #f0f0f0);
}

.node-audit-summary__link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--color-primary, #457ab0);
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  transition: opacity 0.15s ease;
}

.node-audit-summary__link:hover {
  opacity: 0.8;
}
</style>