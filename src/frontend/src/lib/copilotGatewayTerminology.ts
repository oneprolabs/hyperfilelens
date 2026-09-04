export type CopilotGatewayKind = 'public' | 'private'

export function copilotGatewayKind(
  gatewayScope: string | null | undefined,
  selectionMode: string | null | undefined,
): CopilotGatewayKind {
  if (gatewayScope === 'organization' || gatewayScope === 'user') return 'private'
  if (gatewayScope === 'platform') return 'public'
  return selectionMode === 'manual' ? 'private' : 'public'
}
