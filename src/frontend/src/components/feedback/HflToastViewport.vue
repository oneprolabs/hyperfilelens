<script setup lang="ts">
import HflToastItem from './HflToastItem.vue'
import { toastState } from '../../lib/toast/store'
</script>

<template>
  <div
    class="hfl-toast-viewport"
    aria-live="polite"
    aria-relevant="additions removals"
  >
    <TransitionGroup name="hfl-toast-list">
      <HflToastItem
        v-for="toast in toastState.items"
        :key="toast.id"
        :toast="toast"
      />
    </TransitionGroup>
  </div>
</template>

<style scoped>
.hfl-toast-viewport {
  position: fixed;
  top: 68px;
  right: 16px;
  z-index: 4000;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: flex-end;
  max-height: calc(var(--app-viewport-height) - 84px);
  pointer-events: none;
}

.hfl-toast-list-enter-active,
.hfl-toast-list-leave-active,
.hfl-toast-list-move { transition: opacity 180ms ease, transform 180ms ease; }
.hfl-toast-list-enter-from,
.hfl-toast-list-leave-to { opacity: 0; transform: translateX(20px); }

@media (max-width: 640px) {
  .hfl-toast-viewport {
    top: calc(var(--app-header-height) + 8px);
    right: max(16px, var(--app-safe-right));
    left: max(16px, var(--app-safe-left));
    gap: 8px;
    align-items: center;
    max-height: calc(var(--app-viewport-height) - var(--app-header-height) - var(--app-safe-bottom) - 16px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .hfl-toast-list-enter-active,
  .hfl-toast-list-leave-active,
  .hfl-toast-list-move { transition: none; }
}
</style>
