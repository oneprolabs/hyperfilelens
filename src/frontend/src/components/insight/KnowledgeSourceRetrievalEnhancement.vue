<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { LensIngestPolicy } from '../../lib/lensApi'
import { DEFAULT_LENS_INGEST_POLICY, normalizeLensIngestPolicy } from '../../lib/knowledgeSourceIngestPolicy'
import HflHelpTip from '../HflHelpTip.vue'

type RetrievalSection = 'all' | 'document' | 'images' | 'limits'

const props = withDefaults(
  defineProps<{
    section?: RetrievalSection
    /** Show the top step description (usually only once on the first block). */
    showLead?: boolean
    /**
     * Compact layout for detail drawers / parent-titled cards:
     * hide section intros and move long hints into help tips.
     */
    compact?: boolean
  }>(),
  {
    section: 'all',
    showLead: false,
    compact: false,
  },
)

const model = defineModel<LensIngestPolicy>({ required: true })

const { t } = useI18n()
const pdfAdvancedOpen = ref(false)

const policy = computed({
  get: () => normalizeLensIngestPolicy(model.value),
  set: (value: LensIngestPolicy) => {
    model.value = normalizeLensIngestPolicy(value)
  },
})

const showDocumentBlock = computed(() => props.section === 'all' || props.section === 'document')
const showImagesBlock = computed(() => props.section === 'all' || props.section === 'images')
const showLimitsBlock = computed(() => props.section === 'all' || props.section === 'limits')
const showGroupTitles = computed(() => props.section === 'all')
const showSectionIntro = computed(() => !props.compact)

function hasPdfAdvancedCustomization(value: LensIngestPolicy): boolean {
  const base = DEFAULT_LENS_INGEST_POLICY
  return (
    value.pdf_extract_images !== base.pdf_extract_images
    || value.pdf_extract_images_on_text_pages !== base.pdf_extract_images_on_text_pages
    || value.pdf_render_scanned_pages !== base.pdf_render_scanned_pages
    || value.pdf_max_pages !== base.pdf_max_pages
    || value.pdf_max_images_per_page !== base.pdf_max_images_per_page
    || value.pdf_render_dpi !== base.pdf_render_dpi
    || value.pdf_min_text_chars !== base.pdf_min_text_chars
    || value.pdf_min_image_area_ratio !== base.pdf_min_image_area_ratio
  )
}

function patchPolicy(patch: Partial<LensIngestPolicy>) {
  policy.value = { ...policy.value, ...patch }
}

function setPdfAdvancedOpen(open: boolean) {
  pdfAdvancedOpen.value = open
}

watch(
  () => policy.value.document,
  (enabled) => {
    if (!enabled && policy.value.embedded_image) {
      patchPolicy({ embedded_image: false })
    }
  },
)

watch(
  () => policy.value.embedded_image,
  (enabled) => {
    if (!enabled) {
      pdfAdvancedOpen.value = false
    }
  },
)

onMounted(() => {
  model.value = normalizeLensIngestPolicy(model.value)
  if (hasPdfAdvancedCustomization(model.value)) {
    pdfAdvancedOpen.value = true
  }
})
</script>

<template>
  <div
    class="ks-retrieval-stack"
    :class="{ 'ks-retrieval-stack--compact': compact }"
  >
    <p
      v-if="showLead && showDocumentBlock"
      class="ks-retrieval-lead"
    >
      {{ t('insight.kb.retrieval.stepDesc') }}
    </p>

    <template v-if="showDocumentBlock">
      <h4
        v-if="showGroupTitles"
        class="ks-retrieval-group-head"
      >
        {{ t('insight.kb.retrieval.documentContentTitle') }}
      </h4>
      <p
        v-if="showSectionIntro"
        class="ks-retrieval-section-intro"
      >
        {{ t('insight.kb.retrieval.documentContentHint') }}
      </p>

      <div class="ks-retrieval-option">
        <div class="ks-retrieval-option__line">
          <ElCheckbox
            :model-value="policy.document"
            @update:model-value="(value) => patchPolicy({ document: Boolean(value) })"
          >
            <span class="ks-retrieval-option__label">
              {{ t('insight.kb.retrieval.convertDocuments') }}
              <HflHelpTip :content="t('insight.kb.retrieval.convertDocumentsHint')" />
            </span>
          </ElCheckbox>
        </div>
      </div>

      <div
        v-if="policy.document"
        class="ks-retrieval-nest"
      >
        <div class="ks-retrieval-option">
          <div class="ks-retrieval-option__line">
            <ElCheckbox
              :model-value="policy.embedded_image"
              :disabled="!policy.document"
              @update:model-value="(value) => patchPolicy({ embedded_image: Boolean(value) })"
            >
              <span class="ks-retrieval-option__label">
                {{ t('insight.kb.retrieval.convertEmbeddedImages') }}
                <HflHelpTip :content="t('insight.kb.retrieval.convertEmbeddedImagesHint')" />
              </span>
            </ElCheckbox>
          </div>
        </div>

        <div
          v-if="policy.embedded_image"
          class="ks-retrieval-nest ks-retrieval-nest--panel"
        >
          <div class="ks-retrieval-option">
            <div class="ks-retrieval-option__line">
              <ElCheckbox
                :model-value="pdfAdvancedOpen"
                @update:model-value="(value) => setPdfAdvancedOpen(Boolean(value))"
              >
                <span class="ks-retrieval-option__label">
                  {{ t('insight.kb.retrieval.pdfAdvancedTitle') }}
                  <HflHelpTip :content="t('insight.kb.retrieval.pdfAdvancedHint')" />
                </span>
              </ElCheckbox>
            </div>
          </div>

          <div
            v-if="pdfAdvancedOpen"
            class="ks-retrieval-advanced"
          >
            <div class="ks-retrieval-option">
              <div class="ks-retrieval-option__line">
                <ElCheckbox
                  :model-value="policy.pdf_extract_images"
                  @update:model-value="(value) => patchPolicy({ pdf_extract_images: Boolean(value) })"
                >
                  <span class="ks-retrieval-option__label">
                    {{ t('insight.kb.retrieval.pdfExtractImages') }}
                    <HflHelpTip :content="t('insight.kb.retrieval.pdfExtractImagesHint')" />
                  </span>
                </ElCheckbox>
              </div>
            </div>

            <div class="ks-retrieval-option">
              <div class="ks-retrieval-option__line">
                <ElCheckbox
                  :model-value="policy.pdf_render_scanned_pages"
                  @update:model-value="(value) => patchPolicy({ pdf_render_scanned_pages: Boolean(value) })"
                >
                  <span class="ks-retrieval-option__label">
                    {{ t('insight.kb.retrieval.pdfRenderScannedPages') }}
                    <HflHelpTip :content="t('insight.kb.retrieval.pdfRenderScannedPagesHint')" />
                  </span>
                </ElCheckbox>
              </div>
            </div>

            <div class="ks-retrieval-option">
              <div class="ks-retrieval-option__line">
                <ElCheckbox
                  :model-value="policy.pdf_extract_images_on_text_pages"
                  @update:model-value="(value) => patchPolicy({ pdf_extract_images_on_text_pages: Boolean(value) })"
                >
                  <span class="ks-retrieval-option__label">
                    {{ t('insight.kb.retrieval.pdfExtractImagesOnTextPages') }}
                    <HflHelpTip :content="t('insight.kb.retrieval.pdfExtractImagesOnTextPagesHint')" />
                  </span>
                </ElCheckbox>
              </div>
            </div>

            <div class="ks-retrieval-limits-grid">
              <div class="ks-retrieval-field">
                <label class="ks-retrieval-field__label">
                  {{ t('insight.kb.retrieval.pdfMaxPages') }}
                  <HflHelpTip :content="t('insight.kb.retrieval.pdfMaxPagesTooltip')" />
                </label>
                <ElInputNumber
                  :model-value="policy.pdf_max_pages"
                  :min="1"
                  :max="200"
                  controls-position="right"
                  class="ks-retrieval-field__control"
                  @update:model-value="(value) => patchPolicy({ pdf_max_pages: Number(value) || 1 })"
                />
              </div>
              <div class="ks-retrieval-field">
                <label class="ks-retrieval-field__label">
                  {{ t('insight.kb.retrieval.pdfMaxImagesPerPage') }}
                  <HflHelpTip :content="t('insight.kb.retrieval.pdfMaxImagesPerPageTooltip')" />
                </label>
                <ElInputNumber
                  :model-value="policy.pdf_max_images_per_page"
                  :min="1"
                  :max="10"
                  controls-position="right"
                  class="ks-retrieval-field__control"
                  @update:model-value="(value) => patchPolicy({ pdf_max_images_per_page: Number(value) || 1 })"
                />
              </div>
              <div class="ks-retrieval-field">
                <label class="ks-retrieval-field__label">
                  {{ t('insight.kb.retrieval.pdfRenderDpi') }}
                  <HflHelpTip :content="t('insight.kb.retrieval.pdfRenderDpiTooltip')" />
                </label>
                <ElInputNumber
                  :model-value="policy.pdf_render_dpi"
                  :min="1"
                  :max="300"
                  controls-position="right"
                  class="ks-retrieval-field__control"
                  @update:model-value="(value) => patchPolicy({ pdf_render_dpi: Number(value) || 1 })"
                />
              </div>
              <div class="ks-retrieval-field">
                <label class="ks-retrieval-field__label">
                  {{ t('insight.kb.retrieval.pdfMinTextChars') }}
                  <HflHelpTip :content="t('insight.kb.retrieval.pdfMinTextCharsTooltip')" />
                </label>
                <ElInputNumber
                  :model-value="policy.pdf_min_text_chars"
                  :min="1"
                  :max="10000"
                  controls-position="right"
                  class="ks-retrieval-field__control"
                  @update:model-value="(value) => patchPolicy({ pdf_min_text_chars: Number(value) || 1 })"
                />
              </div>
              <div class="ks-retrieval-field ks-retrieval-field--full">
                <label class="ks-retrieval-field__label">
                  {{ t('insight.kb.retrieval.pdfMinImageAreaRatio') }}
                  <HflHelpTip :content="t('insight.kb.retrieval.pdfMinImageAreaRatioTooltip')" />
                </label>
                <ElInputNumber
                  :model-value="policy.pdf_min_image_area_ratio"
                  :min="0.01"
                  :max="1"
                  :step="0.01"
                  controls-position="right"
                  class="ks-retrieval-field__control"
                  @update:model-value="(value) => patchPolicy({ pdf_min_image_area_ratio: Number(value) || 0.08 })"
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <template v-if="showImagesBlock">
      <h4
        v-if="showGroupTitles"
        class="ks-retrieval-group-head"
      >
        {{ t('insight.kb.retrieval.standaloneImagesTitle') }}
      </h4>
      <p
        v-if="showSectionIntro"
        class="ks-retrieval-section-intro"
      >
        {{ t('insight.kb.retrieval.standaloneImagesHint') }}
      </p>

      <div class="ks-retrieval-option">
        <div class="ks-retrieval-option__line">
          <ElCheckbox
            :model-value="policy.image"
            @update:model-value="(value) => patchPolicy({ image: Boolean(value) })"
          >
            <span class="ks-retrieval-option__label">
              {{ t('insight.kb.retrieval.convertImages') }}
              <HflHelpTip :content="t('insight.kb.retrieval.convertImagesHint')" />
            </span>
          </ElCheckbox>
        </div>
        <p
          v-if="policy.image"
          class="ks-retrieval-option__note"
        >
          {{ t('insight.kb.retrieval.adminMultimodalModelHint') }}
        </p>
      </div>
    </template>

    <template v-if="showLimitsBlock">
      <h4
        v-if="showGroupTitles"
        class="ks-retrieval-group-head"
      >
        {{ t('insight.kb.retrieval.globalConversionLimitsTitle') }}
      </h4>
      <p
        v-if="showSectionIntro"
        class="ks-retrieval-section-intro"
      >
        {{ t('insight.kb.retrieval.globalConversionLimitsHint') }}
      </p>

      <div class="ks-retrieval-limits-grid">
        <div class="ks-retrieval-field">
          <label class="ks-retrieval-field__label">
            {{ t('insight.kb.retrieval.maxFileSizeMb') }}
            <HflHelpTip :content="t('insight.kb.retrieval.maxFileSizeTooltip')" />
          </label>
          <ElInputNumber
            :model-value="policy.max_file_size_mb"
            :min="1"
            :max="256"
            controls-position="right"
            class="ks-retrieval-field__control"
            @update:model-value="(value) => patchPolicy({ max_file_size_mb: Number(value) || 1 })"
          />
        </div>
        <div class="ks-retrieval-field">
          <label class="ks-retrieval-field__label">
            {{ t('insight.kb.retrieval.maxImages') }}
            <HflHelpTip :content="t('insight.kb.retrieval.maxImagesTooltip')" />
          </label>
          <ElInputNumber
            :model-value="policy.max_images"
            :min="1"
            :max="500"
            controls-position="right"
            class="ks-retrieval-field__control"
            @update:model-value="(value) => patchPolicy({ max_images: Number(value) || 1 })"
          />
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.ks-retrieval-stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ks-retrieval-stack--compact {
  gap: 10px;
}

.ks-retrieval-lead,
.ks-retrieval-section-intro {
  margin: 0;
  color: var(--color-text-secondary, #64748b);
  font-size: 12px;
  line-height: 1.5;
}

.ks-retrieval-group-head {
  margin: 0;
  color: var(--color-text-title, #0f172a);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
}

.ks-retrieval-group-head:not(:first-child) {
  margin-top: 8px;
  padding-top: 16px;
  border-top: 1px solid var(--color-border, rgba(226, 232, 240, 0.95));
}

.ks-retrieval-option {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ks-retrieval-option__line {
  min-width: 0;
}

.ks-retrieval-option__label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text-title, #0f172a);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
}

.ks-retrieval-option__note {
  margin: 0 0 0 22px;
  color: var(--color-text-tertiary, #94a3b8);
  font-size: 12px;
  line-height: 1.45;
}

.ks-retrieval-nest {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-left: 4px;
  padding: 10px 12px;
  border: 1px solid var(--color-border, rgba(226, 232, 240, 0.95));
  border-radius: 10px;
  background: var(--color-fill-subtle, rgb(248 250 252));
}

.ks-retrieval-nest--panel {
  margin-left: 0;
}

.ks-retrieval-advanced {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 4px;
}

.ks-retrieval-limits-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.ks-retrieval-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.ks-retrieval-field--full {
  grid-column: 1 / -1;
}

.ks-retrieval-field__label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text-secondary, #475569);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.3;
}

.ks-retrieval-field__control {
  width: 100%;
}

.ks-retrieval-stack :deep(.el-checkbox) {
  height: auto;
  align-items: flex-start;
  white-space: normal;
}

.ks-retrieval-stack :deep(.el-checkbox__label) {
  white-space: normal;
  line-height: 1.4;
}

@media (max-width: 720px) {
  .ks-retrieval-limits-grid {
    grid-template-columns: 1fr;
  }
}

html[data-theme='dark'] .ks-retrieval-group-head {
  color: #e5e7eb;
}

html[data-theme='dark'] .ks-retrieval-option__label {
  color: #e5e7eb;
}

html[data-theme='dark'] .ks-retrieval-nest {
  border-color: #3b4658;
  background: #1b202a;
}

html[data-theme='dark'] .ks-retrieval-group-head:not(:first-child) {
  border-top-color: #3b4658;
}
</style>
