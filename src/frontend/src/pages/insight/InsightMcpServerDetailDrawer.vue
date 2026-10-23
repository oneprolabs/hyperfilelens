<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../lib/api'
import { DETAIL_EMPTY } from '../../lib/nodeInventoryDisplay'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import HflBooleanStatusTag from '../../components/HflBooleanStatusTag.vue'
import HflTypeLabel from '../../components/HflTypeLabel.vue'
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
              {{ t('insight.mcpServers.sectionGeneral') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.mcpServers.colName') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(activeRow.name)"
                >
                  {{ displayValue(activeRow.name) }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.mcpServers.colStatus') }}</span>
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
                <span class="hfl-detail-row__label">{{ t('insight.mcpServers.colTransport') }}</span>
                <span class="hfl-detail-row__value">
                  <HflTypeLabel :label="transportLabel(activeRow.transport)" />
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.mcpServers.colEndpoint') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(activeRow.endpoint, true)"
                  :title="activeRow.endpoint || undefined"
                >
                  {{ displayValue(activeRow.endpoint) }}
                </span>
              </div>
            </div>
          </section>

          <section
            v-if="configEntries.length"
            class="hfl-detail-section"
          >
            <h4 class="hfl-detail-section__title">
              {{ t('insight.mcpServers.sectionConfig') }}
            </h4>
            <div class="hfl-detail-grid">
              <div
                v-for="entry in configEntries"
                :key="entry.key"
                class="hfl-detail-row"
              >
                <span class="hfl-detail-row__label">{{ entry.key }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(entry.value || DETAIL_EMPTY, true)"
                  :title="entry.value || undefined"
                >
                  {{ entry.value || DETAIL_EMPTY }}
                </span>
              </div>
            </div>
          </section>
        </div>
      </template>
    </div>

  </ElDrawer>
</template>
