export type AssignmentPatch = {
  id?: number
  assignment_id?: number
  assignee?: string | null
  assignee_id?: number | null
  status?: string
  assignment_status?: string
  claimed_at?: string | null
  claim_source?: string | null
}

export function applyAssignment<T extends Record<string, any>>(row: T, assignment?: AssignmentPatch | null): T {
  const itemId = row.item_id ?? row.id
  return {
    ...row,
    id: row.id ?? itemId,
    item_id: itemId,
    assignment_id: assignment?.id ?? assignment?.assignment_id ?? row.assignment_id,
    assignee: assignment?.assignee ?? row.assignee,
    assignee_id: assignment?.assignee_id ?? row.assignee_id,
    assignment_status: assignment?.assignment_status || assignment?.status || row.assignment_status,
    claimed_at: assignment?.claimed_at ?? row.claimed_at,
    claim_source: assignment?.claim_source ?? row.claim_source,
  }
}
