<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    value: string | number
    sub?: string
    tone?: 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'firing' | 'neutral'
    accentSide?: 'left' | 'top'
    pulse?: boolean
  }>(),
  {
    tone: 'primary',
    accentSide: 'top',
    sub: '',
    pulse: false,
  },
)
</script>

<template>
  <div
    class="hfl-ops-stat-card"
    :class="[
      `hfl-ops-stat-card--tone-${tone}`,
      accentSide === 'left' ? 'hfl-ops-stat-card--side-left' : 'hfl-ops-stat-card--side-top',
    ]"
  >
    <div class="hfl-ops-stat-card__head">
      <div class="hfl-ops-stat-card__label">
        {{ label }}
      </div>
      <div
        v-if="$slots.icon"
        class="hfl-ops-stat-card__icon"
        aria-hidden="true"
      >
        <slot name="icon" />
      </div>
    </div>
    <div class="hfl-ops-stat-card__value-row">
      <div class="hfl-ops-stat-card__value">
        {{ value }}
      </div>
      <span
        v-if="pulse"
        class="hfl-ops-stat-card__pulse"
        aria-hidden="true"
      />
    </div>
    <div
      v-if="sub"
      class="hfl-ops-stat-card__sub"
    >
      {{ sub }}
    </div>
  </div>
</template>
