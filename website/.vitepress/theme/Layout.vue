<script setup lang="ts">
import DefaultTheme from 'vitepress/theme'
import { computed } from 'vue'
import { useData, useRoute } from 'vitepress'
import HomeLanding from './HomeLanding.vue'
import BlogIndex from './BlogIndex.vue'
import BlogPost from './BlogPost.vue'
import DocTrialLink from './DocTrialLink.vue'
import DocLanguageSwitcher from './DocLanguageSwitcher.vue'

const { frontmatter } = useData()
const route = useRoute()
const isDocs = computed(() => /^\/(?:zh\/)?docs(?:\/|$)/.test(route.path))
</script>

<template>
  <HomeLanding v-if="frontmatter.layout === 'hfl-home'" />
  <BlogIndex v-else-if="frontmatter.layout === 'hfl-blog'" />
  <BlogPost v-else-if="frontmatter.layout === 'hfl-blog-post'" />
  <DefaultTheme.Layout v-else>
    <template #nav-bar-content-after>
      <DocLanguageSwitcher v-if="isDocs" placement="bar" />
      <DocTrialLink placement="bar" />
    </template>
    <template #nav-screen-content-after>
      <DocLanguageSwitcher v-if="isDocs" placement="screen" />
      <DocTrialLink placement="screen" />
    </template>
  </DefaultTheme.Layout>
</template>
