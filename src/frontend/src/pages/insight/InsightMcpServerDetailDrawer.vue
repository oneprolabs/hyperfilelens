<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../lib/api'
import { DETAIL_EMPTY } from '../../lib/nodeInventoryDisplay'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import HflDetailDrawerFooter from '../../components/HflDetailDrawerFooter.vue'
import HflBooleanStatusTag from '../../components/HflBooleanStatusTag.vue'
import { fetchLensMcpServer, type LensMcpServer } from '../../lib/lensApi'

const props = defineProps<{
  open: boolean
  row: LensMcpServer | null
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  edit: [uuid: string]
}>()

const { t } = useI18n()
const loading = ref(false)
const detail = ref<LensMcpServer | null>(null)
const { drawerSize, updateDrawerWidth, bindDrawerResize, unbindDrawerResize } = useResponsiveDrawerWidth()

const drawerOpen = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
})

const activeRow = computed(() => detail.value ?? props.row)

const configEntries = computed(() => {
  const config = activeRow.value?.config
  if (!config || typeof config !== 'object') return []
  return Object.entries(config)
    .filter(([key]) => key.trim())
    .map(([key, value]) => ({ key, value: value == null ? '' : String(value) }))
})

function transportLabel(transport: string) {
  if (transport === 'stdio') return 'STDIO'
  if (transport === 'url') return 'URL'
  return transport.toUpperCase()
}

function transportTagType(transport: string): 'primary' | 'info' {
  return transport === 'stdio' ? 'info' : 'primary'
}

function detailValueClass(text: string | null | undefined, mono = false) {
  const value = text == null || text === '' ? DETAIL_EMPTY : String(text)
  const empty = value === DETAIL_EMPTY
  return {
    'hfl-detail-row__empty': empty,
    'hfl-detail-row__value--mono': mono && !empty,
  }
}

function displayValue(text: string | null | undefined) {
  if (text == null || text === '') return DETAIL_EMPTY
  return text
}

async function loadDetail() {
  if (!props.row?.uuid) {
    detail.value = null
    return
  }
  loading.value = true
  try {
    detail.value = await fetchLensMcpServer(props.row.uuid)
  } catch (err) {
    detail.value = props.row
    ElMessage.error(apiErrorMessage(err, t('errors.generic.loadFailed')))
  } finally {
    loading.value = false
  }
}

function onEdit() {
  const uuid = activeRow.value?.uuid || props.row?.uuid
  if (!uuid) return
  drawerOpen.value = false
  emit('edit', uuid)
}

function onDrawerOpened() {
  bindDrawerResize()
}

function onDrawerClosed() {
  unbindDrawerResize()
  detail.value = null
}

watch(
  () => [props.open, props.row?.uuid] as const,
  ([isOpen]) => {
    if (isOpen) void loadDetail()
  },
)

watch(drawerOpen, (isOpen) => {
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
    v-model="drawerOpen"
    direction="rtl"
    destroy-on-close
    :modal="true"
    :size="drawerSize"
    class="hfl-detail-drawer insight-detail-drawer"
    @opened="onDrawerOpened"
    @closed="onDrawerClosed"
  >
    <template #header>
      <span class="hfl-detail-drawer__title">{{ activeRow?.name || DETAIL_EMPTY }}</span>
    </template>

    <div
      v-loading="loading"
      class="hfl-detail-drawer__body"
    >
      <template v-if="activeRow">
        <div class="hfl-detail-sections">
          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('insight.mcpServers.detailSectionConnection') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.mcpServers.fieldName') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(activeRow.name)"
                >
                  {{ displayValue(activeRow.name) }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.mcpServers.fieldEnabled') }}</span>
                <span class="hfl-detail-row__value">
                  <HflBooleanStatusTag
                    :value="activeRow.enabled !== false"
                    :label="activeRow.enabled !== false
                      ? t('insight.mcpServers.statusActive')
                      : t('insight.mcpServers.statusDisabled')"
                  />
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.mcpServers.fieldTransport') }}</span>
                <span class="hfl-detail-row__value">
                  <ElTag
                    size="small"
                    :type="transportTagType(activeRow.transport)"
                    effect="plain"
                  >
                    {{ transportLabel(activeRow.transport) }}
                  </ElTag>
                </span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('insight.mcpServers.fieldEndpoint') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(activeRow.endpoint, true)"
                >
                  {{ displayValue(activeRow.endpoint) }}
                </span>
              </div>
            </div>
          </section>

          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('insight.mcpServers.fieldConfig') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('insight.mcpServers.colRuntimeSettings') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                  <div
                    v-if="configEntries.length"
                    class="create-filter-rules-preview"
                  >
                    <code
                      v-for="entry in configEntries"
                      :key="entry.key"
                      class="create-filter-rules-preview__line"
                    >{{ entry.key }}={{ entry.value || DETAIL_EMPTY }}</code>
                  </div>
                  <span
                    v-else
                    class="hfl-detail-row__empty"
                  >
                    {{ t('insight.mcpServers.runtimeSettingsNone') }}
                  </span>
                </span>
              </div>
            </div>
          </section>
        </div>
      </template>
    </div>

    <template
      v-if="activeRow"
      #footer
    >
      <HflDetailDrawerFooter
        :save-label="t('common.edit')"
        @cancel="drawerOpen = false"
        @save="onEdit"
      />
    </template>
  </ElDrawer>
</template>
