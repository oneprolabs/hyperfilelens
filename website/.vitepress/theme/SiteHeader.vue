<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
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
const mobileNavOpen = ref(false)

const anchorNav = computed(() => copy.value.nav.filter((item) => item.href.startsWith('#')))
const outboundNav = computed(() => copy.value.nav.filter((item) => !item.href.startsWith('#')))

function navHref(href: string): string {
  if (href.startsWith('#') && props.anchorBase) return `${props.anchorBase}${href}`
  return href
}

function toggleMobileNav() {
  mobileNavOpen.value = !mobileNavOpen.value
}

function closeMobileNav() {
  mobileNavOpen.value = false
}

watch(mobileNavOpen, (open) => {
  document.body.classList.toggle('hfl-nav-open', open)
})

function onMobileNavKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeMobileNav()
}

onBeforeUnmount(() => {
  document.body.classList.remove('hfl-nav-open')
})
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
        <button
          class="mobile-menu-toggle"
          type="button"
          :aria-label="mobileNavOpen ? copy.header.closeMenu : copy.header.openMenu"
          :aria-expanded="mobileNavOpen"
          @click="toggleMobileNav"
        >
          <span class="hamburger-bars"></span>
        </button>
        <LanguageSwitcher :current="locale" />
        <a class="github-link" :href="githubUrl" target="_blank" rel="noopener noreferrer" :aria-label="copy.header.githubAria">
          <svg aria-hidden="true"><use href="#icon-github" /></svg>
          <span>{{ copy.header.githubLabel }}</span>
        </a>
        <a class="header-cta" :href="loginUrl" target="_blank" rel="noopener noreferrer" @click="openApp($event, 'header')">{{ copy.header.cta }}</a>
      </div>
    </div>
  </header>

  <div
    class="mobile-nav"
    :class="{ open: mobileNavOpen }"
    :aria-label="copy.header.mobileNavAria"
    @keydown="onMobileNavKeydown"
  >
    <nav>
      <a
        v-for="item in anchorNav"
        :key="item.href"
        :href="navHref(item.href)"
        @click="closeMobileNav"
      >{{ item.label }}</a>
    </nav>
    <div class="mobile-nav-external">
      <a
        v-for="item in outboundNav"
        :key="item.href"
        :href="navHref(item.href)"
        :target="item.external ? '_blank' : undefined"
        :rel="item.external ? 'noopener noreferrer' : undefined"
        @click="closeMobileNav"
      >
        {{ item.label }}
        <svg aria-hidden="true"><use href="#icon-arrow" /></svg>
      </a>
      <a :href="githubUrl" target="_blank" rel="noopener noreferrer" @click="closeMobileNav">
        <svg aria-hidden="true"><use href="#icon-github" /></svg>
        {{ copy.header.githubLabel }}
        <svg aria-hidden="true"><use href="#icon-arrow" /></svg>
      </a>
    </div>
    <a
      class="mobile-nav-cta"
      :href="loginUrl"
      target="_blank"
      rel="noopener noreferrer"
      @click="(event) => { closeMobileNav(); openApp(event, 'header') }"
    >{{ copy.header.cta }}</a>
  </div>
</template>
