<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useData, useRoute } from 'vitepress'
import { localizedSitePath, siteLanguageMenuLabels, siteLanguages, type SiteLanguageLink } from './languages'

const props = defineProps<{
  current: string
  links?: SiteLanguageLink[]
}>()

const route = useRoute()
const { hash } = useData()
const mounted = ref(false)
const open = ref(false)
const switcherRef = ref<HTMLElement | null>(null)
const triggerRef = ref<HTMLButtonElement | null>(null)

function toggle() {
  open.value = !open.value
}

function close() {
  open.value = false
}

function closeAndFocus() {
  close()
  triggerRef.value?.focus()
}

function handleFocusOut(event: FocusEvent) {
  const nextTarget = event.relatedTarget
  if (!(nextTarget instanceof Node) || !switcherRef.value?.contains(nextTarget)) close()
}

function handleDocumentPointerDown(event: PointerEvent) {
  const target = event.target
  if (!(target instanceof Node) || !switcherRef.value?.contains(target)) close()
}

onMounted(() => {
  mounted.value = true
  document.addEventListener('pointerdown', handleDocumentPointerDown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
})

const currentLabel = computed(
  () => siteLanguages.find((lang) => lang.code === props.current)?.label ?? props.current,
)

const languageLinks = computed(() => {
  if (props.links) return props.links
  const pathname = route.path || '/'
  // Defer hashes until mounted so SSR and hydration render the same links.
  const currentHash = mounted.value && !/^\/(?:zh\/|es\/)?blog(?:\/|$)/.test(pathname)
    ? hash.value
    : ''
  return siteLanguages.map((lang) => ({
    ...lang,
    href: localizedSitePath(pathname, lang.code, currentHash),
  }))
})

watch(() => route.path, close)
</script>

<template>
  <div
    ref="switcherRef"
    class="lang-switcher"
    @focusout="handleFocusOut"
    @keydown.esc.prevent="closeAndFocus"
  >
    <button
      ref="triggerRef"
      class="lang-switcher-trigger"
      type="button"
      :aria-expanded="open"
      aria-haspopup="true"
      :aria-label="siteLanguageMenuLabels[current] || siteLanguageMenuLabels.en"
      @click="toggle"
    >
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a17 17 0 0 1 0 18 17 17 0 0 1 0-18Z" />
      </svg>
      <span>{{ currentLabel }}</span>
      <svg aria-hidden="true" class="lang-switcher-caret" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6" /></svg>
    </button>
    <div v-show="open" class="lang-switcher-menu" role="menu">
      <a
        v-for="lang in languageLinks"
        :key="lang.code"
        :href="lang.href"
        class="lang-switcher-item"
        :class="{ active: lang.code === current }"
        :aria-current="lang.code === current ? 'page' : undefined"
        :lang="lang.code"
        :hreflang="lang.code"
        role="menuitem"
        @click="close"
      >
        <svg aria-hidden="true" class="lang-switcher-check" viewBox="0 0 24 24"><path d="m5 12 4 4L19 6" /></svg>
        <span>{{ lang.label }}</span>
      </a>
    </div>
  </div>
</template>
