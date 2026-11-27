<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Plus, RefreshCw } from 'lucide-vue-next'
import { ElButton, ElDialog, ElForm, ElFormItem, ElOption, ElSelect, ElTag } from 'element-plus'
import { proxyNodeSelectLine } from '../lib/nodeInventoryDisplay'
import type { ApiNode } from '../types/node'
import './backupSourceFlowActionDialog.css'

const props = defineProps<{
  modelValue: boolean
  selectedCount: number
  offlineProxyCount: number
  proxyNodesRefreshing: boolean
  onlineProxyNodes: ApiNode[]
  nodeId: number | undefined
  proxySearch?: (search: string) => void
  proxyLoadMore?: (direction: string) => void
  saving?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'update:nodeId', v: number | undefined): void
  (e: 'refresh'): void
  (e: 'deploy'): void
  (e: 'save'): void
  (e: 'cancel'): void
}>()

const { t } = useI18n()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const selectedNodeId = computed({
  get: () => props.nodeId,
  set: (v: number | undefined) => emit('update:nodeId', normalizeNodeId(v)),
})

const saveDisabled = computed(
  () => props.onlineProxyNodes.length === 0 || Boolean(props.saving),
)

const showEmptyWarning = computed(
  () => !props.proxyNodesRefreshing && props.onlineProxyNodes.length === 0,
)

function normalizeNodeId(value: unknown): number | undefined {
  const id = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(id) && id > 0 ? id : undefined
}

function proxyNodeStatusLabel(node: ApiNode) {
  return node.availability === 'online'
    ? t('protection.sourceResources.nodeStatusOnline')
    : t('protection.sourceResources.nodeStatusOffline')
}

function proxyNodeStatusTagType(node: ApiNode): 'success' | 'danger' | 'warning' | 'info' {
  if (node.availability === 'online') return 'success'
  if (node.status === 'offline') return 'danger'
  return 'info'
}

function close() {
  visible.value = false
  emit('cancel')
}

function save() {
  emit('save')
}
</script>

<template>
  <ElDialog
    v-model="visible"
    :title="t('protection.sourceResources.changeProxyNode')"
    class="hfl-flow-action-dialog hfl-flow-action-dialog--form"
    align-center
    destroy-on-close
    @close="close"
  >
    <p class="hfl-flow-action-dialog__form-hint">
      {{ t('protection.sourceResources.rebindProxyHint', { n: selectedCount }) }}
    </p>
    <p
      v-if="offlineProxyCount > 0"
      class="hfl-flow-action-dialog__form-hint hfl-flow-action-dialog__form-hint--sub"
    >
      {{ t('protection.sourceResources.rebindProxyOnlineOnlyNote', { n: offlineProxyCount }) }}
    </p>

    <ElForm
      label-position="top"
      require-asterisk-position="right"
      class="hfl-flow-action-dialog__form"
      @submit.prevent="save"
    >
      <ElFormItem
        :label="t('protection.sourceResources.selectSourceProxy')"
        required
      >
        <div class="hfl-flow-action-dialog__proxy-select-row">
          <ElSelect
            v-model="selectedNodeId"
            class="hfl-flow-action-dialog__proxy-select-row__select"
            filterable
            :remote="Boolean(proxySearch)"
            :remote-method="proxySearch"
            @end-reached="proxyLoadMore?.($event)"
            fit-input-width
            :loading="proxyNodesRefreshing"
            :disabled="!proxySearch && onlineProxyNodes.length === 0"
            popper-class="hfl-flow-action-dialog__proxy-select-popper"
            :placeholder="t('protection.sourceResources.selectSourceProxy')"
          >
            <ElOption
              v-for="node in onlineProxyNodes"
              :key="node.id"
              :label="proxyNodeSelectLine(node)"
              :value="node.id"
            >
              <div class="hfl-flow-action-dialog__proxy-option">
                <span class="hfl-flow-action-dialog__proxy-option__name">
                  {{ proxyNodeSelectLine(node) }}
                </span>
                <ElTag
                  size="small"
                  :type="proxyNodeStatusTagType(node)"
                  effect="plain"
                >
                  {{ proxyNodeStatusLabel(node) }}
                </ElTag>
              </div>
            </ElOption>
          </ElSelect>
          <ElButton
            class="hfl-refresh-button hfl-flow-action-dialog__proxy-select-row__refresh"
            :title="t('protection.sourceResources.proxyRefresh')"
            :aria-label="t('protection.sourceResources.proxyRefresh')"
            :disabled="proxyNodesRefreshing"
            @click="emit('refresh')"
          >
            <RefreshCw
              :size="16"
              :class="{ 'is-spinning': proxyNodesRefreshing }"
            />
          </ElButton>
          <ElButton
            class="fullscreen-form-icon-btn hfl-flow-action-dialog__proxy-select-row__deploy"
            :title="t('protection.sourceResources.nasDeployProxy')"
            :aria-label="t('protection.sourceResources.nasDeployProxy')"
            @click="emit('deploy')"
          >
            <Plus :size="14" />
          </ElButton>
        </div>
      </ElFormItem>
    </ElForm>

    <p
      v-if="showEmptyWarning"
      class="hfl-flow-action-dialog__proxy-empty-warn"
    >
      {{ t('protection.sourceResources.rebindProxyNoOnlineBefore') }}
      <button
        type="button"
        class="hfl-flow-action-dialog__proxy-empty-warn__link"
        @click="emit('deploy')"
      >
        {{ t('protection.sourceResources.deployProxy') }}
      </button>
      {{ t('protection.sourceResources.rebindProxyNoOnlineAfter') }}
    </p>

    <template #footer>
      <ElButton @click="close">
        {{ t('common.cancel') }}
      </ElButton>
      <ElButton
        type="primary"
        :disabled="saveDisabled"
        @click="save"
      >
        {{ t('common.save') }}
      </ElButton>
    </template>
  </ElDialog>
</template>
