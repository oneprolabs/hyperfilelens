import { defineComponent, h, type PropType } from 'vue'
import { useI18n } from 'vue-i18n'
import { RefreshCw } from 'lucide-vue-next'
import { isDynamicImportFailure } from './chunkLoadRecovery'
import './chunkLoadError.css'

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
          h('h1', { id: 'chunk-load-error-title', class: 'chunk-load-error__title' },
            t(`errors.pageLoad.${copyKey}.title`)),
          h('p', { class: 'chunk-load-error__message' },
            t(`errors.pageLoad.${copyKey}.message`)),
          h('button', {
            class: 'chunk-load-error__action',
            type: 'button',
            onClick: props.reload,
          }, [
            h(RefreshCw, {
              class: 'chunk-load-error__action-icon',
              size: 16,
              'stroke-width': 2,
              'aria-hidden': true,
            }),
            h('span', t('errors.pageLoad.reload')),
          ]),
        ]),
      ])
    }
  },
})
