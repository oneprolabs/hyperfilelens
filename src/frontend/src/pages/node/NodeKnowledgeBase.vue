<script setup lang="ts">
import ModulePage from '../../components/ModulePage.vue'
import { useInsightSideNav } from '../../composables/useInsightSideNav'
import InsightKnowledgeBase from '../insight/InsightKnowledgeBase.vue'

const insightMenus = useInsightSideNav()
</script>

<template>
  <ModulePage
    :menus="insightMenus"
    body-fill
  >
    <InsightKnowledgeBase />
  </ModulePage>
</template>
