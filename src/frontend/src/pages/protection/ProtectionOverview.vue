<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue'
import { useTenantPermissions } from '../../composables/useTenantPermissions'

const { has } = useTenantPermissions()
// Discard cached inventory and cancel in-flight reads when Source grants change.
const inventoryContext = computed(() => [
  has('protection.source_hosts.view'), has('protection.source_nas.view'),
].join(':'))
const Wizard = defineAsyncComponent(() => import('./DataProtection.vue').then(module => module.default))
</script>

<template>
  <Wizard
    v-if="has('protection.backup_configurations.view')"
    :key="inventoryContext"
  />
</template>
