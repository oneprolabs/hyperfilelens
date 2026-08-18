import { describe, expect, it } from 'vitest'
import type { BackupPolicy } from './protectionPolicyApi'
import {
  backupPolicyToForm,
  createEmptyPolicyForm,
  formatScheduleStartForDisplay,
  getScheduleTimezoneOptions,
  policyFormToWritePayload,
  quickScheduleToCron,
  summarizeSchedule,
  validateRetentionForm,
  validateScheduleForm,
} from './protectionPolicyFormModel'

function policyWithSchedule(schedule: BackupPolicy['schedule']): BackupPolicy {
  return {
    id: 1,
    name: 'Schedule policy',
    is_active: true,
    schedule,
    retention: {
      enabled: false,
      recent_points: 1,
      hourly_enabled: false,
      hourly_hours: 1,
      daily_enabled: false,
      daily_days: 1,
      weekly_enabled: false,
      weekly_weeks: 1,
      monthly_enabled: false,
      monthly_months: 1,
      annual_enabled: false,
      annual_years: 1,
    },
    throttling: { enabled: false, unlimited: true, rate_mbps: 0 },
    error_handling: {
      enabled: false,
      ignore_directory_read_errors: false,
      ignore_file_read_errors: false,
      ignore_unknown_entries: false,
    },
    schedule_summary: '',
    retention_summary: '',
    related_backup_count: 0,
    created_at: '2026-07-30T00:00:00Z',
    updated_at: '2026-07-30T00:00:00Z',
  }
}

describe('protection policy schedule mapping', () => {
  it('rejects an empty enabled retention period and omits disabled periods from writes', () => {
    const form = createEmptyPolicyForm()
    form.retentionShortHourly = true
    form.retentionShortDaysMax = undefined

    expect(validateRetentionForm(form)).toBe('hourly retention must be at least 1 when enabled.')

    form.retentionShortHourly = false
    form.retentionMidDaily = false
    form.retentionLongMonthly = false
    form.retentionShortDaysMax = undefined
    form.retentionMidDaysMax = undefined
    form.retentionLongMonths = undefined

    expect(validateRetentionForm(form)).toBe('')
    expect(policyFormToWritePayload(form).retention).toMatchObject({
      hourly_enabled: false,
      daily_enabled: false,
      monthly_enabled: false,
    })
    expect(policyFormToWritePayload(form).retention).not.toHaveProperty('hourly_hours')
    expect(policyFormToWritePayload(form).retention).not.toHaveProperty('daily_days')
    expect(policyFormToWritePayload(form).retention).not.toHaveProperty('monthly_months')
  })

  it('ignores stale disabled retention periods when loading a policy', () => {
    const form = backupPolicyToForm(policyWithSchedule({ enabled: true, cron_expr: '0 2 * * *' }))

    expect(form.retentionShortHourly).toBe(false)
    expect(form.retentionShortDaysMax).toBeUndefined()
    expect(form.retentionMidDaily).toBe(false)
    expect(form.retentionMidDaysMax).toBeUndefined()
    expect(form.retentionLongMonthly).toBe(false)
    expect(form.retentionLongMonths).toBeUndefined()
  })

  it('labels timezones with their current GMT offsets and sorts by offset', () => {
    const options = getScheduleTimezoneOptions('Asia/Shanghai', new Date('2026-08-08T00:00:00Z'))
    const shanghai = options.find((option) => option.value === 'Asia/Shanghai')
    const newYork = options.find((option) => option.value === 'America/New_York')
    const utc = options.find((option) => option.value === 'UTC')

    expect(shanghai).toMatchObject({ label: '(GMT+08:00) Asia/Shanghai', offsetMinutes: 480 })
    expect(newYork).toMatchObject({ label: '(GMT-04:00) America/New_York', offsetMinutes: -240 })
    expect(utc).toMatchObject({ label: '(GMT+00:00) UTC', offsetMinutes: 0 })
    expect(options.indexOf(newYork!)).toBeLessThan(options.indexOf(utc!))
    expect(options.indexOf(utc!)).toBeLessThan(options.indexOf(shanghai!))
  })

  it('formats schedule wall-clock start times without changing their timezone meaning', () => {
    const form = createEmptyPolicyForm()
    form.scheduleStartsAt = '2026-08-13T14:30:45'

    expect(formatScheduleStartForDisplay(form.scheduleStartsAt)).toBe('2026-08-13 14:30:45')
    expect(summarizeSchedule(form)).toContain('starts 2026-08-13 14:30:45')
    expect(summarizeSchedule(form)).not.toContain('2026-08-13T14:30:45')
  })

  it('serializes hour and day intervals through the shared mapper', () => {
    const form = createEmptyPolicyForm()
    form.scheduleTimezone = 'UTC'
    form.scheduleStartsAt = '2026-07-31T02:30:15'
    form.simpleIntervalUnit = 'hour'
    form.simpleIntervalValue = 6

    expect(quickScheduleToCron(form)).toBe('0 */6 * * *')
    expect(policyFormToWritePayload(form).schedule).toMatchObject({
      mode: 'interval',
      interval_unit: 'hour',
      interval_value: 6,
      cron_expr: '0 */6 * * *',
      starts_at: '2026-07-31T02:30:15',
    })

    form.simpleIntervalUnit = 'day'
    form.simpleIntervalValue = 2
    expect(policyFormToWritePayload(form).schedule).toMatchObject({
      interval_unit: 'day',
      interval_value: 2,
      cron_expr: '0 0 */2 * *',
    })
  })

  it('round-trips a timezone-aware multi-weekday schedule', () => {
    const form = backupPolicyToForm(policyWithSchedule({
      enabled: true,
      mode: 'weekly',
      timezone: 'Asia/Shanghai',
      starts_at: '2026-07-31T09:30',
      time: '09:30',
      weekdays: [1, 3, 5],
      cron_expr: '30 9 * * 1,3,5',
    }))

    expect(form).toMatchObject({
      freqMode: 'simple',
      quickScheduleType: 'weekly',
      scheduleTimezone: 'Asia/Shanghai',
      scheduleStartsAt: '2026-07-31T09:30:00',
      scheduleTime: '09:30',
      scheduleWeekdays: [1, 3, 5],
    })
    expect(policyFormToWritePayload(form).schedule).toMatchObject({
      mode: 'weekly',
      timezone: 'Asia/Shanghai',
      starts_at: '2026-07-31T09:30:00',
      time: '09:30',
      weekdays: [1, 3, 5],
      cron_expr: '30 9 * * 1,3,5',
    })
    expect(summarizeSchedule(form)).toContain('Monday, Wednesday, Friday')
  })

  it('preserves month dates and end-of-month as structured fields', () => {
    const form = createEmptyPolicyForm()
    form.quickScheduleType = 'monthly'
    form.scheduleTimezone = 'America/New_York'
    form.scheduleStartsAt = '2026-08-01T08:00'
    form.scheduleTime = '08:00'
    form.scheduleMonthDays = [1, 15]
    form.scheduleMonthEnd = true

    expect(validateScheduleForm(form)).toBe('')
    expect(policyFormToWritePayload(form).schedule).toMatchObject({
      mode: 'monthly',
      timezone: 'America/New_York',
      time: '08:00',
      month_days: [1, 15],
      month_end: true,
      cron_expr: '0 8 1,15,31 * *',
    })
  })

  it('keeps legacy cron-only policies on UTC without an activation gate', () => {
    const interval = backupPolicyToForm(policyWithSchedule({
      enabled: true,
      cron_expr: '0 */4 * * *',
    }))
    const advanced = backupPolicyToForm(policyWithSchedule({
      enabled: true,
      cron_expr: '30 9 * * 1,3,5',
    }))

    expect(interval).toMatchObject({
      freqMode: 'simple',
      quickScheduleType: 'interval',
      scheduleTimezone: 'UTC',
      scheduleStartsAt: '',
    })
    expect(advanced).toMatchObject({
      freqMode: 'advanced',
      scheduleTimezone: 'UTC',
      scheduleStartsAt: '',
    })
  })

  it('rejects empty calendar selections and invalid start dates', () => {
    const form = createEmptyPolicyForm()
    form.quickScheduleType = 'weekly'
    form.scheduleWeekdays = []
    expect(validateScheduleForm(form)).toBe('Select at least one weekday.')

    form.quickScheduleType = 'monthly'
    form.scheduleMonthDays = []
    form.scheduleMonthEnd = false
    expect(validateScheduleForm(form)).toBe('Select at least one month day or end of month.')

    form.scheduleStartsAt = '2026-02-31T09:00:00'
    expect(validateScheduleForm(form)).toBe('Start time must be a valid date and time.')

    form.scheduleStartsAt = '2026-08-13T14:30:60'
    expect(validateScheduleForm(form)).toBe('Start time must be a valid date and time.')
  })

  it('normalizes legacy minute precision before writing', () => {
    const form = createEmptyPolicyForm()
    form.scheduleStartsAt = '2026-08-13T14:30'

    expect(validateScheduleForm(form)).toBe('')
    expect(policyFormToWritePayload(form).schedule.starts_at).toBe('2026-08-13T14:30:00')
  })
})
