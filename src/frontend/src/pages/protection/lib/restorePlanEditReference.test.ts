import { describe, expect, it } from 'vitest'
import { restorePlanTargetReferenceIsValid, savedRestoreTargetReference } from './restorePlanEditReference'

const configs = [
  { id: 7, recovery_plans: [
    { id: 11, target_type: 'agent', target_ref_id: 101 },
    { id: 12, target_type: 'nas', target_ref_id: 201 },
  ] },
  { id: 8, recovery_plans: [{ id: 21, target_type: 'agent', target_ref_id: 102 }] },
]
const undiscovered = { getRecord: () => undefined, isSelectable: () => false }
const draft = { id: 'restore-plan-11', targetHostId: 'agent:101' }

describe('Saved restore target references, not execution grants', () => {
  it('retains references only in their own edited configuration and persisted plan', () => {
    expect(savedRestoreTargetReference(configs, 7, draft.id)).toBe('agent:101')
    expect(savedRestoreTargetReference(configs, 7, 'restore-plan-12')).toBe('nas:201')
    expect(restorePlanTargetReferenceIsValid(configs, 7, draft, undiscovered)).toBe(true)
    expect(restorePlanTargetReferenceIsValid(configs, 8, draft, undiscovered)).toBe(false)
    expect(restorePlanTargetReferenceIsValid([], 7, draft, undiscovered)).toBe(false)
    expect(restorePlanTargetReferenceIsValid(configs, undefined, draft, undiscovered)).toBe(false)
  })

  it('does not authorize newly selected targets or new mappings from an existing reference', () => {
    for (const targetHostId of ['', 'agent:102', 'nas:101']) {
      expect(restorePlanTargetReferenceIsValid(configs, 7, { ...draft, targetHostId }, undiscovered)).toBe(false)
    }
    for (const id of ['recovery-dir-new', 'restore-plan-21', 'restore-plan-11-copy']) {
      expect(restorePlanTargetReferenceIsValid(configs, 7, { ...draft, id }, undiscovered)).toBe(false)
    }
  })

  it('preserves readiness checks for targets actually known to inventory', () => {
    const known = { getRecord: () => ({ id: 'agent:101' }), isSelectable: () => false }
    expect(restorePlanTargetReferenceIsValid(configs, 7, draft, known)).toBe(false)
    expect(restorePlanTargetReferenceIsValid([], undefined, {
      id: 'new-plan', targetHostId: 'agent:101',
    }, { ...known, isSelectable: () => true })).toBe(true)
  })

  it.each(['', 'restore-plan-0', 'restore-plan--1', 'restore-plan-1.1', 'restore-plan-11x', 'restore-plan-999999999999999999999'])(
    'rejects malformed or unsafe plan identity %s', id => {
      expect(savedRestoreTargetReference(configs, 7, id)).toBeUndefined()
    },
  )

  it.each([undefined, 0, -1, NaN, Number.MAX_SAFE_INTEGER + 1])(
    'requires a valid edited configuration identity %s', configId => {
      expect(savedRestoreTargetReference(configs, configId, draft.id)).toBeUndefined()
    },
  )

  it('does not invent valid targets from malformed backend reference data', () => {
    for (const target of [
      { target_type: 'proxy', target_ref_id: 101 },
      { target_type: 'agent', target_ref_id: 0 },
      { target_type: 'nas', target_ref_id: null },
      { target_type: 'agent', target_ref_id: Number.MAX_SAFE_INTEGER + 1 },
    ]) {
      expect(savedRestoreTargetReference([
        { id: 7, recovery_plans: [{ id: 11, ...target }] },
      ], 7, draft.id)).toBeUndefined()
    }
  })
})
