<script setup lang="ts">
import DefaultTheme from 'vitepress/theme'
import { useData } from 'vitepress'
import HomeLanding from './HomeLanding.vue'
import HomeLandingZh from './HomeLandingZh.vue'
import DocTrialLink from './DocTrialLink.vue'

const { frontmatter } = useData()
</script>

<template>
  <HomeLanding v-if="frontmatter.layout === 'hfl-home'" />
  <HomeLandingZh v-else-if="frontmatter.layout === 'hfl-home-zh'" />
  <DefaultTheme.Layout v-else>
    <template #nav-bar-content-after>
      <DocTrialLink placement="bar" />
    </template>
    <template #nav-screen-content-after>
      <DocTrialLink placement="screen" />
    </template>
  </DefaultTheme.Layout>
</template>
