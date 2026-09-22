<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vitepress'
import { useAppOrigin } from './useAppOrigin'
import { siteTrialLabels } from './languages'

const props = defineProps<{
  placement: 'bar' | 'screen'
}>()

const route = useRoute()
const { loginUrl, openApp } = useAppOrigin()

const isDocs = computed(() =>
  route.path.startsWith('/zh/docs')
  || route.path.startsWith('/docs'),
)
const label = computed(() => route.path.startsWith('/zh/docs') ? siteTrialLabels.zh : siteTrialLabels.en)
</script>

<template>
  <a
    v-if="isDocs"
    :class="['hfl-doc-trial', `hfl-doc-trial--${props.placement}`]"
    :href="loginUrl"
    target="_blank"
    rel="noopener noreferrer"
    @click="openApp($event, 'docs_header')"
  >
    {{ label }}
  </a>
</template>
