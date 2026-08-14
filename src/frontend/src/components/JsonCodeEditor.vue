<script setup lang="ts">
import { basicSetup } from 'codemirror'
import { json } from '@codemirror/lang-json'
import { Compartment, EditorState } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  modelValue: string
  readonly?: boolean
  ariaLabel?: string
}>(), {
  readonly: false,
  ariaLabel: 'JSON editor',
})

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const host = ref<HTMLElement | null>(null)
const editable = new Compartment()
let view: EditorView | null = null

onMounted(() => {
  if (!host.value) return
  view = new EditorView({
    parent: host.value,
    state: EditorState.create({
      doc: props.modelValue,
      extensions: [
        basicSetup,
        json(),
        EditorView.lineWrapping,
        EditorView.contentAttributes.of({ 'aria-label': props.ariaLabel }),
        editable.of(EditorView.editable.of(!props.readonly)),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) emit('update:modelValue', update.state.doc.toString())
        }),
      ],
    }),
  })
})

watch(() => props.modelValue, (value) => {
  if (!view || view.state.doc.toString() === value) return
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } })
})

watch(() => props.readonly, (value) => {
  view?.dispatch({ effects: editable.reconfigure(EditorView.editable.of(!value)) })
})

onBeforeUnmount(() => {
  view?.destroy()
  view = null
})
</script>

<template>
  <div
    ref="host"
    class="hfl-json-editor"
  />
</template>

<style>
.hfl-json-editor {
  min-height: 280px;
  overflow: hidden;
  border: 1px solid var(--color-border, #dedee8);
  border-radius: 9px;
  background: var(--color-card-bg, #fff);
}

.hfl-json-editor:focus-within {
  border-color: var(--color-primary, #6d5ef6);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-primary, #6d5ef6) 16%, transparent);
}

.hfl-json-editor .cm-editor {
  min-height: 280px;
  color: var(--color-text-primary, #30303d);
  background: transparent;
  font-size: 12px;
}

.hfl-json-editor .cm-gutters {
  color: var(--color-text-secondary, #777786);
  background: var(--color-fill-light, #f7f7fa);
  border-color: var(--color-border-light, #ececf2);
}

.hfl-json-editor .cm-activeLine,
.hfl-json-editor .cm-activeLineGutter {
  background: color-mix(in srgb, var(--color-primary, #6d5ef6) 7%, transparent);
}

.hfl-json-editor .cm-content {
  min-height: 280px;
  caret-color: var(--color-primary, #6d5ef6);
}
</style>
