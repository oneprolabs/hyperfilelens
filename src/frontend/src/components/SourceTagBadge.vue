<script setup lang="ts">
import { Tag } from 'lucide-vue-next'
import HflPopover from './HflPopover.vue'
import { sourceTagColor } from '../lib/sourceTagColor'
import type { SourceTag } from '../lib/sourceApi'
import '../styles/source-tag-ui.css'

const props = withDefaults(defineProps<{
  tag: Pick<SourceTag, 'name' | 'color' | 'description'>
  interactive?: boolean
  showNameTitle?: boolean
  showIcon?: boolean
}>(), { interactive: true, showNameTitle: true, showIcon: true })
</script>

<template>
  <HflPopover
    v-if="tag.description && interactive"
    trigger="click"
    placement="top"
    :width="280"
  >
    <template #reference>
      <button
        type="button"
        class="source-tag-badge"
        :data-color="sourceTagColor(tag.color)"
        :aria-label="`${tag.name}: ${tag.description}`"
        :title="props.showNameTitle ? tag.name : undefined"
        @click.stop
      >
        <Tag
          v-if="props.showIcon"
          :size="14"
          :stroke-width="2.2"
          class="source-tag-badge__icon"
          aria-hidden="true"
        />
        <span class="source-tag-badge__label">{{ tag.name }}</span>
      </button>
    </template>
    <span>{{ tag.description }}</span>
  </HflPopover>
  <span
    v-else
    class="source-tag-badge"
    :data-color="sourceTagColor(tag.color)"
    :title="props.showNameTitle ? tag.name : undefined"
  >
    <Tag
      v-if="props.showIcon"
      :size="14"
      :stroke-width="2.2"
      class="source-tag-badge__icon"
      aria-hidden="true"
    />
    <span class="source-tag-badge__label">{{ tag.name }}</span>
  </span>
</template>
