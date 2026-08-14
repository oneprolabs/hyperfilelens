<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'

withDefaults(
  defineProps<{
    minHeight?: string
    fill?: boolean
  }>(),
  {
    minHeight: '',
    fill: false,
  },
)

const panelRef = ref<HTMLElement | null>(null)
const tableViewportRef = ref<HTMLElement | null>(null)
const tableMaxHeight = ref(400)

function updateTableMaxHeight() {
  const tableViewport = tableViewportRef.value
  if (!tableViewport) return

  const pageBody = tableViewport.closest('.page-body') as HTMLElement | null
  const panel = panelRef.value
  const footer = panel?.querySelector<HTMLElement>('.hfl-list-footer')
  const footerHeight = footer?.offsetHeight ?? 0
  const pageBottom = pageBody?.getBoundingClientRect().bottom ?? window.visualViewport?.height ?? window.innerHeight
  const tableTop = tableViewport.getBoundingClientRect().top
  const scrollbarReserve = 12
  const next = Math.max(280, Math.floor(pageBottom - tableTop - footerHeight - scrollbarReserve - 8))

  if (tableMaxHeight.value !== next) {
    tableMaxHeight.value = next
  }
}

let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  resizeObserver = new ResizeObserver(() => updateTableMaxHeight())
  if (panelRef.value) resizeObserver.observe(panelRef.value)
  if (tableViewportRef.value) resizeObserver.observe(tableViewportRef.value)
  const pageBody = tableViewportRef.value?.closest('.page-body')
  if (pageBody) resizeObserver.observe(pageBody)
  window.addEventListener('resize', updateTableMaxHeight)
  void nextTick(updateTableMaxHeight)
  requestAnimationFrame(updateTableMaxHeight)
})

onUnmounted(() => {
  resizeObserver?.disconnect()
  window.removeEventListener('resize', updateTableMaxHeight)
})
</script>

<template>
  <div
    ref="panelRef"
    class="hfl-list-panel"
    :class="{ 'hfl-list-panel--fill': fill }"
    :style="minHeight && !fill ? { minHeight } : undefined"
  >
    <div
      v-if="$slots.toolbar || $slots['toolbar-actions'] || $slots['toolbar-utility']"
      class="hfl-list-toolbar"
      :class="{
        'hfl-list-toolbar--mobile-primary-utility': $slots.toolbar && $slots['toolbar-actions'] && $slots['toolbar-utility'],
      }"
    >
      <div
        v-if="$slots.toolbar"
        class="hfl-list-toolbar__primary"
      >
        <slot name="toolbar" />
      </div>
      <div
        v-if="$slots['toolbar-actions'] || $slots['toolbar-utility']"
        class="hfl-list-toolbar__right"
        :class="{
          'hfl-list-toolbar__right--mobile-split': $slots['toolbar-actions'] && $slots['toolbar-utility'],
          'hfl-list-toolbar__right--utility-only': !$slots['toolbar-actions'] && $slots['toolbar-utility'],
          'hfl-list-toolbar__right--full': !$slots.toolbar && $slots['toolbar-actions'],
          'hfl-list-toolbar__right--solo': !$slots.toolbar && $slots['toolbar-actions'],
        }"
      >
        <slot name="toolbar-actions" />
        <div
          v-if="$slots['toolbar-utility']"
          class="hfl-list-toolbar__utility"
        >
          <slot name="toolbar-utility" />
        </div>
      </div>
    </div>
    <slot />
    <div
      v-if="$slots.table"
      ref="tableViewportRef"
      class="hfl-list-table-viewport"
    >
      <slot
        name="table"
        :table-max-height="tableMaxHeight"
      />
    </div>
    <div
      v-if="$slots.footer"
      class="hfl-list-footer"
    >
      <slot name="footer" />
    </div>
  </div>
</template>
