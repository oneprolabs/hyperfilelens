<script setup lang="ts">
import { computed } from 'vue'
import HflTypeLabel from './HflTypeLabel.vue'
import { useAlertPolicyLabels } from '../composables/useAlertPolicyLabels'
import { getAlertPolicyTypeIcon } from '../composables/useAlertPolicyTypeIcon'

const props = defineProps<{
  type?: string | null
}>()

const { policyTypeLabel } = useAlertPolicyLabels()
const icon = computed(() => getAlertPolicyTypeIcon(props.type))
const label = computed(() => policyTypeLabel(props.type))
</script>

<template>
  <HflTypeLabel
    :icon="icon"
    :label="label"
  />
</template>
