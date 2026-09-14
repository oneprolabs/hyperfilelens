import { defineComponent } from 'vue'

/** Community build: no extension-provided runtime service connections. */
export const RuntimeServiceConnections = defineComponent({
  name: 'EmptyRuntimeServiceConnections',
  setup: () => () => null,
})
