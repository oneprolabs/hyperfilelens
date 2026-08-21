<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { siteLanguages } from './languages'

const props = defineProps<{
  current: string
}>()

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
  document.addEventListener('pointerdown', handleDocumentPointerDown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
})

const currentLabel = siteLanguages.find((lang) => lang.code === props.current)?.label ?? props.current
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
      @click="toggle"
    >
      <svg aria-hidden="true"><use href="#icon-globe" /></svg>
      <span>{{ currentLabel }}</span>
      <svg aria-hidden="true" class="lang-switcher-caret"><use href="#icon-chevron" /></svg>
    </button>
    <div v-show="open" class="lang-switcher-menu" role="menu">
      <a
        v-for="lang in siteLanguages"
        :key="lang.code"
        :href="lang.path"
        class="lang-switcher-item"
        :class="{ active: lang.code === current }"
        role="menuitem"
      >
        <svg aria-hidden="true" class="lang-switcher-check"><use href="#icon-check" /></svg>
        <span>{{ lang.label }}</span>
      </a>
    </div>
  </div>
</template>
