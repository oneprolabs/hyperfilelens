import {
  inject, onBeforeUnmount, onMounted, provide, ref, shallowRef, toValue, watch,
  type InjectionKey, type MaybeRefOrGetter, type Ref, type ShallowRef,
} from 'vue'

const shellSidebarKey: InjectionKey<ShallowRef<HTMLElement | null>> = Symbol('shellSidebar')

/** Track the rendered sidebar rather than assuming its width or route layout. */
export function provideShellSidebar(
  enabled: MaybeRefOrGetter<boolean>,
  fallback: Ref<HTMLElement | null>,
) {
  const sidebar = shallowRef<HTMLElement | null>(null)
  const inset = ref(0)
  provide(shellSidebarKey, sidebar)
  let observer: ResizeObserver | undefined

  function measure() {
    const bounds = (sidebar.value || fallback.value)?.getBoundingClientRect()
    inset.value = toValue(enabled) && bounds && bounds.width > 0
      ? Math.max(0, Math.round(bounds.right))
      : 0
  }

  watch([() => toValue(enabled), sidebar, fallback], () => {
    observer?.disconnect()
    observer = undefined
    const target = sidebar.value || fallback.value
    if (toValue(enabled) && target && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(measure)
      observer.observe(target)
    }
    measure()
  }, { flush: 'post', immediate: true })

  onMounted(() => window.addEventListener('resize', measure))
  onBeforeUnmount(() => {
    observer?.disconnect()
    window.removeEventListener('resize', measure)
  })
  return inset
}

/** Register a common page's actual sidebar, including collapse/visibility changes. */
export function useShellSidebar(
  element: Ref<HTMLElement | null>,
  enabled: MaybeRefOrGetter<boolean>,
) {
  const sidebar = inject(shellSidebarKey, null)
  if (!sidebar) return
  let registered: HTMLElement | null = null

  function release() {
    if (sidebar.value === registered) sidebar.value = null
    registered = null
  }

  watch([element, () => toValue(enabled)], ([target, active]) => {
    release()
    if (target && active) {
      registered = target
      sidebar.value = target
    }
  }, { flush: 'post', immediate: true })
  onBeforeUnmount(release)
}
