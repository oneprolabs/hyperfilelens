import { nextTick, reactive, type Ref } from 'vue'

export type InlineValidationRule = {
  field: string
  message: string
  valid: boolean
}

/**
 * Shared client-side required-field validation for long forms.
 *
 * Keep server and workflow errors in their existing presentation; this is
 * intentionally only for fields a user can correct in the current form.
 */
export function useInlineFormValidation(root: Ref<HTMLElement | null>) {
  const errors = reactive<Record<string, string>>({})

  function clear(field: string) {
    delete errors[field]
  }

  function locate(field: string) {
    void nextTick(() => {
      const fields = root.value?.querySelectorAll<HTMLElement>(`[data-validation-field="${field}"]`)
      const fieldEl = fields && [...fields].find((candidate) => {
        let current: HTMLElement | null = candidate
        while (current) {
          const style = window.getComputedStyle(current)
          if (style.display === 'none' || style.visibility === 'hidden') return false
          if (current === root.value) break
          current = current.parentElement
        }
        return true
      })
      if (!fieldEl) return
      fieldEl.scrollIntoView({ behavior: 'smooth', block: 'center' })
      window.setTimeout(() => {
        fieldEl.querySelector<HTMLElement>(
          'input:not([type="hidden"]), textarea, button, [tabindex]:not([tabindex="-1"])',
        )?.focus({ preventScroll: true })
      }, 240)
    })
  }

  function validate(rules: InlineValidationRule[]) {
    for (const key of Object.keys(errors)) delete errors[key]
    const invalid = rules.filter((rule) => !rule.valid)
    for (const rule of invalid) errors[rule.field] = rule.message
    if (invalid[0]) locate(invalid[0].field)
    return invalid.length === 0
  }

  return { clear, errors, validate }
}
