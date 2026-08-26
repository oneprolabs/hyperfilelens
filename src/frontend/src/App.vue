<script setup lang="ts">
import { shallowRef, watch } from 'vue'
import { RouterView } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElConfigProvider } from 'element-plus'
import enLocale from 'element-plus/dist/locale/en.mjs'
import { installedLangPacks } from './lib/langPacks'
import HflToastViewport from './components/feedback/HflToastViewport.vue'
import HflErrorDetailsDialog from './components/feedback/HflErrorDetailsDialog.vue'

const { locale } = useI18n()
const elLocale = shallowRef(enLocale)

watch(
  locale,
  (value) => {
    const pack = installedLangPacks.value.find((item) => item.frontend_code === value)
    elLocale.value = (pack?.component_messages as typeof enLocale | undefined) ?? enLocale
  },
  { immediate: true },
)
</script>

<template>
  <ElConfigProvider :locale="elLocale">
    <RouterView v-slot="{ Component }">
      <Suspense timeout="0">
        <template #default>
          <div class="app-route-view-root">
            <component :is="Component" />
          </div>
        </template>
        <template #fallback>
          <div
            class="app-route-loading"
            role="status"
            aria-label="Loading"
          >
            <div
              class="app-route-loading__panel"
              aria-hidden="true"
            >
              <img
                class="app-route-loading__lockup"
                src="/brand/images/hyperfilelens-lockup-on-light.png"
                alt="HyperFileLens"
              >
              <div class="app-route-loading__bar" />
            </div>
          </div>
        </template>
      </Suspense>
    </RouterView>
    <HflToastViewport />
    <HflErrorDetailsDialog />
  </ElConfigProvider>
</template>

<style scoped>
.app-route-view-root {
  display: contents;
}

.app-route-loading {
  display: grid;
  min-height: var(--app-viewport-height);
  padding: 32px;
  box-sizing: border-box;
  place-items: center;
  background: var(--color-content-bg, #f4f4f7);
}

.app-route-loading__panel {
  display: grid;
  justify-items: center;
  gap: 16px;
  width: min(320px, 100%);
  color: #171721;
  text-align: center;
}

.app-route-loading__lockup {
  display: block;
  width: min(260px, 100%);
  height: 48px;
  object-fit: cover;
  object-position: center 40%;
}

.app-route-loading__bar {
  position: relative;
  width: min(243px, 100%);
  height: 4px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(109, 94, 246, 0.18);
}

.app-route-loading__bar::after {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: 42%;
  border-radius: inherit;
  background: linear-gradient(90deg, #f5a623, var(--color-primary, #6d5ef6));
  box-shadow: 0 0 10px rgba(109, 94, 246, 0.24);
  animation: app-route-loading-progress 1s ease-in-out infinite;
}

@keyframes app-route-loading-progress {
  0% {
    transform: translateX(-115%);
  }

  100% {
    transform: translateX(260%);
  }
}
</style>
