<script setup lang="ts">
defineProps<{
  title: string
}>()
</script>

<template>
  <section class="hfl-detail-section">
    <h4 class="hfl-detail-section__title">
      {{ title }}
    </h4>
    <slot />
  </section>
</template>
