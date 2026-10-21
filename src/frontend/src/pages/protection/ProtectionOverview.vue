<script setup lang="ts">
import { defineAsyncComponent } from 'vue'
import { useTenantPermissions } from '../../composables/useTenantPermissions'
import { canViewProtectionSources } from '../../lib/protectionAccess'
import BackupConfigurationDirectory from './BackupConfigurationDirectory.vue'

const { has } = useTenantPermissions()
const Wizard = defineAsyncComponent(() => import('./DataProtection.vue').then(module => module.default))
</script>

<template>
  <Wizard
    v-if="has('protection.backup_configurations.view') && canViewProtectionSources(has)"
  />
  <BackupConfigurationDirectory v-else />
</template>
