import type {
  LensChatAttachment,
  LensChatMessage,
  LensCitation,
  LensPlannedEvidence,
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
  isWelcome?: boolean
  isError?: boolean
  createdAt?: string
  completedAt?: string | null
  runId?: string
  thinking?: LensChatMessage['thinking']
  citations?: LensCitation[]
  plannedEvidence?: LensPlannedEvidence
  clarificationRequest?: { requestId: string; question: string }
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
