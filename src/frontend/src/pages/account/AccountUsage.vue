<script setup lang="ts">
import UserResourceUsage from '../../components/UserResourceUsage.vue'
</script>

<template>
  <div class="account-usage-page">
    <UserResourceUsage
      :show-title="false"
      :show-refresh="false"
    />
  </div>
</template>

<style scoped>
.account-usage-page { min-width: 0; }
</style>
