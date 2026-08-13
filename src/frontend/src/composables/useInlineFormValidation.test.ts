// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'
import { useInlineFormValidation } from './useInlineFormValidation'

describe('useInlineFormValidation', () => {
  it('renders errors, locates the first invalid field, and clears a corrected field', async () => {
    const root = document.createElement('div')
    root.innerHTML = '<div data-validation-field="name"><input /></div><div data-validation-field="bucket"><input /></div>'
    const nameField = root.querySelector<HTMLElement>('[data-validation-field="name"]')!
    const input = nameField.querySelector<HTMLInputElement>('input')!
    nameField.scrollIntoView = vi.fn()
    input.focus = vi.fn()
    const validation = useInlineFormValidation(ref(root))

    expect(validation.validate([
      { field: 'name', message: 'Name is required', valid: false },
      { field: 'bucket', message: 'Bucket is required', valid: false },
    ])).toBe(false)
    await nextTick()
    await new Promise((resolve) => window.setTimeout(resolve, 250))

    expect(validation.errors).toEqual({ name: 'Name is required', bucket: 'Bucket is required' })
    expect(nameField.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
    expect(input.focus).toHaveBeenCalledWith({ preventScroll: true })

    validation.clear('name')
    expect(validation.errors).toEqual({ bucket: 'Bucket is required' })
  })

  it('skips an identically named field hidden by a conditional form', async () => {
    const root = document.createElement('div')
    root.innerHTML = '<div style="display: none"><div data-validation-field="name"><input /></div></div><div data-validation-field="name"><input /></div>'
    const hiddenField = root.querySelector<HTMLElement>('[style] [data-validation-field="name"]')!
    const visibleField = root.querySelectorAll<HTMLElement>('[data-validation-field="name"]')[1]
    hiddenField.scrollIntoView = vi.fn()
    visibleField.scrollIntoView = vi.fn()
    visibleField.querySelector<HTMLInputElement>('input')!.focus = vi.fn()
    const validation = useInlineFormValidation(ref(root))

    validation.validate([{ field: 'name', message: 'Name is required', valid: false }])
    await nextTick()
    await new Promise((resolve) => window.setTimeout(resolve, 250))

    expect(hiddenField.scrollIntoView).not.toHaveBeenCalled()
    expect(visibleField.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
  })
})
