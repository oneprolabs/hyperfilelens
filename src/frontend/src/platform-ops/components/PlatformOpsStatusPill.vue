<script setup lang="ts">
import { computed } from 'vue'
import { opsStatusPillClass } from '../lib/statusDisplay'

const props = defineProps<{ status?: string | null }>()

const label = computed(() => {
  const raw = String(props.status || '').trim()
  return raw || '—'
})
</script>

<template>
  <span
    v-if="label !== '—'"
    class="hfl-ops-status-pill"
    :class="opsStatusPillClass(label)"
  >{{ label }}</span>
  <span
    v-else
    class="hfl-empty-mark"
  >—</span>
</template>
