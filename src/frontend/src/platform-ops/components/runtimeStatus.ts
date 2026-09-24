export type RuntimeStatusTone = 'success' | 'warning' | 'danger' | 'info'

export type RuntimeStatusCell = {
  label: string
  type: RuntimeStatusTone
}

export type RuntimeStatusNoticeLevel = 'info' | 'warning' | 'error'

export type RuntimeStatusNotice = {
  message: string
  level: RuntimeStatusNoticeLevel
}

export type RuntimeStatusRow = {
  key: string
  service: string
  runtime: RuntimeStatusCell
  health: RuntimeStatusCell
  availability: RuntimeStatusCell
  details: string[]
  notices?: RuntimeStatusNotice[]
}
