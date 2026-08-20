<script setup lang="ts">
import { ref } from 'vue'
import { siteLanguages } from './languages'

const props = defineProps<{
  current: string
}>()

const open = ref(false)

function toggle() {
  open.value = !open.value
}

function close() {
  open.value = false
}

const currentLabel = siteLanguages.find((lang) => lang.code === props.current)?.label ?? props.current
</script>

<template>
  <div class="lang-switcher" @mouseleave="close">
    <button
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
