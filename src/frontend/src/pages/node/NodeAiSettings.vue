<script setup lang="ts">
import ModulePage from '../../components/ModulePage.vue'
import { useInsightSideNav } from '../../composables/useInsightSideNav'
import InsightAiSettings from '../insight/InsightAiSettings.vue'

const insightMenus = useInsightSideNav()
</script>

<template>
  <ModulePage
    :menus="insightMenus"
    body-fill
  >
    <InsightAiSettings />
  </ModulePage>
</template>
