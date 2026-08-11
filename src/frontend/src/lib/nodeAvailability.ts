import type { ApiNode } from '../types/node'

export function summarizeNodeAvailability(
  nodes: Array<Pick<ApiNode, 'availability'>>,
): { online: number; offline: number } {
  let online = 0
  let offline = 0
  for (const node of nodes) {
    if (node.availability === 'online') online += 1
    if (node.availability === 'offline') offline += 1
  }
  return { online, offline }
}
