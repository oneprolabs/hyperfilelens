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
    showLead?: boolean
  }>(),
  {
    section: 'all',
    showLead: false,
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
  <div class="ks-retrieval-stack">
    <p
      v-if="showLead && showDocumentBlock"
      class="fullscreen-form-field__hint ks-retrieval-lead"
    >
      {{ t('insight.kb.retrieval.stepDesc') }}
    </p>

    <!-- Document Content -->
    <template v-if="showDocumentBlock">
      <h4
        v-if="showGroupTitles"
        class="fullscreen-form-section__subtitle ks-retrieval-group-head"
      >
        {{ t('insight.kb.retrieval.documentContentTitle') }}
      </h4>
      <p class="fullscreen-form-field__hint ks-retrieval-section-intro">
        {{ t('insight.kb.retrieval.documentContentHint') }}
      </p>

      <div class="fullscreen-form-field">
        <div class="fullscreen-form-checkline">
          <ElCheckbox
            :model-value="policy.document"
            @update:model-value="(value) => patchPolicy({ document: Boolean(value) })"
          />
          <span class="ks-retrieval-checkline__text">{{ t('insight.kb.retrieval.convertDocuments') }}</span>
        </div>
        <p class="fullscreen-form-field__hint">
          {{ t('insight.kb.retrieval.convertDocumentsHint') }}
        </p>
      </div>

      <div
        v-if="policy.document"
        class="ks-retrieval-nest"
      >
        <div class="fullscreen-form-field">
          <div
            class="fullscreen-form-checkline"
            :class="{ 'ks-retrieval-checkline--disabled': !policy.document }"
          >
            <ElCheckbox
              :model-value="policy.embedded_image"
              :disabled="!policy.document"
              @update:model-value="(value) => patchPolicy({ embedded_image: Boolean(value) })"
            />
            <span class="ks-retrieval-checkline__text">{{ t('insight.kb.retrieval.convertEmbeddedImages') }}</span>
          </div>
          <p class="fullscreen-form-field__hint">
            {{ t('insight.kb.retrieval.convertEmbeddedImagesHint') }}
          </p>
        </div>

        <div
          v-if="policy.embedded_image"
          class="ks-retrieval-nest ks-retrieval-nest--inner"
        >
          <div class="fullscreen-form-field">
            <div class="fullscreen-form-checkline">
              <ElCheckbox
                :model-value="pdfAdvancedOpen"
                @update:model-value="(value) => setPdfAdvancedOpen(Boolean(value))"
              />
              <span class="ks-retrieval-checkline__text">{{ t('insight.kb.retrieval.pdfAdvancedTitle') }}</span>
            </div>
            <p class="fullscreen-form-field__hint">
              {{ t('insight.kb.retrieval.pdfAdvancedHint') }}
            </p>
          </div>

          <div
            v-if="pdfAdvancedOpen"
            class="ks-retrieval-nest ks-retrieval-nest--inner"
          >
            <div class="fullscreen-form-field">
              <div class="fullscreen-form-checkline">
                <ElCheckbox
                  :model-value="policy.pdf_extract_images"
                  @update:model-value="(value) => patchPolicy({ pdf_extract_images: Boolean(value) })"
                />
                <span class="ks-retrieval-checkline__text">{{ t('insight.kb.retrieval.pdfExtractImages') }}</span>
              </div>
              <p class="fullscreen-form-field__hint">
                {{ t('insight.kb.retrieval.pdfExtractImagesHint') }}
              </p>
            </div>

            <div class="fullscreen-form-field">
              <div class="fullscreen-form-checkline">
                <ElCheckbox
                  :model-value="policy.pdf_render_scanned_pages"
                  @update:model-value="(value) => patchPolicy({ pdf_render_scanned_pages: Boolean(value) })"
                />
                <span class="ks-retrieval-checkline__text">{{ t('insight.kb.retrieval.pdfRenderScannedPages') }}</span>
              </div>
              <p class="fullscreen-form-field__hint ks-retrieval-hint--warn">
                {{ t('insight.kb.retrieval.pdfRenderScannedPagesHint') }}
              </p>
            </div>

            <div class="fullscreen-form-field">
              <div class="fullscreen-form-checkline">
                <ElCheckbox
                  :model-value="policy.pdf_extract_images_on_text_pages"
                  @update:model-value="(value) => patchPolicy({ pdf_extract_images_on_text_pages: Boolean(value) })"
                />
                <span class="ks-retrieval-checkline__text">{{ t('insight.kb.retrieval.pdfExtractImagesOnTextPages') }}</span>
              </div>
              <p class="fullscreen-form-field__hint ks-retrieval-hint--warn">
                {{ t('insight.kb.retrieval.pdfExtractImagesOnTextPagesHint') }}
              </p>
            </div>

            <div class="fullscreen-form-grid">
              <div class="fullscreen-form-field">
                <label class="fullscreen-form-field__label">
                  {{ t('insight.kb.retrieval.pdfMaxPages') }}
                  <HflHelpTip :content="t('insight.kb.retrieval.pdfMaxPagesTooltip')" />
                </label>
                <ElInputNumber
                  :model-value="policy.pdf_max_pages"
                  :min="1"
                  :max="200"
                  controls-position="right"
                  style="width: 100%"
                  @update:model-value="(value) => patchPolicy({ pdf_max_pages: Number(value) || 1 })"
                />
              </div>
              <div class="fullscreen-form-field">
                <label class="fullscreen-form-field__label">
                  {{ t('insight.kb.retrieval.pdfMaxImagesPerPage') }}
                  <HflHelpTip :content="t('insight.kb.retrieval.pdfMaxImagesPerPageTooltip')" />
                </label>
                <ElInputNumber
                  :model-value="policy.pdf_max_images_per_page"
                  :min="1"
                  :max="10"
                  controls-position="right"
                  style="width: 100%"
                  @update:model-value="(value) => patchPolicy({ pdf_max_images_per_page: Number(value) || 1 })"
                />
              </div>
              <div class="fullscreen-form-field">
                <label class="fullscreen-form-field__label">
                  {{ t('insight.kb.retrieval.pdfRenderDpi') }}
                  <HflHelpTip :content="t('insight.kb.retrieval.pdfRenderDpiTooltip')" />
                </label>
                <ElInputNumber
                  :model-value="policy.pdf_render_dpi"
                  :min="1"
                  :max="300"
                  controls-position="right"
                  style="width: 100%"
                  @update:model-value="(value) => patchPolicy({ pdf_render_dpi: Number(value) || 1 })"
                />
              </div>
              <div class="fullscreen-form-field">
                <label class="fullscreen-form-field__label">
                  {{ t('insight.kb.retrieval.pdfMinTextChars') }}
                  <HflHelpTip :content="t('insight.kb.retrieval.pdfMinTextCharsTooltip')" />
                </label>
                <ElInputNumber
                  :model-value="policy.pdf_min_text_chars"
                  :min="1"
                  :max="10000"
                  controls-position="right"
                  style="width: 100%"
                  @update:model-value="(value) => patchPolicy({ pdf_min_text_chars: Number(value) || 1 })"
                />
              </div>
              <div class="fullscreen-form-field fullscreen-form-field--full">
                <label class="fullscreen-form-field__label">
                  {{ t('insight.kb.retrieval.pdfMinImageAreaRatio') }}
                  <HflHelpTip :content="t('insight.kb.retrieval.pdfMinImageAreaRatioTooltip')" />
                </label>
                <ElInputNumber
                  :model-value="policy.pdf_min_image_area_ratio"
                  :min="0.01"
                  :max="1"
                  :step="0.01"
                  controls-position="right"
                  style="width: 100%"
                  @update:model-value="(value) => patchPolicy({ pdf_min_image_area_ratio: Number(value) || 0.08 })"
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- Standalone Images -->
    <template v-if="showImagesBlock">
      <h4
        v-if="showGroupTitles"
        class="fullscreen-form-section__subtitle ks-retrieval-group-head"
      >
        {{ t('insight.kb.retrieval.standaloneImagesTitle') }}
      </h4>
      <p class="fullscreen-form-field__hint ks-retrieval-section-intro">
        {{ t('insight.kb.retrieval.standaloneImagesHint') }}
      </p>

      <div class="fullscreen-form-field">
        <div class="fullscreen-form-checkline">
          <ElCheckbox
            :model-value="policy.image"
            @update:model-value="(value) => patchPolicy({ image: Boolean(value) })"
          />
          <span class="ks-retrieval-checkline__text">{{ t('insight.kb.retrieval.convertImages') }}</span>
        </div>
        <p class="fullscreen-form-field__hint">
          {{ t('insight.kb.retrieval.convertImagesHint') }}
        </p>
      </div>

      <p
        v-if="policy.image"
        class="fullscreen-form-field__hint"
      >
        {{ t('insight.kb.retrieval.adminMultimodalModelHint') }}
      </p>
    </template>

    <!-- Global Conversion Limits -->
    <template v-if="showLimitsBlock">
      <h4
        v-if="showGroupTitles"
        class="fullscreen-form-section__subtitle ks-retrieval-group-head"
      >
        {{ t('insight.kb.retrieval.globalConversionLimitsTitle') }}
      </h4>
      <p class="fullscreen-form-field__hint ks-retrieval-section-intro">
        {{ t('insight.kb.retrieval.globalConversionLimitsHint') }}
      </p>

      <div class="fullscreen-form-grid ks-retrieval-limits-grid">
        <div class="fullscreen-form-field">
          <label class="fullscreen-form-field__label">
            {{ t('insight.kb.retrieval.maxFileSizeMb') }}
            <HflHelpTip :content="t('insight.kb.retrieval.maxFileSizeTooltip')" />
          </label>
          <ElInputNumber
            :model-value="policy.max_file_size_mb"
            :min="1"
            :max="256"
            controls-position="right"
            style="width: 100%"
            @update:model-value="(value) => patchPolicy({ max_file_size_mb: Number(value) || 1 })"
          />
        </div>
        <div class="fullscreen-form-field">
          <label class="fullscreen-form-field__label">
            {{ t('insight.kb.retrieval.maxImages') }}
            <HflHelpTip :content="t('insight.kb.retrieval.maxImagesTooltip')" />
          </label>
          <ElInputNumber
            :model-value="policy.max_images"
            :min="1"
            :max="500"
            controls-position="right"
            style="width: 100%"
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
  gap: 14px;
}

.ks-retrieval-lead {
  margin-bottom: 2px;
}

.ks-retrieval-group-head {
  margin: 0;
}

.ks-retrieval-group-head:not(:first-child) {
  margin-top: 6px;
  padding-top: 18px;
  border-top: 1px solid rgba(226, 232, 240, 0.95);
}

.ks-retrieval-section-intro {
  margin-top: -6px;
}

.ks-retrieval-stack :deep(.fullscreen-form-checkline) {
  gap: 10px;
  border-color: var(--color-border, #e5e6eb);
  background: var(--color-card-bg, #fff);
}

.ks-retrieval-checkline__text {
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  color: var(--color-text-title, #1c1c26);
}

.ks-retrieval-checkline--disabled {
  opacity: 0.55;
}

.ks-retrieval-nest {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-left: 6px;
  padding-left: 14px;
  border-left: 2px solid var(--color-border, rgba(226, 232, 240, 0.95));
}

.ks-retrieval-nest--inner {
  margin-left: 0;
  padding-left: 12px;
}

.ks-retrieval-hint--warn {
  color: var(--color-warning, #b45309);
}

html[data-theme='dark'] .ks-retrieval-stack :deep(.fullscreen-form-checkline) {
  border-color: #3b4658;
  background: #1b202a;
}

html[data-theme='dark'] .ks-retrieval-checkline__text {
  color: #e5e7eb;
}

html[data-theme='dark'] .ks-retrieval-nest {
  border-left-color: #3b4658;
}

html[data-theme='dark'] .ks-retrieval-group-head:not(:first-child) {
  border-top-color: rgba(59, 70, 88, 0.95);
}

html[data-theme='hybrid'] .ks-retrieval-stack :deep(.fullscreen-form-checkline) {
  border-color: var(--color-border, #e5e6eb);
  background: var(--color-card-bg, #fff);
}

html[data-theme='hybrid'] .ks-retrieval-checkline__text {
  color: var(--color-text-title, #1c1c26);
}
</style>
