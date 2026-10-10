import type { ErrorDetailsOverrides, ErrorDetailsPayload } from './errors/details'
import { errorDetailsCopyText, openErrorDetails, toErrorDetails } from './errors/details'
import { claimTaskNotice } from './asyncNoticeLedger'
import { pushToast, type ToastOptions, type ToastType } from './toast/store'

export type NotifyOptions = Omit<ToastOptions, 'type' | 'message' | 'details'> & {
  message: string
  error?: unknown
  errorDetails?: ErrorDetailsOverrides
  /** Prebuilt details payload (e.g. unregisterFailureToErrorDetails). */
  details?: ErrorDetailsPayload
  showDetails?: boolean
  detailsPresentation?: 'toast' | 'dialog'
}

export type NotifyInput = string | NotifyOptions
export type NotifyHandler = { close: () => void }

function notify(type: ToastType, input: NotifyInput = ''): NotifyHandler {
  const options: NotifyOptions = typeof input === 'string' ? { message: input } : input
  const toastTitle = options.title?.trim() === options.message.trim() ? undefined : options.title
  const details = !options.showDetails
    ? undefined
    : options.details
      || (options.error !== undefined
        ? toErrorDetails(options.error, {
            title: options.title,
            summary: options.message,
            ...options.errorDetails,
          })
        : undefined)

  if (details && options.detailsPresentation === 'dialog') {
    openErrorDetails(details)
    return { close: () => undefined }
  }

  // Source unregister batches claim their per-target identities before merging.
  if (details?.taskUuid && details.taskType && details.taskType !== 'source_unregister' && !claimTaskNotice(details)) return { close: () => undefined }

  return pushToast({
    type,
    title: toastTitle,
    message: options.message,
    dedupeKey: options.dedupeKey,
    duration: options.duration ?? (details ? 12000 : undefined),
    copyText: options.copyText || (details ? errorDetailsCopyText(details) : undefined),
    details,
    onClose: options.onClose,
  })
}

export function notifySuccess(input?: NotifyInput): NotifyHandler {
  return notify('success', input)
}

export function notifyWarning(input?: NotifyInput): NotifyHandler {
  return notify('warning', input)
}

export function notifyError(input?: NotifyInput): NotifyHandler {
  return notify('error', input)
}

export function notifyInfo(input?: NotifyInput): NotifyHandler {
  return notify('info', input)
}

export { notify }
