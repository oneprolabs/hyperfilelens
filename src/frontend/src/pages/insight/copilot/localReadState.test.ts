import { describe, expect, it } from 'vitest'
import { advanceLocalReadVersion, compareReplyVersions, withLocalReadState } from './localReadState'

const firstReply = '2026-10-21T01:00:00.000001Z'
const nextReply = '2026-10-21T01:00:00.000002Z'

describe('component-local Chat read state', () => {
  it('clears only observed replies and never mutates the server snapshot', () => {
    const row = { id: 7, has_unread: true, last_assistant_message_at: firstReply }
    expect(withLocalReadState(row, firstReply)).toEqual({ ...row, has_unread: false })
    expect(row.has_unread).toBe(true)
    expect(withLocalReadState(row)).toBe(row)
  })

  it('does not let an older list or sync result restore a known unread marker', () => {
    expect(withLocalReadState({
      has_unread: true, last_assistant_message_at: firstReply,
    }, nextReply).has_unread).toBe(false)
    expect(advanceLocalReadVersion(nextReply, firstReply)).toBe(nextReply)
  })

  it('keeps a genuinely new reply unread even within the same millisecond', () => {
    expect(Date.parse(firstReply)).toBe(Date.parse(nextReply))
    expect(compareReplyVersions(nextReply, firstReply)).toBe(1)
    expect(compareReplyVersions(firstReply, nextReply)).toBe(-1)
    expect(withLocalReadState({
      has_unread: true, last_assistant_message_at: nextReply,
    }, firstReply).has_unread).toBe(true)
    expect(advanceLocalReadVersion(firstReply, nextReply)).toBe(nextReply)
  })

  it('compares timezone offsets and fractional precision consistently', () => {
    expect(compareReplyVersions(firstReply, '2026-10-21T09:00:00.000001+08:00')).toBe(0)
    expect(compareReplyVersions('2026-10-21T01:00:00Z', '2026-10-21T01:00:00.000000Z')).toBe(0)
    expect(compareReplyVersions('2026-10-21T01:00:00.1Z', '2026-10-21T01:00:00.100000Z')).toBe(0)
    expect(compareReplyVersions('2026-10-21T01:00:01Z', nextReply)).toBe(1)
  })

  it.each([null, undefined, 'not-a-timestamp'])('does not suppress unversioned unread data (%s)', (version) => {
    const row = { has_unread: true, last_assistant_message_at: version }
    expect(withLocalReadState(row, firstReply)).toBe(row)
    expect(advanceLocalReadVersion(firstReply, version)).toBe(firstReply)
  })

  it('does not manufacture unread state and initializes only from a valid server version', () => {
    const row = { has_unread: false, last_assistant_message_at: nextReply }
    expect(withLocalReadState(row, firstReply)).toBe(row)
    expect(advanceLocalReadVersion(undefined, firstReply)).toBe(firstReply)
    expect(advanceLocalReadVersion(undefined, 'bad')).toBeUndefined()
    expect(compareReplyVersions('bad', firstReply)).toBeNull()
  })
})
