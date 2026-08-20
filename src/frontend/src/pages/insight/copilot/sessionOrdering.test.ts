import { describe, expect, it } from 'vitest'
import type { LensSessionLink } from '../../../lib/lensApi'
import { toSessionRows } from './sessionOrdering'

function session(
  id: number,
  createdAt: string,
  lastMessageAt: string | null,
  pinnedAt: string | null = null,
): LensSessionLink {
  return {
    id,
    title: `Chat ${id}`,
    created_at: createdAt,
    updated_at: createdAt,
    last_message_at: lastMessageAt,
    last_assistant_message_at: lastMessageAt,
    last_viewed_at: null,
    has_unread: false,
    pinned_at: pinnedAt,
    knowledge_source: null,
    knowledge_source_name: null,
    sl_session_uuid: null,
    sl_assistant_uuid: null,
    agent_model_ref: null,
    backup_config_id: null,
    backup_source_name: null,
    backup_source_snapshot_id: null,
    snapshot_created_at: null,
    snapshot_size_bytes: null,
    source_scopes_json: [],
    gateway_link: null,
    gateway_selection_mode: 'auto',
    gateway_name: null,
    gateway_scope: null,
    status: 'active',
  }
}

describe('copilot session ordering', () => {
  const now = new Date('2026-08-20T12:00:00+08:00')

  it('keeps normal chats ordered by creation time when answers arrive', () => {
    const older = session(
      1,
      '2026-08-20T08:00:00+08:00',
      '2026-08-20T11:59:00+08:00',
    )
    const newer = session(
      2,
      '2026-08-20T09:00:00+08:00',
      '2026-08-20T09:01:00+08:00',
    )

    expect(toSessionRows([older, newer], now).map((row) => row.id)).toEqual([2, 1])
    expect(toSessionRows([older, newer], now).map((row) => row.group)).toEqual([
      'today',
      'today',
    ])
  })

  it('moves chats only for an explicit pin and keeps deterministic ties', () => {
    const rows = [
      session(1, '2026-08-20T08:00:00+08:00', null),
      session(2, '2026-08-20T09:00:00+08:00', null, '2026-08-20T10:00:00+08:00'),
      session(3, '2026-08-20T07:00:00+08:00', null, '2026-08-20T11:00:00+08:00'),
    ]

    expect(toSessionRows(rows, now).map((row) => row.id)).toEqual([3, 2, 1])
  })
})
