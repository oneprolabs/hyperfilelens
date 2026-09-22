<script setup lang="ts">
import { computed } from 'vue'
import LanguageSwitcher from './LanguageSwitcher.vue'
import { homeCopy, type HomeLocale } from './homeCopy'
import { githubUrl, useAppOrigin } from './useAppOrigin'

const props = defineProps<{
  locale: HomeLocale
  /**
   * In-page anchors only resolve on the landing itself. Elsewhere they need the
   * locale root prefixed so "#use-cases" still lands somewhere sensible.
   */
  anchorBase?: string
}>()

const copy = computed(() => homeCopy[props.locale])
const { loginUrl, openApp } = useAppOrigin()

function navHref(href: string): string {
  if (href.startsWith('#') && props.anchorBase) return `${props.anchorBase}${href}`
  return href
}
</script>

<template>
  <header class="site-header-wrap">
    <div class="site-header">
      <a class="brand" :href="copy.home" :aria-label="copy.header.brandAria">
        <img class="brand-lockup" src="/brand/images/hyperfilelens-lockup-transparent-on-light.png" alt="HyperFileLens" />
      </a>
      <nav :aria-label="copy.header.navAria">
        <a
          v-for="item in copy.nav"
          :key="item.href"
          :href="navHref(item.href)"
          :target="item.external ? '_blank' : undefined"
          :rel="item.external ? 'noopener noreferrer' : undefined"
        >{{ item.label }}</a>
      </nav>
      <div class="header-actions">
        <LanguageSwitcher :current="locale" />
        <a class="github-link" :href="githubUrl" target="_blank" rel="noopener noreferrer" :aria-label="copy.header.githubAria">
          <svg aria-hidden="true"><use href="#icon-github" /></svg>
          <span>{{ copy.header.githubLabel }}</span>
        </a>
        <a class="header-cta" :href="loginUrl" target="_blank" rel="noopener noreferrer" @click="openApp($event, 'header')">{{ copy.header.cta }}</a>
      </div>
    </div>
  </header>
</template>
