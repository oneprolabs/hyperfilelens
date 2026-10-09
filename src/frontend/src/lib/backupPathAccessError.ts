/** Only structured Agent errors can select reason-specific path guidance. */
export function backupPathAccessSummaryKey(code: string): string | undefined {
  switch (code) {
    case 'AGENT.PATH_OUTSIDE_USER_HOME':
      return 'errors.codes.agentPathOutsideUserHomeShort'
    case 'AGENT.PATH_READ_PERMISSION_DENIED':
      return 'errors.codes.agentPathReadPermissionDeniedShort'
    case 'AGENT.PATH_PERMISSION_DENIED':
      return 'errors.codes.agentPathPermissionDeniedShort'
    default:
      return undefined
  }
}
