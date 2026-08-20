import type {
  LensChatAttachment,
  LensChatMessage,
  LensRunFeedback,
  LensRunOutputFile,
} from '../../../lib/lensApi'

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
  completedAt?: string | null
  runId?: string
  thinking?: LensChatMessage['thinking']
  attachments?: LensChatMessage['attachments']
  outputFiles?: LensRunOutputFile[]
  feedback?: LensRunFeedback | null
}

export type CopilotFeedbackUpdate = {
  sessionId: number
  messageId: string
  runId: string
  feedback: LensRunFeedback | null
}

export type CopilotRetryDraft = {
  sessionId: number
  question: string
  runId: string
}
