<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ChevronDown,
  Check,
  CirclePlay,
  CircleStop,
  Plus,
  RefreshCw,
  Search,
  Send,
  Pencil,
  SquarePen,
  Trash2,
  X,
} from 'lucide-vue-next'
import { ElDropdown, ElDropdownItem, ElDropdownMenu, ElMessage, ElTable, ElTableColumn } from 'element-plus'
import ModulePage from '../../components/ModulePage.vue'
import OpsStatCard from '../../components/ops/OpsStatCard.vue'
import HflTablePanel from '../../components/HflTablePanel.vue'
import HflPagination from '../../components/HflPagination.vue'
import HflTypeLabel from '../../components/HflTypeLabel.vue'
import { useOpsMenus } from '../../composables/useOpsMenus'
import { useDebouncedAction } from '../../composables/useDebouncedAction'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import { usePageRequestScope } from '../../composables/usePageRequestScope'
import { getNotificationTypeIcon } from '../../composables/useNotificationTypeIcon'
import { formatLocalDateTime } from '../../lib/dateTime'
import { booleanStatusTag, lifecycleStatusTagAttrs } from '../../lib/statusTag'
import { apiErrorMessageI18n } from '../../lib/api'
import { openErrorDetails } from '../../lib/errors/details'
import { notifyError, notifySuccess } from '../../lib/notify'
import {
  bulkDeleteChannels,
  bulkStateChannels,
  channelStatistics,
  deleteChannel,
  getChannelAssociationSummary,
  getChannelDetails,
  listChannels,
  testChannel,
  updateChannel,
  type NotificationChannel,
  type NotificationChannelAssociationPolicy,
  type NotificationChannelDetails,
} from '../../lib/notificationApi'
import DangerConfirmDialog, {
  type DangerConfirmItem,
} from '../../components/DangerConfirmDialog.vue'

type ChannelInlineField = 'name' | 'enabled' | `config:${string}`

const router = useRouter()
const { t, te } = useI18n()
const opsMenus = useOpsMenus()
const pageRequests = usePageRequestScope()
const { drawerSize, bindDrawerResize } = useResponsiveDrawerWidth()
const detailsLoading = ref(false)
const detailsError = ref<string | null>(null)
const channelInlineEditing = ref<ChannelInlineField | null>(null)
const channelInlineDraft = ref<string | boolean>('')
const channelInlineSaving = ref(false)
const { schedule: scheduleFilterSearch, runNow: runFilterSearch } = useDebouncedAction(() => {
  applyFilters()
})

const channels = ref<NotificationChannel[]>([])
const loading = ref(false)
const listError = ref('')
const testing = ref(false)
const skipNextSearchWatch = ref(false)
const pagination = reactive({ page: 1, pageSize: 20, count: 0 })
const filters = reactive({ search: '', type: '', enabled: '' })
const hasActiveFilters = computed(() => (
  Boolean(filters.search.trim())
  || Boolean(filters.type)
  || Boolean(filters.enabled)
))
const emptyDescription = computed(() => (
  hasActiveFilters.value ? t('ops.notification.emptyFilteredChannels') : t('ops.notification.emptyChannels')
))

const showDetails = ref(false)
const details = ref<NotificationChannelDetails | null>(null)
const detailTab = ref<'overview' | 'policies' | 'deliveries'>('overview')

const configPatchKeyMap: Record<string, string> = {
  webhookUrl: 'url',
  messagePrefix: 'message_prefix',
  smtpHost: 'smtp_host',
  smtpPort: 'smtp_port',
  smtpUsername: 'smtp_username',
  fromEmail: 'from_email',
  toEmails: 'to_emails',
  atMobiles: 'at_mobiles',
  atUserIds: 'at_user_ids',
  atAll: 'at_all',
  mentionedList: 'mentioned_list',
  mentionedMobileList: 'mentioned_mobile_list',
  signName: 'sign_name',
}

function channelTarget(ch: NotificationChannel) {
  const cfg = (ch.config || {}) as Record<string, unknown>
  if (ch.type === 'email') {
    const emails = cfg.to_emails || cfg.to || ''
    return String(emails) || t('common.empty')
  }
  return String(cfg.url || cfg.webhook_url || t('common.empty'))
}

function hasChannelTarget(ch: NotificationChannel) {
  const cfg = (ch.config || {}) as Record<string, unknown>
  if (ch.type === 'email') return Boolean(cfg.to_emails || cfg.to)
  return Boolean(cfg.url || cfg.webhook_url)
}

function hasEmailRecipients(ch: NotificationChannel) {
  if (ch.type !== 'email') return true
  const cfg = (ch.config || {}) as Record<string, unknown>
  const recipients = cfg.to_emails || cfg.to || cfg.recipients || ''
  if (Array.isArray(recipients)) return recipients.some((item) => String(item).trim())
  return String(recipients).split(',').some((item) => item.trim())
}

function detailChannel(d: NotificationChannelDetails | null) {
  return d?.channel ?? null
}

function channelInlineValue(ch: NotificationChannel, field: ChannelInlineField): string | boolean {
  if (field === 'name') return ch.name || ''
  if (field === 'enabled') return Boolean(ch.enabled)
  const key = field.slice('config:'.length)
  const configKey = configPatchKeyMap[key] || key
  const value = (ch.config || {})[configKey]
  if (typeof value === 'boolean') return value
  if (value === undefined || value === null) return ''
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function isChannelInlineEditing(field: ChannelInlineField) {
  return channelInlineEditing.value === field
}

function beginChannelInlineEdit(field: ChannelInlineField) {
  const ch = detailChannel(details.value)
  if (!ch || channelInlineSaving.value) return
  channelInlineEditing.value = field
  channelInlineDraft.value = channelInlineValue(ch, field)
}

function cancelChannelInlineEdit() {
  channelInlineEditing.value = null
  channelInlineDraft.value = ''
}

async function saveChannelInlineEdit() {
  const ch = detailChannel(details.value)
  const field = channelInlineEditing.value
  if (!ch || !field || channelInlineSaving.value) return

  const payload: Record<string, unknown> = {}
  if (field === 'name') {
    const name = String(channelInlineDraft.value).trim()
    if (!name) {
      ElMessage.warning({ message: t('ops.notification.validateChannelNameRequired'), grouping: true })
      return
    }
    payload.name = name
  } else if (field === 'enabled') {
    payload.enabled = Boolean(channelInlineDraft.value)
  } else {
    const key = field.slice('config:'.length)
    const configKey = configPatchKeyMap[key] || key
    const nextConfig = { ...(ch.config || {}) }
    nextConfig[configKey] = typeof channelInlineDraft.value === 'boolean'
      ? channelInlineDraft.value
      : String(channelInlineDraft.value).trim()
    payload.config = nextConfig
  }

  channelInlineSaving.value = true
  try {
    const updated = await updateChannel(ch.id, payload)
    if (details.value) details.value = { ...details.value, channel: updated }
    ElMessage.success({ message: t('ops.notification.updateSuccess'), grouping: true })
    cancelChannelInlineEdit()
    await fetchChannels()
  } finally {
    channelInlineSaving.value = false
  }
}

function logStatusLabel(status?: string) {
  if (status === 'success') return t('ops.notification.statusSent')
  if (status === 'failed') return t('ops.notification.statusFailed')
  if (status === 'pending') return t('ops.notification.statusPending')
  return status || t('common.empty')
}

function channelPoliciesCount(ch: NotificationChannel) {
  return ch.policiesCount ?? ch.policies_count ?? 0
}

function channelLastDeliveryStatus(ch: NotificationChannel) {
  return ch.lastDeliveryStatus ?? ch.last_delivery_status ?? ''
}

function channelLastDeliveryAt(ch: NotificationChannel) {
  return ch.lastDeliveryAt ?? ch.last_delivery_at ?? null
}

type ConfigItem = {
  key: string
  labelKey: string
  value: unknown
  mono?: boolean
  full?: boolean
  editable?: boolean
}

function asList<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : []
}

function summarizeConfig(ch: NotificationChannel | null): ConfigItem[] {
  if (!ch) return []
  const cfg = (ch.config || {}) as Record<string, unknown>
  const items: ConfigItem[] = []
  const k = (sub: string) => `ops.notification.cfgLabel.${ch.type}.${sub}`
  const push = (
    key: string,
    labelKey: string,
    value: unknown,
    opts?: { mono?: boolean; full?: boolean; editable?: boolean },
  ) => {
    if (value === undefined || value === null || value === '') return
    items.push({
      key,
      labelKey,
      value,
      mono: opts?.mono,
      full: opts?.full,
      editable: opts?.editable ?? true,
    })
  }
  if (ch.type === 'webhook') {
    push('url', k('url'), cfg.url || cfg.webhook_url, { mono: true, full: true })
    push('method', k('method'), cfg.method)
    push('headers', k('headers'), cfg.headers, { mono: true, full: true })
    push('messagePrefix', k('messagePrefix'), cfg.message_prefix)
  } else if (ch.type === 'email') {
    push('smtpHost', k('smtpHost'), cfg.smtp_host, { mono: true })
    push('smtpPort', k('smtpPort'), cfg.smtp_port)
    push('smtpUsername', k('smtpUsername'), cfg.smtp_username)
    push('fromEmail', k('fromEmail'), cfg.from_email, { mono: true })
    push('toEmails', k('toEmails'), cfg.to_emails, { full: true })
    push('subject', k('subject'), cfg.subject)
  } else if (ch.type === 'dingtalk') {
    push('webhookUrl', k('url'), cfg.url || cfg.webhook_url, { mono: true, full: true })
    push('secret', k('secret'), t('ops.notification.secretConfigured'), {
      mono: true,
      full: true,
      editable: false,
    })
    push('atMobiles', k('atMobiles'), cfg.at_mobiles)
    push('atUserIds', k('atUserIds'), cfg.at_user_ids)
    push('atAll', k('atAll'), cfg.at_all)
  } else if (ch.type === 'wecom') {
    push('webhookUrl', k('url'), cfg.url || cfg.webhook_url, { mono: true, full: true })
    push('mentionedList', k('mentionedList'), cfg.mentioned_list)
    push('mentionedMobileList', k('mentionedMobileList'), cfg.mentioned_mobile_list)
    push('atAll', k('atAll'), cfg.at_all)
  } else if (ch.type === 'sms') {
    push('endpoint', k('endpoint'), cfg.endpoint, { mono: true, full: true })
    push('template', k('template'), cfg.template, { mono: true, full: true })
    push('signName', k('signName'), cfg.sign_name)
  } else {
    Object.entries(cfg).forEach(([key, v]) => {
      if (v === undefined || v === null || v === '') return
      const sensitive = isSensitiveConfigKey(key)
      items.push({
        key,
        labelKey: `ops.notification.cfgLabel.raw.${key}`,
        value: sensitive ? t('ops.notification.secretConfigured') : v,
        mono: typeof v === 'string',
        editable: !sensitive,
      })
    })
  }
  return items
}

function configLabel(item: ConfigItem) {
  if (te(item.labelKey)) return t(item.labelKey)
  return item.key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/_/g, ' ')
    .replace(/^./, (char) => char.toUpperCase())
}

function isSensitiveConfigKey(key: string) {
  return /password|secret|token|api[_-]?key|authorization|cookie/i.test(key)
}

function formatConfigValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map((item) => formatConfigValue(item)).join(', ')
  }
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key}: ${isSensitiveConfigKey(key)
        ? t('ops.notification.secretConfigured')
        : formatConfigValue(item)}`)
      .join('; ')
  }
  return String(value)
}

function formatDate(value?: string | null) {
  return formatLocalDateTime(value, t('common.empty'))
}

async function fetchChannels() {
  const signal = pageRequests.nextSignal('notification-channel-list')
  loading.value = true
  listError.value = ''
  try {
    const [res, statRes] = await Promise.all([
      listChannels({
        page: pagination.page,
        page_size: pagination.pageSize,
        search: filters.search,
        type: filters.type,
        enabled: filters.enabled,
      }, { signal }),
      channelStatistics({
        search: filters.search,
        type: filters.type,
        enabled: filters.enabled,
      }, { signal }),
    ])
    channels.value = res.results
    pagination.count = res.count
    stats.value = {
      total: statRes.total,
      enabled: statRes.enabled,
      disabled: statRes.disabled,
      enabledRate: statRes.enabled_rate,
    }
  } catch (err) {
    if (pageRequests.isAbortError(err)) return
    listError.value = apiErrorMessageI18n(err, t, t('ops.notification.listLoadFailed'))
  } finally {
    pageRequests.releaseSignal('notification-channel-list', signal)
    if (!signal.aborted) loading.value = false
  }
}

function applyFilters() {
  pagination.page = 1
  void fetchChannels()
}

function clearSearch() {
  skipNextSearchWatch.value = true
  runFilterSearch()
}

function openCreate() {
  router.push('/ops/channels/create')
}

function openEdit(row: NotificationChannel) {
  router.push(`/ops/channels/${row.id}/edit`)
}

type DeleteChannelState = {
  row: NotificationChannel
  items: DangerConfirmItem[]
  totalPolicies: number
  hasLinkedPolicies: boolean
  associationStatus: 'checking' | 'ready' | 'error'
  associationError?: string
}
const deleteDialogOpen = ref(false)
const deleteDialogLoading = ref(false)
const deleteDialogSource = ref<'row' | 'drawer' | null>(null)
const deleteDialogState = ref<DeleteChannelState | null>(null)
let deleteAssociationRequestId = 0

function channelTypeLabel(type?: string | null) {
  if (!type) return t('common.empty')
  const key = `ops.notification.channelType.${type}`
  return te(key) ? t(key) : type
}

function buildDeleteItems(policies: NotificationChannelAssociationPolicy[]): DangerConfirmItem[] {
  return policies.map((p) => ({
    name: p.name,
    description: p.enabled === false ? t('ops.notification.policyStatusDisabled') : undefined,
    status: {
      label: t('ops.notification.linkedPolicyBadge'),
      tone: 'warning',
    },
  }))
}

const deleteDialogTitle = computed(() => {
  const row = deleteDialogState.value?.row
  return row ? t('ops.notification.deleteChannelConfirmTitle', { name: row.name }) : ''
})
const deleteDialogSubtitle = computed(() => {
  const row = deleteDialogState.value?.row
  if (!row) return ''
  return t('ops.notification.deleteChannelSubtitle', {
    id: row.id,
    type: channelTypeLabel(row.type),
  })
})
const deleteDialogMessage = computed(() => {
  const state = deleteDialogState.value
  if (!state || state.associationStatus !== 'ready') return ''
  return state.hasLinkedPolicies
    ? t('ops.notification.deleteChannelLinkedMessage', {
      n: state.totalPolicies,
    })
    : t('ops.notification.deleteConfirm', { name: state.row.name })
})
const deleteDialogWarning = computed(() => {
  const state = deleteDialogState.value
  if (!state?.hasLinkedPolicies || state.associationStatus !== 'ready') return ''
  return t('ops.notification.deleteChannelLinkedWarning', {
    n: state.totalPolicies,
  })
})
const deleteDialogItemsHeading = computed(() => {
  const state = deleteDialogState.value
  if (!state?.hasLinkedPolicies || state.associationStatus !== 'ready') return ''
  return t('ops.notification.deleteChannelLinkedHeading', { n: state.totalPolicies })
})
const deleteDialogIrreversible = computed(() => {
  const state = deleteDialogState.value
  if (!state || state.associationStatus !== 'ready') return ''
  return state.hasLinkedPolicies
    ? t('ops.notification.deleteChannelIrreversible')
    : t('ops.notification.deleteChannelIrreversible')
})
const deleteDialogMode = computed(() => {
  const state = deleteDialogState.value
  return state?.associationStatus === 'ready' && state.hasLinkedPolicies ? 'keyword' : 'single'
})
const deleteDialogConfirmKeywordHint = computed(() => {
  const state = deleteDialogState.value
  if (!state) return ''
  return t('ops.notification.deleteChannelKeywordHint', { name: state.row.name })
})


async function refreshDeleteAssociation(row?: NotificationChannel) {
  const current = row ?? deleteDialogState.value?.row
  if (!current) return
  const requestId = ++deleteAssociationRequestId
  deleteDialogState.value = {
    row: current,
    items: [],
    totalPolicies: 0,
    hasLinkedPolicies: false,
    associationStatus: 'checking',
  }
  try {
    const summaries = await loadChannelAssociationSummaries([current])
    if (requestId !== deleteAssociationRequestId || !deleteDialogState.value) return
    const policies = summaries[0]?.policies ?? []
    deleteDialogState.value = {
      row: current,
      items: buildDeleteItems(policies),
      totalPolicies: policies.length,
      hasLinkedPolicies: policies.length > 0,
      associationStatus: 'ready',
    }
  } catch (e: unknown) {
    if (requestId !== deleteAssociationRequestId || !deleteDialogState.value) return
    deleteDialogState.value = {
      row: current,
      items: [],
      totalPolicies: 0,
      hasLinkedPolicies: false,
      associationStatus: 'error',
      associationError: asErrMessage(e),
    }
  }
}


function closeDeleteDialog() {
  if (deleteDialogLoading.value) return
  deleteDialogOpen.value = false
  deleteDialogState.value = null
  deleteDialogSource.value = null
  deleteAssociationRequestId++
}

async function executeDelete() {
  const state = deleteDialogState.value
  if (!state || state.associationStatus !== 'ready') return
  const source = deleteDialogSource.value
  deleteDialogLoading.value = true
  try {
    await deleteChannel(state.row.id)
    ElMessage.success({ message: t('ops.notification.deleteSuccess'), grouping: true })
    if (source === 'drawer') showDetails.value = false
    deleteDialogOpen.value = false
    deleteDialogState.value = null
    deleteDialogSource.value = null
    await fetchChannels()
  } catch (e: unknown) {
    const message = apiErrorMessageI18n(e, t, t('ops.notification.deleteFailed'))
    notifyError({
      title: t('ops.notification.deleteFailed'),
      message,
      dedupeKey: `notification-channel-delete:${state.row.id}`,
      error: e,
      errorDetails: { issue: message },
      showDetails: true,
    })
  } finally {
    deleteDialogLoading.value = false
  }
}

async function runTest(row: NotificationChannel) {
  if (!hasEmailRecipients(row)) {
    ElMessage.warning({ message: t('ops.notification.validateRecipientsRequired'), grouping: true })
    return
  }
  testing.value = true
  try {
    const res = await testChannel(row.id)
    const ok = res.status === 'success'
    if (ok) {
      notifySuccess({
        message: t('ops.notification.testSuccess'),
        dedupeKey: `notification-channel-test:${row.id}:success`,
      })
    } else {
      const message = res.error || t('ops.notification.testFailed')
      openErrorDetails({
        title: t('ops.notification.testFailed'),
        summary: message,
        issue: message,
        rawDetail: res,
      })
    }
  } catch (e: unknown) {
    const message = apiErrorMessageI18n(e, t, t('ops.notification.testFailed'))
    openErrorDetails({
      error: e,
      overrides: {
        title: t('ops.notification.testFailed'),
        summary: message,
        issue: message,
      },
    })
  } finally {
    testing.value = false
  }
}

async function openDetails(row: NotificationChannel) {
  details.value = null
  detailsError.value = null
  detailsLoading.value = true
  detailTab.value = 'overview'
  showDetails.value = true
  requestAnimationFrame(() => bindDrawerResize())
  try {
    details.value = await getChannelDetails(row.id)
  } catch (e: unknown) {
    detailsError.value = apiErrorMessageI18n(e, t)
  } finally {
    detailsLoading.value = false
  }
}

function closeDetails() {
  showDetails.value = false
  detailsError.value = null
  cancelChannelInlineEdit()
}

async function retryLoadDetails() {
  const ch = detailChannel(details.value)
  if (!ch) return
  await openDetails(ch)
}



const stats = ref({ total: 0, enabled: 0, disabled: 0, enabledRate: 0 })

const channelsTableRef = ref<{ clearSelection?: () => void } | null>(null)
const selectedChannels = ref<NotificationChannel[]>([])
const moreActionsOpen = ref(false)
const batchActionLoading = ref(false)

const hasSelectedChannels = computed(() => selectedChannels.value.length > 0)
const moreBatchDisabled = computed(() => !hasSelectedChannels.value || batchActionLoading.value)
const moreEditDisabled = computed(() => selectedChannels.value.length !== 1 || batchActionLoading.value)
const moreTestDisabled = computed(() => selectedChannels.value.length !== 1 || batchActionLoading.value || testing.value)
const batchEnableDisabled = computed(() => batchActionLoading.value || !selectedChannels.value.some((row) => !row.enabled))
const batchDisableDisabled = computed(() => batchActionLoading.value || !selectedChannels.value.some((row) => row.enabled))

function onChannelSelectionChange(rows: NotificationChannel[]) {
  selectedChannels.value = rows
  if (!rows.length) moreActionsOpen.value = false
}

function clearChannelSelection() {
  selectedChannels.value = []
  channelsTableRef.value?.clearSelection?.()
  moreActionsOpen.value = false
}

function asErrMessage(err: unknown): string {
  return apiErrorMessageI18n(err, t)
}

type ChannelAssociationSummary = {
  channel: NotificationChannel
  policies: NotificationChannelAssociationPolicy[]
  error?: string
}

async function loadChannelAssociationSummaries(rows: NotificationChannel[]): Promise<ChannelAssociationSummary[]> {
  if (!rows.length) return []
  const res = await getChannelAssociationSummary(rows.map((r) => r.id))
  const byId = new Map<number, NotificationChannelAssociationPolicy[]>()
  for (const item of res.items) {
    byId.set(item.id, Array.isArray(item.policies) ? item.policies : [])
  }
  return rows.map((ch) => {
    const policies = byId.get(ch.id) ?? []
    return policies.length
      ? { channel: ch, policies }
      : { channel: ch, policies: [] }
  })
}

type BatchActionKind = 'enable' | 'disable' | 'delete'

interface PendingBatch {
  kind: BatchActionKind
  summaries: ChannelAssociationSummary[]
  selectedCount: number
  skippedCount: number
  totalPolicies: number
  linkedRows: number
  items: DangerConfirmItem[]
  impactItems: DangerConfirmItem[]
  impactItemsHeading: string
  title: string
  subtitle: string
  message: string
  warning: string
  itemsHeading: string
  irreversible: string
  keyword: string
  keywordHint: string
  level: 'medium' | 'high'
  confirmText: string
}

const pendingBatch = ref<PendingBatch | null>(null)
const batchDialogOpen = ref(false)
const batchDialogLoading = ref(false)
const batchAssociationLoading = ref(false)
const batchAssociationError = ref('')
let batchAssociationRequestId = 0

function buildBatchChannelItems(summaries: ChannelAssociationSummary[]): DangerConfirmItem[] {
  return summaries.map(({ channel }) => ({
    key: channel.id,
    name: channel.name,
    description: `${channelTypeLabel(channel.type)} · ${channelTarget(channel)}`,
    status: {
      label: channel.enabled ? t('ops.notification.statusEnabled') : t('ops.notification.statusDisabled'),
      tone: channel.enabled ? 'success' : 'neutral',
    },
  }))
}

function buildBatchPolicyImpactItems(summaries: ChannelAssociationSummary[]): DangerConfirmItem[] {
  const seen = new Set<string>()
  const items: DangerConfirmItem[] = []
  for (const summary of summaries) {
    for (const policy of summary.policies) {
      const key = policy.id || policy.name
      if (!key || seen.has(key)) continue
      seen.add(key)
      items.push({
        key,
        name: policy.name,
        hint: policy.enabled === false ? t('ops.notification.policyStatusDisabled') : undefined,
      })
    }
  }
  return items
}

function channelBatchSkipNotice(kind: BatchActionKind, selectedCount: number, changeCount: number) {
  const skipped = Math.max(0, selectedCount - changeCount)
  if (!skipped || (kind !== 'enable' && kind !== 'disable')) return ''
  return t(
    kind === 'enable'
      ? 'ops.notification.batchEnableSkipNotice'
      : 'ops.notification.batchDisableSkipNotice',
    { selected: selectedCount, changed: changeCount, skipped },
  )
}

function buildPendingBatch(
  kind: BatchActionKind,
  summaries: ChannelAssociationSummary[],
  selectedCount = summaries.length,
): PendingBatch {
  const totalPolicies = summaries.reduce((acc, s) => acc + s.policies.length, 0)
  const linkedRows = summaries.filter((s) => s.policies.length > 0).length
  const items = buildBatchChannelItems(summaries)
  const impactItems = buildBatchPolicyImpactItems(summaries)
  const hasLinks = totalPolicies > 0

  const titleKey = {
    enable: 'ops.notification.confirmBatchEnableTitle',
    disable: 'ops.notification.confirmBatchDisableTitle',
    delete: 'ops.notification.confirmBatchDeleteTitle',
  }[kind]

  const summaryKey = {
    enable: hasLinks
      ? 'ops.notification.confirmBatchEnableSummary'
      : 'ops.notification.confirmBatchEnableNoLinkSummary',
    disable: hasLinks
      ? 'ops.notification.confirmBatchDisableSummary'
      : 'ops.notification.confirmBatchDisableNoLinkSummary',
    delete: hasLinks
      ? 'ops.notification.confirmBatchDeleteSummary'
      : 'ops.notification.confirmBatchDeleteNoLinkSummary',
  }[kind]

  const itemsHeadingKey = {
    enable: 'ops.notification.batchEnableChannelsHeading',
    disable: 'ops.notification.batchDisableChannelsHeading',
    delete: 'ops.notification.batchDeleteChannelsHeading',
  }[kind]

  const keywordKey = {
    enable: 'ops.notification.batchEnableKeyword',
    disable: 'ops.notification.batchDisableKeyword',
    delete: 'ops.notification.deleteChannelKeyword',
  }[kind]

  const keywordHintKey = {
    enable: 'ops.notification.batchEnableKeywordHint',
    disable: 'ops.notification.batchDisableKeywordHint',
    delete: 'ops.notification.batchDeleteKeywordHint',
  }[kind]

  const confirmTextKey = {
    enable: 'common.confirm',
    disable: 'common.confirm',
    delete: 'ops.notification.actionDelete',
  }[kind]

  const level = kind === 'delete' ? 'high' : 'medium'

  const warning = hasLinks
    ? t({
        enable: 'ops.notification.batchEnableWarning',
        disable: 'ops.notification.batchDisableWarning',
        delete: 'ops.notification.batchDeleteWarning',
      }[kind], { n: totalPolicies })
    : ''

  const baseMessage = hasLinks
    ? t(summaryKey, {
        n: summaries.length,
        linked: linkedRows,
        policy: totalPolicies,
      })
    : t(summaryKey, { n: summaries.length })
  const skipNotice = channelBatchSkipNotice(kind, selectedCount, summaries.length)

  return {
    kind,
    summaries,
    selectedCount,
    skippedCount: Math.max(0, selectedCount - summaries.length),
    totalPolicies,
    linkedRows,
    items,
    impactItems,
    title: t(titleKey),
    subtitle: t('ops.notification.batchSubtitle', { n: summaries.length }),
    message: skipNotice ? `${skipNotice}\n\n${baseMessage}` : baseMessage,
    warning,
    itemsHeading: t(itemsHeadingKey, { n: summaries.length }),
    impactItemsHeading: hasLinks ? t('ops.notification.batchReferencedPoliciesHeading', { n: totalPolicies }) : '',
    irreversible: t('ops.notification.batchIrreversible'),
    keyword: t(keywordKey),
    keywordHint: t(keywordHintKey, { n: summaries.length }),
    level,
    confirmText: t(confirmTextKey),
  }
}

function buildPendingBatchPlaceholder(
  kind: BatchActionKind,
  rows: NotificationChannel[],
  selectedCount = rows.length,
): PendingBatch {
  const titleKey = {
    enable: 'ops.notification.confirmBatchEnableTitle',
    disable: 'ops.notification.confirmBatchDisableTitle',
    delete: 'ops.notification.confirmBatchDeleteTitle',
  }[kind]
  const keywordKey = {
    enable: 'ops.notification.batchEnableKeyword',
    disable: 'ops.notification.batchDisableKeyword',
    delete: 'ops.notification.deleteChannelKeyword',
  }[kind]
  const keywordHintKey = {
    enable: 'ops.notification.batchEnableKeywordHint',
    disable: 'ops.notification.batchDisableKeywordHint',
    delete: 'ops.notification.batchDeleteKeywordHint',
  }[kind]
  const confirmTextKey = {
    enable: 'common.confirm',
    disable: 'common.confirm',
    delete: 'ops.notification.actionDelete',
  }[kind]
  return {
    kind,
    summaries: rows.map((channel) => ({ channel, policies: [] })),
    selectedCount,
    skippedCount: Math.max(0, selectedCount - rows.length),
    totalPolicies: 0,
    linkedRows: 0,
    items: buildBatchChannelItems(rows.map((channel) => ({ channel, policies: [] }))),
    impactItems: [],
    title: t(titleKey),
    subtitle: t('ops.notification.batchSubtitle', { n: rows.length }),
    message: '',
    warning: '',
    itemsHeading: t({
      enable: 'ops.notification.batchEnableChannelsHeading',
      disable: 'ops.notification.batchDisableChannelsHeading',
      delete: 'ops.notification.batchDeleteChannelsHeading',
    }[kind], { n: rows.length }),
    impactItemsHeading: '',
    irreversible: '',
    keyword: t(keywordKey),
    keywordHint: t(keywordHintKey, { n: rows.length }),
    level: kind === 'delete' ? 'high' : 'medium',
    confirmText: t(confirmTextKey),
  }
}

async function openBatchDialog(
  kind: BatchActionKind,
  rowsOverride?: NotificationChannel[],
  selectedCountOverride?: number,
) {
  const rows = rowsOverride ? [...rowsOverride] : [...selectedChannels.value]
  if (!rows.length) return
  const selectedCount = selectedCountOverride ?? rows.length
  moreActionsOpen.value = false
  batchAssociationError.value = ''
  pendingBatch.value = buildPendingBatchPlaceholder(kind, rows, selectedCount)
  batchDialogOpen.value = true
  batchAssociationLoading.value = true
  batchActionLoading.value = true
  const requestId = ++batchAssociationRequestId
  try {
    const summaries = await loadChannelAssociationSummaries(rows)
    if (requestId !== batchAssociationRequestId || !batchDialogOpen.value) return
    pendingBatch.value = buildPendingBatch(kind, summaries, selectedCount)
  } catch (e: unknown) {
    if (requestId !== batchAssociationRequestId || !batchDialogOpen.value) return
    batchAssociationError.value = asErrMessage(e)
  } finally {
    if (requestId === batchAssociationRequestId) {
      batchAssociationLoading.value = false
      batchActionLoading.value = false
    }
  }
}

async function retryBatchAssociationCheck() {
  const batch = pendingBatch.value
  if (!batch) return
  await openBatchDialog(batch.kind, batch.summaries.map((s) => s.channel), batch.selectedCount)
}

async function runBatchEnable() {
  const rows = selectedChannels.value
  const targets = rows.filter((row) => !row.enabled)
  if (!targets.length) {
    ElMessage.info({ message: t('ops.notification.batchNoStateChange'), grouping: true })
    return
  }
  await openBatchDialog('enable', targets, rows.length)
}

async function runBatchDisable() {
  const rows = selectedChannels.value
  const targets = rows.filter((row) => row.enabled)
  if (!targets.length) {
    ElMessage.info({ message: t('ops.notification.batchNoStateChange'), grouping: true })
    return
  }
  await openBatchDialog('disable', targets, rows.length)
}

async function runBatchDelete() {
  await openBatchDialog('delete')
}

function closeBatchDialog() {
  if (batchDialogLoading.value) return
  batchDialogOpen.value = false
  pendingBatch.value = null
  batchAssociationLoading.value = false
  batchAssociationError.value = ''
  batchAssociationRequestId++
}

async function executeBatchAction() {
  const batch = pendingBatch.value
  if (!batch || batchAssociationLoading.value || batchAssociationError.value) return
  batchDialogLoading.value = true
  batchActionLoading.value = true
  const ids = batch.summaries.map((s) => s.channel.id)
  let ok = 0
  let fail = 0
  let skip = 0
  try {
    if (batch.kind === 'delete') {
      const res = await bulkDeleteChannels(ids)
      ok = Array.isArray(res.deleted) ? res.deleted.length : 0
      fail = Array.isArray(res.failed) ? res.failed.length : 0
    } else {
      const res = await bulkStateChannels(ids, batch.kind === 'enable')
      ok = Array.isArray(res.updated) ? res.updated.length : 0
      fail = Array.isArray(res.failed) ? res.failed.length : 0
    }
  } catch (e: unknown) {
    batchDialogLoading.value = false
    batchActionLoading.value = false
    const fallbackKey = batch.kind === 'enable'
      ? 'ops.notification.batchEnableFailed'
      : batch.kind === 'disable'
        ? 'ops.notification.batchDisableFailed'
        : 'ops.notification.batchDeleteFailed'
    ElMessage.error({
      message: apiErrorMessageI18n(e, t, t(fallbackKey)),
      grouping: true,
    })
    return
  }

  batchDialogLoading.value = false
  batchActionLoading.value = false
  batchDialogOpen.value = false
  pendingBatch.value = null

  const successKey = {
    enable: 'ops.notification.batchEnableSuccess',
    disable: 'ops.notification.batchDisableSuccess',
    delete: 'ops.notification.batchDeleteSuccess',
  }[batch.kind]
  const partialKey = {
    enable: 'ops.notification.batchEnablePartial',
    disable: 'ops.notification.batchDisablePartial',
    delete: 'ops.notification.batchDeletePartial',
  }[batch.kind]
  if (batch.kind === 'delete') {
    if (fail === 0 && skip === 0) {
      ElMessage.success({ message: t(successKey, { n: ok }), grouping: true })
    } else {
      ElMessage.warning({ message: t(partialKey, { ok, skip, fail }), grouping: true })
    }
  } else {
    if (fail === 0) {
      ElMessage.success({ message: t(successKey, { n: ok }), grouping: true })
    } else {
      ElMessage.warning({ message: t(partialKey, { ok, fail }), grouping: true })
    }
  }  clearChannelSelection()
  await fetchChannels()
}

function onMoreEdit() {
  moreActionsOpen.value = false
  const row = selectedChannels.value[0]
  if (row) openEdit(row)
}

async function onMoreTest() {
  moreActionsOpen.value = false
  const row = selectedChannels.value[0]
  if (row) await runTest(row)
}

onMounted(fetchChannels)

watch(
  () => filters.search,
  () => {
    if (skipNextSearchWatch.value) {
      skipNextSearchWatch.value = false
      return
    }
    scheduleFilterSearch()
  },
)

watch(
  () => [filters.type, filters.enabled],
  () => runFilterSearch(),
)
</script>

<template>
  <ModulePage
    :title="t('ops.nav.notificationChannels')"
    :menus="opsMenus"
    body-fill
  >
    <div class="hfl-ops-page hfl-ops-page--fill">
      <div class="hfl-ops-stats-grid hfl-ops-stats-grid--4">
        <OpsStatCard
          :label="t('ops.notification.filterActive')"
          :value="stats.enabled"
          accent="green"
          accent-side="left"
          value-class="text-emerald-600"
        />
        <OpsStatCard
          :label="t('ops.notification.channelsTitle')"
          :value="stats.total"
          accent="indigo"
          accent-side="left"
        />
        <OpsStatCard
          :label="t('ops.notification.filterInactive')"
          :value="stats.disabled"
          accent="gray"
          accent-side="left"
        />
        <OpsStatCard
          :label="t('ops.notification.enabledRate')"
          :value="stats.total ? `${Math.round(stats.enabledRate)}%` : '—'"
          accent="blue"
          accent-side="left"
        />
      </div>

      <HflTablePanel fill>
        <template #toolbar>
          <el-button
            type="primary"
            :icon="Plus"
            @click="openCreate"
          >
            {{ t('common.add') }}
          </el-button>
          <el-dropdown
            trigger="click"
            popper-class="hfl-actions-dropdown"
            @visible-change="moreActionsOpen = $event"
          >
            <el-button :loading="batchActionLoading">
              {{ t('ops.notification.btnMoreActions') }}
              <ChevronDown
                :size="16"
                class="hfl-list-more__chev"
                :class="{ 'hfl-list-more__chev--open': moreActionsOpen }"
              />
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item
                  :disabled="moreEditDisabled"
                  @click="onMoreEdit"
                >
                  <span class="el-dropdown-menu__item-content">
                    <SquarePen
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('ops.notification.actionEdit') }}</span>
                  </span>
                </el-dropdown-item>
                <el-dropdown-item
                  :disabled="moreTestDisabled"
                  @click="onMoreTest"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Send
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('ops.notification.testConnection') }}</span>
                  </span>
                </el-dropdown-item>
                <el-dropdown-item
                  divided
                  :disabled="batchEnableDisabled"
                  @click="runBatchEnable"
                >
                  <span class="el-dropdown-menu__item-content">
                    <CirclePlay
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('ops.notification.btnBatchEnable') }}</span>
                  </span>
                </el-dropdown-item>
                <el-dropdown-item
                  :disabled="batchDisableDisabled"
                  @click="runBatchDisable"
                >
                  <span class="el-dropdown-menu__item-content">
                    <CircleStop
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('ops.notification.btnBatchDisable') }}</span>
                  </span>
                </el-dropdown-item>
                <el-dropdown-item
                  divided
                  class="el-dropdown-menu__item--danger"
                  :disabled="moreBatchDisabled"
                  @click="runBatchDelete"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Trash2
                      :size="14"
                      class="shrink-0"
                    />
                    <span>{{ t('ops.notification.btnBatchDelete') }}</span>
                  </span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
        <template #toolbar-actions>
          <el-input
            v-model="filters.search"
            class="hfl-list-search"
            clearable
            :placeholder="t('ops.notification.searchChannelName')"
            @clear="clearSearch"
          >
            <template #prefix>
              <Search class="h-4 w-4 opacity-60" />
            </template>
          </el-input>
          <el-select
            v-model="filters.type"
            clearable
            style="width: 130px"
            :placeholder="t('ops.notification.filterAllType')"
          >
            <el-option
              value="email"
              :label="t('ops.notification.typeEmail')"
            />
            <el-option
              value="sms"
              :label="t('ops.notification.typeSms')"
            />
            <el-option
              value="webhook"
              :label="t('ops.notification.typeWebhook')"
            />
            <el-option
              value="dingtalk"
              :label="t('ops.notification.typeDingtalk')"
            />
            <el-option
              value="wecom"
              :label="t('ops.notification.typeWecom')"
            />
          </el-select>
        </template>
        <template #toolbar-utility>
          <el-button
            class="hfl-refresh-button"
            :title="t('ops.task.btnRefresh')"
            :aria-label="t('ops.task.btnRefresh')"
            :disabled="loading"
            @click="fetchChannels"
          >
            <RefreshCw
              :size="16"
              :class="{ 'is-spinning': loading }"
            />
          </el-button>
        </template>

        <ElAlert
          v-if="listError"
          type="error"
          show-icon
          :closable="false"
          class="mb-3"
          :title="listError"
        >
          <template #default>
            <ElButton
              size="small"
              @click="fetchChannels"
            >
              {{ t('common.retry') }}
            </ElButton>
          </template>
        </ElAlert>

        <template #table="{ tableMaxHeight }">
          <el-table
            ref="channelsTableRef"
            v-table-column-resize="'ops.notificationChannels'"
            v-loading="loading"
            :data="channels"
            stripe
            class="hfl-list-table"
            row-key="id"
            :max-height="tableMaxHeight"
            @selection-change="onChannelSelectionChange"
          >
            <el-table-column
              type="selection"
              width="35"
              fixed="left"
            />
            <el-table-column
              :label="t('ops.notification.colChannelName')"
              min-width="190"
              fixed="left"
            >
              <template #default="{ row }">
                <div class="hfl-ops-primary-cell">
                  <div class="min-w-0">
                    <div class="flex min-w-0 items-center gap-1">
                      <button
                        type="button"
                        class="hfl-table-name-link hfl-table-name-link--single min-w-0"
                        @click="openDetails(row)"
                      >
                        {{ row.name }}
                      </button>
                    </div>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.notification.colType')"
              width="130"
            >
              <template #default="{ row }">
                <HflTypeLabel
                  :icon="getNotificationTypeIcon(row.type)"
                  :label="channelTypeLabel(row.type)"
                />
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.notification.colTarget')"
              min-width="220"
            >
              <template #default="{ row }">
                <span :class="hasChannelTarget(row) ? 'hfl-table-cell-mono' : 'hfl-empty-mark'">{{ channelTarget(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.notification.colStatus')"
              width="100"
            >
              <template #default="{ row }">
                <el-tag
                  :type="booleanStatusTag(row.enabled).type"
                  :class="booleanStatusTag(row.enabled).class"
                  size="small"
                >
                  {{ row.enabled ? t('ops.notification.statusEnabled') : t('ops.notification.statusDisabled') }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.notification.colLinkedPolicies')"
              width="110"
            >
              <template #default="{ row }">
                <span>{{ channelPoliciesCount(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.notification.colLastDelivery')"
              width="170"
            >
              <template #default="{ row }">
                <div
                  v-if="channelLastDeliveryStatus(row)"
                  class="hfl-ops-cell-stack"
                >
                  <el-tag
                    v-bind="lifecycleStatusTagAttrs(channelLastDeliveryStatus(row))"
                    size="small"
                  >
                    {{ logStatusLabel(channelLastDeliveryStatus(row)) }}
                  </el-tag>
                  <div
                    class="hfl-ops-cell-stack__meta hfl-table-cell-time"
                    :class="{ 'hfl-empty-mark': !channelLastDeliveryAt(row) }"
                  >
                    {{ formatDate(channelLastDeliveryAt(row)) }}
                  </div>
                </div>
                <span
                  v-else
                  class="hfl-empty-mark"
                >{{ t('common.empty') }}</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('ops.notification.colUpdatedAt')"
              width="170"
            >
              <template #default="{ row }">
                <span
                  class="hfl-table-cell-time"
                  :class="{ 'hfl-empty-mark': !(row.updatedAt || row.updated_at) }"
                >{{ formatDate(row.updatedAt || row.updated_at) }}</span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty :description="emptyDescription" />
            </template>
          </el-table>
        </template>

        <template #footer>
          <span
            v-if="hasSelectedChannels"
            class="hfl-list-footer__selected"
          >
            {{ t('ops.notification.selectedCount', { n: selectedChannels.length }) }}
          </span>
          <HflPagination
            v-model:current-page="pagination.page"
            v-model:page-size="pagination.pageSize"
            class="hfl-list-footer__pagination"
            layout="total, sizes, prev, pager, next"
            :total="pagination.count"
            :page-sizes="[20, 30, 50, 100]"
            @current-change="fetchChannels"
            @size-change="() => { pagination.page = 1; fetchChannels() }"
          />
        </template>
      </HflTablePanel>
    </div>

    <ElDrawer
      v-model="showDetails"
      class="hfl-detail-drawer"
      :size="drawerSize"
      direction="rtl"
      @opened="bindDrawerResize"
      @closed="closeDetails"
    >
      <template #header>
        <span class="hfl-detail-drawer__title">
          {{ detailChannel(details)?.name || t('common.empty') }}
        </span>
      </template>

      <div class="hfl-detail-drawer__body">
        <div
          v-if="detailsLoading"
          class="hfl-ops-channel-detail-drawer__loading"
        >
          {{ t('common.loading') }}
        </div>
        <div
          v-else-if="detailsError"
          class="hfl-ops-channel-detail-drawer__error"
        >
          <div class="hfl-ops-channel-detail-drawer__error-text">
            {{ detailsError }}
          </div>
          <ElButton
            size="small"
            type="primary"
            plain
            class="mt-2"
            @click="retryLoadDetails"
          >
            {{ t('common.retry') }}
          </ElButton>
        </div>
        <template v-else-if="details && detailChannel(details)">
          <ElTabs
            v-model="detailTab"
            class="hfl-detail-tabs"
          >
            <ElTabPane
              :label="t('ops.notification.detailTabOverview')"
              name="overview"
            >
              <div class="hfl-detail-sections">
                <div
                  v-if="details.stats"
                  class="hfl-ops-stats-grid hfl-ops-stats-grid--4"
                >
                  <OpsStatCard
                    :label="t('ops.notification.logsCount')"
                    :value="details.stats.logs_count ?? 0"
                    accent="indigo"
                    accent-side="left"
                  />
                  <OpsStatCard
                    :label="t('ops.notification.successRate')"
                    :value="`${details.stats.success_rate ?? 0}%`"
                    accent="green"
                    accent-side="left"
                  />
                  <OpsStatCard
                    :label="t('ops.notification.policiesCount')"
                    :value="details.stats.policies_count ?? 0"
                    accent="blue"
                    accent-side="left"
                  />
                  <OpsStatCard
                    :label="t('ops.notification.alertsCount')"
                    :value="details.stats.alerts_count ?? 0"
                    accent="orange"
                    accent-side="left"
                  />
                </div>

                <section class="hfl-detail-section">
                  <h4 class="hfl-detail-section__title">
                    {{ t('ops.notification.basicInfo') }}
                  </h4>
                  <div class="hfl-detail-grid">
                    <div class="hfl-detail-row">
                      <span class="hfl-detail-row__label">{{ t('ops.notification.colId') }}</span>
                      <span class="hfl-detail-row__value hfl-table-cell-mono">
                        {{ detailChannel(details)?.id }}
                      </span>
                    </div>
                    <div class="hfl-detail-row">
                      <span class="hfl-detail-row__label">{{ t('ops.notification.colChannelName') }}</span>
                      <span class="hfl-detail-row__value hfl-detail-row__value--editable">
                        <template v-if="detailChannel(details) && isChannelInlineEditing('name')">
                          <ElInput
                            v-model="channelInlineDraft"
                            size="small"
                            class="hfl-detail-inline-edit__input"
                            @keyup.enter="saveChannelInlineEdit"
                          />
                          <span class="hfl-detail-inline-edit__actions">
                            <ElButton
                              text
                              circle
                              size="small"
                              :title="t('common.save')"
                              :disabled="channelInlineSaving"
                              @click="saveChannelInlineEdit"
                            ><Check :size="14" /></ElButton>
                            <ElButton
                              text
                              circle
                              size="small"
                              :title="t('common.cancel')"
                              :disabled="channelInlineSaving"
                              @click="cancelChannelInlineEdit"
                            ><X :size="14" /></ElButton>
                          </span>
                        </template>
                        <template v-else>
                          <span class="hfl-detail-row__text">{{ detailChannel(details)?.name }}</span>
                          <ElButton
                            text
                            circle
                            size="small"
                            class="hfl-detail-row__edit"
                            :title="t('common.edit')"
                            @click="beginChannelInlineEdit('name')"
                          >
                            <Pencil :size="13" />
                          </ElButton>
                        </template>
                      </span>
                    </div>
                    <div class="hfl-detail-row">
                      <span class="hfl-detail-row__label">{{ t('ops.notification.colType') }}</span>
                      <span class="hfl-detail-row__value">{{ detailChannel(details)?.type }}</span>
                    </div>
                    <div class="hfl-detail-row">
                      <span class="hfl-detail-row__label">{{ t('ops.notification.colStatus') }}</span>
                      <span class="hfl-detail-row__value hfl-detail-row__value--editable">
                        <template v-if="detailChannel(details) && isChannelInlineEditing('enabled')">
                          <ElSwitch v-model="channelInlineDraft" />
                          <span class="hfl-detail-inline-edit__actions">
                            <ElButton
                              text
                              circle
                              size="small"
                              :title="t('common.save')"
                              :disabled="channelInlineSaving"
                              @click="saveChannelInlineEdit"
                            ><Check :size="14" /></ElButton>
                            <ElButton
                              text
                              circle
                              size="small"
                              :title="t('common.cancel')"
                              :disabled="channelInlineSaving"
                              @click="cancelChannelInlineEdit"
                            ><X :size="14" /></ElButton>
                          </span>
                        </template>
                        <template v-else>
                          <el-tag
                            :type="booleanStatusTag(Boolean(detailChannel(details)?.enabled)).type"
                            :class="booleanStatusTag(Boolean(detailChannel(details)?.enabled)).class"
                            size="small"
                          >
                            {{ detailChannel(details)?.enabled ? t('ops.notification.statusEnabled') : t('ops.notification.statusDisabled') }}
                          </el-tag>
                          <ElButton
                            text
                            circle
                            size="small"
                            class="hfl-detail-row__edit"
                            :title="t('common.edit')"
                            @click="beginChannelInlineEdit('enabled')"
                          >
                            <Pencil :size="13" />
                          </ElButton>
                        </template>
                      </span>
                    </div>
                    <div class="hfl-detail-row">
                      <span class="hfl-detail-row__label">{{ t('ops.notification.colCreatedAt') }}</span>
                      <span
                        class="hfl-detail-row__value hfl-table-cell-time"
                        :class="{ 'hfl-detail-row__empty': !(detailChannel(details)?.createdAt || detailChannel(details)?.created_at) }"
                      >
                        {{ formatDate(detailChannel(details)?.createdAt || detailChannel(details)?.created_at) }}
                      </span>
                    </div>
                    <div class="hfl-detail-row">
                      <span class="hfl-detail-row__label">{{ t('ops.notification.colUpdatedAt') }}</span>
                      <span
                        class="hfl-detail-row__value hfl-table-cell-time"
                        :class="{ 'hfl-detail-row__empty': !(detailChannel(details)?.updatedAt || detailChannel(details)?.updated_at) }"
                      >
                        {{ formatDate(detailChannel(details)?.updatedAt || detailChannel(details)?.updated_at) }}
                      </span>
                    </div>
                    <div class="hfl-detail-row">
                      <span class="hfl-detail-row__label">{{ t('ops.notification.lastSuccess') }}</span>
                      <span
                        class="hfl-detail-row__value hfl-table-cell-time"
                        :class="{ 'hfl-detail-row__empty': !details.stats?.last_success_at }"
                      >
                        {{ formatDate(details.stats?.last_success_at) }}
                      </span>
                    </div>
                    <div class="hfl-detail-row">
                      <span class="hfl-detail-row__label">{{ t('ops.notification.lastFailed') }}</span>
                      <span
                        class="hfl-detail-row__value hfl-table-cell-time"
                        :class="{ 'hfl-detail-row__empty': !details.stats?.last_failed_at }"
                      >
                        {{ formatDate(details.stats?.last_failed_at) }}
                      </span>
                    </div>
                  </div>
                </section>

                <section class="hfl-detail-section">
                  <h4 class="hfl-detail-section__title">
                    {{ t('ops.notification.configInfo') }}
                  </h4>
                  <div class="hfl-detail-grid">
                    <template
                      v-for="item in summarizeConfig(detailChannel(details))"
                      :key="item.key"
                    >
                      <div
                        class="hfl-detail-row"
                        :class="{ 'hfl-detail-row--full': item.full }"
                      >
                        <span class="hfl-detail-row__label">{{ configLabel(item) }}</span>
                        <span
                          class="hfl-detail-row__value"
                          :class="{ 'hfl-detail-row__value--editable': true, 'hfl-table-cell-mono': item.mono, 'hfl-detail-row__value--break': item.full }"
                        >
                          <template v-if="detailChannel(details) && isChannelInlineEditing(`config:${item.key}`)">
                            <ElSwitch
                              v-if="typeof item.value === 'boolean'"
                              v-model="channelInlineDraft"
                            />
                            <ElInput
                              v-else
                              v-model="channelInlineDraft"
                              size="small"
                              :type="item.full ? 'textarea' : 'text'"
                              :rows="item.full ? 2 : undefined"
                              class="hfl-detail-inline-edit__input"
                              @keyup.enter="saveChannelInlineEdit"
                            />
                            <span class="hfl-detail-inline-edit__actions">
                              <ElButton
                                text
                                circle
                                size="small"
                                :title="t('common.save')"
                                :disabled="channelInlineSaving"
                                @click="saveChannelInlineEdit"
                              ><Check :size="14" /></ElButton>
                              <ElButton
                                text
                                circle
                                size="small"
                                :title="t('common.cancel')"
                                :disabled="channelInlineSaving"
                                @click="cancelChannelInlineEdit"
                              ><X :size="14" /></ElButton>
                            </span>
                          </template>
                          <template v-else>
                            <span class="hfl-detail-row__text">
                              <span>{{ formatConfigValue(item.value) }}</span>
                            </span>
                            <ElButton
                              v-if="item.editable"
                              text
                              circle
                              size="small"
                              class="hfl-detail-row__edit"
                              :title="t('common.edit')"
                              @click="beginChannelInlineEdit(`config:${item.key}`)"
                            >
                              <Pencil :size="13" />
                            </ElButton>
                          </template>
                        </span>
                      </div>
                    </template>
                    <div
                      v-if="summarizeConfig(detailChannel(details)).length === 0"
                      class="hfl-detail-row hfl-detail-row--full"
                    >
                      <span class="hfl-detail-row__value hfl-detail-row__empty">{{ t('common.empty') }}</span>
                    </div>
                  </div>
                </section>
              </div>
            </ElTabPane>

            <ElTabPane
              :label="t('ops.notification.associatedPolicies')"
              name="policies"
            >
              <el-table
                v-table-column-resize="'ops.notificationChannels.associatedPolicies'"
                :data="asList(details.associated_policies)"
                stripe
                class="hfl-list-table"
              >
                <el-table-column
                  :label="t('ops.notification.colPolicyId')"
                  width="100"
                >
                  <template #default="{ row }">
                    <span class="hfl-table-cell-mono">{{ row.id }}</span>
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('ops.notification.colPolicyName')"
                  min-width="160"
                >
                  <template #default="{ row }">
                    {{ row.name }}
                    <span
                      v-if="row.description"
                      class="hfl-table-cell-muted ml-1"
                    >{{ t('common.dotSeparator') }} {{ row.description }}</span>
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('ops.notification.colStatus')"
                  width="100"
                >
                  <template #default="{ row }">
                    <span
                      class="hfl-ops-severity-pill"
                      :class="row.enabled ? 'hfl-ops-severity-pill--info' : 'hfl-ops-severity-pill--neutral'"
                    >
                      {{ row.enabled ? t('ops.notification.statusEnabled') : t('ops.notification.statusDisabled') }}
                    </span>
                  </template>
                </el-table-column>
                <template #empty>
                  <el-empty :description="t('ops.notification.emptyAssociatedPolicies')" />
                </template>
              </el-table>
            </ElTabPane>

            <ElTabPane
              :label="t('ops.notification.recentDeliveries')"
              name="deliveries"
            >
              <el-table
                v-table-column-resize="'ops.notificationChannels.notificationLogs'"
                :data="asList(details.notification_logs)"
                stripe
                class="hfl-list-table"
              >
                <el-table-column
                  :label="t('ops.notification.colEvent')"
                  min-width="180"
                >
                  <template #default="{ row }">
                    <div class="hfl-ops-channel-detail-drawer__event-title">
                      {{ row.alert?.title || row.event_type || t('common.empty') }}
                    </div>
                    <div
                      v-if="row.policy?.name"
                      class="hfl-ops-channel-detail-drawer__event-meta"
                    >
                      {{ row.policy.name }}
                    </div>
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('ops.notification.colLogType')"
                  prop="notification_type"
                  width="110"
                />
                <el-table-column
                  :label="t('ops.notification.colStatus')"
                  width="110"
                >
                  <template #default="{ row }">
                    <el-tag
                      v-bind="lifecycleStatusTagAttrs(row.status)"
                      size="small"
                    >
                      {{ logStatusLabel(row.status) }}
                    </el-tag>
                    <div
                      v-if="row.error_message"
                      class="hfl-ops-channel-detail-drawer__error-msg"
                      :title="row.error_message"
                    >
                      {{ row.error_message }}
                    </div>
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('ops.notification.colTime')"
                  prop="sent_at"
                  width="170"
                >
                  <template #default="{ row }">
                    <span
                      class="hfl-table-cell-time"
                      :class="{ 'hfl-empty-mark': !(row.sent_at || row.sentAt) }"
                    >{{ formatDate(row.sent_at || row.sentAt) }}</span>
                  </template>
                </el-table-column>
                <template #empty>
                  <el-empty :description="t('ops.notification.emptyDeliveries')" />
                </template>
              </el-table>
            </ElTabPane>
          </ElTabs>
        </template>
      </div>
    </ElDrawer>

    <DangerConfirmDialog
      v-if="deleteDialogState"
      v-model="deleteDialogOpen"
      :title="deleteDialogTitle"
      :subtitle="deleteDialogSubtitle"
      :message="deleteDialogMessage"
      :warning="deleteDialogWarning"
      :items="deleteDialogState.items"
      :items-heading="deleteDialogItemsHeading"
      :irreversible-hint="deleteDialogIrreversible"
      :confirm-mode="deleteDialogMode"
      :confirm-keyword="t('ops.notification.deleteChannelKeyword')"
      :confirm-keyword-hint="deleteDialogConfirmKeywordHint"
      :confirm-keyword-placeholder="t('ops.notification.deleteChannelKeyword')"
      :cancel-text="t('common.cancel')"
      :confirm-text="t('ops.notification.actionDelete')"
      :loading="deleteDialogLoading"
      :pending="deleteDialogState.associationStatus === 'checking'"
      :pending-text="t('ops.notification.associationCheckPending')"
      :error-text="deleteDialogState.associationStatus === 'error'
        ? t('ops.notification.associationCheckFailed', {
          error: deleteDialogState.associationError || '',
        })
        : ''"
      width="640px"
      level="high"
      @confirm="executeDelete"
      @cancel="closeDeleteDialog"
    >
      <template
        v-if="deleteDialogState.associationStatus === 'error'"
        #extra
      >
        <ElButton
          size="small"
          @click="refreshDeleteAssociation()"
        >
          {{ t('common.retry') }}
        </ElButton>
      </template>
    </DangerConfirmDialog>

    <DangerConfirmDialog
      v-if="pendingBatch"
      v-model="batchDialogOpen"
      :title="pendingBatch.title"
      :subtitle="pendingBatch.subtitle"
      :message="pendingBatch.message"
      :warning="pendingBatch.warning"
      :items="pendingBatch.items"
      :items-heading="pendingBatch.itemsHeading"
      :irreversible-hint="pendingBatch.irreversible"
      confirm-mode="keyword"
      :confirm-keyword="pendingBatch.keyword"
      :confirm-keyword-hint="pendingBatch.keywordHint"
      :confirm-keyword-placeholder="pendingBatch.keyword"
      :cancel-text="t('common.cancel')"
      :confirm-text="pendingBatch.confirmText"
      :loading="batchDialogLoading"
      :pending="batchAssociationLoading"
      :pending-text="t('ops.notification.associationCheckPending')"
      :error-text="batchAssociationError
        ? t('ops.notification.associationCheckFailed', { error: batchAssociationError })
        : ''"
      :level="pendingBatch.level"
      width="760px"
      @confirm="executeBatchAction"
      @cancel="closeBatchDialog"
    >
      <template #extra>
        <section
          v-if="pendingBatch.impactItems.length"
          class="notification-batch-impact"
        >
          <header class="notification-batch-impact__heading">
            {{ pendingBatch.impactItemsHeading }}
          </header>
          <ElTable
            :data="pendingBatch.impactItems"
            class="notification-batch-impact__table"
            size="small"
            max-height="168"
          >
            <ElTableColumn
              prop="name"
              label="Name"
              min-width="220"
              show-overflow-tooltip
            />
            <ElTableColumn
              label="Status"
              width="140"
            >
              <template #default="{ row }">
                <span
                  class="notification-batch-impact__hint"
                  :class="{ 'hfl-empty-mark': !row.hint }"
                >{{ row.hint || '—' }}</span>
              </template>
            </ElTableColumn>
          </ElTable>
        </section>
        <ElButton
          v-if="batchAssociationError"
          size="small"
          @click="retryBatchAssociationCheck"
        >
          {{ t('common.retry') }}
        </ElButton>
      </template>
    </DangerConfirmDialog>
  </ModulePage>
</template>

<style scoped>
.hfl-ops-channel-detail-drawer__loading,
.hfl-ops-channel-detail-drawer__error {
  padding: 12px 14px;
  border-radius: 10px;
  font-size: 13px;
}

.hfl-ops-channel-detail-drawer__loading {
  background: rgba(148, 163, 184, 0.12);
  color: rgb(71 85 105);
}

.hfl-ops-channel-detail-drawer__error {
  background: rgba(239, 68, 68, 0.08);
  color: rgb(185 28 28);
}

.hfl-ops-channel-detail-drawer__error-text {
  word-break: break-word;
}

.notification-batch-impact {
  border-radius: 8px;
  background: var(--el-fill-color-blank, #fafafa);
  overflow: hidden;
}

.notification-batch-impact__heading {
  padding: 10px 12px 8px;
  background: var(--el-fill-color-blank, #fafafa);
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary, #4b5563);
}

.notification-batch-impact__table {
  border-right: 0;
  border-left: 0;
}

.notification-batch-impact__hint {
  min-width: 0;
  overflow: hidden;
  color: var(--el-text-color-secondary, #6b7280);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}



.hfl-ops-channel-detail-drawer__event-title {
  font-weight: 500;
  color: rgb(15 23 42);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hfl-ops-channel-detail-drawer__event-meta {
  font-size: 11px;
  color: rgb(100 116 139);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hfl-ops-channel-detail-drawer__error-msg {
  margin-top: 4px;
  font-size: 11px;
  color: rgb(185 28 28);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

</style>
