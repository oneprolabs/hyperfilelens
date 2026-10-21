<script setup lang="ts">
import ModulePage from '../../components/ModulePage.vue'
import { useInsightSideNav } from '../../composables/useInsightSideNav'
import InsightCopilot from './InsightCopilot.vue'
import { useTenantPermissions } from '../../composables/useTenantPermissions'

const insightMenus = useInsightSideNav()
const { has } = useTenantPermissions()
</script>

<template>
  <ModulePage
    :menus="insightMenus"
    body-flush
    body-fill
    hide-page-title
  >
    <KeepAlive
      v-if="has('insights.chats.view')"
      include="InsightCopilot"
    >
      <InsightCopilot />
    </KeepAlive>
  </ModulePage>
</template>
