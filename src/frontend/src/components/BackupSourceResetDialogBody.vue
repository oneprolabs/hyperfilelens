<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElTable, ElTableColumn, ElTag } from 'element-plus'
import AgentPlatformBrandIcon from './agent-deploy/AgentPlatformBrandIcon.vue'
import ExactKeywordConfirmInput from './ExactKeywordConfirmInput.vue'
import {
  BACKUP_SOURCE_RESET_CONFIRMATION,
  buildResetImpactItems,
  resetSourceCountLabel,
} from '../lib/backupSourceResetDialog'
import type { BackupSourceUnregisterDisplayRow } from '../lib/backupSourceUnregisterDialog'
import { nasMountProtocolIcon } from '../lib/resourceIcons'
import { backupSourceTypeIcon, backupSourceTypeIconClass, type BackupSourceType } from '../lib/sourceTypeIcons'
import type { EnrollmentOs } from '../lib/nodeApi'
import './backupSourceFlowActionDialog.css'

const props = withDefaults(defineProps<{
  sources: BackupSourceUnregisterDisplayRow[]
  loading?: boolean
}>(), {
  loading: false,
})

const confirmText = defineModel<string>('confirmText', { default: '' })

const emit = defineEmits<{
  (e: 'confirm'): void
}>()

const { t } = useI18n()

const sourceCountLabel = computed(() =>
  resetSourceCountLabel(props.sources.length, t),
)

const impactItems = computed(() => buildResetImpactItems(t))

function sourceTypeKind(row: BackupSourceUnregisterDisplayRow): BackupSourceType {
  if (row.sourceType) return row.sourceType
  return row.id.startsWith('nas:') ? 'nas' : 'host'
}

function sourceTypeText(row: BackupSourceUnregisterDisplayRow) {
  if (row.type) return row.type
  return sourceTypeKind(row) === 'host'
    ? t('protection.backupsPage.sourceTypeHost')
    : t('protection.backupsPage.sourceTypeNas')
}
</script>

<template>
  <div class="hfl-flow-action-dialog__body">
    <div
      class="hfl-flow-action-dialog__summary"
      role="region"
    >
      <ul class="hfl-flow-action-dialog__summary-list">
        <li class="hfl-flow-action-dialog__summary-list-lead">
          {{ t('protection.backupsPage.msgResetBackupConfigConfirm', { sources: sourceCountLabel }) }}
        </li>
        <li
          v-for="item in impactItems"
          :key="item"
        >
          {{ item }}
        </li>
      </ul>
    </div>

    <div class="hfl-flow-action-dialog__extras">
      <section class="hfl-flow-action-dialog__section">
        <h3 class="hfl-flow-action-dialog__section-title hfl-flow-action-dialog__section-title--plain">
          {{ t('protection.backupsPage.resetSourceListTitle') }}
        </h3>
        <ElTable
          v-table-overflow-title
          :data="sources"
          size="small"
          class="hfl-flow-action-dialog__table hfl-table"
          max-height="260"
        >
          <ElTableColumn
            :label="t('protection.backupsPage.colBackupSource')"
            min-width="210"
          >
            <template #default="{ row }">
              <div class="hfl-flow-action-dialog__source-cell">
                <span class="hfl-flow-action-dialog__source-name">{{ row.name }}</span>
                <span class="hfl-flow-action-dialog__source-meta-row">
                  <span
                    class="backup-source-type-tag"
                    :class="`backup-source-type-tag--${sourceTypeKind(row)}`"
                  >
                    <component
                      :is="backupSourceTypeIcon(sourceTypeKind(row))"
                      :size="12"
                      :class="backupSourceTypeIconClass(sourceTypeKind(row))"
                    />
                    <span>{{ sourceTypeText(row) }}</span>
                  </span>
                  <span
                    v-if="sourceTypeKind(row) === 'host' && row.platform"
                    class="source-os-cell__icon-wrap hfl-flow-action-dialog__source-trait"
                  >
                    <AgentPlatformBrandIcon :os="row.platform as EnrollmentOs" />
                  </span>
                  <span
                    v-else-if="sourceTypeKind(row) === 'nas' && row.protocol"
                    class="repo-protocol-pill repo-protocol-pill--icon-only hfl-flow-action-dialog__source-trait"
                    :class="`repo-protocol-pill--${row.protocol}`"
                  >
                    <component
                      :is="nasMountProtocolIcon(row.protocol)"
                      :size="12"
                      stroke-width="2.25"
                    />
                  </span>
                </span>
              </div>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('protection.backupsPage.colStatus')"
            width="120"
          >
            <template #default="{ row }">
              <ElTag
                v-if="row.statusLabel && row.statusLabel !== '—'"
                size="small"
                effect="light"
                :type="row.statusTag === 'neutral' ? undefined : (row.statusTag || 'info')"
                :class="{ 'hfl-tag--neutral': row.statusTag === 'neutral' }"
              >
                {{ row.statusLabel }}
              </ElTag>
              <span
                v-else
                class="hfl-empty-mark"
              >—</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('protection.backupsPage.flowSourceDetailColSnapshotCount')"
            width="112"
            align="right"
          >
            <template #default="{ row }">
              <span
                class="hfl-flow-action-dialog__count tabular-nums"
                :class="{ 'hfl-empty-mark': row.snapshotCount == null }"
              >
                {{ row.snapshotCount ?? '—' }}
              </span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('protection.backupsPage.colRegistered')"
            width="150"
          >
            <template #default="{ row }">
              <span
                class="hfl-table-cell-time"
                :class="{ 'hfl-empty-mark': !row.registeredAt || row.registeredAt === '—' }"
              >
                {{ row.registeredAt || '—' }}
              </span>
            </template>
          </ElTableColumn>
        </ElTable>
      </section>

      <ExactKeywordConfirmInput
        v-model="confirmText"
        class="hfl-flow-action-dialog__confirm"
        :keyword="BACKUP_SOURCE_RESET_CONFIRMATION"
        :hint="t('protection.backupsPage.resetConfirmTypeKeyword')"
        :placeholder="t('protection.backupsPage.resetConfirmPlaceholder')"
        :disabled="loading"
        @confirm="emit('confirm')"
      />
    </div>
  </div>
</template>
