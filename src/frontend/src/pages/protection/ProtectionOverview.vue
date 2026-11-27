<script setup lang="ts">
import { computed } from 'vue'
import { useTenantPermissions } from '../../composables/useTenantPermissions'
import { lazyRoute } from '../../router/lazyRoute'

const { has } = useTenantPermissions()
// Discard cached inventory and cancel in-flight reads when Source grants change.
const inventoryContext = computed(() => [
  has('protection.source_hosts.view'), has('protection.source_nas.view'),
].join(':'))
const Wizard = lazyRoute(() => import('./DataProtection.vue'), 30_000)
</script>

<template>
  <Wizard
    v-if="has('protection.backup_configurations.view')"
    :key="inventoryContext"
  />
</template>
