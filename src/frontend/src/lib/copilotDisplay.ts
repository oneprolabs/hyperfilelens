type Translate = (key: string, fallback?: string | Record<string, unknown>) => string

export function copilotReasonLabel(t: Translate, reason: string, fallback: string) {
  return t(`insight.copilot.conversionReasons.${reason}`, fallback)
}

export function copilotWarningLabel(t: Translate, code: string, fallback: string) {
  return t(`insight.copilot.conversionWarnings.${code}`, fallback)
}
