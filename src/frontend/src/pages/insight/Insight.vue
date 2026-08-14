<script setup lang="ts">
import ModulePage from '../../components/ModulePage.vue'
import { useInsightSideNav } from '../../composables/useInsightSideNav'
import InsightCopilot from './InsightCopilot.vue'

const insightMenus = useInsightSideNav()
</script>

<template>
  <ModulePage
    :menus="insightMenus"
    body-flush
    body-fill
    hide-page-title
  >
    <KeepAlive include="InsightCopilot">
      <InsightCopilot />
    </KeepAlive>
  </ModulePage>
</template>
