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
              <i class="app-route-loading__mark" />
              <div class="app-route-loading__brand">
                <span>Hyper</span>FileLens
              </div>
              <div class="app-route-loading__sub" />
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

.app-route-loading__mark {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background:
    linear-gradient(135deg, rgba(245, 166, 35, 0.95), transparent 46%),
    linear-gradient(135deg, #a99bff, var(--color-primary, #6d5ef6));
  box-shadow: 0 16px 34px rgba(109, 94, 246, 0.28);
  transform: rotate(45deg);
}

.app-route-loading__brand {
  margin: 4px 0 0;
  color: #171721;
  font-size: 20px;
  font-weight: 750;
  line-height: 1.2;
  letter-spacing: 0;
}

.app-route-loading__brand span {
  color: #f5a623;
}

.app-route-loading__sub {
  width: 160px;
  height: 8px;
  border-radius: 999px;
  background: rgba(119, 116, 134, 0.16);
}

.app-route-loading__bar {
  position: relative;
  width: 220px;
  height: 3px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(109, 94, 246, 0.14);
}

.app-route-loading__bar::after {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: 42%;
  border-radius: inherit;
  background: linear-gradient(90deg, #f5a623, var(--color-primary, #6d5ef6));
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
