<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vitepress'
import { trackWebsiteOpenApp } from './analytics'

const props = defineProps<{
  placement: 'bar' | 'screen'
}>()

const route = useRoute()
const appOrigin = ref('')
const isDocs = computed(() => route.path.startsWith('/zh/docs') || route.path.startsWith('/en/docs'))
const label = computed(() => route.path.startsWith('/zh/docs') ? '免费试用' : 'Try free')

function validOrigin(value: string): string {
  try {
    const parsed = new URL(value)
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) return ''
    if (parsed.pathname !== '/' || parsed.search || parsed.hash) return ''
    return parsed.origin
  } catch {
    return ''
  }
}

function directAppOrigin(): string {
  const hostname = window.location.hostname || '127.0.0.1'
  const host = hostname.includes(':') ? `[${hostname}]` : hostname
  return `https://${host}:11443`
}

onMounted(() => {
  appOrigin.value = validOrigin(window.__HFL_WEBSITE_CONFIG__?.appUrl || '') || directAppOrigin()
})

const loginUrl = computed(() => `${appOrigin.value || '#'}${appOrigin.value ? '/login' : ''}`)

function openApp(event: MouseEvent) {
  const target = loginUrl.value
  if (!target || target === '#') return
  if (
    event.button !== 0
    || event.metaKey
    || event.ctrlKey
    || event.shiftKey
    || event.altKey
  ) {
    trackWebsiteOpenApp('docs_header')
    return
  }
  event.preventDefault()
  trackWebsiteOpenApp('docs_header', () => window.location.assign(target))
}
</script>

<template>
  <a
    v-if="isDocs"
    :class="['hfl-doc-trial', `hfl-doc-trial--${props.placement}`]"
    :href="loginUrl"
    @click="openApp"
  >
    {{ label }}
  </a>
</template>
