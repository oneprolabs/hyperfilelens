<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Check, Copy, Pencil, X } from 'lucide-vue-next'
import { nasMountProtocolIcon } from '../../../lib/resourceIcons'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../../lib/api'
import { copyTextToClipboard } from '../../../lib/clipboard'
import {
  nasMountProtocol,
  nasMountSourceUri,
  nasProxyMountPoint,
  sourceNasStatusPresentation,
  sourceExternalId,
  sourcePasswordDisplay,
} from '../../../lib/sourceNasDisplay'
import { formatAppTime } from '../../../lib/dateTime'
import {
  DETAIL_EMPTY,
  formatNodeBytes,
  formatNodeDate,
  isDetailEmpty,
  proxyNodeStackIpLine,
  proxyNodeStackPrimaryLine,
  proxyNodeSelectLine,
} from '../../../lib/nodeInventoryDisplay'
import { updateSourceResource, type SourceResource } from '../../../lib/sourceApi'
import { useResponsiveDrawerWidth } from '../../../composables/useResponsiveDrawerWidth'
import { debouncedNodeStatus } from '../../../composables/useNodeConnectionDisplay'
import type { ApiNode } from '../../../types/node'
import HflCapacityCell from '../../../components/HflCapacityCell.vue'
import { statusTagAttrs } from '../../../lib/statusTag'

type NasProtocol = 'smb' | 'nfs'

type NasEditableField =
  | 'name'
  | 'server'
  | 'share'
  | 'export_path'
  | 'options'
  | 'username'
  | 'password'
  | 'domain'
  | 'bound_node_id'
  | 'path'

type DetailDraft = {
  name: string
  protocol: NasProtocol
  server: string
  share: string
  export_path: string
  options: string
  username: string
  password: string
  domain: string
  bound_node_id: number | ''
  path: string
}

const props = defineProps<{
  modelValue: boolean
  resource: SourceResource | null
  proxyNodes: ApiNode[]
}>()

const emit = defineEmits<{
  'update:modelValue': [boolean]
  updated: [SourceResource]
}>()

const { t } = useI18n()
const { drawerSize, updateDrawerWidth, bindDrawerResize, unbindDrawerResize } = useResponsiveDrawerWidth()

const detailDraft = ref<DetailDraft | null>(null)
const editingField = ref<NasEditableField | null>(null)
const saving = ref(false)

const open = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const row = computed(() => props.resource)

const protocol = computed((): NasProtocol => {
  if (!row.value) return 'nfs'
  const detected = nasMountProtocol(row.value)
  return detected === 'smb' ? 'smb' : 'nfs'
})

function cfg(key: string): string {
  const value = row.value?.config?.[key]
  return typeof value === 'string' ? value.trim() : ''
}

function cred(key: string): string {
  const value = row.value?.credentials?.[key]
  return typeof value === 'string' ? value : ''
}

function protocolLabel(value: NasProtocol) {
  return value === 'smb' ? t('repositoriesPage.protocolSmb') : t('repositoriesPage.protocolNfs')
}

function protocolPillClass(value: NasProtocol) {
  return value === 'smb'
    ? 'repo-protocol-pill repo-protocol-pill--smb'
    : 'repo-protocol-pill repo-protocol-pill--nfs'
}

function boundProxyNode(): ApiNode | null {
  if (!row.value?.bound_node) return null
  return props.proxyNodes.find((node) => node.id === row.value?.bound_node) ?? null
}

const sourceIdText = computed(() => (row.value ? sourceExternalId(row.value) : DETAIL_EMPTY))
const sourceTypeLabel = computed(() => t('protection.sourceResources.addSourceTypeNas'))
const connectionUri = computed(() => (row.value ? nasMountSourceUri(row.value) : DETAIL_EMPTY))

const proxyNode = computed(() => boundProxyNode())
const onlineProxyNodes = computed(() => props.proxyNodes.filter((node) => node.availability === 'online'))

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

const proxyPrimaryText = computed(() => {
  const name = row.value?.bound_node_name?.trim() || proxyNode.value?.name
  const line = proxyNodeStackPrimaryLine(name, proxyNode.value, row.value?.bound_node)
  return line || DETAIL_EMPTY
})
const proxyIpText = computed(() => {
  const ip = proxyNodeStackIpLine(proxyNode.value)
  return ip || DETAIL_EMPTY
})
const proxyVersionText = computed(() => {
  const fromConfig = cfg('agent_version')
  if (fromConfig) return fromConfig
  return proxyNode.value?.version?.trim() || DETAIL_EMPTY
})
const mountDirText = computed(() => {
  if (!row.value) return DETAIL_EMPTY
  const text = nasProxyMountPoint(row.value)
  return text === '—' ? DETAIL_EMPTY : text
})

const capacityParts = computed(() => {
  if (!row.value?.total_size) return { used: 0, total: 0, pct: 0 }
  const used = Number(row.value.used_size || 0)
  const total = Number(row.value.total_size || 0)
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0
  return { used, total, pct }
})

const proxyConnectionStatus = computed((): 'online' | 'reconnecting' | 'offline' => {
  const explicit = (row.value?.bound_node_availability || '').trim().toLowerCase()
  if (explicit === 'online' || explicit === 'reconnecting' || explicit === 'offline') return explicit
  const status = proxyNode.value ? debouncedNodeStatus(proxyNode.value) : undefined
  if (status === 'online' || status === 'reconnecting') return status
  return 'offline'
})

const runtimeStatus = computed(() =>
  sourceNasStatusPresentation(row.value || {}, proxyConnectionStatus.value),
)
const connectionStatusLabel = computed(() => t(runtimeStatus.value.labelKey))
const connectionStatusTagAttrs = computed(() => statusTagAttrs(runtimeStatus.value.tone))

const availabilityLabel = computed(() =>
  row.value?.availability === 'online'
    ? t('protection.sourceResources.nodeStatusOnline')
    : t('protection.sourceResources.nodeStatusOffline'),
)
const availabilityTagType = computed(() =>
  row.value?.availability === 'online' ? 'success' : 'danger',
)

const lastHeartbeatText = computed(() => formatLastHeartbeat(proxyNode.value?.last_seen_at))

function formatRelativeAgo(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const diffMs = Math.max(0, Date.now() - d.getTime())
  const minutes = Math.floor(diffMs / 60000)
  if (minutes < 1) return t('nav.notificationPopover.relative.minutesAgo', { n: 1 })
  if (minutes < 60) return t('nav.notificationPopover.relative.minutesAgo', { n: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t('nav.notificationPopover.relative.hoursAgo', { n: hours })
  const days = Math.floor(hours / 24)
  return t('nav.notificationPopover.relative.daysAgo', { n: days })
}

function formatLastHeartbeat(iso?: string | null): string {
  if (!iso) return DETAIL_EMPTY
  const timeStr = formatAppTime(iso, iso)
  const relative = formatRelativeAgo(iso)
  return relative ? `${timeStr} (${relative})` : timeStr
}

async function copyText(value: string) {
  if (!value || isDetailEmpty(value)) return
  try {
    await copyTextToClipboard(value)
    ElMessage.success({ message: t('common.copied'), grouping: true })
  } catch {
    ElMessage.error({ message: t('protection.sourceResources.copyFailed'), grouping: true })
  }
}

function detailValueClass(text: string, monoWhenPresent = false) {
  return {
    'hfl-detail-row__empty': isDetailEmpty(text),
    'hfl-detail-row__value--mono': monoWhenPresent && !isDetailEmpty(text),
  }
}

function displayOrEmpty(value: string) {
  return value.trim() ? value : DETAIL_EMPTY
}

function buildDetailDraft(resource: SourceResource): DetailDraft {
  const detected = nasMountProtocol(resource)
  const proto: NasProtocol = detected === 'smb' ? 'smb' : 'nfs'
  const config = resource.config || {}
  const credentials = resource.credentials || {}
  return {
    name: resource.name,
    protocol: proto,
    server: typeof config.server === 'string' ? config.server.trim() : '',
    share: typeof config.share === 'string' ? config.share.trim() : '',
    export_path: typeof config.export_path === 'string' ? config.export_path.trim() : '',
    options: typeof config.options === 'string' ? config.options.trim() : '',
    username: typeof credentials.username === 'string' ? credentials.username : '',
    password: '',
    domain: typeof credentials.domain === 'string' ? credentials.domain : '',
    bound_node_id: resource.bound_node ?? '',
    path:
      nasProxyMountPoint(resource) === '—'
        ? ''
        : nasProxyMountPoint(resource),
  }
}

function initDetailDraft(resource: SourceResource) {
  detailDraft.value = buildDetailDraft(resource)
  editingField.value = null
}

function resetDetailDraft() {
  detailDraft.value = null
  editingField.value = null
}

function isFieldEditing(field: NasEditableField) {
  return editingField.value === field
}

function beginFieldEdit(field: NasEditableField) {
  if (!detailDraft.value || !row.value || saving.value) return
  detailDraft.value = buildDetailDraft(row.value)
  if (field === 'bound_node_id') {
    const draftId = detailDraft.value.bound_node_id === '' ? null : Number(detailDraft.value.bound_node_id)
    if (draftId != null && !onlineProxyNodes.value.some((node) => node.id === draftId)) {
      detailDraft.value.bound_node_id = ''
    }
  }
  editingField.value = field
}

function cancelFieldEdit() {
  if (row.value) detailDraft.value = buildDetailDraft(row.value)
  editingField.value = null
}

const hasDetailChanges = computed(() => {
  const current = row.value
  const draft = detailDraft.value
  const field = editingField.value
  if (!current || !draft || !field) return false
  const original = buildDetailDraft(current)
  if (field === 'password') return Boolean(draft.password.trim())
  if (field === 'bound_node_id') {
    const draftId = draft.bound_node_id === '' ? null : Number(draft.bound_node_id)
    const origId = original.bound_node_id === '' ? null : Number(original.bound_node_id)
    return draftId !== origId
  }
  return draft[field].trim() !== String(original[field]).trim()
})

function passwordDisplay() {
  return sourcePasswordDisplay(row.value?.credentials, DETAIL_EMPTY)
}

async function saveDetailChanges() {
  const current = row.value
  const draft = detailDraft.value
  const field = editingField.value
  if (!current || !draft || !field) return
  if (!hasDetailChanges.value) {
    cancelFieldEdit()
    return
  }

  const original = buildDetailDraft(current)
  const patch: Record<string, unknown> = {}
  const configPatch: Record<string, string> = {}
  const credentialsPatch: Record<string, string> = {}
  const draftProtocol = protocol.value

  switch (field) {
    case 'name': {
      const name = draft.name.trim()
      if (name !== original.name.trim()) {
        if (!name) {
          ElMessage.warning({ message: t('protection.sourceResources.formName'), grouping: true })
          return
        }
        patch.name = name
      }
      break
    }
    case 'server': {
      const server = draft.server.trim()
      if (server !== original.server.trim()) {
        if (!server) {
          ElMessage.warning(
            draftProtocol === 'smb' ? t('addNasRepo.errSmbHost') : t('repositoriesPage.errNfsHost'),
          )
          return
        }
        configPatch.server = server
      }
      break
    }
    case 'share': {
      const share = draft.share.trim()
      if (share !== original.share.trim()) {
        if (!share) {
          ElMessage.warning({ message: t('addNasRepo.errSmbShare'), grouping: true })
          return
        }
        configPatch.share = share
      }
      break
    }
    case 'export_path': {
      const exportPath = draft.export_path.trim()
      if (exportPath !== original.export_path.trim()) {
        if (!exportPath) {
          ElMessage.warning({ message: t('repositoriesPage.errNfsExport'), grouping: true })
          return
        }
        configPatch.export_path = exportPath
      }
      break
    }
    case 'options': {
      const options = draft.options.trim()
      if (options !== original.options.trim()) {
        configPatch.options = options
      }
      break
    }
    case 'username': {
      const username = draft.username.trim()
      if (username !== original.username.trim()) {
        if (!username) {
          ElMessage.warning({ message: t('repositoriesPage.errSmbUsername'), grouping: true })
          return
        }
        credentialsPatch.username = username
      }
      break
    }
    case 'password': {
      const password = draft.password.trim()
      if (password) {
        credentialsPatch.password = password
      }
      break
    }
    case 'domain': {
      const domain = draft.domain.trim()
      if (domain !== original.domain.trim()) {
        credentialsPatch.domain = domain
      }
      break
    }
    case 'bound_node_id': {
      const nodeId = draft.bound_node_id === '' ? null : Number(draft.bound_node_id)
      const origId = original.bound_node_id === '' ? null : Number(original.bound_node_id)
      if (nodeId !== origId) {
        patch.bound_node_id = Number.isFinite(nodeId as number) && (nodeId as number) > 0 ? nodeId : null
      }
      break
    }
    case 'path': {
      const path = draft.path.trim()
      if (path !== original.path.trim()) {
        if (!path) {
          ElMessage.warning({ message: t('repositoriesPage.errRepoDir'), grouping: true })
          return
        }
        configPatch.path = path
      }
      break
    }
    default:
      break
  }

  if (Object.keys(configPatch).length) patch.config = configPatch
  if (Object.keys(credentialsPatch).length) patch.credentials = credentialsPatch

  if (!patch.name && !patch.config && !patch.credentials && !('bound_node_id' in patch)) {
    ElMessage.warning({ message: t('repositoriesPage.noChangesToSave'), grouping: true })
    return
  }

  saving.value = true
  try {
    const updated = await updateSourceResource(current.id, patch)
    emit('updated', updated)
    initDetailDraft(updated)
    ElMessage.success({ message: t('protection.sourceResources.updated'), grouping: true })
  } catch (e) {
    ElMessage.error({ message: apiErrorMessage(e), grouping: true })
  } finally {
    saving.value = false
  }
}

function onDrawerOpened() {
  bindDrawerResize()
  if (row.value) initDetailDraft(row.value)
}

function onDrawerClosed() {
  resetDetailDraft()
  unbindDrawerResize()
}

watch(open, (isOpen) => {
  if (isOpen) {
    void nextTick(() => requestAnimationFrame(() => updateDrawerWidth()))
  }
})

onUnmounted(() => {
  unbindDrawerResize()
})
</script>

<template>
  <ElDrawer
    v-model="open"
    direction="rtl"
    destroy-on-close
    :modal="true"
    :size="drawerSize"
    class="hfl-detail-drawer"
    @opened="onDrawerOpened"
    @closed="onDrawerClosed"
  >
    <template #header>
      <span class="hfl-detail-drawer__title">{{ row?.name || DETAIL_EMPTY }}</span>
    </template>

    <div v-if="row" v-loading="saving" class="hfl-detail-drawer__body">
      <ElTabs model-value="basic" class="hfl-detail-tabs">
        <ElTabPane :label="t('protection.sourceResources.detailTabBasic')" name="basic">
          <div class="hfl-detail-sections">
            <section class="hfl-detail-section">
              <h4 class="hfl-detail-section__title">{{ t('protection.sourceResources.detailSectionIdentity') }}</h4>
              <div class="hfl-detail-grid">
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.fieldSourceName') }}</span>
                  <span class="hfl-detail-row__value hfl-detail-row__value--editable">
                    <template v-if="detailDraft && isFieldEditing('name')">
                      <ElInput v-model="detailDraft.name" size="small" class="hfl-detail-inline-edit__input" :disabled="saving" @keyup.enter="saveDetailChanges" />
                      <span class="hfl-detail-inline-edit__actions">
                        <ElButton text circle size="small" :title="t('common.save')" :disabled="saving" @click="saveDetailChanges"><Check :size="14" /></ElButton>
                        <ElButton text circle size="small" :title="t('common.cancel')" :disabled="saving" @click="cancelFieldEdit"><X :size="14" /></ElButton>
                      </span>
                    </template>
                    <template v-else>
                      <span class="hfl-detail-row__text">{{ row.name }}</span>
                      <ElButton text circle size="small" class="hfl-detail-row__edit" @click="beginFieldEdit('name')"><Pencil :size="13" /></ElButton>
                    </template>
                  </span>
                </div>
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.fieldSourceType') }}</span>
                  <span class="hfl-detail-row__value">{{ sourceTypeLabel }}</span>
                </div>
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.fieldSourceId') }}</span>
                  <span class="hfl-detail-row__value hfl-detail-row__value--editable hfl-detail-row__value--mono">
                    <span class="hfl-detail-row__text">{{ sourceIdText }}</span>
                    <ElButton text circle size="small" class="hfl-detail-row__edit" :title="t('common.copy')" @click="copyText(sourceIdText)">
                      <Copy :size="13" />
                    </ElButton>
                  </span>
                </div>
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colProtocol') }}</span>
                  <span class="hfl-detail-row__value">
                    <span :class="protocolPillClass(protocol)">
                      <component :is="nasMountProtocolIcon(protocol)" :size="12" stroke-width="2.25" />
                      {{ protocolLabel(protocol) }}
                    </span>
                  </span>
                </div>
              </div>
            </section>

            <section class="hfl-detail-section">
              <h4 class="hfl-detail-section__title">{{ t('protection.sourceResources.detailSectionAccess') }}</h4>
              <div class="hfl-detail-grid">
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colNasServer') }}</span>
                  <span class="hfl-detail-row__value hfl-detail-row__value--editable" :class="detailValueClass(displayOrEmpty(cfg('server')), true)">
                    <template v-if="detailDraft && isFieldEditing('server')">
                      <ElInput v-model="detailDraft.server" size="small" class="hfl-detail-inline-edit__input" :disabled="saving" @keyup.enter="saveDetailChanges" />
                      <span class="hfl-detail-inline-edit__actions">
                        <ElButton text circle size="small" :title="t('common.save')" :disabled="saving" @click="saveDetailChanges"><Check :size="14" /></ElButton>
                        <ElButton text circle size="small" :title="t('common.cancel')" :disabled="saving" @click="cancelFieldEdit"><X :size="14" /></ElButton>
                      </span>
                    </template>
                    <template v-else>
                      <span class="hfl-detail-row__text">{{ displayOrEmpty(cfg('server')) }}</span>
                      <ElButton text circle size="small" class="hfl-detail-row__edit" @click="beginFieldEdit('server')"><Pencil :size="13" /></ElButton>
                    </template>
                  </span>
                </div>
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">
                    {{ protocol === 'smb' ? t('addNasRepo.fieldSmbShare') : t('addNasRepo.fieldNfsExport') }}
                  </span>
                  <span
                    class="hfl-detail-row__value hfl-detail-row__value--editable"
                    :class="detailValueClass(displayOrEmpty(protocol === 'smb' ? cfg('share') : cfg('export_path')), true)"
                  >
                    <template v-if="detailDraft && isFieldEditing(protocol === 'smb' ? 'share' : 'export_path')">
                      <ElInput
                        v-if="protocol === 'smb'"
                        v-model="detailDraft.share"
                        size="small"
                        class="hfl-detail-inline-edit__input"
                        :disabled="saving"
                        @keyup.enter="saveDetailChanges"
                      />
                      <ElInput
                        v-else
                        v-model="detailDraft.export_path"
                        size="small"
                        class="hfl-detail-inline-edit__input"
                        :disabled="saving"
                        @keyup.enter="saveDetailChanges"
                      />
                      <span class="hfl-detail-inline-edit__actions">
                        <ElButton text circle size="small" :title="t('common.save')" :disabled="saving" @click="saveDetailChanges"><Check :size="14" /></ElButton>
                        <ElButton text circle size="small" :title="t('common.cancel')" :disabled="saving" @click="cancelFieldEdit"><X :size="14" /></ElButton>
                      </span>
                    </template>
                    <template v-else>
                      <span class="hfl-detail-row__text">
                        {{ displayOrEmpty(protocol === 'smb' ? cfg('share') : cfg('export_path')) }}
                      </span>
                      <ElButton
                        text
                        circle
                        size="small"
                        class="hfl-detail-row__edit"
                        @click="beginFieldEdit(protocol === 'smb' ? 'share' : 'export_path')"
                      >
                        <Pencil :size="13" />
                      </ElButton>
                    </template>
                  </span>
                </div>
                <template v-if="protocol === 'smb'">
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('protection.sourceResources.nasFieldSmbUsername') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--editable" :class="detailValueClass(displayOrEmpty(cred('username')))">
                      <template v-if="detailDraft && isFieldEditing('username')">
                        <ElInput v-model="detailDraft.username" size="small" class="hfl-detail-inline-edit__input" :disabled="saving" @keyup.enter="saveDetailChanges" />
                        <span class="hfl-detail-inline-edit__actions">
                          <ElButton text circle size="small" :title="t('common.save')" :disabled="saving" @click="saveDetailChanges"><Check :size="14" /></ElButton>
                          <ElButton text circle size="small" :title="t('common.cancel')" :disabled="saving" @click="cancelFieldEdit"><X :size="14" /></ElButton>
                        </span>
                      </template>
                      <template v-else>
                        <span class="hfl-detail-row__text">{{ displayOrEmpty(cred('username')) }}</span>
                        <ElButton text circle size="small" class="hfl-detail-row__edit" @click="beginFieldEdit('username')"><Pencil :size="13" /></ElButton>
                      </template>
                    </span>
                  </div>
                  <div class="hfl-detail-row">
                    <span class="hfl-detail-row__label">{{ t('protection.sourceResources.nasFieldSmbPassword') }}</span>
                    <span class="hfl-detail-row__value hfl-detail-row__value--editable" :class="detailValueClass(passwordDisplay())">
                      <template v-if="detailDraft && isFieldEditing('password')">
                        <ElInput
                          v-model="detailDraft.password"
                          size="small"
                          type="password"
                          show-password
                          class="hfl-detail-inline-edit__input"
                          :disabled="saving"
                          :placeholder="t('protection.sourceResources.nasPasswordKeep')"
                          @keyup.enter="saveDetailChanges"
                        />
                        <span class="hfl-detail-inline-edit__actions">
                          <ElButton text circle size="small" :title="t('common.save')" :disabled="saving" @click="saveDetailChanges"><Check :size="14" /></ElButton>
                          <ElButton text circle size="small" :title="t('common.cancel')" :disabled="saving" @click="cancelFieldEdit"><X :size="14" /></ElButton>
                        </span>
                      </template>
                      <template v-else>
                        <span class="hfl-detail-row__text">{{ passwordDisplay() }}</span>
                        <ElButton text circle size="small" class="hfl-detail-row__edit" @click="beginFieldEdit('password')"><Pencil :size="13" /></ElButton>
                      </template>
                    </span>
                  </div>
                </template>
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('addNasRepo.fieldMountOptions') }}</span>
                  <span class="hfl-detail-row__value hfl-detail-row__value--editable" :class="detailValueClass(displayOrEmpty(cfg('options')), true)">
                    <template v-if="detailDraft && isFieldEditing('options')">
                      <ElInput v-model="detailDraft.options" size="small" class="hfl-detail-inline-edit__input" :disabled="saving" @keyup.enter="saveDetailChanges" />
                      <span class="hfl-detail-inline-edit__actions">
                        <ElButton text circle size="small" :title="t('common.save')" :disabled="saving" @click="saveDetailChanges"><Check :size="14" /></ElButton>
                        <ElButton text circle size="small" :title="t('common.cancel')" :disabled="saving" @click="cancelFieldEdit"><X :size="14" /></ElButton>
                      </span>
                    </template>
                    <template v-else>
                      <span class="hfl-detail-row__text">{{ displayOrEmpty(cfg('options')) }}</span>
                      <ElButton text circle size="small" class="hfl-detail-row__edit" @click="beginFieldEdit('options')"><Pencil :size="13" /></ElButton>
                    </template>
                  </span>
                </div>
                <div v-if="protocol === 'nfs'" class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colConnection') }}</span>
                  <span class="hfl-detail-row__value hfl-detail-row__value--editable hfl-detail-row__value--mono">
                    <span class="hfl-detail-row__text">{{ connectionUri }}</span>
                    <ElButton
                      v-if="!isDetailEmpty(connectionUri)"
                      text
                      circle
                      size="small"
                      class="hfl-detail-row__edit"
                      :title="t('common.copy')"
                      @click="copyText(connectionUri)"
                    >
                      <Copy :size="13" />
                    </ElButton>
                  </span>
                </div>
                <div v-if="protocol === 'smb'" class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.nasFieldSmbDomain') }}</span>
                  <span class="hfl-detail-row__value hfl-detail-row__value--editable" :class="detailValueClass(displayOrEmpty(cred('domain')))">
                    <template v-if="detailDraft && isFieldEditing('domain')">
                      <ElInput v-model="detailDraft.domain" size="small" class="hfl-detail-inline-edit__input" :disabled="saving" @keyup.enter="saveDetailChanges" />
                      <span class="hfl-detail-inline-edit__actions">
                        <ElButton text circle size="small" :title="t('common.save')" :disabled="saving" @click="saveDetailChanges"><Check :size="14" /></ElButton>
                        <ElButton text circle size="small" :title="t('common.cancel')" :disabled="saving" @click="cancelFieldEdit"><X :size="14" /></ElButton>
                      </span>
                    </template>
                    <template v-else>
                      <span class="hfl-detail-row__text">{{ displayOrEmpty(cred('domain')) }}</span>
                      <ElButton text circle size="small" class="hfl-detail-row__edit" @click="beginFieldEdit('domain')"><Pencil :size="13" /></ElButton>
                    </template>
                  </span>
                </div>
                <div v-if="protocol === 'smb'" class="hfl-detail-row hfl-detail-row--full">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colConnection') }}</span>
                  <span class="hfl-detail-row__value hfl-detail-row__value--editable hfl-detail-row__value--mono">
                    <span class="hfl-detail-row__text">{{ connectionUri }}</span>
                    <ElButton
                      v-if="!isDetailEmpty(connectionUri)"
                      text
                      circle
                      size="small"
                      class="hfl-detail-row__edit"
                      :title="t('common.copy')"
                      @click="copyText(connectionUri)"
                    >
                      <Copy :size="13" />
                    </ElButton>
                  </span>
                </div>
              </div>
            </section>

            <section class="hfl-detail-section">
              <h4 class="hfl-detail-section__title">{{ t('protection.sourceResources.detailSectionProxy') }}</h4>
              <div class="hfl-detail-grid">
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colSourceProxy') }}</span>
                  <span class="hfl-detail-row__value hfl-detail-row__value--editable" :class="detailValueClass(proxyPrimaryText, true)">
                    <template v-if="detailDraft && isFieldEditing('bound_node_id')">
                      <ElSelect
                        v-model="detailDraft.bound_node_id"
                        size="small"
                        class="hfl-detail-inline-edit__input"
                        filterable
                        fit-input-width
                        :disabled="saving || onlineProxyNodes.length === 0"
                        popper-class="nas-detail-proxy-select-popper"
                        :placeholder="t('protection.sourceResources.selectSourceProxy')"
                      >
                        <ElOption
                          v-for="node in onlineProxyNodes"
                          :key="node.id"
                          :value="node.id"
                          :label="proxyNodeSelectLine(node)"
                        >
                          <div class="nas-detail-proxy-option">
                            <span class="nas-detail-proxy-option__text">{{ proxyNodeSelectLine(node) }}</span>
                            <ElTag size="small" :type="proxyNodeStatusTagType(node)" effect="plain">
                              {{ proxyNodeStatusLabel(node) }}
                            </ElTag>
                          </div>
                        </ElOption>
                      </ElSelect>
                      <span class="hfl-detail-inline-edit__actions">
                        <ElButton text circle size="small" :title="t('common.save')" :disabled="saving" @click="saveDetailChanges"><Check :size="14" /></ElButton>
                        <ElButton text circle size="small" :title="t('common.cancel')" :disabled="saving" @click="cancelFieldEdit"><X :size="14" /></ElButton>
                      </span>
                    </template>
                    <template v-else>
                      <div class="table-stack-cell hfl-table-no-tooltip">
                        <span class="table-stack-cell__primary">{{ proxyPrimaryText }}</span>
                        <span v-if="!isDetailEmpty(proxyIpText)" class="table-stack-cell__secondary">{{ proxyIpText }}</span>
                      </div>
                      <ElButton text circle size="small" class="hfl-detail-row__edit" @click="beginFieldEdit('bound_node_id')"><Pencil :size="13" /></ElButton>
                    </template>
                  </span>
                </div>
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.fieldProxyVersion') }}</span>
                  <span class="hfl-detail-row__value" :class="detailValueClass(proxyVersionText)">{{ proxyVersionText }}</span>
                </div>
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.nasFieldDir') }}</span>
                  <span class="hfl-detail-row__value hfl-detail-row__value--editable" :class="detailValueClass(mountDirText, true)">
                    <template v-if="detailDraft && isFieldEditing('path')">
                      <ElInput v-model="detailDraft.path" size="small" class="hfl-detail-inline-edit__input" :disabled="saving" @keyup.enter="saveDetailChanges" />
                      <span class="hfl-detail-inline-edit__actions">
                        <ElButton text circle size="small" :title="t('common.save')" :disabled="saving" @click="saveDetailChanges"><Check :size="14" /></ElButton>
                        <ElButton text circle size="small" :title="t('common.cancel')" :disabled="saving" @click="cancelFieldEdit"><X :size="14" /></ElButton>
                      </span>
                    </template>
                    <template v-else>
                      <span class="hfl-detail-row__text">{{ mountDirText }}</span>
                      <ElButton text circle size="small" class="hfl-detail-row__edit" @click="beginFieldEdit('path')"><Pencil :size="13" /></ElButton>
                    </template>
                  </span>
                </div>
              </div>
            </section>

            <section class="hfl-detail-section">
              <h4 class="hfl-detail-section__title">{{ t('protection.sourceResources.detailSectionRuntime') }}</h4>
              <div class="hfl-detail-grid">
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colLifecycleStatus') }}</span>
                  <span class="hfl-detail-row__value">
                    <ElTag
                      v-bind="connectionStatusTagAttrs"
                      size="small"
                      class="nas-detail-status-tag"
                      :title="connectionStatusLabel"
                    >
                      {{ connectionStatusLabel }}
                    </ElTag>
                  </span>
                </div>
                <div class="hfl-detail-row hfl-detail-row--full">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colCapacity') }}</span>
                  <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__value--stacked': capacityParts.total > 0 }">
                    <HflCapacityCell
                      :used-bytes="capacityParts.used"
                      :total-bytes="capacityParts.total"
                      variant="compact"
                      :format-bytes="formatNodeBytes"
                      :empty-label="DETAIL_EMPTY"
                    />
                  </span>
                </div>
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colConnectivity') }}</span>
                  <span class="hfl-detail-row__value">
                    <ElTag :type="availabilityTagType" size="small">{{ availabilityLabel }}</ElTag>
                  </span>
                </div>
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.fieldConnectivityUpdatedAt') }}</span>
                  <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__empty': !row.availability_updated_at }">{{ formatNodeDate(row.availability_updated_at) }}</span>
                </div>
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colRegisteredAt') }}</span>
                  <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__empty': !row.created_at }">{{ formatNodeDate(row.created_at) }}</span>
                </div>
                <div class="hfl-detail-row">
                  <span class="hfl-detail-row__label">{{ t('protection.sourceResources.colLastHeartbeat') }}</span>
                  <span class="hfl-detail-row__value" :class="detailValueClass(lastHeartbeatText)">{{ lastHeartbeatText }}</span>
                </div>
              </div>
            </section>
          </div>
        </ElTabPane>
      </ElTabs>
    </div>
  </ElDrawer>
</template>

<style scoped>
.hfl-detail-row__value--stacked > .hfl-detail-row__text {
  width: 100%;
}

.nas-detail-status-tag {
  display: inline-flex;
  min-width: 0;
  max-width: 100%;
  vertical-align: middle;
  white-space: nowrap;
}

.nas-detail-status-tag :deep(.el-tag__content) {
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nas-detail-proxy-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 2px 0;
  line-height: 1.35;
  max-width: 100%;
}

.nas-detail-proxy-option__text {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
  color: rgb(15 23 42);
}
</style>
