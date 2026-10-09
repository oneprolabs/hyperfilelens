<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import { ExternalLink } from 'lucide-vue-next'
import SourceTagBadge from './SourceTagBadge.vue'
import type { SourceTag } from '../lib/sourceApi'
import '../styles/source-tag-ui.css'

const props = defineProps<{
  sources: Array<{ id: string; name: string }>
  options: SourceTag[]
  operation: 'add' | 'remove'
  affected: number
  loading: boolean
  saving: boolean
  wizard?: boolean
}>()
const ids = defineModel<number[]>('ids', { required: true })
const search = defineModel<string>('search', { required: true })
const { t } = useI18n()
const expanded = ref(false)
watch(() => props.sources, () => { expanded.value = false })
const impact = computed(() => {
  if (!ids.value.length) return t(props.operation === 'add'
    ? 'protection.tags.selectTagsToBind' : 'protection.tags.selectTagsToUnbind')
  if (!props.affected) return t('protection.tags.noAssociationChanges')
  return t(props.operation === 'add' ? 'protection.tags.bindSummary' : 'protection.tags.unbindSummary', {
    tags: ids.value.length, associations: props.affected,
  })
})
</script>

<template>
  <div class="source-tag-action">
    <div class="source-tag-action__targets">
      <span>{{ t('protection.tags.selectedSources', { n: sources.length }) }}</span>
      <ElButton
        text
        size="small"
        :aria-expanded="expanded"
        @click="expanded = !expanded"
      >
        {{ t(expanded ? 'protection.tags.hideSelectedSources' : 'protection.tags.viewSelectedSources') }}
      </ElButton>
    </div>
    <ul
      v-if="expanded"
      class="source-tag-bulk-targets"
    >
      <li
        v-for="source in sources"
        :key="source.id"
      >
        {{ source.name }}
      </li>
    </ul>
    <p class="source-tag-action__manage-hint">
      <i18n-t
        :keypath="operation === 'add' ? 'protection.tags.bindManageHint' : 'protection.tags.unbindManageHint'"
        tag="span"
        scope="global"
      >
        <template #link>
          <RouterLink
            to="/protection/source-tags"
            target="_blank"
            rel="noopener noreferrer"
            class="source-tag-wizard-manage-link"
          >
            <span v-text="t('protection.tags.managementLink')" />
            <ExternalLink
              :size="13"
              aria-hidden="true"
            />
          </RouterLink>
        </template>
      </i18n-t>
    </p>
    <ElInput
      v-model="search"
      clearable
      :aria-label="t('protection.tags.searchTags')"
      :placeholder="t('protection.tags.searchTags')"
      :disabled="loading || saving"
    />
    <div
      v-loading="loading"
      class="source-tag-picker source-tag-picker--action"
      :aria-busy="loading"
    >
      <ElCheckboxGroup
        v-model="ids"
        :disabled="loading || saving"
        :max="50"
      >
        <div
          v-for="tag in options"
          :key="tag.id"
          class="source-tag-picker__row"
        >
          <ElCheckbox
            :value="tag.id"
            :aria-label="tag.name"
            :disabled="loading || saving || (ids.length >= 50 && !ids.includes(tag.id))"
          />
          <div class="source-tag-picker__content">
            <SourceTagBadge
              :tag="tag"
              :interactive="false"
              :show-icon="false"
            />
            <span
              v-if="tag.description"
              class="source-tag-picker__description"
            >{{ tag.description }}</span>
          </div>
        </div>
      </ElCheckboxGroup>
      <ElEmpty
        v-if="!loading && !options.length"
        :description="t('protection.tags.noMatchingTags')"
        :image-size="48"
      />
    </div>
    <p
      class="source-tag-bulk-impact"
      aria-live="polite"
    >
      {{ impact }}
    </p>
    <div class="source-tag-action__notes">
      <p v-if="operation === 'remove'">
        {{ t('protection.tags.unbindPreservesHint') }}
      </p>
      <p v-if="wizard">
        {{ t('protection.tags.wizardSaveHint') }}
      </p>
    </div>
  </div>
</template>
