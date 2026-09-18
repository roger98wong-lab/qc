import { applyAssignment } from './applyAssignment.js'

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message)
}

const row = { id: 88, item_id: 88, slice_id: 'mbackend:2:718332', assignment_status: 'unassigned' }
const claimed = applyAssignment(row, { id: 501, status: 'in_progress', assignee: 'test', assignee_id: 2, claim_source: 'self_claim', claimed_at: '2026-09-17T00:00:00' })
assert(claimed.id === 88, `id should stay 88, got ${claimed.id}`)
assert(claimed.item_id === 88, `item_id should stay 88, got ${claimed.item_id}`)
assert(claimed.assignment_id === 501, `assignment_id should be 501, got ${claimed.assignment_id}`)
assert(claimed.assignment_status === 'in_progress', `status should be in_progress, got ${claimed.assignment_status}`)
assert(claimed.assignee === 'test', `assignee should be test, got ${claimed.assignee}`)
assert(claimed.slice_id === 'mbackend:2:718332', 'business fields must remain')

const released = applyAssignment(claimed, { id: claimed.assignment_id, status: 'cancelled', assignee: null, assignee_id: null })
assert(released.id === 88, 'id must still be the business key after later assignment updates')
console.log('applyAssignment tests passed')
