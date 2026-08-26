<script setup lang="ts">
defineProps<{
  description: string
  slogan: string
}>()
</script>

<template>
  <div class="auth-brand-panel">
    <img
      class="auth-logo-lockup"
      src="/brand/images/hyperfilelens-lockup-on-dark.png"
      alt="HyperFileLens"
    >

    <div class="auth-brand-copy">
      <p class="auth-brand-description">
        {{ description }}
      </p>
      <p class="auth-brand-slogan">
        {{ slogan }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.auth-brand-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 44px;
  margin: 0;
  opacity: 0;
  transform: translateY(10px);
  animation: auth-brand-enter 420ms cubic-bezier(0.16, 1, 0.3, 1) 60ms forwards;
}

.auth-logo-lockup {
  display: block;
  width: min(360px, 100%);
  height: auto;
  object-fit: contain;
  transform: translate(-4.4%, 6px);
}

.auth-brand-copy {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 28px;
  width: 100%;
}

.auth-brand-description {
  margin: 0;
  max-width: 680px;
  color: #f8f8ff;
  font-size: clamp(40px, 2.45vw, 44px);
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.13;
  white-space: pre-line;
}

.auth-brand-slogan {
  margin: 0;
  max-width: 640px;
  color: rgba(214, 205, 246, 0.78);
  font-size: clamp(16px, 0.95vw, 17px);
  font-weight: 400;
  letter-spacing: 0;
  line-height: 1.55;
  white-space: normal;
}

@keyframes auth-brand-enter {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 1279.98px) {
  .auth-brand-panel {
    gap: 14px;
    margin: 0;
  }

  .auth-brand-description,
  .auth-brand-slogan {
    display: none;
  }

  .auth-logo-lockup {
    width: min(250px, 100%);
  }
}

@media (max-width: 479.98px) {
  .auth-brand-description {
    display: block;
    color: rgba(248, 250, 252, 0.62);
    font-size: 12px;
    line-height: 1.35;
  }
}

@media (prefers-reduced-motion: reduce) {
  .auth-brand-panel {
    opacity: 1;
    transform: none;
    animation: none;
  }
}
</style>
