export type RuntimeStatusTone = 'success' | 'warning' | 'danger' | 'info'

export type RuntimeStatusCell = {
  label: string
  type: RuntimeStatusTone
}

export type RuntimeStatusRow = {
  key: string
  service: string
  runtime: RuntimeStatusCell
  health: RuntimeStatusCell
  availability: RuntimeStatusCell
  details: string[]
}
