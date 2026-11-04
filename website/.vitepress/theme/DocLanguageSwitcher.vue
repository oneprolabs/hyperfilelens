<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vitepress'
import LanguageSwitcher from './LanguageSwitcher.vue'
import { localizedDocPath, siteLanguageMenuLabels, siteLanguages } from './languages'

defineProps<{ placement: 'bar' | 'screen' }>()

const route = useRoute()
const current = computed(() => route.path.startsWith('/zh/') ? 'zh' : 'en')
const label = computed(() => siteLanguageMenuLabels[current.value])
const links = computed(() => siteLanguages
  .filter((language) => language.code === 'en' || language.code === 'zh')
  .map((language) => ({
    code: language.code,
    label: language.label,
    href: localizedDocPath(route.path, language.code as 'en' | 'zh'),
  })))
</script>

<template>
  <LanguageSwitcher
    v-if="placement === 'bar'"
    class="hfl-doc-languages hfl-doc-languages--bar"
    :current="current"
    :links="links"
  />
  <nav v-else class="hfl-doc-languages hfl-doc-languages--screen" :aria-label="label">
    <a
      v-for="language in links"
      :key="language.code"
      :href="language.href"
      :lang="language.code"
      :hreflang="language.code"
      class="lang-switcher-item"
      :class="{ active: language.code === current }"
      :aria-current="language.code === current ? 'page' : undefined"
    >
      <svg aria-hidden="true" class="lang-switcher-check" viewBox="0 0 24 24"><path d="m5 12 4 4L19 6" /></svg>
      <span>{{ language.label }}</span>
    </a>
  </nav>
</template>
