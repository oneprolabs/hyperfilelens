import { defineComponent, h, type PropType } from 'vue'
import { useI18n } from 'vue-i18n'
import { isDynamicImportFailure } from './chunkLoadRecovery'
import './chunkLoadError.css'

function refreshIcon(className: string) {
  return h('svg', {
    class: className,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    'stroke-width': '1.8',
    'stroke-linecap': 'round',
    'stroke-linejoin': 'round',
    'aria-hidden': 'true',
  }, [
    h('path', { d: 'M20 11a8 8 0 1 0 2 5.3' }),
    h('path', { d: 'M20 4v7h-7' }),
  ])
}

export const ChunkLoadError = defineComponent({
  name: 'ChunkLoadError',
  props: {
    error: {
      type: Object as PropType<Error>,
      default: undefined,
    },
    reload: {
      type: Function as PropType<() => void>,
      default: () => window.location.reload(),
    },
  },
  setup(props) {
    const { t } = useI18n()

    return () => {
      const copyKey = isDynamicImportFailure(props.error) ? 'update' : 'loadFailed'

      return h('section', {
        class: 'chunk-load-error',
        role: 'alert',
        'aria-live': 'assertive',
        'aria-labelledby': 'chunk-load-error-title',
      }, [
        h('div', { class: 'chunk-load-error__halo', 'aria-hidden': 'true' }),
        h('div', { class: 'chunk-load-error__card' }, [
          h('div', { class: 'chunk-load-error__icon' }, [
            refreshIcon('chunk-load-error__icon-svg'),
          ]),
          h('p', { class: 'chunk-load-error__eyebrow' }, t(`errors.pageLoad.${copyKey}.eyebrow`)),
          h('h1', { id: 'chunk-load-error-title', class: 'chunk-load-error__title' },
            t(`errors.pageLoad.${copyKey}.title`)),
          h('p', { class: 'chunk-load-error__message' },
            t(`errors.pageLoad.${copyKey}.message`)),
          h('button', {
            class: 'chunk-load-error__action',
            type: 'button',
            onClick: props.reload,
          }, [
            refreshIcon('chunk-load-error__action-icon'),
            h('span', t('errors.pageLoad.reload')),
          ]),
          h('p', { class: 'chunk-load-error__note' }, t('errors.pageLoad.note')),
        ]),
      ])
    }
  },
})
