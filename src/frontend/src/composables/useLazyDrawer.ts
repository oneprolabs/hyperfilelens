import { ElDrawer } from 'element-plus'
import { computed, defineComponent, Fragment, h, onScopeDispose, ref, shallowRef, watch, type Component, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import LazyDrawerFeedback from '../components/feedback/LazyDrawerFeedback.vue'
import { LazyDrawerContentHost, type DrawerFrameContent } from '../components/feedback/LazyDrawerFrame'
import { useResponsiveDrawerWidth } from './useResponsiveDrawerWidth'

function invokeListeners(listeners: unknown, ...args: unknown[]) {
  for (const listener of Array.isArray(listeners) ? listeners : [listeners]) {
    if (typeof listener === 'function') listener(...args)
  }
}

export function defineLazyDrawer<Drawer extends Component>(
  load: () => Promise<{ default: Drawer }>,
  options?: { titleKey: string; loadingLayout: 'task' },
) {
  const resolved = shallowRef<Drawer>()
  let request: Promise<void> | undefined

  function loadDrawer() {
    request ??= Promise.resolve().then(load).then(module => {
      resolved.value = module.default
    }).finally(() => { request = undefined })
    return request
  }

  return defineComponent({
    inheritAttrs: false,
    props: {
      modelValue: { type: Boolean, default: undefined },
      open: { type: Boolean, default: undefined },
      detailLoading: Boolean,
    },
    setup(props, { attrs, slots }) {
      const { t } = useI18n()
      const { drawerSize } = useResponsiveDrawerWidth()
      const visible = computed(() => props.modelValue ?? props.open ?? false)
      const failed = ref(false)
      const frame = shallowRef<DrawerFrameContent>()
      const opened = ref(false)
      let contentOpened = false

      async function resolveDrawer() {
        if (resolved.value) return
        failed.value = false
        try {
          await loadDrawer()
        } catch {
          failed.value = true
        }
      }

      watch(visible, (shown) => { if (shown) void resolveDrawer() }, { immediate: true })
      watch([frame, opened], ([content, shown]) => {
        if (!content || !shown || contentOpened) return
        contentOpened = true
        invokeListeners(content.attrs.onOpened)
      }, { flush: 'post' })

      const header = ({ titleId, titleClass }: { titleId: string; titleClass: string }) => frame.value?.slots.header?.({ titleId, titleClass })
        ?? h('span', { id: titleId, class: titleClass, role: 'heading', 'aria-level': 2 },
          t(options?.titleKey ?? (failed.value ? 'errors.generic.loadFailed' : 'common.loading')))
      const body = () => frame.value?.slots.default?.()
        ?? h(LazyDrawerFeedback, { failed: failed.value, layout: options?.loadingLayout, onRetry: resolveDrawer })
      const footer = () => frame.value?.slots.footer?.()

      return () => {
        const content = frame.value
        const drawer = resolved.value
        const close = attrs[props.modelValue !== undefined ? 'onUpdate:modelValue' : 'onUpdate:open']
        const size = attrs.drawerSize ?? attrs['drawer-size']
        const zIndex = attrs.drawerZIndex ?? attrs['drawer-z-index']
        const shell = h(ElDrawer, {
          key: 'drawer-shell',
          size: typeof size === 'string' ? size : drawerSize.value,
          zIndex: typeof zIndex === 'number' ? zIndex : undefined,
          class: 'hfl-detail-drawer',
          ...content?.attrs,
          modelValue: visible.value,
          'onUpdate:modelValue': (shown: boolean) => invokeListeners(content?.attrs['onUpdate:modelValue'] ?? close, shown),
          onOpened: () => { opened.value = true },
          onClose: () => {
            opened.value = false
            invokeListeners(frame.value?.attrs.onClose)
          },
          onClosed: () => {
            contentOpened = false
            invokeListeners(frame.value?.attrs.onClosed)
          },
        }, { header, default: body, ...(content?.slots.footer ? { footer } : {}) })

        return h(Fragment, [shell, drawer && !props.detailLoading ? h(LazyDrawerContentHost, { key: 'drawer-content', frame }, {
          default: () => h(drawer, {
            ...attrs,
            ...(props.modelValue !== undefined ? { modelValue: props.modelValue } : { open: props.open }),
          }, slots),
        }) : null])
      }
    },
  }) as unknown as Drawer
}

export function useDrawerMounted(isOpen: () => boolean) {
  return computed<boolean>((mounted) => mounted || isOpen())
}

export function useDrawerDetailRequest(open: Ref<boolean>) {
  const detailLoading = ref(false)
  let active: AbortController | undefined

  function cancel() {
    active?.abort()
    active = undefined
    detailLoading.value = false
  }

  watch(open, (shown) => { if (!shown) cancel() }, { flush: 'sync' })
  onScopeDispose(cancel)

  async function loadDetails<Result>(
    load: (signal: AbortSignal) => Promise<Result>,
    apply: (result: Result) => void,
    onError?: (error: unknown) => void,
  ) {
    cancel()
    const controller = new AbortController()
    active = controller
    detailLoading.value = true
    open.value = true
    try {
      const result = await load(controller.signal)
      if (!controller.signal.aborted) apply(result)
    } catch (error) {
      if (!controller.signal.aborted) {
        open.value = false
        onError?.(error)
      }
    } finally {
      if (active === controller) {
        active = undefined
        detailLoading.value = false
      }
    }
  }

  return { detailLoading, loadDetails }
}
