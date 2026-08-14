<script setup lang="ts">
import ModulePage from '../../components/ModulePage.vue'
import { useInsightSideNav } from '../../composables/useInsightSideNav'
import InsightAssistants from '../insight/InsightAssistants.vue'

const insightMenus = useInsightSideNav()
</script>

<template>
  <ModulePage
    :menus="insightMenus"
    body-fill
  >
    <InsightAssistants />
  </ModulePage>
</template>
