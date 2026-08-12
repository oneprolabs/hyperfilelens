<script setup lang="ts">
import { computed } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import ModulePage from '../../components/ModulePage.vue'
import { useAccountCenterMenus } from '../../composables/useAccountCenterMenus'

const accountMenus = useAccountCenterMenus()
const route = useRoute()
const bodyFill = computed(() => route.path === '/account/notifications')
</script>

<template>
  <ModulePage :menus="accountMenus" :body-fill="bodyFill">
    <RouterView v-slot="{ Component }">
      <Transition name="account-route" mode="out-in">
        <component :is="Component" />
      </Transition>
    </RouterView>
  </ModulePage>
</template>

<style scoped>
.account-route-enter-active,
.account-route-leave-active {
  transition: opacity 0.2s ease;
}

.account-route-enter-from,
.account-route-leave-to {
  opacity: 0;
}
</style>
