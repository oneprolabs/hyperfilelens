<script setup lang="ts">
import ModulePage from '../../components/ModulePage.vue'
import { useInsightSideNav } from '../../composables/useInsightSideNav'
import InsightDataGateways from './InsightDataGateways.vue'

const insightMenus = useInsightSideNav()
</script>

<template>
  <ModulePage
    :menus="insightMenus"
    body-flush
    body-fill
  >
    <InsightDataGateways />
  </ModulePage>
</template>
