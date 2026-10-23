<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../lib/api'
import { DETAIL_EMPTY } from '../../lib/nodeInventoryDisplay'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import HflBooleanStatusTag from '../../components/HflBooleanStatusTag.vue'
import { fetchLensSkill, type LensSkill } from '../../lib/lensApi'
import {
  isWorkspaceGuideSkill,
  skillContent,
  skillDescription,
} from '../../lib/lensSkillHelpers'

const props = defineProps<{
  open: boolean
  row: LensSkill | null
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  edit: [uuid: string]
}>()

const { t } = useI18n()
const loading = ref(false)
const detail = ref<LensSkill | null>(null)
const { drawerSize, updateDrawerWidth, bindDrawerResize, unbindDrawerResize } = useResponsiveDrawerWidth()

const drawerOpen = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
})

const activeRow = computed(() => detail.value ?? props.row)

const descriptionText = computed(() => {
  if (!activeRow.value) return DETAIL_EMPTY
  return skillDescription(activeRow.value) || DETAIL_EMPTY
})

const contentText = computed(() => {
  if (!activeRow.value) return DETAIL_EMPTY
  return skillContent(activeRow.value) || DETAIL_EMPTY
})

const slugValue = computed(() => {
  const slug = activeRow.value?.slug?.trim()
  return slug || DETAIL_EMPTY
})

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
    detail.value = await fetchLensSkill(props.row.uuid)
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
              {{ t('insight.skills.sectionGeneral') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.skills.colName') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(activeRow.name)"
                >
                  {{ displayValue(activeRow.name) }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.skills.colStatus') }}</span>
                <span class="hfl-detail-row__value">
                  <HflBooleanStatusTag
                    :value="activeRow.enabled !== false"
                    :label="activeRow.enabled !== false
                      ? t('insight.skills.statusActive')
                      : t('insight.skills.statusDisabled')"
                  />
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.skills.colType') }}</span>
                <span class="hfl-detail-row__value">
                  <ElTag
                    size="small"
                    :type="isWorkspaceGuideSkill(activeRow) ? 'primary' : 'info'"
                    effect="plain"
                  >
                    {{
                      isWorkspaceGuideSkill(activeRow)
                        ? t('insight.skills.typeWorkspaceGuide')
                        : t('insight.skills.typeSkill')
                    }}
                  </ElTag>
                </span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('insight.skills.colDescription') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(descriptionText === DETAIL_EMPTY ? '' : descriptionText)"
                >
                  {{ descriptionText }}
                </span>
              </div>
            </div>
          </section>

          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('insight.skills.sectionContent') }}
            </h4>
            <pre
              v-if="contentText !== DETAIL_EMPTY"
              class="insight-skill-detail-content"
            >{{ contentText }}</pre>
            <p
              v-else
              class="insight-skill-detail-content insight-skill-detail-content--empty"
            >
              {{ DETAIL_EMPTY }}
            </p>
          </section>

          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('insight.skills.sectionTechnical') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.skills.fieldSlug') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(slugValue === DETAIL_EMPTY ? '' : slugValue, true)"
                >
                  {{ slugValue }}
                </span>
              </div>
            </div>
          </section>
        </div>
      </template>
    </div>

  </ElDrawer>
</template>

<style scoped>
.insight-skill-detail-content {
  margin: 0;
  max-height: min(42vh, 320px);
  overflow: auto;
  padding: 14px 16px;
  color: var(--el-text-color-primary);
  font: inherit;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.insight-skill-detail-content--empty {
  color: var(--el-text-color-placeholder);
}
</style>
