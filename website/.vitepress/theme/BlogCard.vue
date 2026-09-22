<script setup lang="ts">
import { computed } from 'vue'
import { formatDate, type BlogEntry } from './blog'
import { blogCopy } from './blogCopy'
import type { HomeLocale } from './homeCopy'

const props = defineProps<{
  entry: BlogEntry
  locale: HomeLocale
}>()

const copy = computed(() => blogCopy[props.locale])
const isExternal = computed(() => props.entry.kind === 'link')

const meta = computed(() =>
  [props.entry.author, props.entry.source, formatDate(props.entry.date, props.locale)].filter(Boolean),
)

const cta = computed(() =>
  isExternal.value && props.entry.source
    ? copy.value.readOn(props.entry.source)
    : copy.value.readMore,
)
</script>

<template>
  <article class="blog-card">
    <h2 class="blog-card-title">
      <a
        :href="entry.url"
        :target="isExternal ? '_blank' : undefined"
        :rel="isExternal ? 'noopener noreferrer' : undefined"
      >{{ entry.title }}</a>
    </h2>

    <p class="blog-card-summary">{{ entry.summary }}</p>

    <p class="blog-card-meta">
      <span v-for="(item, index) in meta" :key="item">
        <span v-if="index > 0" class="blog-meta-sep" aria-hidden="true">·</span>{{ item }}
      </span>
    </p>

    <a
      class="text-link blog-card-cta"
      :href="entry.url"
      :target="isExternal ? '_blank' : undefined"
      :rel="isExternal ? 'noopener noreferrer' : undefined"
      tabindex="-1"
      aria-hidden="true"
    >
      {{ cta }}
      <svg aria-hidden="true"><use :href="isExternal ? '#icon-external' : '#icon-arrow'" /></svg>
    </a>
  </article>
</template>
