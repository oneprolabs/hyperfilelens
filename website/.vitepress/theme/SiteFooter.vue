<script setup lang="ts">
import { computed } from 'vue'
import { homeCopy, type HomeFooterLink, type HomeLocale } from './homeCopy'
import { githubUrl, sourceLensUrl, useAppOrigin } from './useAppOrigin'

const props = defineProps<{
  locale: HomeLocale
  anchorBase?: string
}>()

const copy = computed(() => homeCopy[props.locale])
const { loginUrl, openApp } = useAppOrigin()

/** Footer copy stores tokens for links whose target is computed at runtime. */
function footerHref(link: HomeFooterLink): string {
  switch (link.href) {
    case '@app': return loginUrl.value
    case '@github': return githubUrl
    case '@sourcelens': return sourceLensUrl
    case '@releases': return `${githubUrl}/releases`
    case '@issues': return `${githubUrl}/issues`
    default:
      return link.href.startsWith('#') && props.anchorBase
        ? `${props.anchorBase}${link.href}`
        : link.href
  }
}
</script>

<template>
  <footer>
    <div class="footer-brand">
      <a class="brand" :href="copy.home"><img class="brand-lockup" src="/brand/images/hyperfilelens-lockup-transparent-on-light.png" alt="HyperFileLens" /></a>
      <p>{{ copy.footer.blurb }}</p>
      <a class="footer-social" :href="githubUrl" target="_blank" rel="noopener noreferrer" :aria-label="copy.footer.socialAria"><svg aria-hidden="true"><use href="#icon-github" /></svg></a>
    </div>
    <div class="footer-links">
      <div v-for="column in copy.footer.columns" :key="column.title">
        <strong>{{ column.title }}</strong>
        <a
          v-for="link in column.links"
          :key="link.label"
          :href="footerHref(link)"
          :target="link.external ? '_blank' : undefined"
          :rel="link.external ? 'noopener noreferrer' : undefined"
          @click="link.href === '@app' ? openApp($event, 'footer') : undefined"
        >{{ link.label }}</a>
      </div>
    </div>
    <div class="footer-bottom"><span>{{ copy.footer.bottom[0] }}</span><span>{{ copy.footer.bottom[1] }}</span></div>
  </footer>
</template>
