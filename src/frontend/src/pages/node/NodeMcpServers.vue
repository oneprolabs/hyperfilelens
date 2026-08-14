<script setup lang="ts">
import ModulePage from '../../components/ModulePage.vue'
import { useInsightSideNav } from '../../composables/useInsightSideNav'
import InsightMcpServers from '../insight/InsightMcpServers.vue'

const insightMenus = useInsightSideNav()
</script>

<template>
  <ModulePage
    :menus="insightMenus"
    body-fill
  >
    <InsightMcpServers />
  </ModulePage>
</template>
