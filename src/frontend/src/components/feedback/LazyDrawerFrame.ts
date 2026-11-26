import { ElDrawer } from 'element-plus'
import { defineComponent, getCurrentInstance, h, inject, onBeforeUnmount, onMounted, onUpdated, provide, type InjectionKey, type PropType, type ShallowRef, type Slots } from 'vue'

export type DrawerFrameContent = {
  attrs: Record<string, unknown>
  slots: Slots
}

const drawerFrameKey: InjectionKey<ShallowRef<DrawerFrameContent | undefined>> = Symbol('drawer-frame')

export const LazyDrawerContentHost = defineComponent({
  props: {
    frame: { type: Object as PropType<ShallowRef<DrawerFrameContent | undefined>>, required: true },
  },
  setup(props, { slots }) {
    provide(drawerFrameKey, props.frame)
    return () => slots.default?.()
  },
})

export const LazyDrawerFrame = defineComponent({
  inheritAttrs: false,
  setup(_, { attrs, slots }) {
    const frame = inject(drawerFrameKey, undefined)
    if (!frame) return () => h(ElDrawer, attrs, slots)

    const scopeId = getCurrentInstance()?.vnode.scopeId
    let current: DrawerFrameContent | undefined
    // Vue mutates slots in place; preserve the previously registered names.
    let registeredSlotNames: string[] = []

    function register() {
      const bindings = { ...attrs, ...(scopeId ? { [scopeId]: '' } : {}) }
      const slotNames = Object.keys(slots)
      if (current && Object.keys(current.attrs).length === Object.keys(bindings).length
        && Object.keys(bindings).every(key => current?.attrs[key] === bindings[key])
        && slotNames.length === registeredSlotNames.length
        && slotNames.every(name => registeredSlotNames.includes(name))) return
      registeredSlotNames = slotNames
      current = { attrs: bindings, slots }
      frame.value = current
    }

    onMounted(register)
    onUpdated(register)
    onBeforeUnmount(() => { if (frame.value === current) frame.value = undefined })
    return () => null
  },
})
