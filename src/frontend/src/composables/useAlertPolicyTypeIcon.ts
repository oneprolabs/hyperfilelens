import type { Component } from 'vue'
import { Activity, HeartPulse, ListTodo, Logs } from 'lucide-vue-next'

const alertPolicyTypeIcons = {
  metric: Activity,
  task: ListTodo,
  event: Logs,
  health: HeartPulse,
  // Legacy values still used in older list rows until refresh.
  availability: HeartPulse,
  system: Activity,
} as const

export function getAlertPolicyTypeIcon(type?: string | null): Component | null {
  if (type && type in alertPolicyTypeIcons) {
    return alertPolicyTypeIcons[type as keyof typeof alertPolicyTypeIcons]
  }
  return null
}
