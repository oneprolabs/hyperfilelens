<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import ModulePage from '../../components/ModulePage.vue'
import { useResolvedPlatformOpsSideNav } from '../composables/useResolvedPlatformOpsSideNav'
import { setLensApiScope } from '../../lib/lensApi'

defineOptions({ name: 'PlatformEngineLayout' })

/**
 * Admin Engine always uses platform lens APIs
 * (``/api/v1/platform-ops/lens/*`` → Host ``__platform_lens__`` models).
 */
setLensApiScope('platform')

onMounted(() => {
  setLensApiScope('platform')
})

onUnmounted(() => {
  // Only reset when leaving Admin Engine; child route changes keep this layout mounted.
  setLensApiScope('tenant')
})

const sideNav = useResolvedPlatformOpsSideNav()
const route = useRoute()
const hidePageTitle = computed(() => /\/(?:add|edit)$/.test(route.path))
</script>

<template>
  <ModulePage
    :menus="sideNav"
    body-fill
    :hide-page-title="hidePageTitle"
  >
    <router-view />
  </ModulePage>
</template>
