import type { LensChatAttachment, LensChatMessage, LensRunOutputFile } from '../../../lib/lensApi'

export type CopilotComposerAttachment = LensChatAttachment & {
  key: string
  localUrl?: string
  status: 'uploading' | 'ready'
}

export type CopilotDisplayMessage = {
  id: string
  role: 'user' | 'assistant'
  text?: string
  starterChips?: boolean
  isError?: boolean
  createdAt?: string
  runId?: string
  thinking?: LensChatMessage['thinking']
  attachments?: LensChatMessage['attachments']
  outputFiles?: LensRunOutputFile[]
}
