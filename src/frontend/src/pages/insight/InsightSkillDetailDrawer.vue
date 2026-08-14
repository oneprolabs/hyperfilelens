<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../lib/api'
import { DETAIL_EMPTY } from '../../lib/nodeInventoryDisplay'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import HflDetailDrawerFooter from '../../components/HflDetailDrawerFooter.vue'
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

function detailValueClass(text: string | null | undefined) {
  const value = text == null || text === '' ? DETAIL_EMPTY : String(text)
  return { 'hfl-detail-row__empty': value === DETAIL_EMPTY }
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
              {{ t('insight.skills.detailSectionBasics') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.skills.fieldName') }}</span>
                <span
                  class="hfl-detail-row__value"
                  :class="detailValueClass(activeRow.name)"
                >
                  {{ displayValue(activeRow.name) }}
                </span>
              </div>
              <div class="hfl-detail-row">
                <span class="hfl-detail-row__label">{{ t('insight.skills.fieldEnabled') }}</span>
                <span class="hfl-detail-row__value">
                  <HflBooleanStatusTag
                    :value="activeRow.enabled !== false"
                    :label="activeRow.enabled !== false
                      ? t('insight.skills.statusActive')
                      : t('insight.skills.statusDisabled')"
                  />
                </span>
              </div>
              <div
                v-if="isWorkspaceGuideSkill(activeRow)"
                class="hfl-detail-row"
              >
                <span class="hfl-detail-row__label">{{ t('insight.skills.colType') }}</span>
                <span class="hfl-detail-row__value">
                  <ElTag
                    size="small"
                    type="primary"
                    effect="plain"
                  >
                    {{ t('insight.skills.typeWorkspaceGuide') }}
                  </ElTag>
                </span>
              </div>
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('insight.skills.fieldDescription') }}</span>
                <span
                  class="hfl-detail-row__value hfl-detail-row__value--stacked"
                  :class="detailValueClass(descriptionText === DETAIL_EMPTY ? '' : descriptionText)"
                >
                  <span class="hfl-detail-row__text">{{ descriptionText }}</span>
                </span>
              </div>
            </div>
          </section>

          <section class="hfl-detail-section">
            <h4 class="hfl-detail-section__title">
              {{ t('insight.skills.fieldContent') }}
            </h4>
            <div class="hfl-detail-grid">
              <div class="hfl-detail-row hfl-detail-row--full">
                <span class="hfl-detail-row__label">{{ t('insight.skills.colContent') }}</span>
                <span class="hfl-detail-row__value hfl-detail-row__value--stacked">
                  <div
                    v-if="contentText !== DETAIL_EMPTY"
                    class="create-filter-rules-preview"
                  >
                    <pre class="create-filter-rules-preview__line">{{ contentText }}</pre>
                  </div>
                  <span
                    v-else
                    class="hfl-detail-row__empty"
                  >{{ DETAIL_EMPTY }}</span>
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
