<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    usedBytes: number
    totalBytes: number
    variant?: 'compact' | 'detail'
    formatBytes: (value: number) => string
    emptyLabel?: string
    showBar?: boolean
    showPercent?: boolean
    usedOnly?: boolean
    unlimitedTotalLabel?: string
    /**
     * When set, overrides the default "totalBytes > 0 means known".
     * Use true for hard-empty pools (totalBytes === 0) such as capacity_gb=0.
     */
    knownTotal?: boolean | null
  }>(),
  {
    variant: 'compact',
    emptyLabel: '—',
    showBar: true,
    showPercent: true,
    usedOnly: false,
    knownTotal: null,
  },
)

const variantClass = computed(() =>
  props.variant === 'detail' ? 'repo-usage-cell--detail' : 'repo-usage-cell--compact',
)

const hasKnownTotal = computed(() =>
  props.knownTotal == null ? props.totalBytes > 0 : Boolean(props.knownTotal),
)

const showFullCapacity = computed(() => hasKnownTotal.value)

const showUsedOnly = computed(() => props.usedOnly && props.usedBytes > 0 && !hasKnownTotal.value)

const showUnlimited = computed(
  () => !hasKnownTotal.value && !showUsedOnly.value && Boolean(props.unlimitedTotalLabel) && props.usedBytes >= 0,
)

const percent = computed(() => {
  if (!hasKnownTotal.value) return 0
  // Hard-empty total (0 bytes) is fully allocated / blocked.
  if (props.totalBytes <= 0) return 100
  return Math.min(100, Math.round((props.usedBytes / props.totalBytes) * 100))
})
</script>

<template>
  <div v-if="showFullCapacity" class="repo-usage-cell" :class="variantClass">
    <div class="repo-usage-cell__numbers">
      <span class="repo-usage-cell__used">{{ formatBytes(usedBytes) }}</span>
      <span class="repo-usage-cell__capacity">/ {{ formatBytes(totalBytes) }}</span>
      <span v-if="showPercent" class="repo-usage-cell__percent">{{ percent }}%</span>
    </div>
    <div v-if="showBar" class="repo-usage-bar">
      <span class="repo-usage-bar__fill" :style="{ width: `${percent}%` }" />
    </div>
  </div>
  <div v-else-if="showUsedOnly" class="repo-usage-cell" :class="variantClass">
    <div class="repo-usage-cell__numbers">
      <span class="repo-usage-cell__used">{{ formatBytes(usedBytes) }}</span>
    </div>
    <div v-if="showBar" class="repo-usage-bar">
      <span class="repo-usage-bar__fill" style="width: 0%" />
    </div>
  </div>
  <div v-else-if="showUnlimited" class="repo-usage-cell repo-usage-cell--unlimited" :class="variantClass">
    <div class="repo-usage-cell__numbers">
      <span class="repo-usage-cell__used">{{ formatBytes(usedBytes) }}</span>
      <span class="repo-usage-cell__capacity">/ {{ unlimitedTotalLabel }}</span>
    </div>
  </div>
  <span v-else class="hfl-empty-mark">{{ emptyLabel }}</span>
</template>
